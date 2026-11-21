"""Framework-independent execution boundary for YAML Skills and builtins."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from jsonschema import Draft202012Validator
from jsonschema import ValidationError as JsonSchemaValidationError

from hostpilot.budget import BudgetExceededError, RequestBudget
from hostpilot.contracts import BudgetContext, ContextSnapshot, ModelTokenUsage
from hostpilot.runtime import RequestContext
from hostpilot.skills.compensation import (
    CancellationSink,
    CancellationSnapshot,
    CompensationRecord,
    CompensationRegistry,
    CompensationStatus,
    ExecutionCancelled,
    persist_cancellation,
)
from hostpilot.skills.workflow import (
    LangGraphWorkflowRunner,
    SequentialWorkflowRunner,
    WorkflowRunner,
    WorkflowState,
    step_dependencies,
)
from hostpilot.skills.yaml_registry import (
    SideEffectLevel,
    SkillStep,
    YamlSkillPackage,
    YamlSkillRegistry,
)
from hostpilot.tools.executor import AsyncConfirmationProvider


class AssessmentDisposition(StrEnum):
    """Control decision returned by admission or result assessors."""

    ACCEPT = "accept"
    CLARIFY = "clarify"
    PLAN = "plan"
    REJECT = "reject"


@dataclass(frozen=True)
class SkillAssessment:
    disposition: AssessmentDisposition
    confidence: float
    reason_code: str
    message: str | None = None
    token_usage: ModelTokenUsage | None = None

    def __post_init__(self) -> None:
        if not 0 <= self.confidence <= 1:
            raise ValueError("Assessment confidence must be between 0 and 1.")
        if not self.reason_code.strip():
            raise ValueError("Assessment reason_code cannot be blank.")


class AssessmentPort(Protocol):
    """Evaluate Skill suitability or the completed workflow result."""

    async def assess(
        self,
        package: YamlSkillPackage,
        payload: Mapping[str, Any],
        context: ContextSnapshot,
        *,
        budget: RequestBudget,
        snapshot: SkillExecutionSnapshot | None = None,
    ) -> SkillAssessment: ...


@dataclass(frozen=True)
class SkillStepState:
    step_id: str
    status: str
    output: Any = None
    error_code: str | None = None


@dataclass(frozen=True)
class SkillSideEffect:
    action: str
    level: SideEffectLevel
    idempotency_key: str
    compensation_handle: str | None = None


@dataclass(frozen=True)
class SkillExecutionSnapshot:
    """Request-local evidence passed to Plan fallback; never persisted implicitly."""

    request_id: str
    skill_id: str
    manifest_hash: str
    manifest_version: str
    started_at: datetime
    budget: BudgetContext
    completed_at: datetime | None = None
    step_states: tuple[SkillStepState, ...] = ()
    side_effects: tuple[SkillSideEffect, ...] = ()
    invoked_tool_ids: tuple[str, ...] = ()
    failure_code: str | None = None
    admission: SkillAssessment | None = None
    result_assessment: SkillAssessment | None = None
    compensation_records: tuple[CompensationRecord, ...] = ()
    step_token_usage: ModelTokenUsage | None = None

    def with_step(self, state: SkillStepState) -> SkillExecutionSnapshot:
        return SkillExecutionSnapshot(
            **{
                **self.__dict__,
                "step_states": (*self.step_states, state),
            }
        )


@dataclass(frozen=True)
class StepActionResult:
    output: Any
    side_effect: SkillSideEffect | None = None
    tool_id: str | None = None
    token_usage: ModelTokenUsage | None = None
    budget_usage_accounted: bool = False


@dataclass(frozen=True)
class StepActionCall:
    action: str
    skill_id: str
    step_id: str
    inputs: Mapping[str, Any]
    params: Mapping[str, Any]
    context: ContextSnapshot
    budget: RequestBudget
    idempotency_key: str
    confirmation_provider: AsyncConfirmationProvider | None = None
    compensation_handle: str | None = None


class StepActionHandler(Protocol):
    async def __call__(self, call: StepActionCall) -> StepActionResult: ...


@dataclass(frozen=True)
class StepActionRegistration:
    action: str
    side_effect_level: SideEffectLevel
    handler: StepActionHandler
    prepare_compensation: Callable[[StepActionCall], Awaitable[str | None]] | None = None
    tool_id: str | None = None


class StepActionRegistry:
    """Allowlist of trusted step operations supplied by HostPilot adapters."""

    def __init__(self) -> None:
        self._actions: dict[str, StepActionRegistration] = {}

    def register(self, action: StepActionRegistration) -> None:
        if action.action in self._actions:
            raise ValueError(f"Step action is already registered: {action.action}")
        self._actions[action.action] = action

    def get(self, action: str) -> StepActionRegistration:
        try:
            return self._actions[action]
        except KeyError as error:
            raise UnknownSkillActionError(action) from error


class BuiltinExecutor(Protocol):
    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult: ...


class SingleTurnLLM(Protocol):
    """A tool-free one-turn text completion boundary for direct_answer."""

    async def complete(
        self, message: str, context: ContextSnapshot, *, budget: RequestBudget
    ) -> str | SingleTurnCompletion: ...


@dataclass(frozen=True)
class SingleTurnCompletion:
    text: str
    token_usage: ModelTokenUsage | None = None
    budget_usage_accounted: bool = False


class PlanAndExecutePort(Protocol):
    async def execute_plan(self, request: SkillExecutionRequest) -> SkillExecutionResult: ...


@dataclass(frozen=True)
class DirectAnswerExecutor:
    llm: SingleTurnLLM

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        message = request.payload.get("message")
        if not isinstance(message, str) or not message.strip():
            raise SkillInputError("direct_answer requires a non-empty message.")
        completion = await self.llm.complete(
            message, request.request_context.snapshot, budget=request.request_context.budget
        )
        if isinstance(completion, SingleTurnCompletion):
            answer = completion.text
            token_usage = completion.token_usage
            if token_usage is not None and not completion.budget_usage_accounted:
                request.request_context.budget.consume_tokens(token_usage.total_tokens)
        else:
            answer = completion
            token_usage = None
        if not answer.strip():
            raise SkillEngineError("direct_answer returned an empty response.")
        return SkillExecutionResult(
            SkillExecutionStatus.COMPLETED,
            output={"message": answer},
            token_usage=token_usage,
        )


class ClarifyExecutor:
    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        prompt = request.payload.get("clarification_prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise SkillInputError("clarify requires a non-empty clarification_prompt.")
        return SkillExecutionResult(
            SkillExecutionStatus.CLARIFICATION,
            output={"message": prompt},
            clarification_prompt=prompt,
        )


@dataclass(frozen=True)
class OrchestratorPlanAdapter:
    port: PlanAndExecutePort

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        return await self.port.execute_plan(request)


def register_core_builtin_skills(
    registry: BuiltinExecutorRegistry,
    *,
    llm: SingleTurnLLM,
    plan_executor: BuiltinExecutor,
) -> None:
    """Register the three builtin Skill IDs and their framework-neutral schemas."""
    registry.register("single_turn_llm", DirectAnswerExecutor(llm))
    registry.register("clarify", ClarifyExecutor())
    registry.register("orchestrator_plan", plan_executor)
    registry.register_skill(
        "builtin/direct_answer",
        "single_turn_llm",
        input_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "minLength": 1}},
            "required": ["message"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "minLength": 1}},
            "required": ["message"],
            "additionalProperties": False,
        },
    )
    registry.register_skill(
        "builtin/clarify",
        "clarify",
        input_schema={
            "type": "object",
            "properties": {"clarification_prompt": {"type": "string", "minLength": 1}},
            "required": ["clarification_prompt"],
            "additionalProperties": False,
        },
        output_schema={
            "type": "object",
            "properties": {"message": {"type": "string", "minLength": 1}},
            "required": ["message"],
            "additionalProperties": False,
        },
    )
    registry.register_skill(
        "builtin/orchestrator_plan",
        "orchestrator_plan",
        input_schema={"type": "object"},
        output_schema={"type": "object"},
    )


@dataclass(frozen=True)
class BuiltinSkillDefinition:
    skill_id: str
    skill_type: str
    input_schema: Mapping[str, Any]
    output_schema: Mapping[str, Any]


class BuiltinExecutorRegistry:
    """Map manifest skill_type values to native, allowlisted executors."""

    def __init__(self) -> None:
        self._executors: dict[str, BuiltinExecutor] = {}
        self._definitions: dict[str, BuiltinSkillDefinition] = {}

    def register(self, skill_type: str, executor: BuiltinExecutor) -> None:
        if not skill_type.strip():
            raise ValueError("Builtin skill_type cannot be blank.")
        if skill_type in self._executors:
            raise ValueError(f"Builtin executor is already registered: {skill_type}")
        self._executors[skill_type] = executor

    def register_skill(
        self,
        skill_id: str,
        skill_type: str,
        *,
        input_schema: Mapping[str, Any],
        output_schema: Mapping[str, Any],
    ) -> None:
        if not skill_id.startswith("builtin/"):
            raise ValueError("Native builtin skill IDs must use the builtin/ namespace.")
        if skill_id in self._definitions:
            raise ValueError(f"Builtin Skill is already registered: {skill_id}")
        Draft202012Validator.check_schema(dict(input_schema))
        Draft202012Validator.check_schema(dict(output_schema))
        self._definitions[skill_id] = BuiltinSkillDefinition(
            skill_id=skill_id,
            skill_type=skill_type,
            input_schema=MappingProxyType(dict(input_schema)),
            output_schema=MappingProxyType(dict(output_schema)),
        )

    def get_for_skill(self, skill_id: str) -> tuple[BuiltinSkillDefinition, BuiltinExecutor]:
        try:
            definition = self._definitions[skill_id]
        except KeyError as error:
            raise UnknownBuiltinExecutorError(skill_id) from error
        try:
            return definition, self._executors[definition.skill_type]
        except KeyError as error:
            raise UnknownBuiltinExecutorError(definition.skill_type) from error

    def get_by_type(self, skill_type: str) -> BuiltinExecutor:
        try:
            return self._executors[skill_type]
        except KeyError as error:
            raise UnknownBuiltinExecutorError(skill_type) from error


@dataclass(frozen=True)
class SkillExecutionRequest:
    skill_id: str
    payload: Mapping[str, Any]
    request_context: RequestContext
    idempotency_key: str | None = None
    delegation_depth: int = 0
    original_goal: str | None = None
    candidate_skill_ids: tuple[str, ...] = ()


class SkillExecutionStatus(StrEnum):
    COMPLETED = "completed"
    CLARIFICATION = "clarification"
    PLAN_FALLBACK = "plan_fallback"
    HUMAN_REVIEW = "human_review"
    FAILED = "failed"


@dataclass(frozen=True)
class SkillExecutionResult:
    status: SkillExecutionStatus
    output: Any = None
    clarification_prompt: str | None = None
    snapshot: SkillExecutionSnapshot | None = None
    token_usage: ModelTokenUsage | None = None


class SkillExecutor(Protocol):
    """Execute a normalized Skill request behind the Runtime governance boundary."""

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult: ...


class SkillExecutorUnavailableError(RuntimeError):
    """Raised when an adapter cannot start because its execution hook is absent."""


class PlanFallbackPort(Protocol):
    async def execute_fallback(
        self,
        request: SkillExecutionRequest,
        snapshot: SkillExecutionSnapshot,
        *,
        reason_code: str,
    ) -> SkillExecutionResult: ...


class SkillEngineError(RuntimeError):
    """Raised when a Skill contract or runtime dispatch cannot be honored."""


class SkillInputError(SkillEngineError):
    """Raised when input does not satisfy the Skill's JSON Schema."""


class SkillOutputError(SkillEngineError):
    """Raised when output does not satisfy the Skill's JSON Schema."""


class UnknownBuiltinExecutorError(SkillEngineError):
    """Raised when a builtin skill_type has no registered native executor."""


class UnknownSkillActionError(SkillEngineError):
    """Raised when a workflow names an unregistered action."""


class SkillAdmissionError(SkillEngineError):
    """Raised when admission policy rejects or cannot assess a business Skill."""


class SkillEngine:
    """Execute registered builtins or bounded YAML workflows through adapters."""

    def __init__(
        self,
        registry: YamlSkillRegistry,
        actions: StepActionRegistry,
        builtins: BuiltinExecutorRegistry,
        admission_assessor: AssessmentPort,
        result_assessor: AssessmentPort,
        *,
        plan_fallback: PlanFallbackPort | None = None,
        workflow_runner: WorkflowRunner | None = None,
        max_steps: int = 64,
        max_delegation_depth: int = 4,
        compensation_registry: CompensationRegistry | None = None,
        cancellation_sink: CancellationSink | None = None,
    ) -> None:
        if max_steps < 1 or max_delegation_depth < 1:
            raise ValueError("max_steps and max_delegation_depth must be positive.")
        self.registry = registry
        self.actions = actions
        self.builtins = builtins
        self.admission_assessor = admission_assessor
        self.result_assessor = result_assessor
        self.plan_fallback = plan_fallback
        self.workflow_runner = workflow_runner or _default_workflow_runner()
        self.max_steps = max_steps
        self.max_delegation_depth = max_delegation_depth
        self.compensation_registry = compensation_registry or CompensationRegistry()
        self.cancellation_sink = cancellation_sink

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        """Validate, assess, and dispatch one Skill invocation."""
        if request.delegation_depth >= self.max_delegation_depth:
            raise SkillEngineError("Skill delegation depth limit exceeded.")
        if request.skill_id.startswith("builtin/"):
            definition, executor = self.builtins.get_for_skill(request.skill_id)
            _validate_json_schema(
                definition.input_schema,
                request.payload,
                "Builtin input",
                error_type=SkillInputError,
            )
            result = await executor.execute(request)
            _validate_json_schema(
                definition.output_schema,
                result.output,
                "Builtin output",
                error_type=SkillOutputError,
            )
            return result

        package = self.registry.load(request.skill_id)
        manifest = package.manifest
        required = manifest.input_schema.get("required", [])
        missing = (
            [name for name in required if isinstance(name, str) and name not in request.payload]
            if isinstance(required, list)
            else []
        )
        if missing:
            properties = manifest.input_schema.get("properties", {})
            labels = []
            for name in missing:
                field = properties.get(name, {}) if isinstance(properties, Mapping) else {}
                label = field.get("title", name) if isinstance(field, Mapping) else name
                labels.append(str(label))
            return SkillExecutionResult(
                SkillExecutionStatus.CLARIFICATION,
                clarification_prompt=(
                    "请补充以下信息后我再执行该 Skill：" + "、".join(labels) + "。"
                ),
            )
        _validate_json_schema(
            manifest.input_schema, request.payload, "Skill input", error_type=SkillInputError
        )

        execution = manifest.execution
        if execution.kind == "builtin":
            raise SkillEngineError("User Skill packages cannot select builtin executors.")
        return await self._execute_workflow(package, request)

    async def _execute_workflow(
        self, package: YamlSkillPackage, request: SkillExecutionRequest
    ) -> SkillExecutionResult:
        manifest = package.manifest
        steps = manifest.execution.steps
        if len(steps) > self.max_steps:
            raise SkillEngineError("Workflow exceeds the configured step limit.")

        # Resolve every action and its risk before invoking the first side-effecting step.
        registrations = tuple(self.actions.get(step.action) for step in steps)
        declared_tools = set(manifest.dependencies.tools)
        undeclared_tools = {
            registration.tool_id
            for registration in registrations
            if registration.tool_id is not None and registration.tool_id not in declared_tools
        }
        if undeclared_tools:
            raise SkillEngineError(
                "Workflow uses undeclared tools: " + ", ".join(sorted(undeclared_tools))
            )
        maximum_risk = max(
            (registration.side_effect_level for registration in registrations), key=_risk_rank
        )
        if _risk_rank(maximum_risk) > _risk_rank(manifest.side_effect_level):
            raise SkillEngineError("Workflow action risk exceeds declared side_effect_level.")

        started_at = datetime.now(UTC)
        snapshot = SkillExecutionSnapshot(
            request_id=request.request_context.snapshot.request_id,
            skill_id=package.skill_id,
            manifest_hash=package.content_hash,
            manifest_version=manifest.meta.version,
            started_at=started_at,
            budget=request.request_context.budget.snapshot(),
        )
        try:
            admission = await self.admission_assessor.assess(
                package,
                request.payload,
                request.request_context.snapshot,
                budget=request.request_context.budget,
            )
        except Exception as error:
            snapshot = _replace_snapshot(
                snapshot,
                failure_code=f"admission_assessor_{type(error).__name__}",
                completed_at=datetime.now(UTC),
                budget=request.request_context.budget.snapshot(),
            )
            return SkillExecutionResult(SkillExecutionStatus.HUMAN_REVIEW, snapshot=snapshot)
        snapshot = _replace_snapshot(snapshot, admission=admission)
        if admission.disposition is AssessmentDisposition.CLARIFY:
            return SkillExecutionResult(
                SkillExecutionStatus.CLARIFICATION,
                clarification_prompt=admission.message,
                snapshot=snapshot,
                token_usage=admission.token_usage,
            )
        if admission.disposition is AssessmentDisposition.PLAN:
            result = await self._fallback_or_review(
                request, snapshot, reason_code=admission.reason_code
            )
            return result
        if admission.disposition is AssessmentDisposition.REJECT:
            snapshot = _replace_snapshot(
                snapshot,
                failure_code=f"admission_rejected_{admission.reason_code}",
                completed_at=datetime.now(UTC),
                budget=request.request_context.budget.snapshot(),
            )
            return SkillExecutionResult(
                SkillExecutionStatus.FAILED,
                output={"error_code": "skill_admission_rejected"},
                snapshot=snapshot,
                token_usage=admission.token_usage,
            )

        registrations_by_step = dict(
            zip((step.step_id for step in steps), registrations, strict=True)
        )
        completed_effects: list[SkillSideEffect] = []
        in_flight_effects: dict[str, SkillSideEffect] = {}
        step_token_usages: dict[str, ModelTokenUsage] = {}

        async def execute_step(step: SkillStep, state: WorkflowState) -> Mapping[str, Any]:
            registration = registrations_by_step[step.step_id]
            step_outputs = state["step_outputs"]
            try:
                request.request_context.budget.ensure_available()
            except BudgetExceededError:
                return {
                    "step_states": {
                        step.step_id: SkillStepState(
                            step.step_id, "skipped", error_code="budget_exceeded"
                        )
                    }
                }
            idempotency_key = _step_idempotency_key(request, package, step)
            if registration.side_effect_level is not SideEffectLevel.READ_ONLY:
                in_flight_effects[step.step_id] = SkillSideEffect(
                    action=step.action,
                    level=registration.side_effect_level,
                    idempotency_key=idempotency_key,
                )
            try:
                mapped_inputs = _resolve_input_mapping(step, request.payload, step_outputs)
                call = StepActionCall(
                    action=step.action,
                    skill_id=package.skill_id,
                    step_id=step.step_id,
                    inputs=MappingProxyType(mapped_inputs),
                    params=MappingProxyType(dict(step.params)),
                    context=request.request_context.snapshot,
                    budget=request.request_context.budget,
                    idempotency_key=idempotency_key,
                    confirmation_provider=request.request_context.confirmation_provider,
                )
                if registration.prepare_compensation is not None:
                    handle = await registration.prepare_compensation(call)
                    if registration.side_effect_level is SideEffectLevel.REVERSIBLE:
                        in_flight_effects[step.step_id] = SkillSideEffect(
                            action=step.action,
                            level=registration.side_effect_level,
                            idempotency_key=idempotency_key,
                            compensation_handle=handle,
                        )
                        call = replace(call, compensation_handle=handle)
                action_result = await registration.handler(call)
                if action_result.token_usage is not None:
                    step_token_usages[step.step_id] = action_result.token_usage
                    if not action_result.budget_usage_accounted:
                        request.request_context.budget.consume_tokens(
                            action_result.token_usage.total_tokens
                        )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                failure_state = SkillStepState(
                    step.step_id, "failed", error_code=type(error).__name__
                )
                update: dict[str, Any] = {"step_states": {step.step_id: failure_state}}
                if registration.side_effect_level is not SideEffectLevel.READ_ONLY:
                    uncertain_effect = in_flight_effects[step.step_id]
                    update["side_effects"] = {step.step_id: uncertain_effect}
                return update

            last_output = action_result.output
            step_state = SkillStepState(step.step_id, "completed", output=last_output)
            update = {
                "step_outputs": {step.step_id: last_output},
                "step_states": {step.step_id: step_state},
            }
            side_effect = action_result.side_effect
            if (
                side_effect is None
                and registration.side_effect_level is not SideEffectLevel.READ_ONLY
            ):
                side_effect = SkillSideEffect(
                    action=step.action,
                    level=registration.side_effect_level,
                    idempotency_key=idempotency_key,
                )
                update["step_states"] = {
                    step.step_id: SkillStepState(
                        step.step_id,
                        "human_review",
                        output=last_output,
                        error_code="side_effect_record_missing",
                    )
                }
                update["side_effects"] = {step.step_id: side_effect}
                completed_effects.append(side_effect)
                in_flight_effects.pop(step.step_id, None)
                return update
            if side_effect is not None:
                update["side_effects"] = {step.step_id: side_effect}
                completed_effects.append(side_effect)
                in_flight_effects.pop(step.step_id, None)
                if (
                    side_effect.level is not registration.side_effect_level
                    or side_effect.action != step.action
                    or side_effect.idempotency_key != idempotency_key
                ):
                    update["step_states"] = {
                        step.step_id: SkillStepState(
                            step.step_id,
                            "human_review",
                            output=last_output,
                            error_code="side_effect_declaration_mismatch",
                        )
                    }
            if action_result.tool_id is not None:
                update["invoked_tool_ids"] = {step.step_id: action_result.tool_id}
            return update

        initial_state: WorkflowState = {
            "step_outputs": {},
            "step_states": {},
            "side_effects": {},
            "invoked_tool_ids": {},
        }
        try:
            workflow_state = await self.workflow_runner.run(steps, initial_state, execute_step)
        except asyncio.CancelledError as cancellation:
            uncertain_effects = tuple(in_flight_effects.values())
            all_effects = (*completed_effects, *uncertain_effects)
            compensation_records = await self.compensation_registry.compensate(
                request_id=request.request_context.snapshot.request_id,
                context=request.request_context.snapshot,
                budget=request.request_context.budget,
                effects=completed_effects,
                uncertain_effects=uncertain_effects,
                confirmation_provider=request.request_context.confirmation_provider,
            )
            snapshot = _replace_snapshot(
                snapshot,
                failure_code="workflow_cancelled",
                completed_at=datetime.now(UTC),
                side_effects=all_effects,
                compensation_records=compensation_records,
                step_token_usage=_sum_model_usages(tuple(step_token_usages.values())),
                budget=request.request_context.budget.snapshot(),
            )
            cancellation_snapshot = await persist_cancellation(
                self.cancellation_sink,
                CancellationSnapshot(
                    request_id=request.request_context.snapshot.request_id,
                    scope=package.skill_id,
                    uncertain_operations=tuple(in_flight_effects),
                    side_effects=all_effects,
                    compensation_records=compensation_records,
                ),
            )
            raise ExecutionCancelled(
                cancellation_snapshot, runtime_snapshot=snapshot
            ) from cancellation
        step_states = tuple(
            state
            if isinstance(state, SkillStepState)
            else SkillStepState(
                step_id=step.step_id,
                status=str(state.get("status", "skipped")),
                error_code=state.get("error_code"),
            )
            for step in steps
            if (state := workflow_state["step_states"].get(step.step_id)) is not None
        )
        snapshot = _replace_snapshot(
            snapshot,
            step_states=step_states,
            side_effects=tuple(
                effect
                for step in steps
                if (effect := workflow_state["side_effects"].get(step.step_id)) is not None
            ),
            invoked_tool_ids=tuple(
                tool_id
                for step in steps
                if (tool_id := workflow_state["invoked_tool_ids"].get(step.step_id)) is not None
            ),
            step_token_usage=_sum_model_usages(tuple(step_token_usages.values())),
            budget=request.request_context.budget.snapshot(),
        )
        failed_states = [state for state in step_states if state.status != "completed"]
        if failed_states:
            failure_code = next(
                (
                    state.error_code
                    for state in failed_states
                    if state.status in {"failed", "human_review"} and state.error_code
                ),
                None,
            ) or next(
                (state.error_code for state in failed_states if state.error_code),
                "workflow_step_failed",
            )
            snapshot = _replace_snapshot(
                snapshot,
                failure_code=failure_code,
                completed_at=datetime.now(UTC),
            )
            if any(state.error_code == "budget_exceeded" for state in failed_states):
                return SkillExecutionResult(
                    SkillExecutionStatus.FAILED,
                    snapshot=snapshot,
                    token_usage=_sum_model_usage(admission.token_usage, snapshot.step_token_usage),
                )
            if any(state.status == "human_review" for state in failed_states):
                return SkillExecutionResult(
                    SkillExecutionStatus.HUMAN_REVIEW,
                    snapshot=snapshot,
                    token_usage=_sum_model_usage(admission.token_usage, snapshot.step_token_usage),
                )
            return await self._fallback_or_review(
                request, snapshot, reason_code="workflow_step_failed"
            )

        dependencies = step_dependencies(steps)
        downstream = {dependency for values in dependencies.values() for dependency in values}
        terminal_steps = [step for step in steps if step.step_id not in downstream]
        if len(terminal_steps) == 1:
            last_output = workflow_state["step_outputs"][terminal_steps[0].step_id]
        else:
            last_output = {
                step.step_id: workflow_state["step_outputs"][step.step_id]
                for step in terminal_steps
            }

        try:
            Draft202012Validator(manifest.output_schema).validate(last_output)
        except JsonSchemaValidationError:
            snapshot = _replace_snapshot(
                snapshot,
                failure_code="output_schema_invalid",
                completed_at=datetime.now(UTC),
                budget=request.request_context.budget.snapshot(),
            )
            return await self._fallback_or_review(
                request, snapshot, reason_code="output_schema_invalid"
            )

        snapshot = _replace_snapshot(snapshot, budget=request.request_context.budget.snapshot())
        try:
            assessment = await self.result_assessor.assess(
                package,
                {"input": dict(request.payload), "output": last_output},
                request.request_context.snapshot,
                budget=request.request_context.budget,
                snapshot=snapshot,
            )
        except Exception as error:
            snapshot = _replace_snapshot(
                snapshot,
                failure_code=f"result_assessor_{type(error).__name__}",
                completed_at=datetime.now(UTC),
                budget=request.request_context.budget.snapshot(),
            )
            return SkillExecutionResult(
                SkillExecutionStatus.HUMAN_REVIEW,
                snapshot=snapshot,
                token_usage=_sum_model_usage(admission.token_usage, snapshot.step_token_usage),
            )
        snapshot = _replace_snapshot(
            snapshot,
            result_assessment=assessment,
            completed_at=datetime.now(UTC),
            budget=request.request_context.budget.snapshot(),
        )
        if assessment.disposition is AssessmentDisposition.ACCEPT:
            return SkillExecutionResult(
                SkillExecutionStatus.COMPLETED,
                output=last_output,
                snapshot=snapshot,
                token_usage=_sum_model_usage(
                    _sum_model_usage(admission.token_usage, snapshot.step_token_usage),
                    assessment.token_usage,
                ),
            )
        if assessment.disposition is AssessmentDisposition.CLARIFY:
            if any(
                effect.level is SideEffectLevel.IRREVERSIBLE for effect in snapshot.side_effects
            ):
                return SkillExecutionResult(
                    SkillExecutionStatus.HUMAN_REVIEW,
                    snapshot=snapshot,
                    token_usage=_sum_model_usage(
                        _sum_model_usage(admission.token_usage, snapshot.step_token_usage),
                        assessment.token_usage,
                    ),
                )
            return SkillExecutionResult(
                SkillExecutionStatus.CLARIFICATION,
                clarification_prompt=assessment.message,
                snapshot=snapshot,
                token_usage=_sum_model_usage(
                    _sum_model_usage(admission.token_usage, snapshot.step_token_usage),
                    assessment.token_usage,
                ),
            )
        if assessment.disposition is AssessmentDisposition.PLAN:
            result = await self._fallback_or_review(
                request, snapshot, reason_code=assessment.reason_code
            )
            return result
        return SkillExecutionResult(
            SkillExecutionStatus.FAILED,
            snapshot=snapshot,
            token_usage=_sum_model_usage(
                _sum_model_usage(admission.token_usage, snapshot.step_token_usage),
                assessment.token_usage,
            ),
        )

    async def _fallback_or_review(
        self,
        request: SkillExecutionRequest,
        snapshot: SkillExecutionSnapshot,
        *,
        reason_code: str,
    ) -> SkillExecutionResult:
        fallback_safe = _fallback_is_safe(snapshot, self.compensation_registry)
        if not fallback_safe:
            reason_code = f"{reason_code}_requires_state_reconciliation"
        try:
            request.request_context.budget.ensure_available()
        except BudgetExceededError:
            return SkillExecutionResult(
                SkillExecutionStatus.FAILED,
                snapshot=snapshot,
                token_usage=_snapshot_token_usage(snapshot),
            )
        reversible_effects = (
            tuple(
                effect
                for effect in snapshot.side_effects
                if effect.level is SideEffectLevel.REVERSIBLE
            )
            if fallback_safe
            else ()
        )
        if reversible_effects:
            try:
                compensation_records = await self.compensation_registry.compensate(
                    request_id=request.request_context.snapshot.request_id,
                    context=request.request_context.snapshot,
                    budget=request.request_context.budget,
                    effects=reversible_effects,
                    confirmation_provider=request.request_context.confirmation_provider,
                )
            except asyncio.CancelledError as cancellation:
                cancelled_snapshot = _replace_snapshot(
                    snapshot,
                    failure_code="compensation_cancelled",
                    compensation_records=(),
                    budget=request.request_context.budget.snapshot(),
                )
                cancellation_snapshot = await persist_cancellation(
                    self.cancellation_sink,
                    CancellationSnapshot(
                        request_id=request.request_context.snapshot.request_id,
                        scope=snapshot.skill_id,
                        uncertain_operations=tuple(effect.action for effect in reversible_effects),
                        side_effects=snapshot.side_effects,
                    ),
                )
                raise ExecutionCancelled(
                    cancellation_snapshot, runtime_snapshot=cancelled_snapshot
                ) from cancellation
            snapshot = _replace_snapshot(snapshot, compensation_records=compensation_records)
            if any(
                record.status is not CompensationStatus.COMPLETED for record in compensation_records
            ):
                reason_code = "compensation_failed_requires_state_reconciliation"
                snapshot = _replace_snapshot(
                    snapshot,
                    failure_code=reason_code,
                )
        if self.plan_fallback is None:
            return SkillExecutionResult(
                SkillExecutionStatus.HUMAN_REVIEW,
                snapshot=snapshot,
                token_usage=_snapshot_token_usage(snapshot),
            )
        result = await self.plan_fallback.execute_fallback(
            request, snapshot, reason_code=reason_code
        )
        if result.snapshot is not None:
            return _add_token_usage(result, _snapshot_token_usage(snapshot))
        result = SkillExecutionResult(
            status=result.status,
            output=result.output,
            clarification_prompt=result.clarification_prompt,
            snapshot=snapshot,
            token_usage=result.token_usage,
        )
        return _add_token_usage(result, _snapshot_token_usage(snapshot))


def _snapshot_token_usage(snapshot: SkillExecutionSnapshot) -> ModelTokenUsage | None:
    return _sum_model_usage(
        _sum_model_usage(
            snapshot.admission.token_usage if snapshot.admission is not None else None,
            snapshot.step_token_usage,
        ),
        snapshot.result_assessment.token_usage if snapshot.result_assessment is not None else None,
    )


def _sum_model_usages(usages: Sequence[ModelTokenUsage]) -> ModelTokenUsage | None:
    total: ModelTokenUsage | None = None
    for usage in usages:
        total = _sum_model_usage(total, usage)
    return total


def _add_token_usage(
    result: SkillExecutionResult, usage: ModelTokenUsage | None
) -> SkillExecutionResult:
    return SkillExecutionResult(
        status=result.status,
        output=result.output,
        clarification_prompt=result.clarification_prompt,
        snapshot=result.snapshot,
        token_usage=_sum_model_usage(result.token_usage, usage),
    )


def _sum_model_usage(
    left: ModelTokenUsage | None, right: ModelTokenUsage | None
) -> ModelTokenUsage | None:
    if left is None:
        return right
    if right is None:
        return left
    return ModelTokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _risk_rank(level: SideEffectLevel) -> int:
    return _SIDE_EFFECT_RANK[level]


def _default_workflow_runner() -> WorkflowRunner:
    try:
        import langgraph.graph  # noqa: F401
    except ImportError:
        return SequentialWorkflowRunner()
    return LangGraphWorkflowRunner()


_SIDE_EFFECT_RANK = {
    SideEffectLevel.READ_ONLY: 0,
    SideEffectLevel.IDEMPOTENT: 1,
    SideEffectLevel.REVERSIBLE: 2,
    SideEffectLevel.IRREVERSIBLE: 3,
}


def _fallback_is_safe(
    snapshot: SkillExecutionSnapshot, compensations: CompensationRegistry
) -> bool:
    levels = [effect.level for effect in snapshot.side_effects]
    if any(level is SideEffectLevel.IRREVERSIBLE for level in levels):
        return False
    if any(level is SideEffectLevel.REVERSIBLE for level in levels):
        return all(
            compensations.can_compensate(effect)
            for effect in snapshot.side_effects
            if effect.level is SideEffectLevel.REVERSIBLE
        )
    return True


def _step_idempotency_key(
    request: SkillExecutionRequest, package: YamlSkillPackage, step: SkillStep
) -> str:
    namespace = request.idempotency_key or request.request_context.snapshot.request_id
    return str(
        uuid5(
            NAMESPACE_URL,
            f"{namespace}:{package.skill_id}:{package.content_hash}:{step.step_id}",
        )
    )


def _validate_json_schema(
    schema: Mapping[str, Any],
    value: Any,
    label: str,
    *,
    error_type: type[SkillEngineError] = SkillEngineError,
) -> None:
    try:
        Draft202012Validator(dict(schema)).validate(value)
    except JsonSchemaValidationError as error:
        raise error_type(f"{label} does not satisfy its JSON Schema.") from error


def _resolve_input_mapping(
    step: SkillStep,
    payload: Mapping[str, Any],
    step_outputs: Mapping[str, Any],
) -> dict[str, Any]:
    def resolve(value: Any) -> Any:
        if isinstance(value, Mapping):
            if set(value) == {"$ref"}:
                reference = value["$ref"]
                if not isinstance(reference, str):
                    raise SkillEngineError("Step $ref must be a string.")
                return _resolve_reference(reference, payload, step_outputs)
            return {key: resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [resolve(item) for item in value]
        return value

    return {key: resolve(value) for key, value in step.input_mapping.items()}


def _resolve_reference(
    reference: str,
    payload: Mapping[str, Any],
    step_outputs: Mapping[str, Any],
) -> Any:
    parts = reference.split(".")
    if len(parts) < 2 or parts[0] not in {"input", "steps"}:
        raise SkillEngineError(f"Unsupported step reference: {reference}")
    source: Any
    if parts[0] == "input":
        source = payload
    else:
        source = step_outputs
    try:
        for part in parts[1:]:
            if isinstance(source, Mapping):
                source = source[part]
            elif isinstance(source, Sequence) and not isinstance(source, str | bytes):
                source = source[int(part)]
            else:
                raise KeyError(part)
    except (KeyError, IndexError, ValueError) as error:
        raise SkillEngineError(f"Step reference cannot be resolved: {reference}") from error
    return source


def _replace_snapshot(snapshot: SkillExecutionSnapshot, **updates: Any) -> SkillExecutionSnapshot:
    values = {**snapshot.__dict__, **updates}
    return SkillExecutionSnapshot(**values)


__all__ = [
    "AssessmentDisposition",
    "AssessmentPort",
    "ClarifyExecutor",
    "DirectAnswerExecutor",
    "BuiltinExecutor",
    "BuiltinExecutorRegistry",
    "BuiltinSkillDefinition",
    "OrchestratorPlanAdapter",
    "PlanAndExecutePort",
    "PlanFallbackPort",
    "SingleTurnLLM",
    "SkillAdmissionError",
    "SkillAssessment",
    "SkillExecutionRequest",
    "SkillExecutionResult",
    "SkillExecutionSnapshot",
    "SkillExecutionStatus",
    "SkillExecutorUnavailableError",
    "SkillInputError",
    "SkillEngine",
    "SkillEngineError",
    "SkillSideEffect",
    "SkillStepState",
    "StepActionCall",
    "StepActionRegistry",
    "StepActionRegistration",
    "StepActionResult",
    "UnknownBuiltinExecutorError",
    "UnknownSkillActionError",
    "register_core_builtin_skills",
]
