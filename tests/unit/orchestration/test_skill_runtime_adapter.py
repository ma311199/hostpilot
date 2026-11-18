import asyncio
from uuid import uuid4

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    ConversationMessage,
    MessageRole,
    ModelTokenUsage,
    Route,
)
from hostpilot.observability import TraceContext, TraceEvent, trace_context_scope
from hostpilot.orchestration.runtime import AgentResponse
from hostpilot.orchestration.skill_runtime import (
    SkillRuntimeAgentAdapter,
    SkillSelection,
    _render_result,
)
from hostpilot.skills.runtime import SkillExecutionResult, SkillExecutionStatus


def context() -> ContextSnapshot:
    return ContextSnapshot(request_id="request-1", conversation_id="conversation-1")


def budget() -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100,
                tool_calls=5,
                retrieval_calls=2,
                timeout_seconds=30,
            )
        )
    )


class FakeChatRuntime:
    def __init__(self) -> None:
        self.turns = []
        self.responses = []

    async def read_messages(self, conversation_id: str):
        return (ConversationMessage(role=MessageRole.USER, content="prior"),)

    async def respond(self, message, context, *, budget=None, confirmation_provider=None):
        self.responses.append((message, context, budget, confirmation_provider))
        return AgentResponse("fallback response")

    async def record_turn(self, conversation_id, user_message, assistant_message):
        self.turns.append((conversation_id, user_message, assistant_message))


class FakeSkillRuntime:
    def __init__(self, result: SkillExecutionResult) -> None:
        self.result = result
        self.requests = []

    async def execute(self, request):
        self.requests.append(request)
        return self.result


class FakeSelector:
    def __init__(self, selection: SkillSelection | None) -> None:
        self.selection = selection

    async def select(self, message, context, *, budget):
        del message, context, budget
        return self.selection


def test_selected_skill_executes_and_persists_through_chat_runtime() -> None:
    chat_runtime = FakeChatRuntime()
    skill_runtime = FakeSkillRuntime(
        SkillExecutionResult(SkillExecutionStatus.COMPLETED, output={"message": "Skill result"})
    )
    selector = FakeSelector(SkillSelection("builtin/direct_answer", {"message": "hello"}, "direct"))
    adapter = SkillRuntimeAgentAdapter(chat_runtime, skill_runtime, selector)
    confirmation_provider = object()

    response = asyncio.run(
        adapter.respond(
            "hello",
            context(),
            budget=budget(),
            confirmation_provider=confirmation_provider,
        )
    )

    assert response.content == "Skill result"
    assert chat_runtime.responses == []
    assert chat_runtime.turns == [("conversation-1", "hello", "Skill result")]
    assert skill_runtime.requests[0].skill_id == "builtin/direct_answer"
    assert skill_runtime.requests[0].confirmation_provider is confirmation_provider
    assert skill_runtime.requests[0].idempotency_key == "request-1"


def test_selected_deepagents_skill_uses_only_the_chat_skill_entry() -> None:
    class DeepAgentsChatRuntime(FakeChatRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.selected_skills = []

        async def respond_with_skill(
            self,
            message,
            context,
            *,
            skill_id,
            budget=None,
            confirmation_provider=None,
        ):
            self.selected_skills.append((message, skill_id, budget, confirmation_provider))
            return AgentResponse(
                "Markdown Skill result",
                token_usage=ModelTokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
                budget_usage_accounted=True,
            )

    chat_runtime = DeepAgentsChatRuntime()
    skill_runtime = FakeSkillRuntime(SkillExecutionResult(SkillExecutionStatus.COMPLETED))
    adapter = SkillRuntimeAgentAdapter(
        chat_runtime,
        skill_runtime,
        FakeSelector(
            SkillSelection(
                "legacy/meeting-notes",
                {"message": "summarize"},
                "markdown_match",
                execution_mode="deepagents",
            )
        ),
    )
    request_budget = budget()
    confirmation_provider = object()

    response = asyncio.run(
        adapter.respond(
            "summarize",
            context(),
            budget=request_budget,
            confirmation_provider=confirmation_provider,
        )
    )

    assert response.content == "Markdown Skill result"
    assert response.budget_usage_accounted is True
    assert request_budget.snapshot().usage.total_tokens == 0
    assert chat_runtime.selected_skills == [
        ("summarize", "legacy/meeting-notes", request_budget, confirmation_provider)
    ]
    assert skill_runtime.requests == []


def test_deepagents_failure_falls_back_to_plan_with_reason_and_candidates() -> None:
    class FailingDeepAgentsRuntime(FakeChatRuntime):
        async def respond_with_skill(
            self,
            message,
            context,
            *,
            skill_id,
            budget=None,
            confirmation_provider=None,
        ):
            raise RuntimeError("graph failed")

    chat_runtime = FailingDeepAgentsRuntime()
    skill_runtime = FakeSkillRuntime(
        SkillExecutionResult(SkillExecutionStatus.COMPLETED, output={"answer": "Plan result"})
    )
    selection = SkillSelection(
        "legacy/meeting-notes",
        {"message": "summarize"},
        "markdown_match",
        execution_mode="deepagents",
        candidate_skill_ids=("business/notes",),
    )
    adapter = SkillRuntimeAgentAdapter(chat_runtime, skill_runtime, FakeSelector(selection))

    response = asyncio.run(adapter.respond("summarize", context(), budget=budget()))

    assert response.route is Route.PLAN_EXECUTE
    assert response.content.startswith("Plan result")
    assert "deepagents_execution_RuntimeError" in response.content
    assert skill_runtime.requests[0].skill_id == "builtin/orchestrator_plan"
    assert skill_runtime.requests[0].payload["evaluation_failure_reason"] == (
        "deepagents_execution_RuntimeError"
    )
    assert skill_runtime.requests[0].payload["candidate_skill_ids"] == ["business/notes"]


def test_deepagents_result_assessment_failure_falls_back_to_read_only_plan() -> None:
    class AssessingSelector(FakeSelector):
        async def assess_deepagents_result(self, skill_id, goal, result, context, *, budget):
            del skill_id, goal, result, context, budget
            return (
                False,
                "requested_decisions_missing",
                ModelTokenUsage(input_tokens=4, output_tokens=2, total_tokens=6),
            )

    class DeepAgentsChatRuntime(FakeChatRuntime):
        async def respond_with_skill(
            self,
            message,
            context,
            *,
            skill_id,
            budget=None,
            confirmation_provider=None,
        ):
            del message, context, skill_id, budget, confirmation_provider
            return AgentResponse(
                "The answer skipped decisions.",
                token_usage=ModelTokenUsage(input_tokens=8, output_tokens=3, total_tokens=11),
                budget_usage_accounted=True,
            )

    skill_runtime = FakeSkillRuntime(
        SkillExecutionResult(SkillExecutionStatus.COMPLETED, output={"answer": "checked"})
    )
    adapter = SkillRuntimeAgentAdapter(
        DeepAgentsChatRuntime(),
        skill_runtime,
        AssessingSelector(
            SkillSelection(
                "legacy/meeting-notes",
                {"message": "summarize"},
                "selected",
                execution_mode="deepagents",
                candidate_skill_ids=("business/notes",),
            )
        ),
    )

    response = asyncio.run(adapter.respond("summarize", context(), budget=budget()))

    assert response.route is Route.PLAN_EXECUTE
    assert "requested_decisions_missing" in response.content
    assert skill_runtime.requests[0].payload["previous_execution_outcome_uncertain"] is True
    assert response.token_usage == ModelTokenUsage(
        input_tokens=12,
        output_tokens=5,
        total_tokens=17,
    )


def test_yaml_execution_failure_falls_back_to_plan() -> None:
    chat_runtime = FakeChatRuntime()

    class PlanFallbackRuntime(FakeSkillRuntime):
        async def execute(self, request):
            self.requests.append(request)
            if request.skill_id == "builtin/orchestrator_plan":
                return SkillExecutionResult(
                    SkillExecutionStatus.COMPLETED,
                    output={"answer": "Plan result"},
                )
            return SkillExecutionResult(
                SkillExecutionStatus.FAILED,
                output={"error_code": "output_schema_invalid"},
            )

    skill_runtime = PlanFallbackRuntime(SkillExecutionResult(SkillExecutionStatus.FAILED))
    adapter = SkillRuntimeAgentAdapter(
        chat_runtime,
        skill_runtime,
        FakeSelector(
            SkillSelection(
                "business/notes",
                {"notes": "content"},
                "unique_high_confidence",
                candidate_skill_ids=("business/notes",),
            )
        ),
    )

    response = asyncio.run(adapter.respond("summarize", context(), budget=budget()))

    assert response.route is Route.PLAN_EXECUTE
    assert response.content.startswith("Plan result")
    assert "output_schema_invalid" in response.content
    assert [request.skill_id for request in skill_runtime.requests] == [
        "business/notes",
        "builtin/orchestrator_plan",
    ]


def test_plan_fallback_clarification_uses_clarify_route() -> None:
    prompt = "请补充事件的具体日期和参会人邮箱。"

    class PlanFallbackRuntime(FakeSkillRuntime):
        async def execute(self, request):
            self.requests.append(request)
            if request.skill_id == "builtin/orchestrator_plan":
                return SkillExecutionResult(
                    SkillExecutionStatus.CLARIFICATION,
                    output={"message": prompt},
                    clarification_prompt=prompt,
                )
            return SkillExecutionResult(SkillExecutionStatus.FAILED)

    skill_runtime = PlanFallbackRuntime(SkillExecutionResult(SkillExecutionStatus.FAILED))
    adapter = SkillRuntimeAgentAdapter(
        FakeChatRuntime(),
        skill_runtime,
        FakeSelector(SkillSelection("business/meeting-minutes", {}, "selected")),
    )

    response = asyncio.run(adapter.respond("Schedule and notify", context(), budget=budget()))

    assert response.route is Route.CLARIFY
    assert response.clarification_prompt == prompt
    assert response.content == prompt


def test_selected_skill_records_safe_child_span() -> None:
    class TraceRecorder:
        def __init__(self) -> None:
            self.events: list[TraceEvent] = []

        def record(self, event: TraceEvent) -> None:
            self.events.append(event)

    trace_id = uuid4()
    request_context = context().model_copy(update={"request_id": str(trace_id)})
    root_span = TraceContext(trace_id=trace_id)
    trace = TraceRecorder()
    adapter = SkillRuntimeAgentAdapter(
        FakeChatRuntime(),
        FakeSkillRuntime(
            SkillExecutionResult(SkillExecutionStatus.COMPLETED, output={"message": "ok"})
        ),
        FakeSelector(
            SkillSelection(
                "business/demo",
                {"message": "private payload"},
                "selected",
                execution_mode="yaml",
            )
        ),
        trace_sink=trace,
    )

    with trace_context_scope(root_span):
        asyncio.run(adapter.respond("private prompt", request_context, budget=budget()))

    assert len(trace.events) == 1
    event = trace.events[0]
    assert event.name == "skill.execute"
    assert event.context.trace_id == trace_id
    assert event.context.parent_span_id == root_span.span_id
    assert event.attributes["skill_id"] == "business/demo"
    assert "private payload" not in str(event.attributes)
    assert "private prompt" not in str(event.attributes)


def test_unselected_request_uses_existing_chat_runtime() -> None:
    chat_runtime = FakeChatRuntime()
    skill_runtime = FakeSkillRuntime(SkillExecutionResult(SkillExecutionStatus.COMPLETED))
    adapter = SkillRuntimeAgentAdapter(chat_runtime, skill_runtime, FakeSelector(None))

    response = asyncio.run(adapter.respond("ordinary", context(), budget=budget()))

    assert response.content == "fallback response"
    assert len(chat_runtime.responses) == 1
    assert skill_runtime.requests == []


def test_skill_failure_is_rendered_as_user_facing_status() -> None:
    adapter = SkillRuntimeAgentAdapter(
        FakeChatRuntime(),
        FakeSkillRuntime(
            SkillExecutionResult(
                SkillExecutionStatus.FAILED,
                output={"error_code": "budget_exceeded"},
            )
        ),
        FakeSelector(SkillSelection("builtin/orchestrator_plan", {}, "plan")),
    )

    response = asyncio.run(adapter.respond("plan this", context(), budget=budget()))

    assert response.content == "Skill 执行失败：budget_exceeded"


def test_adapter_delegates_message_history_and_close() -> None:
    class ClosableRuntime(FakeChatRuntime):
        def __init__(self) -> None:
            super().__init__()
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    runtime = ClosableRuntime()
    adapter = SkillRuntimeAgentAdapter(
        runtime,
        FakeSkillRuntime(SkillExecutionResult(SkillExecutionStatus.COMPLETED)),
        FakeSelector(None),
    )

    messages = asyncio.run(adapter.read_messages("conversation-1"))
    asyncio.run(adapter.aclose())

    assert messages[0].content == "prior"
    assert runtime.closed


def test_render_result_uses_text_field_as_user_facing_content() -> None:
    result = SkillExecutionResult(
        SkillExecutionStatus.COMPLETED,
        output={"text": "**会议纪要**\n- 决策：下周灰度。"},
    )

    assert _render_result(result) == "**会议纪要**\n- 决策：下周灰度。"
