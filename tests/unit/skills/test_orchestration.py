import asyncio
from typing import Any

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot, ModelTokenUsage
from hostpilot.models import ModelMessage, ModelRequest, ModelResponse, ModelRole
from hostpilot.models.budget import complete_with_budget
from hostpilot.observability import TraceContext, TraceEvent, trace_context_scope
from hostpilot.skills.compensation import (
    CompensationRegistry,
    CompensationStatus,
    ExecutionCancelled,
)
from hostpilot.skills.orchestration import (
    ExecutionPlan,
    OrchestratorPlanExecutor,
    PlanExecutionType,
    PlanTask,
    PlanTaskResult,
    PlanValidation,
    PlanVerification,
)
from hostpilot.skills.runtime import (
    SkillExecutionRequest,
    SkillExecutionStatus,
    SkillSideEffect,
)
from hostpilot.skills.yaml_registry import SideEffectLevel


def request(*, total_tokens: int = 100) -> SkillExecutionRequest:
    return SkillExecutionRequest(
        "builtin/orchestrator_plan",
        {"task_description": "Prepare the requested report"},
        ContextSnapshot(request_id="orchestration-test", conversation_id="conversation-1"),
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
    )


def task(
    task_id: str,
    *,
    depends_on: tuple[str, ...] = (),
    description: str | None = None,
    input: dict[str, Any] | None = None,
) -> PlanTask:
    return PlanTask(
        task_id,
        description or f"Run {task_id}",
        PlanExecutionType.TOOL_TASK,
        depends_on=depends_on,
        input=input,
    )


class FakePlanner:
    def __init__(self, *plans: ExecutionPlan) -> None:
        self.plans = list(plans)
        self.calls = 0

    async def create_plan(self, request, blackboard):
        self.calls += 1
        return self.plans.pop(0)


class FakeValidator:
    async def validate(self, request, plan, blackboard):
        return PlanValidation(True, "accepted")


class FakeTaskExecutor:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls = []
        self.fail_first = fail_first

    async def execute_task(self, call):
        self.calls.append(call)
        if self.fail_first and len(self.calls) == 1:
            effect = SkillSideEffect(
                "write",
                SideEffectLevel.IDEMPOTENT,
                call.idempotency_key,
            )
            return PlanTaskResult(
                False,
                output={"partial": True},
                error_code="temporary_failure",
                side_effects=(effect,),
                idempotency_key=call.idempotency_key,
            )
        return PlanTaskResult(
            True,
            output={"task": call.task.task_id},
            idempotency_key=call.idempotency_key,
        )


class FakeVerifier:
    def __init__(self, *results: PlanVerification) -> None:
        self.results = list(results) or [PlanVerification(True, "verified")]
        self.calls = 0

    async def verify(self, request, blackboard):
        self.calls += 1
        return self.results.pop(0)


class FakeReplanner:
    def __init__(self, *plans: ExecutionPlan) -> None:
        self.plans = list(plans)
        self.calls = []

    async def replan(self, request, failure_reason, blackboard):
        self.calls.append((failure_reason, blackboard))
        return self.plans.pop(0)


class TraceRecorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(self, event: TraceEvent) -> None:
        self.events.append(event)


def execute(executor: OrchestratorPlanExecutor):
    return asyncio.run(executor.execute(request()))


def test_orchestrator_executes_dag_in_dependency_order_and_is_builtin_compatible() -> None:
    plan = ExecutionPlan((task("second", depends_on=("first",)), task("first")))
    task_executor = FakeTaskExecutor()
    executor = OrchestratorPlanExecutor(
        FakePlanner(plan), FakeValidator(), task_executor, FakeVerifier(), FakeReplanner()
    )

    result = execute(executor)

    assert result.status is SkillExecutionStatus.COMPLETED
    assert [call.task.task_id for call in task_executor.calls] == ["first", "second"]
    assert task_executor.calls[1].dependencies == {"first": {"task": "first"}}


def test_plan_clarification_stops_before_validation_or_task_execution() -> None:
    prompt = "请补充会议的具体日期和参会人邮箱。"
    task_executor = FakeTaskExecutor()
    verifier = FakeVerifier()
    executor = OrchestratorPlanExecutor(
        FakePlanner(ExecutionPlan((), clarification_prompt=prompt)),
        FakeValidator(),
        task_executor,
        verifier,
        FakeReplanner(),
    )

    result = execute(executor)

    assert result.status is SkillExecutionStatus.CLARIFICATION
    assert result.clarification_prompt == prompt
    assert task_executor.calls == []
    assert verifier.calls == 0


def test_replanner_can_turn_blocked_tool_task_into_user_clarification() -> None:
    prompt = "请补充三位参会人的邮箱和日历事件的具体日期。"
    initial = ExecutionPlan((task("schedule"),))

    class BlockedTaskExecutor:
        async def execute_task(self, call):
            return PlanTaskResult(False, error_code="tool_not_invoked")

    task_executor = BlockedTaskExecutor()
    verifier = FakeVerifier()
    result = execute(
        OrchestratorPlanExecutor(
            FakePlanner(initial),
            FakeValidator(),
            task_executor,
            verifier,
            FakeReplanner(ExecutionPlan((), clarification_prompt=prompt)),
            max_replans=1,
        )
    )

    assert result.status is SkillExecutionStatus.CLARIFICATION
    assert result.clarification_prompt == prompt
    assert verifier.calls == 0


def test_plan_task_trace_records_safe_lifecycle_metadata() -> None:
    plan = ExecutionPlan((task("first", description="private plan description"),))
    trace = TraceRecorder()
    parent = TraceContext()

    class PrivateOutputExecutor:
        async def execute_task(self, call):
            return PlanTaskResult(
                True,
                output={"private": "task output"},
                invoked_tool_ids=("read_text_file",),
                idempotency_key=call.idempotency_key,
            )

    executor = OrchestratorPlanExecutor(
        FakePlanner(plan),
        FakeValidator(),
        PrivateOutputExecutor(),
        FakeVerifier(),
        FakeReplanner(),
        trace_sink=trace,
    )

    with trace_context_scope(parent):
        result = execute(executor)

    assert result.status is SkillExecutionStatus.COMPLETED
    assert [event.name for event in trace.events] == ["plan.task", "plan.task"]
    started, completed = trace.events
    assert started.attributes == {
        "task_id": "first",
        "execution_type": "tool_task",
        "status": "started",
    }
    assert completed.attributes["status"] == "completed"
    assert completed.attributes["invoked_tool_count"] == 1
    assert "duration_ms" in completed.attributes
    assert started.context.trace_id == parent.trace_id
    assert started.context.parent_span_id == parent.span_id
    assert completed.context.span_id == started.context.span_id
    assert "private plan description" not in str(trace.events)
    assert "task output" not in str(trace.events)


def test_independent_plan_tasks_run_concurrently_and_join_after_dependencies() -> None:
    plan = ExecutionPlan(
        (
            task("join", depends_on=("alpha", "beta")),
            task("alpha"),
            task("beta"),
        )
    )

    class ConcurrentExecutor:
        def __init__(self) -> None:
            self.active = 0
            self.maximum_active = 0
            self.started_roots: set[str] = set()
            self.roots_started_together = asyncio.Event()
            self.calls = []

        async def execute_task(self, call):
            self.calls.append(call)
            if call.task.task_id in {"alpha", "beta"}:
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
                self.started_roots.add(call.task.task_id)
                if self.started_roots == {"alpha", "beta"}:
                    self.roots_started_together.set()
                await asyncio.wait_for(self.roots_started_together.wait(), timeout=1)
                self.active -= 1
            else:
                assert call.dependencies == {
                    "alpha": {"task": "alpha"},
                    "beta": {"task": "beta"},
                }
            return PlanTaskResult(
                True,
                output={"task": call.task.task_id},
                idempotency_key=call.idempotency_key,
            )

    task_executor = ConcurrentExecutor()
    executor = OrchestratorPlanExecutor(
        FakePlanner(plan),
        FakeValidator(),
        task_executor,
        FakeVerifier(),
        FakeReplanner(),
    )

    result = execute(executor)

    assert result.status is SkillExecutionStatus.COMPLETED
    assert task_executor.maximum_active == 2
    assert [call.task.task_id for call in task_executor.calls] == ["alpha", "beta", "join"]


def test_plan_reasoning_overage_blocks_queued_model_and_dependent_tool_tasks() -> None:
    plan = ExecutionPlan(
        (
            PlanTask("reason-a", "Reason A", PlanExecutionType.REASONING_TASK),
            PlanTask("reason-b", "Reason B", PlanExecutionType.REASONING_TASK),
            task("follow-up", depends_on=("reason-a", "reason-b")),
        )
    )

    class OverreportingModel:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def complete(self, model_request: ModelRequest) -> ModelResponse:
            self.requests.append(model_request)
            return ModelResponse(
                text="reasoned",
                token_usage=ModelTokenUsage(input_tokens=4, output_tokens=2, total_tokens=6),
            )

    class BudgetedTaskExecutor:
        def __init__(self, model: OverreportingModel) -> None:
            self.model = model
            self.tool_calls: list[str] = []

        async def execute_task(self, call):
            if call.task.execution_type is PlanExecutionType.REASONING_TASK:
                await complete_with_budget(
                    self.model,
                    ModelRequest(messages=(ModelMessage(ModelRole.USER, call.task.description),)),
                    call.request.budget,
                )
            else:
                self.tool_calls.append(call.task.task_id)
            return PlanTaskResult(True, output={"task": call.task.task_id})

    model = OverreportingModel()
    task_executor = BudgetedTaskExecutor(model)
    executor = OrchestratorPlanExecutor(
        FakePlanner(plan), FakeValidator(), task_executor, FakeVerifier(), FakeReplanner()
    )

    result = asyncio.run(executor.execute(request(total_tokens=10)))

    assert result.status is SkillExecutionStatus.FAILED
    assert len(model.requests) == 2
    assert [item.max_output_tokens for item in model.requests] == [10, 4]
    assert task_executor.tool_calls == []


def test_plan_parallelism_respects_configured_concurrency_limit() -> None:
    plan = ExecutionPlan(tuple(task(f"task-{index}") for index in range(5)))

    class BoundedExecutor:
        def __init__(self) -> None:
            self.active = 0
            self.maximum_active = 0

        async def execute_task(self, call):
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return PlanTaskResult(
                True, output=call.task.task_id, idempotency_key=call.idempotency_key
            )

    task_executor = BoundedExecutor()
    result = execute(
        OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            task_executor,
            FakeVerifier(),
            FakeReplanner(),
            max_parallel_tasks=2,
        )
    )

    assert result.status is SkillExecutionStatus.COMPLETED
    assert task_executor.maximum_active == 2


def test_parallel_wave_finishes_independent_tasks_and_preserves_failure_state() -> None:
    plan = ExecutionPlan((task("alpha"), task("beta")))

    class PartiallyFailingExecutor:
        def __init__(self) -> None:
            self.completed: set[str] = set()

        async def execute_task(self, call):
            await asyncio.sleep(0)
            if call.task.task_id == "alpha":
                self.completed.add("alpha")
                return PlanTaskResult(False, error_code="alpha_failed")
            self.completed.add("beta")
            return PlanTaskResult(
                True,
                output={"task": "beta"},
                idempotency_key=call.idempotency_key,
            )

    task_executor = PartiallyFailingExecutor()
    result = execute(
        OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            task_executor,
            FakeVerifier(),
            FakeReplanner(),
            max_replans=0,
        )
    )

    assert result.status is SkillExecutionStatus.FAILED
    assert task_executor.completed == {"alpha", "beta"}
    assert result.output["task_results"] == {"beta": {"task": "beta"}}
    assert [state["status"] for state in result.output["task_states"]] == ["failed", "completed"]


def test_cancelling_plan_cleans_up_concurrent_sibling_tasks() -> None:
    plan = ExecutionPlan((task("alpha"), task("beta")))
    trace = TraceRecorder()

    class BlockingExecutor:
        def __init__(self) -> None:
            self.started: set[str] = set()
            self.both_started = asyncio.Event()
            self.cancelled: set[str] = set()

        async def execute_task(self, call):
            task_id = call.task.task_id
            self.started.add(task_id)
            if self.started == {"alpha", "beta"}:
                self.both_started.set()
            try:
                await self.both_started.wait()
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.add(task_id)
                raise

    async def scenario() -> None:
        task_executor = BlockingExecutor()
        executor = OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            task_executor,
            FakeVerifier(),
            FakeReplanner(),
            trace_sink=trace,
        )
        running = asyncio.create_task(executor.execute(request()))
        await asyncio.wait_for(task_executor.both_started.wait(), timeout=1)
        running.cancel()
        try:
            await running
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("Plan cancellation should propagate to its caller.")
        assert task_executor.cancelled == {"alpha", "beta"}
        interrupted = [
            event for event in trace.events if event.attributes.get("status") == "interrupted"
        ]
        assert {event.attributes["task_id"] for event in interrupted} == {"alpha", "beta"}
        assert all(event.attributes["outcome_uncertain"] for event in interrupted)

    asyncio.run(scenario())


def test_cancelling_plan_compensates_completed_effects_and_reports_inflight_task() -> None:
    plan = ExecutionPlan((task("alpha"), task("beta")))
    both_started = asyncio.Event()
    alpha_completed = asyncio.Event()
    compensated: list[str] = []

    class PartiallyCompletedExecutor:
        def __init__(self) -> None:
            self.started: set[str] = set()

        async def execute_task(self, call):
            task_id = call.task.task_id
            self.started.add(task_id)
            if self.started == {"alpha", "beta"}:
                both_started.set()
            await both_started.wait()
            if task_id == "alpha":
                effect = SkillSideEffect(
                    "write",
                    SideEffectLevel.REVERSIBLE,
                    call.idempotency_key,
                    compensation_handle="undo-alpha",
                )
                alpha_completed.set()
                return PlanTaskResult(
                    True,
                    output={"changed": True},
                    side_effects=(effect,),
                    idempotency_key=call.idempotency_key,
                )
            await asyncio.Event().wait()

    async def compensate(call):
        compensated.append(call.compensation_handle)

    async def scenario() -> ExecutionCancelled:
        task_executor = PartiallyCompletedExecutor()
        compensations = CompensationRegistry()
        compensations.register("write", compensate)
        executor = OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            task_executor,
            FakeVerifier(),
            FakeReplanner(),
            compensation_registry=compensations,
        )
        running = asyncio.create_task(executor.execute(request()))
        await asyncio.wait_for(alpha_completed.wait(), timeout=1)
        await asyncio.sleep(0)
        running.cancel()
        try:
            await running
        except ExecutionCancelled as error:
            return error
        raise AssertionError("Cancellation should carry a compensation snapshot.")

    cancellation = asyncio.run(scenario())

    assert compensated == ["undo-alpha"]
    assert cancellation.snapshot.scope == "builtin/orchestrator_plan"
    assert cancellation.snapshot.uncertain_operations == ("beta",)
    assert cancellation.snapshot.compensation_records[0].status is CompensationStatus.COMPLETED


def test_orchestrator_rejects_missing_dependencies_and_cycles() -> None:
    missing = ExecutionPlan((task("first", depends_on=("absent",)),))
    cycle = ExecutionPlan(
        (task("first", depends_on=("second",)), task("second", depends_on=("first",)))
    )

    for plan in (missing, cycle):
        task_executor = FakeTaskExecutor()
        executor = OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            task_executor,
            FakeVerifier(),
            FakeReplanner(),
            max_replans=0,
        )
        result = execute(executor)
        assert result.status is SkillExecutionStatus.FAILED
        assert result.output["error_code"] == "invalid_plan"
        assert task_executor.calls == []


def test_orchestrator_preserves_completed_results_and_effects_on_failure() -> None:
    plan = ExecutionPlan((task("first"), task("second", depends_on=("first",))))
    task_executor = FakeTaskExecutor(fail_first=False)

    class FailSecond(FakeTaskExecutor):
        async def execute_task(self, call):
            self.calls.append(call)
            if call.task.task_id == "second":
                effect = SkillSideEffect("write", SideEffectLevel.IDEMPOTENT, call.idempotency_key)
                return PlanTaskResult(
                    False,
                    error_code="failed_after_write",
                    side_effects=(effect,),
                    invoked_tool_ids=("local::write",),
                )
            return PlanTaskResult(True, output={"ok": 1}, idempotency_key=call.idempotency_key)

    task_executor = FailSecond()
    result = execute(
        OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            task_executor,
            FakeVerifier(),
            FakeReplanner(),
            max_replans=0,
        )
    )

    assert result.status is SkillExecutionStatus.FAILED
    assert result.output["task_results"] == {"first": {"ok": 1}}
    assert result.output["task_states"][1]["error_code"] == "failed_after_write"
    assert result.output["side_effects"][0]["level"] == "idempotent"
    assert result.output["task_states"][1]["invoked_tool_ids"] == ["local::write"]


def test_replan_does_not_repeat_completed_equivalent_task_and_limits_attempts() -> None:
    initial = ExecutionPlan((task("first", description="collect data"),))
    replanned = ExecutionPlan(
        (task("renamed", description="Collect   data"), task("final", depends_on=("renamed",)))
    )
    task_executor = FakeTaskExecutor()
    verifier = FakeVerifier(
        PlanVerification(False, "needs_revision"), PlanVerification(True, "verified")
    )
    replanner = FakeReplanner(replanned)
    result = execute(
        OrchestratorPlanExecutor(
            FakePlanner(initial), FakeValidator(), task_executor, verifier, replanner, max_replans=1
        )
    )

    assert result.status is SkillExecutionStatus.COMPLETED
    assert [call.task.task_id for call in task_executor.calls] == ["first", "final"]
    reused_state = next(
        state for state in task_executor.calls[1].blackboard.states if state.task_id == "renamed"
    )
    assert task_executor.calls[0].idempotency_key == reused_state.idempotency_key
    assert len(replanner.calls) == 1


def test_same_failed_task_is_not_retried_after_replan() -> None:
    plan = ExecutionPlan((task("write", input={"destination": "report.txt"}),))
    replanner = FakeReplanner(plan)
    task_executor = FakeTaskExecutor(fail_first=True)
    result = execute(
        OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            task_executor,
            FakeVerifier(),
            replanner,
            max_replans=1,
        )
    )

    assert result.status is SkillExecutionStatus.FAILED
    assert len(task_executor.calls) == 1
    assert result.output["error_code"] == "repeated_failed_task_without_new_information"
    assert result.output["side_effects"]


def test_validator_and_verifier_exceptions_fail_closed_with_state() -> None:
    plan = ExecutionPlan((task("first"),))

    class BrokenValidator:
        async def validate(self, request, plan, blackboard):
            raise RuntimeError("validator unavailable")

    invalid = execute(
        OrchestratorPlanExecutor(
            FakePlanner(plan),
            BrokenValidator(),
            FakeTaskExecutor(),
            FakeVerifier(),
            FakeReplanner(),
        )
    )
    assert invalid.status is SkillExecutionStatus.FAILED
    assert invalid.output["error_code"] == "validator_RuntimeError"
    assert invalid.output["task_states"] == []

    class BrokenVerifier:
        async def verify(self, request, blackboard):
            raise RuntimeError("verifier unavailable")

    verified = execute(
        OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            FakeTaskExecutor(),
            BrokenVerifier(),
            FakeReplanner(),
        )
    )
    assert verified.status is SkillExecutionStatus.FAILED
    assert verified.output["error_code"] == "verifier_RuntimeError"
    assert verified.output["task_results"] == {"first": {"task": "first"}}


def test_orchestrator_returns_failure_snapshot_when_planner_fails() -> None:
    class BrokenPlanner:
        async def create_plan(self, request, blackboard):
            raise RuntimeError("planner unavailable")

    result = execute(
        OrchestratorPlanExecutor(
            BrokenPlanner(), FakeValidator(), FakeTaskExecutor(), FakeVerifier(), FakeReplanner()
        )
    )

    assert result.status is SkillExecutionStatus.FAILED
    assert result.output["error_code"] == "planner_RuntimeError"
    assert result.output["task_states"] == []


def test_replan_failure_preserves_failure_state() -> None:
    plan = ExecutionPlan((task("first"),))

    class BrokenReplanner:
        async def replan(self, request, failure_reason, blackboard):
            raise RuntimeError("cannot replan")

    result = execute(
        OrchestratorPlanExecutor(
            FakePlanner(plan),
            FakeValidator(),
            FakeTaskExecutor(),
            FakeVerifier(PlanVerification(False, "verification_failed")),
            BrokenReplanner(),
        )
    )

    assert result.status is SkillExecutionStatus.FAILED
    assert result.output["error_code"] == "replanner_RuntimeError"
    assert result.output["task_results"] == {"first": {"task": "first"}}
