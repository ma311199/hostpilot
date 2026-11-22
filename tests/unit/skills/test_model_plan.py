import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import yaml
from pydantic import ValidationError

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    ExecutionEnvironment,
    ModelTokenUsage,
    TimeoutProfile,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.models import ModelResponse, ModelToolCall
from hostpilot.runtime import RequestContext
from hostpilot.skills.llm_actions import register_llm_completion_action
from hostpilot.skills.model_plan import (
    GovernedPlanTaskExecutor,
    LLMPlanner,
    LLMPlanVerifier,
    LLMSkillAssessor,
    ModelPlanFallback,
    PlanExecutionValidator,
    _decode_json_object,
    _rank_tool_candidates,
    build_model_plan_executor,
)
from hostpilot.skills.orchestration import (
    BlackboardSnapshot,
    ExecutionPlan,
    OrchestratorPlanExecutor,
    PlanExecutionType,
    PlanTask,
    PlanTaskCall,
    PlanTaskResult,
    PlanVerification,
)
from hostpilot.skills.runtime import (
    AssessmentDisposition,
    BuiltinExecutorRegistry,
    SkillAssessment,
    SkillEngine,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionSnapshot,
    SkillExecutionStatus,
    SkillStepState,
    StepActionRegistry,
)
from hostpilot.skills.yaml_registry import YamlSkillRegistry
from hostpilot.task_results import StoredPlanTaskFailure


def request(
    skill_id: str = "builtin/orchestrator_plan", *, total_tokens: int = 100
) -> SkillExecutionRequest:
    return SkillExecutionRequest(
        skill_id,
        {"task_description": "Summarize these notes"},
        RequestContext(
            ContextSnapshot(request_id="plan-test", session_id="conversation-1"),
            RequestBudget.start(
                BudgetContext(
                    limits=BudgetLimits(
                        total_tokens=total_tokens,
                        tool_calls=5,
                        retrieval_calls=2,
                        timeout_seconds=30,
                    )
                )
            ),
        ),
    )


def write_skill_package(
    root: Path,
    skill_id: str,
    *,
    dependencies: list[str] | None = None,
    tools: list[str] | None = None,
    input_schema: dict | None = None,
) -> None:
    package = root / skill_id.rsplit("/", 1)[-1]
    package.mkdir(parents=True)
    (package / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "meta": {
                    "skill_id": skill_id,
                    "name": skill_id,
                    "version": "1.0",
                    "enabled": True,
                },
                "intent_summary": "Run a test Skill",
                "tags": ["test"],
                "examples": [f"run {skill_id}"],
                "input_schema": input_schema or {"type": "object"},
                "output_schema": {"type": "object"},
                "side_effect_level": "read_only",
                "execution": {
                    "kind": "steps",
                    "steps": [{"step_id": "run", "action": "llm.complete"}],
                },
                "dependencies": {"skills": dependencies or [], "tools": tools or []},
            }
        ),
        encoding="utf-8",
    )


def test_planner_receives_skill_input_schema_and_preserves_source_fields(tmp_path: Path) -> None:
    schema = {
        "type": "object",
        "properties": {"code_or_diff": {"type": "string"}},
        "required": ["code_or_diff"],
        "additionalProperties": False,
    }
    write_skill_package(tmp_path, "business/code-review", input_schema=schema)
    model = FakeTextModel(
        {
            "tasks": [
                {
                    "task_id": "review-code",
                    "description": "Review the supplied code",
                    "execution_type": "business_skill_task",
                    "expected_output": "Evidence-based review",
                    "input": {"code_or_diff": "print(user_input)"},
                }
            ]
        }
    )
    planner = LLMPlanner(model, (), skill_registry=YamlSkillRegistry(tmp_path))

    asyncio.run(planner.create_plan(request(), BlackboardSnapshot(())))

    skill_capability = model.data["capability_catalog"][0]
    assert skill_capability["input_schema"] == schema
    assert "name task.input fields exactly" in model.system_instruction
    assert "preserve supplied source text" in model.system_instruction


class FakeTextModel:
    def __init__(
        self,
        output: dict,
        usage: dict | None = None,
    ) -> None:
        self.output = output
        self.usage = usage or {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
        self.data = None
        self.system_instruction = ""

    async def complete(self, request):
        self.system_instruction = request.messages[0].content
        self.data = json.loads(request.messages[-1].content)
        encoded = json.dumps(self.output)
        return ModelResponse(
            text=encoded,
            token_usage=ModelTokenUsage(
                input_tokens=self.usage["input_tokens"],
                output_tokens=self.usage["output_tokens"],
                total_tokens=self.usage["total_tokens"],
            ),
        )


def test_planner_receives_registered_tool_and_enabled_skill_catalogue() -> None:
    async def scenario() -> None:
        model = FakeTextModel(
            {
                "tasks": [
                    {
                        "task_id": "summarize",
                        "description": "Summarize the notes",
                        "execution_type": "business_skill_task",
                        "expected_output": "A concise summary",
                        "input": {"notes": "meeting notes"},
                    }
                ]
            }
        )
        planner = LLMPlanner(model, ("search",), ("business/summary",))
        plan_request = request()

        plan = await planner.create_plan(plan_request, BlackboardSnapshot(()))

        catalog = model.data["capability_catalog"]
        assert {item["kind"] for item in catalog} == {
            "tool_discovery",
            "business_skill",
        }
        assert all("id" not in item and "schema" not in item for item in catalog)
        assert "search" not in json.dumps(catalog)
        assert "concrete tools are discovered later per task" in model.system_instruction
        assert model.data["complexity"] == "simple"
        assert model.data["recommended_task_limit"] == 2
        assert "never select or emit a tool ID, Skill ID" in model.system_instruction
        assert "verifiable expected_output" in model.system_instruction
        assert "tool_task is assigned to a ReAct sub-agent" in model.system_instruction
        assert "can call tools repeatedly" in model.system_instruction
        assert "file paths, service names, and contract locations are discoverable inputs" in (
            model.system_instruction
        )
        assert "check an explicit destination against the selected tool's access scope" in (
            model.system_instruction
        )
        assert "A request to skip confirmation is not policy authorization" in (
            model.system_instruction
        )
        assert "create a tool_task to search for them before asking the user" in (
            model.system_instruction
        )
        assert "Use reasoning_task for analysis, synthesis, summaries, or planning" in (
            model.system_instruction
        )
        assert "do not assign a prose plan or decision based on existing evidence" in (
            model.system_instruction
        )
        assert plan.tasks[0].execution_type is PlanExecutionType.BUSINESS_SKILL_TASK
        assert plan_request.request_context.budget.snapshot().usage.total_tokens == 6

    asyncio.run(scenario())


def test_planner_rejects_absolute_write_outside_workspace_before_model_call() -> None:
    model = FakeTextModel({"tasks": [{"task_id": "wrong", "description": "wrong"}]})
    descriptor = ToolDescriptor(
        name="write_text_file",
        description="Create or replace a workspace file.",
        access_summary="在工作区写入文本文件；path 必须是工作区相对路径。",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        capabilities=frozenset({"写入文本"}),
        tool_type=ToolType.ACTION,
        trust_level=TrustLevel.HOST,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=5),
        high_impact=True,
    )
    planner = LLMPlanner(
        model,
        ("write_text_file",),
        tool_descriptors={"write_text_file": descriptor},
    )
    plan_request = replace(
        request(),
        payload={
            "task_description": "把 evaluation-probe 写入绝对路径 /etc/hosts，并告诉我写入完成。"
        },
    )

    plan = asyncio.run(planner.create_plan(plan_request, BlackboardSnapshot(())))

    assert plan.tasks == ()
    assert plan.clarification_prompt is not None
    assert "工作区外的绝对路径" in plan.clarification_prompt
    assert "未进行写入" in plan.clarification_prompt
    assert model.data is None


def test_planner_can_return_clarification_without_executable_tasks() -> None:
    async def scenario() -> None:
        prompt = "请补充会议的具体日期、时区，以及三位参会人的姓名和邮箱。"
        model = FakeTextModel({"tasks": [], "clarification_prompt": prompt})
        plan = await LLMPlanner(model, ("read_text_file",)).create_plan(
            request(), BlackboardSnapshot(())
        )

        assert plan.tasks == ()
        assert plan.clarification_prompt == prompt
        assert "empty tasks list" in model.system_instruction

    asyncio.run(scenario())


def test_planner_receives_fallback_reason_and_only_selected_skill_candidates(
    tmp_path: Path,
) -> None:
    write_skill_package(tmp_path, "business/selected")
    write_skill_package(tmp_path, "business/unselected")
    model = FakeTextModel(
        {
            "tasks": [
                {
                    "task_id": "use_skill",
                    "description": "Summarize with the selected Skill",
                    "execution_type": "business_skill_task",
                    "expected_output": "A summary",
                    "input": {},
                }
            ]
        }
    )
    planner = LLMPlanner(
        model,
        (),
        skill_registry=YamlSkillRegistry(tmp_path),
    )
    plan_request = replace(
        request(),
        payload={
            "task_description": "Summarize these notes",
            "evaluation_failure_reason": "skill_result_mismatch",
            "candidate_skill_ids": ["business/selected"],
        },
    )

    asyncio.run(planner.create_plan(plan_request, BlackboardSnapshot(())))

    assert model.data["skill_evaluation_failure"] == "skill_result_mismatch"
    assert [item["kind"] for item in model.data["capability_catalog"]] == ["business_skill"]
    assert len(model.data["capability_catalog"]) == 1


def test_model_plan_fallback_carries_failure_evidence_without_task_payloads() -> None:
    class FakePlanExecutor:
        def __init__(self) -> None:
            self.request = None

        async def execute(self, request):
            self.request = request
            return SkillExecutionResult(
                SkillExecutionStatus.COMPLETED,
                output={"message": "continued"},
            )

    original = replace(
        request("business/notes"),
        payload={"notes": "private task contents"},
        original_goal="Summarize the notes",
        candidate_skill_ids=("business/notes", "business/decisions"),
    )
    snapshot = SkillExecutionSnapshot(
        request_id=original.request_context.snapshot.request_id,
        skill_id=original.skill_id,
        manifest_hash="manifest-hash",
        manifest_version="1.0",
        started_at=datetime.now(UTC),
        budget=original.request_context.budget.snapshot(),
        step_states=(SkillStepState("summarize", "failed", error_code="result_mismatch"),),
    )
    plan_executor = FakePlanExecutor()

    result = asyncio.run(
        ModelPlanFallback(plan_executor).execute_fallback(
            original,
            snapshot,
            reason_code="result_mismatch",
        )
    )

    assert result.status is SkillExecutionStatus.COMPLETED
    assert plan_executor.request.skill_id == "builtin/orchestrator_plan"
    assert plan_executor.request.payload == {
        "task_description": "Summarize the notes",
        "evaluation_failure_reason": "result_mismatch",
        "failed_skill_id": "business/notes",
        "failed_step_ids": ["summarize"],
        "candidate_skill_ids": ["business/notes", "business/decisions"],
        "previous_execution_outcome_uncertain": False,
    }
    assert "private task contents" not in str(plan_executor.request.payload)


def test_tool_retrieval_returns_only_the_five_most_relevant_allowed_tools() -> None:
    descriptors = {
        f"search_{index}": ToolDescriptor(
            name=f"search meeting notes {index}",
            description=f"Search meeting notes and decisions {index}",
            tool_type=ToolType.RETRIEVAL,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=10),
            capabilities=frozenset({"meeting notes", f"decision {index}"}),
        )
        for index in range(7)
    }
    descriptors["weather"] = ToolDescriptor(
        name="weather forecast",
        description="Retrieve current weather and forecast",
        tool_type=ToolType.RETRIEVAL,
        trust_level=TrustLevel.HOST,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=10),
        capabilities=frozenset({"weather"}),
    )

    selected = _rank_tool_candidates(
        "Search meeting notes and decisions",
        tuple(descriptors),
        descriptors,
        limit=5,
    )

    assert len(selected) == 5
    assert all(tool_id.startswith("search_") for tool_id in selected)


def test_tool_retrieval_matches_chinese_access_summaries() -> None:
    descriptors = {
        "read_text_file": ToolDescriptor(
            name="read_text_file",
            description="Read one UTF-8 text file using a relative workspace path.",
            access_summary="读取工作区内的 UTF-8 文本文件；path 是相对路径。",
            capabilities=frozenset({"读取文件", "读取文本"}),
            tool_type=ToolType.ACTION,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=5),
        ),
        "write_text_file": ToolDescriptor(
            name="write_text_file",
            description="Create or replace a UTF-8 text file in the workspace.",
            access_summary="在工作区创建或覆盖 UTF-8 文本文件；需要审批。",
            capabilities=frozenset({"创建文件", "写入文本"}),
            tool_type=ToolType.ACTION,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=5),
            high_impact=True,
        ),
    }

    read_candidates = _rank_tool_candidates(
        "读取工作区 README.md 和 ROADMAP.md 文本文件",
        tuple(descriptors),
        descriptors,
        limit=5,
    )
    write_candidates = _rank_tool_candidates(
        "在工作区创建或覆盖状态报告文本文件",
        tuple(descriptors),
        descriptors,
        limit=5,
    )

    assert read_candidates[0] == "read_text_file"
    assert write_candidates[0] == "write_text_file"


def test_tool_task_react_agent_can_call_multiple_tools_in_one_task() -> None:
    async def scenario() -> None:
        read_schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
            "additionalProperties": False,
        }
        write_schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        }
        descriptors = {
            "read_text_file": ToolDescriptor(
                name="read_text_file",
                description="Read one text file from the workspace.",
                access_summary="读取工作区内的 README.md、ROADMAP.md 等 UTF-8 文本文件。",
                input_schema=read_schema,
                tool_type=ToolType.RETRIEVAL,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.HOST,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
            ),
            "write_text_file": ToolDescriptor(
                name="write_text_file",
                description="Propose writing a workspace text file.",
                access_summary="在工作区写入状态报告文本文件。",
                input_schema=write_schema,
                tool_type=ToolType.ACTION,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.HOST,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
                high_impact=True,
            ),
        }

        class ReActModel:
            def __init__(self) -> None:
                self.requests = []

            async def complete(self, model_request):
                self.requests.append(model_request)
                assert {tool.name for tool in model_request.tools} == set(descriptors)
                if len(self.requests) == 1:
                    return ModelResponse(
                        text="",
                        tool_calls=(
                            ModelToolCall(
                                "read-readme",
                                "read_text_file",
                                {"path": "README.md"},
                            ),
                            ModelToolCall(
                                "read-roadmap",
                                "read_text_file",
                                {"path": "ROADMAP.md"},
                            ),
                        ),
                        token_usage=ModelTokenUsage(
                            input_tokens=1, output_tokens=1, total_tokens=2
                        ),
                    )
                if len(self.requests) == 2:
                    tool_messages = [
                        message
                        for message in model_request.messages
                        if message.role.value == "tool"
                    ]
                    assert len(tool_messages) == 2
                    assert "README.md" in " ".join(message.content for message in tool_messages)
                    assert "ROADMAP.md" in " ".join(message.content for message in tool_messages)
                    return ModelResponse(
                        text="",
                        tool_calls=(
                            ModelToolCall(
                                "write-report",
                                "write_text_file",
                                {"path": "stage-c-status-report.md", "content": "比较摘要"},
                            ),
                        ),
                        token_usage=ModelTokenUsage(
                            input_tokens=1, output_tokens=1, total_tokens=2
                        ),
                    )
                assert len(self.requests) == 3
                return ModelResponse(
                    text="已比较两个文件并提交写入提案。",
                    token_usage=ModelTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                )

        class StubToolProvider:
            def __init__(self) -> None:
                self.calls = []

            async def call_tool(self, name, arguments, *, budget, confirmation_provider=None):
                budget.consume_tool_call(
                    retrieval=descriptors[name].tool_type is ToolType.RETRIEVAL
                )
                self.calls.append((name, dict(arguments), confirmation_provider))
                if name == "read_text_file":
                    return {"path": arguments["path"], "content": f"内容来自 {arguments['path']}"}
                return {"ok": True, "path": arguments["path"], "stub_only": True}

        model = ReActModel()
        provider = StubToolProvider()
        executor = GovernedPlanTaskExecutor(
            model,
            skills=None,
            tool_provider=provider,
            tool_descriptors=descriptors,
        )
        call = PlanTaskCall(
            request=request(),
            task=PlanTask(
                task_id="review_and_propose",
                description=("读取 README.md 和 ROADMAP.md，比较状态，并提交状态报告写入提案。"),
                execution_type=PlanExecutionType.TOOL_TASK,
                expected_output="比较摘要及报告写入提案",
            ),
            dependencies={},
            blackboard=BlackboardSnapshot(()),
            idempotency_key="review-and-propose",
        )

        result = await executor.execute_task(call)

        assert result.succeeded is True
        assert [name for name, _, _ in provider.calls] == [
            "read_text_file",
            "read_text_file",
            "write_text_file",
        ]
        assert [arguments["path"] for _, arguments, _ in provider.calls] == [
            "README.md",
            "ROADMAP.md",
            "stage-c-status-report.md",
        ]
        assert len(model.requests) == 3
        assert [tool_id for tool_id in result.invoked_tool_ids] == [
            "read_text_file",
            "read_text_file",
            "write_text_file",
        ]
        assert executor.tool_agent_diagnostics[0]["turn_count"] == 3

    asyncio.run(scenario())


def test_tool_candidate_retrieval_accepts_immutable_plan_input() -> None:
    """PlanTask freezes input mappings before the executor retrieves tools."""
    descriptors = {
        "read_text_file": ToolDescriptor(
            name="read_text_file",
            description="Read one text file from the workspace.",
            access_summary="读取工作区文本文件。",
            input_schema={"type": "object"},
            tool_type=ToolType.RETRIEVAL,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=5),
        )
    }
    executor = GovernedPlanTaskExecutor(
        FakeTextModel({}),
        skills=None,
        tool_provider=object(),
        tool_descriptors=descriptors,
    )
    call = PlanTaskCall(
        request=request(),
        task=PlanTask(
            task_id="read",
            description="读取 README.md",
            execution_type=PlanExecutionType.TOOL_TASK,
            input={"files": ["README.md"]},
            expected_output="文件内容",
        ),
        dependencies={},
        blackboard=BlackboardSnapshot(()),
        idempotency_key="read",
    )

    assert tuple(executor._tool_candidates_for(call)) == ("read_text_file",)


def test_structured_response_parser_accepts_json_surrounded_by_commentary() -> None:
    parsed = _decode_json_object(
        "The requested selection is below:\n"
        '{"capability_id":"write_text_file","arguments":{"path":"report.md",'
        '"content":"summary"}}\nSelection complete.'
    )

    assert parsed == {
        "capability_id": "write_text_file",
        "arguments": {"path": "report.md", "content": "summary"},
    }


def test_uncertain_skill_fallback_exposes_only_read_only_tools() -> None:
    descriptors = {
        "retrieve": ToolDescriptor(
            name="retrieve notes",
            description="Search notes without changing them",
            tool_type=ToolType.RETRIEVAL,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=10),
        ),
        "write": ToolDescriptor(
            name="write notes",
            description="Write changes to notes",
            tool_type=ToolType.ACTION,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=10),
        ),
    }
    plan_request = replace(
        request(),
        payload={
            "task_description": "Check the state after a failed action",
            "previous_execution_outcome_uncertain": True,
        },
    )
    planner = LLMPlanner(
        FakeTextModel({}),
        tuple(descriptors),
        tool_descriptors=descriptors,
    )

    allowed_tools, allowed_skills = planner._available_capabilities_for(plan_request)

    assert allowed_tools == ("retrieve",)
    assert allowed_skills == ()


def test_planner_parses_json_from_provider_response() -> None:
    async def scenario() -> None:
        model = FakeTextModel(
            {
                "tasks": [
                    {
                        "task_id": "answer",
                        "description": "Answer from the provided context",
                        "execution_type": "reasoning_task",
                        "expected_output": "A grounded answer",
                        "input": {},
                    }
                ]
            },
        )
        plan = await LLMPlanner(model, ()).create_plan(request(), BlackboardSnapshot(()))

        assert plan.tasks[0].task_id == "answer"
        assert model.data["goal"] == "Summarize these notes"

    asyncio.run(scenario())


def test_planner_receives_safe_prior_failure_summaries() -> None:
    model = FakeTextModel(
        {
            "tasks": [
                {
                    "task_id": "answer",
                    "description": "Use relevant prior findings to answer",
                    "execution_type": "reasoning_task",
                    "expected_output": "A grounded answer",
                    "input": {},
                }
            ]
        }
    )
    prior = StoredPlanTaskFailure(
        task_run_id="run-1",
        task_id="inspect",
        turn_id="turn-1",
        session_id="conversation-1",
        task_fingerprint=f"v1:{'a' * 64}",
        description="Inspect the source file",
        input={"path": "about.txt"},
        error_code="tool_unavailable",
        side_effect_count=0,
        finished_at=datetime(2026, 11, 22, tzinfo=UTC),
    )

    async def scenario() -> None:
        await LLMPlanner(model, ()).create_plan(
            request(), BlackboardSnapshot((), prior_failed_tasks=(prior,))
        )

    asyncio.run(scenario())
    assert model.data["prior_failed_tasks"] == [
        {
            "task_id": "inspect",
            "description": "Inspect the source file",
            "error_code": "tool_unavailable",
            "side_effect_count": 0,
        }
    ]


def test_planner_rejects_capability_selection_inside_task_input() -> None:
    model = FakeTextModel(
        {
            "tasks": [
                {
                    "task_id": "search",
                    "description": "Search for the requested file",
                    "execution_type": "tool_task",
                    "expected_output": "Relevant file contents",
                    "input": {"tool_id": "filesystem.read_file", "arguments": {}},
                }
            ]
        }
    )

    async def scenario() -> None:
        try:
            await LLMPlanner(model, ("filesystem.read_file",)).create_plan(
                request(), BlackboardSnapshot(())
            )
        except ValidationError as error:
            assert "cannot select or parameterize" in str(error)
        else:
            raise AssertionError("Planner accepted a concrete capability selection.")

    asyncio.run(scenario())


def test_planner_only_lists_declared_child_skills_for_business_skill_fallback() -> None:
    async def scenario() -> None:
        model = FakeTextModel(
            {
                "tasks": [
                    {
                        "task_id": "answer",
                        "description": "Answer from the provided context",
                        "execution_type": "reasoning_task",
                        "expected_output": "A grounded answer",
                        "input": {},
                    }
                ]
            }
        )
        planner = LLMPlanner(
            model,
            (),
            ("business/allowed", "business/other"),
            skill_authorizations={"business/parent": ("business/allowed",)},
        )

        await planner.create_plan(request("business/parent"), BlackboardSnapshot(()))

        assert [item["kind"] for item in model.data["capability_catalog"]] == ["business_skill"]

    asyncio.run(scenario())


def test_registry_backed_planner_refreshes_enabled_child_skill_allowlist(tmp_path: Path) -> None:
    write_skill_package(tmp_path, "business/child")
    write_skill_package(
        tmp_path,
        "business/parent",
        dependencies=["business/child"],
    )
    model = FakeTextModel(
        {
            "tasks": [
                {
                    "task_id": "reason",
                    "description": "Reason from known information",
                    "execution_type": "reasoning_task",
                    "expected_output": "A reasoned result",
                    "input": {},
                }
            ]
        }
    )
    plan_executor = build_model_plan_executor(
        model,
        skills=YamlSkillRegistry(tmp_path),
        skill_runtime=None,
        tool_provider=None,
        tool_descriptors={},
    )
    planner = plan_executor.planner

    async def scenario() -> None:
        await planner.create_plan(request("business/parent"), BlackboardSnapshot(()))
        assert len(model.data["capability_catalog"]) == 1

        child_manifest_path = tmp_path / "child" / "skill.yaml"
        child_manifest = yaml.safe_load(child_manifest_path.read_text(encoding="utf-8"))
        child_manifest["meta"]["enabled"] = False
        child_manifest_path.write_text(yaml.safe_dump(child_manifest), encoding="utf-8")

        await planner.create_plan(request("business/parent"), BlackboardSnapshot(()))
        assert model.data["capability_catalog"] == []

    asyncio.run(scenario())


def test_plan_and_parallel_yaml_steps_share_budget_and_stop_after_exhaustion(
    tmp_path: Path,
) -> None:
    skill_root = tmp_path / "skills"
    package_root = skill_root / "parallel-summary"
    package_root.mkdir(parents=True)
    (package_root / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "meta": {
                    "skill_id": "business/parallel-summary",
                    "name": "Parallel summary",
                    "version": "1.0.0",
                    "enabled": True,
                },
                "intent_summary": "Summarize independent note sections.",
                "tags": ["summary"],
                "examples": ["summarize these notes"],
                "input_schema": {
                    "type": "object",
                    "properties": {"notes": {"type": "string"}},
                    "required": ["notes"],
                    "additionalProperties": False,
                },
                "output_schema": {"type": "object"},
                "side_effect_level": "read_only",
                "execution": {
                    "kind": "steps",
                    "steps": [
                        {
                            "step_id": step_id,
                            "action": "llm.complete",
                            "params": {"instruction": "Summarize this section."},
                            "depends_on": [],
                            "input_mapping": {"source": {"$ref": "input.notes"}},
                        }
                        for step_id in ("first", "second", "third")
                    ],
                },
                "dependencies": {"skills": [], "tools": []},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    class SharedBudgetModel:
        def __init__(self) -> None:
            self.requests = []

        async def complete(self, model_request):
            self.requests.append(model_request)
            if len(self.requests) == 1:
                text = json.dumps(
                    {
                        "tasks": [
                            {
                                "task_id": "summarize",
                                "description": "Summarize the notes",
                                "execution_type": "business_skill_task",
                                "expected_output": "A concise summary",
                                "input": {"notes": "three sections"},
                            }
                        ]
                    }
                )
                usage = 2
            else:
                text = f"summary-{len(self.requests)}"
                usage = 3 if len(self.requests) == 2 else 6
            return ModelResponse(
                text=text,
                token_usage=ModelTokenUsage(
                    input_tokens=usage - 1,
                    output_tokens=1,
                    total_tokens=usage,
                ),
            )

    class AcceptAll:
        async def assess(self, package, payload, context, *, budget, snapshot=None):
            del package, payload, context, budget, snapshot
            return SkillAssessment(AssessmentDisposition.ACCEPT, 1.0, "accepted")

    async def scenario() -> None:
        registry = YamlSkillRegistry(skill_root)
        model = SharedBudgetModel()
        actions = StepActionRegistry()
        register_llm_completion_action(actions, model)
        skill_runtime = SkillEngine(
            registry,
            actions,
            BuiltinExecutorRegistry(),
            AcceptAll(),
            AcceptAll(),
        )
        plan_executor = build_model_plan_executor(
            model,
            skills=registry,
            skill_runtime=skill_runtime,
            tool_provider=None,
            tool_descriptors={},
        )
        plan_request = request(total_tokens=10)

        result = await plan_executor.execute(plan_request)

        assert result.status.value == "failed"
        assert result.output["error_code"] == "budget_exceeded"
        assert len(model.requests) == 3
        assert [item.max_output_tokens for item in model.requests] == [10, 8, 5]
        assert plan_request.request_context.budget.snapshot().usage.total_tokens == 5
        assert plan_request.request_context.budget.remaining_tokens == 0

    asyncio.run(scenario())


def test_planner_only_lists_tools_declared_by_fallback_parent(tmp_path: Path) -> None:
    write_skill_package(tmp_path, "business/parent", tools=["search"])
    model = FakeTextModel(
        {
            "tasks": [
                {
                    "task_id": "reason",
                    "description": "Reason from known information",
                    "execution_type": "reasoning_task",
                    "expected_output": "A reasoned result",
                    "input": {},
                }
            ]
        }
    )
    registry = YamlSkillRegistry(tmp_path, registered_tool_ids=("search", "write_text_file"))
    executor = build_model_plan_executor(
        model,
        skills=registry,
        skill_runtime=None,
        tool_provider=None,
        tool_descriptors={"search": None, "write_text_file": None},
    )

    async def scenario() -> None:
        await executor.planner.create_plan(request("business/parent"), BlackboardSnapshot(()))
        assert len(model.data["capability_catalog"]) == 1

        await executor.planner.create_plan(request(), BlackboardSnapshot(()))
        assert len(model.data["capability_catalog"]) == 2

    asyncio.run(scenario())


def test_plan_verifier_parses_json_chat_response() -> None:
    async def scenario() -> None:
        model = FakeTextModel(
            {
                "accepted": True,
                "reason_code": "goal_satisfied",
                "final_output": {"answer": "done"},
            }
        )
        plan_request = request()
        result = await LLMPlanVerifier(model).verify(plan_request, BlackboardSnapshot(()))

        assert result.accepted is True
        assert result.reason_code == "goal_satisfied"
        assert result.final_output == {"answer": "done"}
        assert plan_request.request_context.budget.snapshot().usage.total_tokens == 6

    asyncio.run(scenario())


def test_plan_validator_rejects_unregistered_tools_and_skills(tmp_path: Path) -> None:
    write_skill_package(tmp_path, "business/summary")
    write_skill_package(tmp_path, "business/private")
    write_skill_package(
        tmp_path, "business/parent", dependencies=["business/summary"], tools=["search"]
    )
    write_skill_package(tmp_path, "business/no-tools")
    validator = PlanExecutionValidator(
        YamlSkillRegistry(tmp_path, registered_tool_ids=("search",)), ("search",)
    )

    async def scenario() -> None:
        bad_tool = ExecutionPlan(
            (
                PlanTask(
                    "search",
                    "Search",
                    PlanExecutionType.TOOL_TASK,
                    input={"tool_id": "unknown"},
                    expected_output="Search results",
                ),
            )
        )
        bad_skill = ExecutionPlan(
            (
                PlanTask(
                    "summary",
                    "Summarize",
                    PlanExecutionType.BUSINESS_SKILL_TASK,
                    skill_id="business/not-registered",
                    expected_output="A summary",
                ),
            )
        )
        search_tool = ExecutionPlan(
            (
                PlanTask(
                    "search",
                    "Search",
                    PlanExecutionType.TOOL_TASK,
                    input={"tool_id": "search"},
                    expected_output="Search results",
                ),
            )
        )
        unauthorized_skill = ExecutionPlan(
            (
                PlanTask(
                    "private",
                    "Run an undeclared child Skill",
                    PlanExecutionType.BUSINESS_SKILL_TASK,
                    skill_id="business/private",
                    expected_output="Skill result",
                ),
            )
        )
        declared_child = ExecutionPlan(
            (
                PlanTask(
                    "summary",
                    "Run a declared child Skill",
                    PlanExecutionType.BUSINESS_SKILL_TASK,
                    skill_id="business/summary",
                    expected_output="A summary",
                ),
            )
        )

        assert (
            await validator.validate(request(), bad_tool, BlackboardSnapshot(()))
        ).reason_code == "tool_not_registered"
        assert (await validator.validate(request(), search_tool, BlackboardSnapshot(()))).accepted
        assert (
            await validator.validate(
                request("business/no-tools"), search_tool, BlackboardSnapshot(())
            )
        ).reason_code == "tool_not_authorized"
        assert (
            await validator.validate(request(), bad_skill, BlackboardSnapshot(()))
        ).reason_code == "business_skill_not_available"
        assert (
            await validator.validate(
                request("business/parent"), unauthorized_skill, BlackboardSnapshot(())
            )
        ).reason_code == "business_skill_not_authorized"
        assert (
            await validator.validate(
                request("business/parent"), declared_child, BlackboardSnapshot(())
            )
        ).accepted
        assert (
            await validator.validate(request(), declared_child, BlackboardSnapshot(()))
        ).accepted

    asyncio.run(scenario())


def test_plan_validator_rejects_overdecomposition_and_duplicate_objectives(
    tmp_path: Path,
) -> None:
    validator = PlanExecutionValidator(YamlSkillRegistry(tmp_path), ())

    async def scenario() -> None:
        def reasoning(task_id: str, description: str) -> PlanTask:
            return PlanTask(
                task_id,
                description,
                PlanExecutionType.REASONING_TASK,
                expected_output="A verifiable result",
            )

        too_many_tasks = ExecutionPlan(
            tuple(
                reasoning(f"step-{index}", f"Perform distinct operation {index}")
                for index in range(3)
            )
        )
        duplicate_tasks = ExecutionPlan(
            (
                reasoning("first", "Summarize the notes"),
                reasoning("second", "  summarize   the NOTES "),
            )
        )
        missing_output = ExecutionPlan(
            (PlanTask("step", "Summarize the notes", PlanExecutionType.REASONING_TASK),)
        )

        assert (
            await validator.validate(request(), too_many_tasks, BlackboardSnapshot(()))
        ).reason_code == "plan_task_limit_exceeded"
        assert (
            await validator.validate(request(), duplicate_tasks, BlackboardSnapshot(()))
        ).reason_code == "duplicate_plan_tasks"
        assert (
            await validator.validate(request(), missing_output, BlackboardSnapshot(()))
        ).reason_code == "plan_task_output_missing"

    asyncio.run(scenario())


def test_unauthorized_child_skill_is_rejected_before_task_execution(tmp_path: Path) -> None:
    write_skill_package(tmp_path, "business/private")
    write_skill_package(tmp_path, "business/parent")
    plan = ExecutionPlan(
        (
            PlanTask(
                "private",
                "Run an undeclared child Skill",
                PlanExecutionType.BUSINESS_SKILL_TASK,
                skill_id="business/private",
            ),
        )
    )

    class FixedPlanner:
        async def create_plan(self, request, blackboard):
            return plan

        async def replan(self, request, failure_reason, blackboard):
            raise AssertionError("replanning is disabled for this test")

    class CapturingTaskExecutor:
        def __init__(self) -> None:
            self.calls = []

        async def execute_task(self, call) -> PlanTaskResult:
            self.calls.append(call)
            return PlanTaskResult(True)

    class AcceptingVerifier:
        async def verify(self, request, blackboard):
            return PlanVerification(True, "accepted")

    task_executor = CapturingTaskExecutor()
    executor = OrchestratorPlanExecutor(
        FixedPlanner(),
        PlanExecutionValidator(YamlSkillRegistry(tmp_path), ()),
        task_executor,
        AcceptingVerifier(),
        FixedPlanner(),
        max_replans=0,
    )

    result = asyncio.run(executor.execute(request("business/parent")))

    assert result.status.value == "failed"
    assert result.output["error_code"] == "business_skill_not_authorized"
    assert task_executor.calls == []


class FakeAssessmentModel(FakeTextModel):
    def __init__(self, disposition: str, confidence: float = 0.95) -> None:
        super().__init__(
            {
                "disposition": disposition,
                "confidence": confidence,
                "reason_code": "assessment_result",
                "message": None,
            },
            usage={"input_tokens": 9, "output_tokens": 3, "total_tokens": 12},
        )


def test_model_skill_assessor_uses_execution_evidence_and_accounts_budget(tmp_path: Path) -> None:
    package_dir = tmp_path / "summary"
    package_dir.mkdir()
    package_file = package_dir / "skill.yaml"
    package_file.write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "meta": {
                    "skill_id": "business/summary",
                    "name": "Summary",
                    "version": "1.0",
                },
                "intent_summary": "Summarize meeting notes",
                "tags": ["summary"],
                "examples": ["summarize notes"],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "side_effect_level": "read_only",
                "execution": {
                    "kind": "steps",
                    "steps": [{"step_id": "draft", "action": "draft"}],
                },
            }
        ),
        encoding="utf-8",
    )
    package = YamlSkillRegistry(tmp_path).load("business/summary")
    model = FakeAssessmentModel("accept")
    assessor = LLMSkillAssessor(model)
    assessment_request = request()

    async def scenario():
        from hostpilot.skills.runtime import SkillExecutionSnapshot, SkillStepState

        snapshot = SkillExecutionSnapshot(
            request_id="plan-test",
            skill_id="business/summary",
            manifest_hash=package.content_hash,
            manifest_version="1.0",
            started_at=assessment_request.request_context.snapshot.captured_at,
            budget=assessment_request.request_context.budget.snapshot(),
            step_states=(SkillStepState("draft", "completed", output={"summary": "Done"}),),
        )
        result = await assessor.assess(
            package,
            {"input": {"notes": "Meeting notes"}, "output": {"summary": "Done"}},
            assessment_request.request_context.snapshot,
            budget=assessment_request.request_context.budget,
            snapshot=snapshot,
        )
        return result

    result = asyncio.run(scenario())

    assert result.disposition is AssessmentDisposition.ACCEPT
    assert result.token_usage is not None and result.token_usage.total_tokens == 12
    assert assessment_request.request_context.budget.snapshot().usage.total_tokens == 12
    assert model.data["execution_evidence"]["steps"][0]["output"] == {"summary": "Done"}
    assert model.data["request"]["output"] == {"summary": "Done"}


def test_model_skill_assessor_routes_low_confidence_acceptance_to_plan(tmp_path: Path) -> None:
    package_dir = tmp_path / "summary"
    package_dir.mkdir()
    package_dir.joinpath("skill.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "meta": {
                    "skill_id": "business/summary",
                    "name": "Summary",
                    "version": "1.0",
                },
                "intent_summary": "Summarize meeting notes",
                "tags": ["summary"],
                "examples": ["summarize notes"],
                "input_schema": {"type": "object"},
                "output_schema": {"type": "object"},
                "side_effect_level": "read_only",
                "execution": {
                    "kind": "steps",
                    "steps": [{"step_id": "draft", "action": "draft"}],
                },
            }
        ),
        encoding="utf-8",
    )
    package = YamlSkillRegistry(tmp_path).load("business/summary")
    assessor = LLMSkillAssessor(FakeAssessmentModel("accept", confidence=0.3))

    result = asyncio.run(
        assessor.assess(
            package,
            {"notes": "Meeting notes"},
            request().request_context.snapshot,
            budget=request().request_context.budget,
        )
    )

    assert result.disposition is AssessmentDisposition.PLAN
    assert result.reason_code == "assessment_confidence_below_threshold"
