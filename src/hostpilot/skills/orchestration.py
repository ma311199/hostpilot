"""Bounded plan-and-execute orchestration for the builtin plan Skill."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from time import perf_counter
from types import MappingProxyType
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from hostpilot.budget import BudgetExceededError
from hostpilot.observability import (
    TraceContext,
    TraceEvent,
    TraceSink,
    child_trace_context,
    trace_context_scope,
)
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.runtime.context import TaskCacheApprovalRequest, TaskCacheDecision
from hostpilot.skills.compensation import (
    CancellationSink,
    CancellationSnapshot,
    CompensationRegistry,
    ExecutionCancelled,
    persist_cancellation,
)
from hostpilot.skills.runtime import (
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillSideEffect,
)
from hostpilot.task_results import (
    PlanTaskExecutionSummary,
    PlanTaskToolUsage,
    StoredPlanTaskFailure,
)
from hostpilot.tools.executor import AsyncConfirmationProvider, RemoteToolOutcomeUnknownError


@asynccontextmanager
async def _task_run_scope(request: SkillExecutionRequest, task: PlanTask) -> AsyncIterator[Any]:
    """Track the Plan task when ChatService supplied a Runtime lifecycle manager."""
    lifecycle = request.request_context.task_lifecycle
    if lifecycle is None:
        yield None
        return
    session_id = request.request_context.snapshot.session_id
    if session_id is None:
        raise RuntimeError("Cannot persist a Plan TaskRun without a Session ID.")
    async with lifecycle.track(
        task.task_id,
        request.request_context.snapshot.request_id,
        session_id,
    ) as handle:
        yield handle


PLAN_TASK_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class PlanExecutionType(StrEnum):
    TOOL_TASK = "tool_task"
    BUSINESS_SKILL_TASK = "business_skill_task"
    REASONING_TASK = "reasoning_task"


@dataclass(frozen=True)
class PlanTask:
    task_id: str
    description: str
    execution_type: PlanExecutionType
    depends_on: tuple[str, ...] = ()
    skill_id: str | None = None
    input: Mapping[str, Any] | None = None
    expected_output: str | None = None

    def __post_init__(self) -> None:
        if not PLAN_TASK_ID_PATTERN.fullmatch(self.task_id):
            raise ValueError("Plan task_id must be a lowercase identifier.")
        if not self.description.strip():
            raise ValueError("Plan task description cannot be blank.")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("Plan task dependencies must be unique.")
        if self.skill_id is not None and not self.skill_id.startswith(("business/", "community/")):
            raise ValueError("Only user Skill IDs may be assigned to a plan task.")
        object.__setattr__(self, "input", MappingProxyType(dict(self.input or {})))


@dataclass(frozen=True)
class ExecutionPlan:
    tasks: tuple[PlanTask, ...]
    clarification_prompt: str | None = None

    def __post_init__(self) -> None:
        if self.clarification_prompt is not None:
            if not self.clarification_prompt.strip() or self.tasks:
                raise ValueError(
                    "A clarification plan requires a non-empty prompt and no executable tasks."
                )
        elif not self.tasks:
            raise ValueError("An execution plan must contain tasks or request clarification.")


@dataclass(frozen=True)
class PlanTaskState:
    task_id: str
    status: str
    output: Any = None
    error_code: str | None = None
    side_effects: tuple[SkillSideEffect, ...] = ()
    idempotency_key: str | None = None
    invoked_tool_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BlackboardSnapshot:
    """Read-only copy of execution state passed to model and task adapters."""

    states: tuple[PlanTaskState, ...]
    prior_failed_tasks: tuple[StoredPlanTaskFailure, ...] = ()

    @property
    def results(self) -> Mapping[str, Any]:
        return MappingProxyType(
            {state.task_id: state.output for state in self.states if state.status == "completed"}
        )

    @property
    def side_effects(self) -> tuple[SkillSideEffect, ...]:
        return tuple(effect for state in self.states for effect in state.side_effects)

    @property
    def invoked_tool_ids(self) -> tuple[str, ...]:
        return tuple(tool_id for state in self.states for tool_id in state.invoked_tool_ids)


@dataclass(frozen=True)
class PlanTaskCall:
    request: SkillExecutionRequest
    task: PlanTask
    dependencies: Mapping[str, Any]
    blackboard: BlackboardSnapshot
    idempotency_key: str


@dataclass(frozen=True)
class PlanTaskResult:
    succeeded: bool
    output: Any = None
    error_code: str | None = None
    side_effects: tuple[SkillSideEffect, ...] = ()
    invoked_tool_ids: tuple[str, ...] = ()
    idempotency_key: str | None = None
    tool_usage: tuple[PlanTaskToolUsage, ...] = ()


class _TaskConfirmationRecorder:
    """Delegate confirmations while retaining only safe decision enums."""

    def __init__(self, delegate: AsyncConfirmationProvider, decisions: list[str]) -> None:
        self._delegate = delegate
        self.decisions = decisions

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        decision = await self._delegate.confirm(request)
        self.decisions.append(decision.value)
        return decision


@dataclass(frozen=True)
class PlanValidation:
    accepted: bool
    reason_code: str
    message: str | None = None


@dataclass(frozen=True)
class PlanVerification:
    accepted: bool
    reason_code: str
    final_output: Mapping[str, Any] | None = None


class PlannerPort(Protocol):
    async def create_plan(
        self,
        request: SkillExecutionRequest,
        blackboard: BlackboardSnapshot,
    ) -> ExecutionPlan: ...


class PlanValidatorPort(Protocol):
    async def validate(
        self,
        request: SkillExecutionRequest,
        plan: ExecutionPlan,
        blackboard: BlackboardSnapshot,
    ) -> PlanValidation: ...


class PlanTaskExecutorPort(Protocol):
    async def execute_task(self, call: PlanTaskCall) -> PlanTaskResult: ...


class PlanVerifierPort(Protocol):
    async def verify(
        self,
        request: SkillExecutionRequest,
        blackboard: BlackboardSnapshot,
    ) -> PlanVerification: ...


class RePlannerPort(Protocol):
    async def replan(
        self,
        request: SkillExecutionRequest,
        failure_reason: str,
        blackboard: BlackboardSnapshot,
    ) -> ExecutionPlan: ...


class InvalidPlanError(ValueError):
    """Raised when a generated plan is malformed or exceeds configured limits."""


def plan_complexity(goal: str) -> tuple[str, int]:
    """Return a conservative complexity hint and suggested task ceiling."""
    separators = len(re.findall(r"(?:\band\b|\bthen\b|并且|然后|之后|同时|；|;|\n)", goal, re.I))
    if len(goal) <= 80 and separators <= 1:
        return "simple", 2
    if len(goal) <= 240 and separators <= 3:
        return "medium", 4
    return "complex", 6


class OrchestratorPlanExecutor:
    """Validate a task DAG, execute it serially, verify, and replan within limits."""

    def __init__(
        self,
        planner: PlannerPort,
        validator: PlanValidatorPort,
        task_executor: PlanTaskExecutorPort,
        verifier: PlanVerifierPort,
        replanner: RePlannerPort,
        *,
        max_tasks: int = 32,
        max_replans: int = 2,
        max_parallel_tasks: int = 4,
        compensation_registry: CompensationRegistry | None = None,
        cancellation_sink: CancellationSink | None = None,
        trace_sink: TraceSink | None = None,
    ) -> None:
        if max_tasks < 1 or max_replans < 0 or max_parallel_tasks < 1:
            raise ValueError("Plan limits must be positive (max_replans may be zero).")
        self.planner = planner
        self.validator = validator
        self.task_executor = task_executor
        self.verifier = verifier
        self.replanner = replanner
        self.max_tasks = max_tasks
        self.max_replans = max_replans
        self.max_parallel_tasks = max_parallel_tasks
        self.compensation_registry = compensation_registry or CompensationRegistry()
        self.cancellation_sink = cancellation_sink
        self.trace_sink = trace_sink

    def configure_harness(self, *, max_tasks: int) -> None:
        """Apply the live maximum task count to future Plan executions."""
        if max_tasks < 1:
            raise ValueError("Plan max_tasks must be positive.")
        self.max_tasks = max_tasks

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        task = request.payload.get("task_description")
        if not isinstance(task, str) or not task.strip():
            return SkillExecutionResult(
                SkillExecutionStatus.CLARIFICATION,
                output={"answer": "Please describe the task you want planned."},
                clarification_prompt="Please describe the task you want planned.",
            )

        states: dict[str, PlanTaskState] = {}
        failures: set[tuple[PlanExecutionType, str | None, str, str]] = set()
        completed_by_signature: dict[
            tuple[PlanExecutionType, str | None, str, str], PlanTaskState
        ] = {}
        completed_effects: list[SkillSideEffect] = []
        in_flight_operations: set[str] = set()
        try:
            request.request_context.budget.ensure_available()
        except BudgetExceededError:
            return _failed_result("budget_exceeded", blackboard=BlackboardSnapshot(()))
        prior_failures: tuple[StoredPlanTaskFailure, ...] = ()
        task_lifecycle = request.request_context.task_lifecycle
        session_id = request.request_context.snapshot.session_id
        if task_lifecycle is not None and session_id is not None:
            try:
                prior_failures = await task_lifecycle.list_recent_task_failures(
                    session_id,
                    exclude_turn_id=request.request_context.snapshot.request_id,
                )
            except Exception as error:
                return _failed_result(
                    f"task_result_history_{type(error).__name__}",
                    blackboard=BlackboardSnapshot(()),
                )

        def current_board() -> BlackboardSnapshot:
            return BlackboardSnapshot(tuple(states.values()))

        def planning_board() -> BlackboardSnapshot:
            return BlackboardSnapshot(tuple(states.values()), prior_failures)

        try:
            plan = await self.planner.create_plan(request, planning_board())
        except Exception as error:
            return _failed_result(f"planner_{type(error).__name__}", blackboard=current_board())
        if plan.clarification_prompt is not None:
            return _clarification_result(plan.clarification_prompt)

        for replan_count in range(self.max_replans + 1):
            try:
                _validate_plan(plan, self.max_tasks)
            except InvalidPlanError as error:
                if replan_count == self.max_replans:
                    return _failed_result("invalid_plan", str(error), current_board())
                board = current_board()
                try:
                    request.request_context.budget.ensure_available()
                except BudgetExceededError:
                    return _failed_result("budget_exceeded", blackboard=board)
                try:
                    plan = await self.replanner.replan(request, "invalid_plan", planning_board())
                except Exception as replan_error:
                    if isinstance(replan_error, BudgetExceededError):
                        return _failed_result("budget_exceeded", blackboard=board)
                    return _failed_result(
                        f"replanner_{type(replan_error).__name__}", blackboard=board
                    )
                if plan.clarification_prompt is not None:
                    return _clarification_result(plan.clarification_prompt)
                continue

            board = current_board()
            try:
                validation = await self.validator.validate(request, plan, board)
            except Exception as error:
                return _failed_result(f"validator_{type(error).__name__}", blackboard=board)
            if not validation.accepted:
                if replan_count == self.max_replans:
                    return _failed_result(validation.reason_code, validation.message, board)
                try:
                    request.request_context.budget.ensure_available()
                except BudgetExceededError:
                    return _failed_result("budget_exceeded", blackboard=board)
                try:
                    plan = await self.replanner.replan(
                        request, validation.reason_code, planning_board()
                    )
                except Exception as error:
                    if isinstance(error, BudgetExceededError):
                        return _failed_result("budget_exceeded", blackboard=board)
                    return _failed_result(f"replanner_{type(error).__name__}", blackboard=board)
                if plan.clarification_prompt is not None:
                    return _clarification_result(plan.clarification_prompt)
                continue

            failure_reason: str | None = None
            for wave in _execution_waves(plan):
                wave_board = current_board()
                leaders: list[PlanTask] = []
                fingerprints: dict[str, str] = {}
                signatures: dict[str, tuple[PlanExecutionType, str | None, str, str]] = {}
                followers: dict[str, str] = {}
                wave_leader_by_signature: dict[
                    tuple[PlanExecutionType, str | None, str, str], str
                ] = {}

                for task_item in wave:
                    existing = states.get(task_item.task_id)
                    if existing is not None and existing.status == "completed":
                        self._record_task_event(
                            child_trace_context(),
                            task_item,
                            status="reused",
                        )
                        continue
                    signature = _failure_signature(task_item)
                    completed_equivalent = completed_by_signature.get(signature)
                    if completed_equivalent is not None:
                        states[task_item.task_id] = _reuse_completed_state(
                            task_item.task_id, completed_equivalent
                        )
                        self._record_task_event(
                            child_trace_context(),
                            task_item,
                            status="reused",
                            source_task_id=completed_equivalent.task_id,
                        )
                        continue
                    if signature in failures:
                        failure_reason = "repeated_failed_task_without_new_information"
                        break
                    dependencies = {
                        dependency: wave_board.results[dependency]
                        for dependency in task_item.depends_on
                        if dependency in wave_board.results
                    }
                    fingerprint = _task_fingerprint(task_item, dependencies)
                    fingerprints[task_item.task_id] = fingerprint
                    candidate = None
                    if task_lifecycle is not None and session_id is not None:
                        try:
                            candidate = await task_lifecycle.find_completed_task_result(
                                session_id,
                                exclude_turn_id=request.request_context.snapshot.request_id,
                                task_fingerprint=fingerprint,
                            )
                        except Exception as error:
                            return _failed_result(
                                f"task_result_lookup_{type(error).__name__}",
                                blackboard=wave_board,
                            )
                    if candidate is not None:
                        self._record_task_event(
                            child_trace_context(),
                            task_item,
                            status="cache_candidate",
                            source_task_run_id=candidate.task_run_id,
                        )
                        provider = request.request_context.task_cache_approval_provider
                        decision = (
                            await provider.decide(
                                TaskCacheApprovalRequest(
                                    request_id=str(uuid4()),
                                    task_id=task_item.task_id,
                                    description=candidate.description,
                                    source_turn_id=candidate.turn_id,
                                    finished_at=candidate.finished_at,
                                    output=candidate.output,
                                    execution_summary=candidate.execution_summary,
                                )
                            )
                            if provider is not None
                            else None
                        )
                        if decision is None:
                            self._record_task_event(
                                child_trace_context(),
                                task_item,
                                status="cache_decision_missing",
                                source_task_run_id=candidate.task_run_id,
                            )
                            return _clarification_result(
                                "发现同 Session 的历史成功子任务，但当前没有可用的审批通道"
                                "或审批未完成；未复用历史结果，也未执行该子任务。"
                                "请通过交互式聊天查看结果并选择后再继续。"
                            )
                        if decision is TaskCacheDecision.REUSE:
                            states[task_item.task_id] = PlanTaskState(
                                task_id=task_item.task_id,
                                status="completed",
                                output=candidate.output,
                            )
                            self._record_task_event(
                                child_trace_context(),
                                task_item,
                                status="cache_reused",
                                source_task_run_id=candidate.task_run_id,
                                cache_decision=decision.value,
                            )
                            continue
                        self._record_task_event(
                            child_trace_context(),
                            task_item,
                            status="cache_ignored",
                            source_task_run_id=candidate.task_run_id,
                            cache_decision=decision.value,
                        )
                    leader_id = wave_leader_by_signature.get(signature)
                    if leader_id is not None:
                        followers[task_item.task_id] = leader_id
                        signatures[task_item.task_id] = signature
                        continue
                    wave_leader_by_signature[signature] = task_item.task_id
                    signatures[task_item.task_id] = signature
                    leaders.append(task_item)

                if failure_reason is not None:
                    break
                try:
                    request.request_context.budget.ensure_available()
                except BudgetExceededError:
                    return _failed_result("budget_exceeded", blackboard=wave_board)

                semaphore = asyncio.Semaphore(self.max_parallel_tasks)

                async def execute_one(
                    task_item: PlanTask,
                    *,
                    wave_signatures: Mapping[
                        str, tuple[PlanExecutionType, str | None, str, str]
                    ] = signatures,
                    wave_fingerprints: Mapping[str, str] = fingerprints,
                    board_snapshot: BlackboardSnapshot = wave_board,
                    concurrency_gate: asyncio.Semaphore = semaphore,
                ) -> tuple[
                    PlanTask,
                    tuple[PlanExecutionType, str | None, str, str],
                    PlanTaskResult,
                ]:
                    signature = wave_signatures[task_item.task_id]
                    fingerprint = wave_fingerprints[task_item.task_id]
                    task_trace = child_trace_context()
                    started_at = perf_counter()
                    self._record_task_event(task_trace, task_item, status="started")
                    key = _idempotency_key(request, task_item)
                    dependencies = {
                        dependency: board_snapshot.results[dependency]
                        for dependency in task_item.depends_on
                        if dependency in board_snapshot.results
                    }
                    async with concurrency_gate:
                        operation_started = False
                        async with _task_run_scope(request, task_item) as task_run:
                            confirmation_decisions: list[str] = []
                            try:
                                request.request_context.budget.ensure_available()
                                operation_started = True
                                in_flight_operations.add(task_item.task_id)
                                confirmation_provider = (
                                    request.request_context.confirmation_provider
                                )
                                task_request = request
                                if confirmation_provider is not None:
                                    task_request = replace(
                                        request,
                                        request_context=replace(
                                            request.request_context,
                                            confirmation_provider=_TaskConfirmationRecorder(
                                                confirmation_provider,
                                                confirmation_decisions,
                                            ),
                                        ),
                                    )
                                with trace_context_scope(task_trace):
                                    result = await self.task_executor.execute_task(
                                        PlanTaskCall(
                                            request=task_request,
                                            task=task_item,
                                            dependencies=MappingProxyType(dependencies),
                                            blackboard=board_snapshot,
                                            idempotency_key=key,
                                        )
                                    )
                            except asyncio.CancelledError:
                                self._record_task_event(
                                    task_trace,
                                    task_item,
                                    status="interrupted",
                                    duration_ms=int((perf_counter() - started_at) * 1000),
                                    reason_code="request_cancelled",
                                    outcome_uncertain=task_item.task_id in in_flight_operations,
                                )
                                raise
                            except BudgetExceededError:
                                if not operation_started:
                                    in_flight_operations.discard(task_item.task_id)
                                result = PlanTaskResult(False, error_code="budget_exceeded")
                            except RemoteToolOutcomeUnknownError:
                                result = PlanTaskResult(
                                    False,
                                    error_code="remote_tool_outcome_unknown",
                                )
                            except Exception as error:
                                if not operation_started:
                                    in_flight_operations.discard(task_item.task_id)
                                result = PlanTaskResult(False, error_code=type(error).__name__)
                            else:
                                in_flight_operations.discard(task_item.task_id)
                                completed_effects.extend(result.side_effects)
                            if result.idempotency_key not in {None, key}:
                                result = PlanTaskResult(
                                    False,
                                    error_code="idempotency_key_mismatch",
                                    side_effects=result.side_effects,
                                    invoked_tool_ids=result.invoked_tool_ids,
                                    idempotency_key=result.idempotency_key,
                                    tool_usage=result.tool_usage,
                                )
                            if task_run is not None and not result.succeeded:
                                if result.error_code == "remote_tool_outcome_unknown":
                                    task_run.fail()
                                else:
                                    task_run.fail(
                                        task_fingerprint=fingerprint,
                                        description=task_item.description,
                                        input={},
                                        error_code=result.error_code or "task_failed",
                                        side_effect_count=len(result.side_effects),
                                    )
                            elif task_run is not None:
                                task_run.complete(
                                    description=task_item.description,
                                    input=task_item.input or {},
                                    depends_on=task_item.depends_on,
                                    output=result.output,
                                    task_fingerprint=fingerprint,
                                    execution_summary=_execution_summary(
                                        result, tuple(confirmation_decisions)
                                    ),
                                )
                    self._record_task_event(
                        task_trace,
                        task_item,
                        status="completed" if result.succeeded else "failed",
                        duration_ms=int((perf_counter() - started_at) * 1000),
                        reason_code=result.error_code,
                        invoked_tool_count=len(result.invoked_tool_ids),
                        side_effect_count=len(result.side_effects),
                        tool_usage=_execution_summary(result, tuple(confirmation_decisions)).tools,
                        confirmation_decisions=tuple(confirmation_decisions),
                    )
                    return task_item, signature, result

                execution_tasks = [asyncio.create_task(execute_one(item)) for item in leaders]
                try:
                    executions = await asyncio.gather(*execution_tasks)
                except asyncio.CancelledError as cancellation:
                    for execution_task in execution_tasks:
                        execution_task.cancel()
                    await asyncio.gather(*execution_tasks, return_exceptions=True)
                    compensation_records = await self.compensation_registry.compensate(
                        request_id=request.request_context.snapshot.request_id,
                        context=request.request_context.snapshot,
                        budget=request.request_context.budget,
                        effects=completed_effects,
                        confirmation_provider=request.request_context.confirmation_provider,
                    )
                    cancellation_snapshot = await persist_cancellation(
                        self.cancellation_sink,
                        CancellationSnapshot(
                            request_id=request.request_context.snapshot.request_id,
                            scope="builtin/orchestrator_plan",
                            uncertain_operations=tuple(sorted(in_flight_operations)),
                            side_effects=tuple(completed_effects),
                            compensation_records=compensation_records,
                        ),
                    )
                    raise ExecutionCancelled(cancellation_snapshot) from cancellation
                for task_item, signature, result in executions:
                    state = PlanTaskState(
                        task_id=task_item.task_id,
                        status="completed" if result.succeeded else "failed",
                        output=result.output,
                        error_code=result.error_code,
                        side_effects=result.side_effects,
                        idempotency_key=_idempotency_key(request, task_item),
                        invoked_tool_ids=result.invoked_tool_ids,
                    )
                    states[task_item.task_id] = state
                    if result.succeeded:
                        completed_by_signature[signature] = state
                    else:
                        failures.add(signature)

                for follower_id, leader_id in followers.items():
                    leader_state = states[leader_id]
                    states[follower_id] = PlanTaskState(
                        task_id=follower_id,
                        status=leader_state.status,
                        output=leader_state.output,
                        error_code=leader_state.error_code,
                        side_effects=leader_state.side_effects,
                        idempotency_key=leader_state.idempotency_key,
                        invoked_tool_ids=leader_state.invoked_tool_ids,
                    )
                    follower = next(item for item in wave if item.task_id == follower_id)
                    self._record_task_event(
                        child_trace_context(),
                        follower,
                        status="reused",
                        source_task_id=leader_id,
                    )

                wave_failures = [
                    states[item.task_id]
                    for item in wave
                    if item.task_id in states and states[item.task_id].status == "failed"
                ]
                if wave_failures:
                    if any(
                        state.error_code == "remote_tool_outcome_unknown" for state in wave_failures
                    ):
                        return _failed_result(
                            "remote_tool_outcome_unknown",
                            blackboard=current_board(),
                        )
                    if any(state.error_code == "budget_exceeded" for state in wave_failures):
                        return _failed_result("budget_exceeded", blackboard=current_board())
                    failure_reason = next(
                        (state.error_code for state in wave_failures if state.error_code),
                        "task_failed",
                    )
                    break

            board = current_board()
            if failure_reason is None:
                try:
                    request.request_context.budget.ensure_available()
                except BudgetExceededError:
                    return _failed_result("budget_exceeded", blackboard=board)
                try:
                    verification = await self.verifier.verify(request, board)
                except Exception as error:
                    return _failed_result(f"verifier_{type(error).__name__}", blackboard=board)
                if verification.accepted:
                    output = dict(verification.final_output or {})
                    output.setdefault("task_results", dict(board.results))
                    return SkillExecutionResult(SkillExecutionStatus.COMPLETED, output=output)
                failure_reason = verification.reason_code

            if replan_count == self.max_replans:
                return _failed_result(failure_reason, blackboard=board)
            try:
                request.request_context.budget.ensure_available()
            except BudgetExceededError:
                return _failed_result("budget_exceeded", blackboard=board)
            try:
                plan = await self.replanner.replan(request, failure_reason, planning_board())
            except Exception as error:
                if isinstance(error, BudgetExceededError):
                    return _failed_result("budget_exceeded", blackboard=board)
                return _failed_result(f"replanner_{type(error).__name__}", blackboard=board)
            if plan.clarification_prompt is not None:
                return _clarification_result(plan.clarification_prompt)

        return _failed_result("replan_limit_reached", blackboard=current_board())

    def _record_task_event(
        self,
        context: TraceContext,
        task: PlanTask,
        *,
        status: str,
        duration_ms: int | None = None,
        reason_code: str | None = None,
        source_task_id: str | None = None,
        source_task_run_id: str | None = None,
        cache_decision: str | None = None,
        invoked_tool_count: int | None = None,
        side_effect_count: int | None = None,
        tool_usage: tuple[PlanTaskToolUsage, ...] | None = None,
        confirmation_decisions: tuple[str, ...] = (),
        outcome_uncertain: bool | None = None,
    ) -> None:
        """Record allowlisted task progress without task content or execution payloads."""
        if self.trace_sink is None:
            return
        attributes: dict[str, str | int | float | bool] = {
            "task_id": task.task_id,
            "execution_type": task.execution_type.value,
            "status": status,
        }
        if duration_ms is not None:
            attributes["duration_ms"] = max(0, duration_ms)
        if reason_code is not None and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", reason_code):
            attributes["reason_code"] = reason_code
        if source_task_id is not None:
            attributes["source_task_id"] = source_task_id
        if source_task_run_id is not None:
            attributes["source_task_run_id"] = source_task_run_id
        if cache_decision is not None:
            attributes["cache_decision"] = cache_decision
        if invoked_tool_count is not None:
            attributes["invoked_tool_count"] = invoked_tool_count
        if side_effect_count is not None:
            attributes["side_effect_count"] = side_effect_count
        if tool_usage is not None:
            attributes["tool_usage"] = json.dumps(
                [
                    {
                        "tool_id": usage.tool_id,
                        "call_count": usage.call_count,
                        "side_effect_level": usage.side_effect_level,
                    }
                    for usage in tool_usage
                ],
                separators=(",", ":"),
            )
        if confirmation_decisions:
            attributes["confirmation_decisions"] = json.dumps(
                confirmation_decisions, separators=(",", ":")
            )
        if outcome_uncertain is not None:
            attributes["outcome_uncertain"] = outcome_uncertain
        try:
            self.trace_sink.record(
                TraceEvent(context=context, name="plan.task", attributes=attributes)
            )
        except Exception:
            return


def _validate_plan(plan: ExecutionPlan, max_tasks: int) -> None:
    if not plan.tasks:
        raise InvalidPlanError("Plan must contain at least one task.")
    if len(plan.tasks) > max_tasks:
        raise InvalidPlanError("Plan exceeds the configured task limit.")
    by_id = {task.task_id: task for task in plan.tasks}
    if len(by_id) != len(plan.tasks):
        raise InvalidPlanError("Plan task IDs must be unique.")
    for task in plan.tasks:
        if task.task_id in task.depends_on:
            raise InvalidPlanError("Plan tasks cannot depend on themselves.")
        missing = set(task.depends_on) - by_id.keys()
        if missing:
            raise InvalidPlanError(
                f"Plan references unknown dependencies: {', '.join(sorted(missing))}"
            )

    _topological_order(plan)


def _execution_summary(
    result: PlanTaskResult, confirmation_decisions: tuple[str, ...]
) -> PlanTaskExecutionSummary:
    """Reconcile observed successful calls and mark missing effect metadata unknown."""
    calls: dict[str, int] = {}
    for tool_id in result.invoked_tool_ids:
        calls[tool_id] = calls.get(tool_id, 0) + 1
    effect_levels: dict[str, str] = {}
    for usage in result.tool_usage:
        current = effect_levels.get(usage.tool_id)
        effect_levels[usage.tool_id] = (
            usage.side_effect_level if current in {None, usage.side_effect_level} else "unknown"
        )
    return PlanTaskExecutionSummary(
        tools=tuple(
            PlanTaskToolUsage(
                tool_id=tool_id,
                call_count=count,
                side_effect_level=effect_levels.get(tool_id, "unknown"),
            )
            for tool_id, count in calls.items()
        ),
        confirmation_decisions=confirmation_decisions,
    )


def _topological_order(plan: ExecutionPlan) -> tuple[PlanTask, ...]:
    return tuple(task for wave in _execution_waves(plan) for task in wave)


def _execution_waves(plan: ExecutionPlan) -> tuple[tuple[PlanTask, ...], ...]:
    by_id = {task.task_id: task for task in plan.tasks}
    pending = set(by_id)
    emitted: set[str] = set()
    waves: list[tuple[PlanTask, ...]] = []
    while pending:
        ready = tuple(
            sorted(
                (
                    by_id[task_id]
                    for task_id in pending
                    if set(by_id[task_id].depends_on) <= emitted
                ),
                key=lambda item: item.task_id,
            )
        )
        if not ready:
            raise InvalidPlanError("Plan dependencies contain a cycle.")
        waves.append(ready)
        for task in ready:
            emitted.add(task.task_id)
            pending.remove(task.task_id)
    return tuple(waves)


def _reuse_completed_state(task_id: str, completed: PlanTaskState) -> PlanTaskState:
    return PlanTaskState(
        task_id=task_id,
        status="completed",
        output=completed.output,
        side_effects=completed.side_effects,
        idempotency_key=completed.idempotency_key,
        invoked_tool_ids=completed.invoked_tool_ids,
    )


def _failure_signature(task: PlanTask) -> tuple[PlanExecutionType, str | None, str, str]:
    description = " ".join(task.description.casefold().split())
    task_input = json.dumps(
        {"input": dict(task.input or {}), "depends_on": sorted(task.depends_on)},
        sort_keys=True,
        default=str,
    )
    return (task.execution_type, task.skill_id, description, task_input)


def _task_fingerprint(task: PlanTask, dependencies: Mapping[str, Any]) -> str:
    """Hash a versioned canonical representation of a task and resolved dependencies."""
    try:
        dependency_outputs = {
            key: json.dumps(
                dependencies[key], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            for key in sorted(dependencies)
        }
        canonical = json.dumps(
            {
                "version": 1,
                "execution_type": task.execution_type.value,
                "skill_id": task.skill_id,
                "description": " ".join(task.description.split()),
                "expected_output": task.expected_output,
                "input": dict(task.input or {}),
                "dependencies": dependency_outputs,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise InvalidPlanError("Plan task fingerprint inputs must be JSON-compatible.") from error
    return f"v1:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


def _idempotency_key(request: SkillExecutionRequest, task: PlanTask) -> str:
    signature = json.dumps(_failure_signature(task), default=str)
    return str(
        uuid5(
            NAMESPACE_URL,
            f"{request.request_context.snapshot.request_id}:builtin/orchestrator_plan:{signature}",
        )
    )


def _failed_result(
    code: str,
    message: str | None = None,
    blackboard: BlackboardSnapshot | None = None,
) -> SkillExecutionResult:
    states = blackboard.states if blackboard is not None else ()
    return SkillExecutionResult(
        SkillExecutionStatus.FAILED,
        output={
            "error_code": code,
            "message": message or code,
            "task_results": dict(blackboard.results) if blackboard is not None else {},
            "task_states": [
                {
                    "task_id": state.task_id,
                    "status": state.status,
                    "output": state.output,
                    "error_code": state.error_code,
                    "idempotency_key": state.idempotency_key,
                    "invoked_tool_ids": list(state.invoked_tool_ids),
                }
                for state in states
            ],
            "side_effects": [
                {
                    "action": effect.action,
                    "level": effect.level.value,
                    "idempotency_key": effect.idempotency_key,
                    "compensation_handle": effect.compensation_handle,
                }
                for effect in blackboard.side_effects
            ]
            if blackboard is not None
            else [],
        },
        snapshot=None,
    )


def _clarification_result(prompt: str) -> SkillExecutionResult:
    return SkillExecutionResult(
        SkillExecutionStatus.CLARIFICATION,
        output={"message": prompt},
        clarification_prompt=prompt,
    )


__all__ = [
    "BlackboardSnapshot",
    "ExecutionPlan",
    "InvalidPlanError",
    "OrchestratorPlanExecutor",
    "PlanExecutionType",
    "PlanTask",
    "PlanTaskCall",
    "PlanTaskExecutorPort",
    "PlanTaskResult",
    "PlanTaskState",
    "PlanValidation",
    "PlanValidatorPort",
    "PlanVerification",
    "PlanVerifierPort",
    "PlannerPort",
    "RePlannerPort",
]
