"""Exercise Skill and Plan execution through the Chat API boundary."""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from hostpilot.api import create_app
from hostpilot.config import Settings
from hostpilot.contracts import (
    ConversationMessage,
    ExecutionEnvironment,
    MessageRole,
    TimeoutProfile,
    ToolDescriptor,
    ToolSideEffect,
    ToolType,
    TrustLevel,
)
from hostpilot.models import ModelResponse, ModelToolCall
from hostpilot.orchestration.runtime import ChatAgentResponse
from hostpilot.orchestration.skill_runtime import (
    SkillEngineAgentAdapter,
    SkillSelection,
)
from hostpilot.policy import PolicyEngine
from hostpilot.runtime import RequestContext
from hostpilot.skills.model_plan import ModelPlanFallback, build_model_plan_executor
from hostpilot.skills.runtime import (
    AssessmentDisposition,
    BuiltinExecutorRegistry,
    SkillAssessment,
    SkillEngine,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    StepActionCall,
    StepActionRegistration,
    StepActionRegistry,
    StepActionResult,
)
from hostpilot.skills.yaml_registry import SideEffectLevel, YamlSkillRegistry
from hostpilot.tools.executor import ToolExecutor
from hostpilot.tools.facade import ToolFacade
from hostpilot.tools.registry import ToolRegistration, ToolRegistry


class AcceptingAssessor:
    async def assess(self, package, payload, context, *, budget, snapshot=None):
        del package, payload, context, budget, snapshot
        return SkillAssessment(AssessmentDisposition.ACCEPT, 1.0, "accepted")


class ScriptedSelector:
    def __init__(self) -> None:
        self.request_context: RequestContext | None = None

    async def select(self, message: str, request_context: RequestContext) -> SkillSelection:
        self.request_context = request_context
        return SkillSelection(
            "business/meeting-minutes",
            {"notes": message},
            "test_fixture_selected",
            execution_mode="yaml",
        )


class MarkdownSelector:
    def __init__(self) -> None:
        self.request_context: RequestContext | None = None

    async def select(self, message: str, request_context: RequestContext) -> SkillSelection:
        del message
        self.request_context = request_context
        return SkillSelection(
            "legacy/meeting-notes",
            {"message": "整理会议记录"},
            "test_markdown_selected",
            execution_mode="markdown",
        )


class RecordingSkillEngine(SkillEngine):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.requests: list[SkillExecutionRequest] = []

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        self.requests.append(request)
        return await super().execute(request)


class InMemoryChatRuntime:
    def __init__(self) -> None:
        self.messages_by_id: dict[str, list[ConversationMessage]] = {}

    async def read_messages(self, session_id: str) -> tuple[ConversationMessage, ...]:
        return tuple(self.messages_by_id.get(session_id, ()))

    async def respond(self, message: str, request_context: RequestContext) -> ChatAgentResponse:
        del message, request_context
        raise AssertionError("A selected Skill must handle this request.")

    async def record_turn(self, session_id: str, user_message: str, assistant_message: str) -> None:
        self.messages_by_id.setdefault(session_id, []).extend(
            (
                ConversationMessage(role=MessageRole.USER, content=user_message),
                ConversationMessage(role=MessageRole.ASSISTANT, content=assistant_message),
            )
        )

    async def aclose(self) -> None:
        return None


class MarkdownChatRuntime(InMemoryChatRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.markdown_requests: list[tuple[str, str, RequestContext]] = []

    async def respond_with_skill(
        self,
        message: str,
        request_context: RequestContext,
        *,
        skill_id: str,
    ) -> ChatAgentResponse:
        self.markdown_requests.append((message, skill_id, request_context))
        return ChatAgentResponse("Markdown Skill result")


class UnusedSkillEngine:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        del request
        self.calls += 1
        raise AssertionError("Markdown Skills must not enter the YAML SkillEngine.")


def test_skill_failure_falls_back_to_governed_plan_tool_through_chat_api(tmp_path) -> None:
    from hostpilot.evaluation.fixtures import write_fixture_skills

    skill_id = "business/meeting-minutes"
    safe_tool_id = "submit_summary"
    denied_tool_id = "send_message"
    skills_dir = tmp_path / "skills"
    write_fixture_skills(
        skills_dir,
        [
            {
                "skill_id": skill_id,
                "required_input": "notes",
                "intent_summary": "整理用户提供的会议记录",
                "examples": ["整理会议纪要"],
            }
        ],
    )
    import yaml

    skill_manifest_path = skills_dir / "meeting-minutes" / "skill.yaml"
    skill_manifest = yaml.safe_load(skill_manifest_path.read_text(encoding="utf-8"))
    skill_manifest["dependencies"]["tools"] = [safe_tool_id]
    skill_manifest_path.write_text(
        yaml.safe_dump(skill_manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    descriptors = {
        safe_tool_id: ToolDescriptor(
            name=safe_tool_id,
            description="提交会议摘要",
            access_summary="将摘要提交到内存测试目标",
            input_schema={
                "type": "object",
                "properties": {"summary": {"type": "string"}},
                "required": ["summary"],
                "additionalProperties": False,
            },
            tool_type=ToolType.ACTION,
            side_effect=ToolSideEffect.POSSIBLE,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=5),
            high_impact=True,
        ),
        denied_tool_id: ToolDescriptor(
            name=denied_tool_id,
            description="发送消息",
            access_summary="发送外部消息",
            input_schema={"type": "object"},
            tool_type=ToolType.ACTION,
            side_effect=ToolSideEffect.POSSIBLE,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=5),
            high_impact=True,
        ),
    }
    handler_calls: list[dict[str, str]] = []

    async def memory_handler(arguments):
        handler_calls.append(dict(arguments))
        return "summary submitted to memory fixture"

    registry = ToolRegistry()
    for descriptor in descriptors.values():
        registry.register(
            ToolRegistration(
                descriptor=descriptor,
                validate_arguments=lambda arguments: arguments,
                handler=memory_handler,
                verify_result=lambda result, arguments: None,
                confirmation_summary=lambda arguments: f"提交摘要：{arguments['summary']}",
            )
        )

    class GovernedMemoryProvider:
        def __init__(self) -> None:
            self.registry = registry
            self.executor = ToolExecutor(registry, PolicyEngine(), audit=lambda _: None)
            self.calls: list[tuple[str, object, object]] = []

        def descriptors(self):
            return self.registry.descriptors()

        async def call_tool(self, name, arguments, *, budget, confirmation_provider=None):
            self.calls.append((name, budget, confirmation_provider))
            budget.consume_tool_call()
            return await self.executor.execute(
                name,
                arguments,
                budget=budget,
                confirmation_provider=confirmation_provider,
            )

    class ScriptedPlanProvider:
        def __init__(self) -> None:
            self.requests = []

        async def complete(self, request):
            self.requests.append(request)
            if request.tools:
                assert {tool.name for tool in request.tools} == {safe_tool_id}
                if not any(message.role.value == "tool" for message in request.messages):
                    return ModelResponse(
                        text="",
                        tool_calls=(
                            ModelToolCall(
                                "submit-summary",
                                safe_tool_id,
                                {"summary": "预算评审安排在周五"},
                            ),
                        ),
                    )
                return ModelResponse(text="会议摘要已提交。")
            if "tool_task" in request.messages[0].content:
                return ModelResponse(
                    text=(
                        '{"tasks":[{"task_id":"submit-summary",'
                        '"description":"提交整理后的会议摘要",'
                        '"execution_type":"tool_task",'
                        '"expected_output":"摘要提交结果"}]}'
                    )
                )
            return ModelResponse(
                text=(
                    '{"accepted":true,"reason_code":"verified",'
                    '"final_output":{"answer":"会议摘要已提交"}}'
                )
            )

    class FailingSkillEngine(SkillEngine):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.requests: list[SkillExecutionRequest] = []

        async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
            self.requests.append(request)
            if request.skill_id == skill_id:
                return SkillExecutionResult(
                    SkillExecutionStatus.FAILED,
                    output={"error_code": "skill_fixture_failed"},
                )
            return await super().execute(request)

    class PlanSelector:
        def __init__(self) -> None:
            self.request_context: RequestContext | None = None

        async def select(self, message: str, request_context: RequestContext) -> SkillSelection:
            self.request_context = request_context
            return SkillSelection(
                skill_id,
                {"notes": message},
                "test_fixture_selected",
                candidate_skill_ids=(skill_id,),
            )

    class AcceptingPlanAssessor:
        async def assess(self, package, payload, context, *, budget, snapshot=None):
            del package, payload, context, budget, snapshot
            return SkillAssessment(AssessmentDisposition.ACCEPT, 1.0, "accepted")

    from hostpilot.skills.yaml_registry import YamlSkillRegistry

    yaml_registry = YamlSkillRegistry(skills_dir, registered_tool_ids=tuple(descriptors))
    builtins = BuiltinExecutorRegistry()
    skill_engine = FailingSkillEngine(
        yaml_registry,
        StepActionRegistry(),
        builtins,
        AcceptingPlanAssessor(),
        AcceptingPlanAssessor(),
    )
    model = ScriptedPlanProvider()
    governed_provider = GovernedMemoryProvider()
    facade = ToolFacade(governed_provider)
    plan_executor = build_model_plan_executor(
        model,
        skills=yaml_registry,
        skill_runtime=skill_engine,
        tool_provider=facade,
        tool_descriptors=descriptors,
    )
    from hostpilot.skills.runtime import register_core_builtin_skills

    register_core_builtin_skills(
        builtins,
        llm=AcceptingAssessor(),
        plan_executor=plan_executor,
    )
    skill_engine.plan_fallback = ModelPlanFallback(plan_executor)
    selector = PlanSelector()
    chat_runtime = InMemoryChatRuntime()
    runtime = SkillEngineAgentAdapter(chat_runtime, skill_engine, selector)
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "session_id": session_id,
                    "message": "整理并提交会议摘要：预算评审安排在周五。",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation["type"] == "tool.confirmation.request"
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": confirmation["data"]["request_id"],
                    "decision": "approved",
                }
            )
            response = websocket.receive_json()
        history = client.get(f"/api/v1/conversations/{session_id}/messages")

    planner_request = model.requests[0]
    planner_context = json.loads(planner_request.messages[-1].content)
    assert planner_context["skill_evaluation_failure"] == "skill_fixture_failed"
    assert selector.request_context is skill_engine.requests[0].request_context
    assert skill_engine.requests[1].request_context is selector.request_context
    assert skill_engine.requests[1].candidate_skill_ids == (skill_id,)
    assert skill_engine.requests[1].payload["candidate_skill_ids"] == [skill_id]
    assert response["type"] == "chat.response"
    assert response["data"]["assistant_message"].startswith("会议摘要已提交")
    assert "skill_fixture_failed" in response["data"]["assistant_message"]
    assert [message["content"] for message in history.json()["messages"]] == [
        "整理并提交会议摘要：预算评审安排在周五。",
        response["data"]["assistant_message"],
    ]
    assert [call[0] for call in governed_provider.calls] == [safe_tool_id]
    assert governed_provider.calls[0][1] is selector.request_context.budget
    assert governed_provider.calls[0][2] is not None
    assert governed_provider.calls[0][2]._delegate is selector.request_context.confirmation_provider
    assert handler_calls == [{"summary": "预算评审安排在周五"}]


def test_selected_yaml_skill_runs_through_chat_api_and_persists_context(tmp_path) -> None:
    from hostpilot.evaluation.fixtures import write_fixture_skills

    skills_dir = tmp_path / "skills"
    write_fixture_skills(
        skills_dir,
        [
            {
                "skill_id": "business/meeting-minutes",
                "required_input": "notes",
                "intent_summary": "整理用户提供的会议记录",
                "examples": ["整理会议纪要"],
            }
        ],
    )
    action_calls: list[StepActionCall] = []

    async def evaluation_action(call: StepActionCall) -> StepActionResult:
        action_calls.append(call)
        return StepActionResult({"result": call.inputs["notes"]})

    actions = StepActionRegistry()
    actions.register(
        StepActionRegistration("evaluation.noop", SideEffectLevel.READ_ONLY, evaluation_action)
    )
    engine = RecordingSkillEngine(
        YamlSkillRegistry(skills_dir),
        actions,
        BuiltinExecutorRegistry(),
        AcceptingAssessor(),
        AcceptingAssessor(),
    )
    selector = ScriptedSelector()
    chat_runtime = InMemoryChatRuntime()
    runtime = SkillEngineAgentAdapter(chat_runtime, engine, selector)
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        created = client.post("/api/v1/conversations", json={"title": "Skill API flow"})
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        response = client.post(
            f"/api/v1/conversations/{session_id}/messages",
            json={"message": "记录：预算评审安排在周五。"},
        )
        history = client.get(f"/api/v1/conversations/{session_id}/messages")

    assert response.status_code == 200
    assert response.json()["route"] == "skill_execute"
    assert response.json()["assistant_message"] == '{"result": "记录：预算评审安排在周五。"}'
    assert [message["content"] for message in history.json()["messages"]] == [
        "记录：预算评审安排在周五。",
        '{"result": "记录：预算评审安排在周五。"}',
    ]
    assert selector.request_context is engine.requests[0].request_context
    assert engine.requests[0].skill_id == "business/meeting-minutes"
    assert engine.requests[0].request_context.budget is action_calls[0].budget
    assert action_calls[0].inputs == {"notes": "记录：预算评审安排在周五。"}


def test_selected_markdown_skill_uses_only_markdown_adapter_through_chat_api(tmp_path) -> None:
    selector = MarkdownSelector()
    chat_runtime = MarkdownChatRuntime()
    skill_engine = UnusedSkillEngine()
    runtime = SkillEngineAgentAdapter(chat_runtime, skill_engine, selector)
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        created = client.post("/api/v1/conversations", json={"title": "Markdown Skill API flow"})
        assert created.status_code == 201
        session_id = created.json()["session_id"]

        response = client.post(
            f"/api/v1/conversations/{session_id}/messages",
            json={"message": "整理这段会议记录"},
        )
        history = client.get(f"/api/v1/conversations/{session_id}/messages")

    assert response.status_code == 200
    assert response.json()["route"] == "skill_execute"
    assert response.json()["assistant_message"] == "Markdown Skill result"
    assert [message["content"] for message in history.json()["messages"]] == [
        "整理这段会议记录",
        "Markdown Skill result",
    ]
    assert skill_engine.calls == 0
    assert chat_runtime.markdown_requests[0][1] == "legacy/meeting-notes"
    assert selector.request_context is chat_runtime.markdown_requests[0][2]
