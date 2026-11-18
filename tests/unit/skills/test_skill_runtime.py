import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
import yaml

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot, ModelTokenUsage
from hostpilot.models import ModelRequest, ModelResponse
from hostpilot.persistence import SQLiteCancellationStore
from hostpilot.skills.compensation import (
    CompensationRegistry,
    CompensationStatus,
    ExecutionCancelled,
)
from hostpilot.skills.llm_actions import register_llm_completion_action
from hostpilot.skills.runtime import (
    AssessmentDisposition,
    BuiltinExecutorRegistry,
    SkillAssessment,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillRuntime,
    SkillRuntimeError,
    SkillSideEffect,
    StepActionRegistration,
    StepActionRegistry,
    StepActionResult,
    register_core_builtin_skills,
)
from hostpilot.skills.yaml_registry import SideEffectLevel, YamlSkillRegistry


def manifest(
    *,
    side_effect_level: str = "read_only",
    action: str = "text.transform",
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "meta": {
            "skill_id": "business/summary",
            "name": "Summary",
            "version": "1.0.0",
            "priority": 50,
        },
        "intent_summary": "Summarize a note.",
        "tags": ["summary"],
        "examples": ["summarize this note"],
        "input_schema": {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "required": ["notes"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        "side_effect_level": side_effect_level,
        "execution": {
            "kind": "steps",
            "steps": [
                {
                    "step_id": "summarize",
                    "action": action,
                    "input_mapping": {"text": {"$ref": "input.notes"}},
                }
            ],
        },
    }


def write_package(root: Path, value: dict[str, Any] | None = None) -> None:
    package = root / "summary"
    package.mkdir(parents=True)
    (package / "skill.yaml").write_text(
        yaml.safe_dump(value or manifest(), sort_keys=False), encoding="utf-8"
    )


def request(*, total_tokens: int = 100) -> SkillExecutionRequest:
    context = ContextSnapshot(request_id="request-1", conversation_id="conversation-1")
    budget = RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=total_tokens,
                tool_calls=5,
                retrieval_calls=2,
                timeout_seconds=30,
            )
        )
    )
    return SkillExecutionRequest(
        skill_id="business/summary",
        payload={"notes": "decisions and owners"},
        context=context,
        budget=budget,
    )


class FakeAssessor:
    def __init__(self, *assessments: SkillAssessment) -> None:
        self.assessments = list(assessments)
        self.calls = 0

    async def assess(self, package, payload, context, *, budget=None, snapshot=None):
        self.calls += 1
        del budget
        return self.assessments.pop(0)


class FakeFallback:
    def __init__(self) -> None:
        self.calls = []

    async def execute_fallback(self, request, snapshot, *, reason_code):
        self.calls.append((request, snapshot, reason_code))
        return SkillExecutionResult(SkillExecutionStatus.PLAN_FALLBACK, output={"planned": True})


class FakeBuiltin:
    async def execute(self, request):
        return SkillExecutionResult(
            SkillExecutionStatus.COMPLETED,
            output={"answer": request.payload["message"]},
        )


class FakeLLM:
    def __init__(self) -> None:
        self.calls = []

    async def complete(self, message, context, *, budget):
        del budget
        self.calls.append((message, context.request_id))
        return f"Answer: {message}"


class FakePlanExecutor:
    async def execute(self, request):
        return SkillExecutionResult(SkillExecutionStatus.COMPLETED, output={"plan": "done"})


def accepts() -> FakeAssessor:
    return FakeAssessor(SkillAssessment(AssessmentDisposition.ACCEPT, 1.0, "accepted"))


def test_runtime_executes_builtin_by_skill_id_and_validates_schemas(tmp_path: Path) -> None:
    write_package(tmp_path)
    builtins = BuiltinExecutorRegistry()
    builtins.register("single_turn_llm", FakeBuiltin())
    builtins.register_skill(
        "builtin/direct_answer",
        "single_turn_llm",
        input_schema={"type": "object", "required": ["message"]},
        output_schema={"type": "object", "required": ["answer"]},
    )
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), StepActionRegistry(), builtins, accepts(), accepts()
    )
    builtin_request = replace(
        request(), skill_id="builtin/direct_answer", payload={"message": "hello"}
    )

    result = asyncio.run(runtime.execute(builtin_request))

    assert result.status is SkillExecutionStatus.COMPLETED
    assert result.output == {"answer": "hello"}


def test_core_direct_answer_is_single_turn_and_clarify_never_calls_model(
    tmp_path: Path,
) -> None:
    llm = FakeLLM()
    builtins = BuiltinExecutorRegistry()
    register_core_builtin_skills(builtins, llm=llm, plan_executor=FakePlanExecutor())
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), StepActionRegistry(), builtins, accepts(), accepts()
    )
    context = request().context
    direct_request = SkillExecutionRequest(
        "builtin/direct_answer",
        {"message": "hello"},
        context,
        request().budget,
    )
    clarify_request = SkillExecutionRequest(
        "builtin/clarify",
        {"clarification_prompt": "Which file should I use?"},
        context,
        request().budget,
    )

    direct = asyncio.run(runtime.execute(direct_request))
    clarification = asyncio.run(runtime.execute(clarify_request))

    assert direct.output == {"message": "Answer: hello"}
    assert direct.status is SkillExecutionStatus.COMPLETED
    assert clarification.status is SkillExecutionStatus.CLARIFICATION
    assert clarification.clarification_prompt == "Which file should I use?"
    assert llm.calls == [("hello", context.request_id)]


def test_runtime_runs_steps_with_mapped_inputs_and_records_snapshot(tmp_path: Path) -> None:
    write_package(tmp_path)
    calls = []

    async def transform(call):
        calls.append(call)
        return StepActionResult({"summary": call.inputs["text"]})

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.READ_ONLY, transform))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), actions, BuiltinExecutorRegistry(), accepts(), accepts()
    )

    result = asyncio.run(runtime.execute(request()))

    assert result.status is SkillExecutionStatus.COMPLETED
    assert result.output == {"summary": "decisions and owners"}
    assert result.snapshot is not None
    assert result.snapshot.manifest_version == "1.0.0"
    assert result.snapshot.step_states[0].status == "completed"
    assert calls[0].inputs == {"text": "decisions and owners"}


def test_runtime_rejects_undeclared_tool_action_before_assessment_or_execution(
    tmp_path: Path,
) -> None:
    package = manifest(action="search")
    package["dependencies"] = {"skills": [], "tools": []}
    write_package(tmp_path, package)
    calls = []
    action = StepActionRegistration(
        "search",
        SideEffectLevel.READ_ONLY,
        lambda call: calls.append(call),
        tool_id="search",
    )
    admission = accepts()
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        StepActionRegistry(),
        BuiltinExecutorRegistry(),
        admission,
        accepts(),
    )
    runtime.actions.register(action)

    with pytest.raises(SkillRuntimeError, match="undeclared tools: search"):
        asyncio.run(runtime.execute(request()))

    assert admission.calls == 0
    assert calls == []


def test_runtime_compiles_declared_steps_as_langgraph_nodes(tmp_path: Path) -> None:
    package = manifest()
    package["execution"]["steps"] = [
        {
            "step_id": "draft",
            "action": "text.transform",
            "input_mapping": {"text": {"$ref": "input.notes"}},
        },
        {
            "step_id": "finalize",
            "action": "text.transform",
            "input_mapping": {"text": {"$ref": "steps.draft.summary"}},
        },
    ]
    write_package(tmp_path, package)
    seen_inputs = []

    async def transform(call):
        seen_inputs.append(call.inputs["text"])
        return StepActionResult({"summary": f"processed {call.inputs['text']}"})

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.READ_ONLY, transform))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), actions, BuiltinExecutorRegistry(), accepts(), accepts()
    )

    result = asyncio.run(runtime.execute(request()))

    assert runtime.workflow_runner.__class__.__name__ == "LangGraphWorkflowRunner"
    assert result.status is SkillExecutionStatus.COMPLETED
    assert seen_inputs == [
        "decisions and owners",
        "processed decisions and owners",
    ]
    assert result.output == {"summary": "processed processed decisions and owners"}
    assert [step.step_id for step in result.snapshot.step_states] == ["draft", "finalize"]


def test_runtime_runs_independent_langgraph_steps_in_parallel(tmp_path: Path) -> None:
    package = manifest()
    package["execution"]["steps"] = [
        {"step_id": "alpha", "action": "parallel.branch", "depends_on": []},
        {"step_id": "beta", "action": "parallel.branch", "depends_on": []},
        {
            "step_id": "combine",
            "action": "parallel.combine",
            "depends_on": ["alpha", "beta"],
            "input_mapping": {
                "left": {"$ref": "steps.alpha.value"},
                "right": {"$ref": "steps.beta.value"},
            },
        },
    ]
    write_package(tmp_path, package)
    both_branches_started = asyncio.Event()
    branch_count = 0

    async def branch(call):
        nonlocal branch_count
        branch_count += 1
        if branch_count == 2:
            both_branches_started.set()
        await asyncio.wait_for(both_branches_started.wait(), timeout=2)
        return StepActionResult({"value": call.step_id.upper()})

    async def combine(call):
        assert call.inputs == {"left": "ALPHA", "right": "BETA"}
        return StepActionResult({"summary": "ALPHA+BETA"})

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("parallel.branch", SideEffectLevel.READ_ONLY, branch))
    actions.register(StepActionRegistration("parallel.combine", SideEffectLevel.READ_ONLY, combine))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), actions, BuiltinExecutorRegistry(), accepts(), accepts()
    )

    result = asyncio.run(runtime.execute(request()))

    assert result.status is SkillExecutionStatus.COMPLETED
    assert branch_count == 2
    assert result.output == {"summary": "ALPHA+BETA"}
    assert {step.step_id for step in result.snapshot.step_states} == {"alpha", "beta", "combine"}


def test_parallel_llm_steps_stop_after_provider_usage_exceeds_shared_budget(
    tmp_path: Path,
) -> None:
    package = manifest()
    package["execution"]["steps"] = [
        {
            "step_id": step_id,
            "action": "llm.complete",
            "params": {"instruction": "Summarize the source."},
            "depends_on": [],
            "input_mapping": {"source": {"$ref": "input.notes"}},
        }
        for step_id in ("first", "second", "third")
    ] + [{"step_id": "after", "action": "tool.after", "depends_on": ["first", "second", "third"]}]
    write_package(tmp_path, package)

    class OverreportingModel:
        def __init__(self) -> None:
            self.requests: list[ModelRequest] = []

        async def complete(self, model_request: ModelRequest) -> ModelResponse:
            self.requests.append(model_request)
            return ModelResponse(
                text="summary",
                token_usage=ModelTokenUsage(input_tokens=4, output_tokens=2, total_tokens=6),
            )

    async def after(call):
        del call
        raise AssertionError("dependent workflow step must not start after budget exhaustion")

    model = OverreportingModel()
    actions = StepActionRegistry()
    register_llm_completion_action(actions, model)
    actions.register(StepActionRegistration("tool.after", SideEffectLevel.READ_ONLY, after))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), actions, BuiltinExecutorRegistry(), accepts(), accepts()
    )
    original = request(total_tokens=10)

    result = asyncio.run(runtime.execute(original))

    assert len(model.requests) == 2
    assert [item.max_output_tokens for item in model.requests] == [10, 4]
    assert original.budget.snapshot().usage.total_tokens == 6
    assert result.status is SkillExecutionStatus.FAILED


def test_admission_can_clarify_without_running_workflow(tmp_path: Path) -> None:
    write_package(tmp_path)
    executed = False

    async def transform(call):
        nonlocal executed
        executed = True
        return StepActionResult({"summary": "unused"})

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.READ_ONLY, transform))
    admission = FakeAssessor(
        SkillAssessment(AssessmentDisposition.CLARIFY, 0.9, "missing_input", "Which note?")
    )
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), actions, BuiltinExecutorRegistry(), admission, accepts()
    )

    result = asyncio.run(runtime.execute(request()))

    assert result.status is SkillExecutionStatus.CLARIFICATION
    assert result.clarification_prompt == "Which note?"
    assert not executed


def test_missing_required_skill_slots_clarify_before_admission_or_workflow(tmp_path: Path) -> None:
    write_package(tmp_path)
    executed = False

    async def transform(call):
        nonlocal executed
        executed = True
        return StepActionResult({"summary": "unexpected"})

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.READ_ONLY, transform))
    admission = FakeAssessor(
        SkillAssessment(AssessmentDisposition.ACCEPT, 1.0, "accepted", "Accepted")
    )
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), actions, BuiltinExecutorRegistry(), admission, accepts()
    )

    result = asyncio.run(runtime.execute(replace(request(), payload={})))

    assert result.status is SkillExecutionStatus.CLARIFICATION
    assert result.clarification_prompt == "请补充以下信息后我再执行该 Skill：notes。"
    assert admission.calls == 0
    assert not executed


def test_admission_rejection_returns_failed_result_without_running_workflow(tmp_path: Path) -> None:
    write_package(tmp_path)
    executed = False

    async def transform(call):
        nonlocal executed
        executed = True
        return StepActionResult({"summary": "unexpected"})

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.READ_ONLY, transform))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        actions,
        BuiltinExecutorRegistry(),
        FakeAssessor(SkillAssessment(AssessmentDisposition.REJECT, 0.99, "unsafe_request")),
        accepts(),
    )

    result = asyncio.run(runtime.execute(request()))

    assert result.status is SkillExecutionStatus.FAILED
    assert result.output == {"error_code": "skill_admission_rejected"}
    assert result.snapshot is not None
    assert result.snapshot.failure_code == "admission_rejected_unsafe_request"
    assert not executed


def test_assessor_failures_fail_closed_with_snapshot(tmp_path: Path) -> None:
    write_package(tmp_path)
    actions = StepActionRegistry()

    async def transform(call):
        return StepActionResult({"summary": "done"})

    actions.register(StepActionRegistration("text.transform", SideEffectLevel.READ_ONLY, transform))

    class BrokenAssessor:
        async def assess(self, package, payload, context, *, budget=None, snapshot=None):
            del budget
            raise RuntimeError("assessment unavailable")

    admission_result = asyncio.run(
        SkillRuntime(
            YamlSkillRegistry(tmp_path),
            actions,
            BuiltinExecutorRegistry(),
            BrokenAssessor(),
            accepts(),
        ).execute(request())
    )
    assert admission_result.status is SkillExecutionStatus.HUMAN_REVIEW
    assert admission_result.snapshot is not None
    assert admission_result.snapshot.failure_code == "admission_assessor_RuntimeError"

    result_assessor_result = asyncio.run(
        SkillRuntime(
            YamlSkillRegistry(tmp_path),
            actions,
            BuiltinExecutorRegistry(),
            accepts(),
            BrokenAssessor(),
        ).execute(request())
    )
    assert result_assessor_result.status is SkillExecutionStatus.HUMAN_REVIEW
    assert result_assessor_result.snapshot is not None
    assert result_assessor_result.snapshot.step_states[0].status == "completed"
    assert result_assessor_result.snapshot.failure_code == "result_assessor_RuntimeError"


def test_admission_plan_decision_uses_plan_fallback_before_execution(tmp_path: Path) -> None:
    write_package(tmp_path)
    fallback = FakeFallback()
    actions = StepActionRegistry()
    actions.register(
        StepActionRegistration("text.transform", SideEffectLevel.READ_ONLY, lambda _: None)
    )
    admission = FakeAssessor(SkillAssessment(AssessmentDisposition.PLAN, 0.2, "not_suitable"))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        actions,
        BuiltinExecutorRegistry(),
        admission,
        accepts(),
        plan_fallback=fallback,
    )

    result = asyncio.run(runtime.execute(request()))

    assert result.status is SkillExecutionStatus.PLAN_FALLBACK
    assert len(fallback.calls) == 1
    assert fallback.calls[0][1].step_states == ()


def test_irreversible_result_failure_uses_plan_for_state_reconciliation(tmp_path: Path) -> None:
    package = manifest(side_effect_level="irreversible")
    write_package(tmp_path, package)
    fallback = FakeFallback()

    async def mutate(call):
        return StepActionResult(
            {"summary": "changed"},
            side_effect=SkillSideEffect(
                action=call.action,
                level=SideEffectLevel.IRREVERSIBLE,
                idempotency_key=call.idempotency_key,
            ),
        )

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.IRREVERSIBLE, mutate))
    result_assessor = FakeAssessor(
        SkillAssessment(AssessmentDisposition.PLAN, 0.1, "result_failed")
    )
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        actions,
        BuiltinExecutorRegistry(),
        accepts(),
        result_assessor,
        plan_fallback=fallback,
    )

    result = asyncio.run(runtime.execute(request()))

    assert result.status is SkillExecutionStatus.PLAN_FALLBACK
    assert result.snapshot is not None
    assert result.snapshot.side_effects[0].level is SideEffectLevel.IRREVERSIBLE
    assert len(fallback.calls) == 1
    assert fallback.calls[0][2] == "result_failed_requires_state_reconciliation"


def test_reversible_result_failure_compensates_before_plan_fallback(
    tmp_path: Path,
) -> None:
    package = manifest(side_effect_level="reversible")
    write_package(tmp_path, package)
    fallback = FakeFallback()

    async def mutate(call):
        return StepActionResult(
            {"summary": "changed"},
            side_effect=SkillSideEffect(
                action=call.action,
                level=SideEffectLevel.REVERSIBLE,
                idempotency_key=call.idempotency_key,
                compensation_handle="undo-1",
            ),
        )

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.REVERSIBLE, mutate))
    compensated: list[str] = []

    async def compensate(call):
        compensated.append(call.compensation_handle)

    compensations = CompensationRegistry()
    compensations.register("text.transform", compensate)
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        actions,
        BuiltinExecutorRegistry(),
        accepts(),
        FakeAssessor(SkillAssessment(AssessmentDisposition.PLAN, 0.1, "result_failed")),
        plan_fallback=fallback,
        compensation_registry=compensations,
    )

    result = asyncio.run(runtime.execute(request()))

    assert result.status is SkillExecutionStatus.PLAN_FALLBACK
    assert fallback.calls[0][1].side_effects[0].compensation_handle == "undo-1"
    assert compensated == ["undo-1"]
    assert result.snapshot is not None
    assert result.snapshot.compensation_records[0].status is CompensationStatus.COMPLETED


def test_uncompensated_reversible_effect_uses_plan_for_state_reconciliation(
    tmp_path: Path,
) -> None:
    write_package(tmp_path, manifest(side_effect_level="reversible"))
    fallback = FakeFallback()

    async def mutate(call):
        return StepActionResult(
            {"summary": "changed"},
            side_effect=SkillSideEffect(
                call.action,
                SideEffectLevel.REVERSIBLE,
                call.idempotency_key,
                compensation_handle="undo-1",
            ),
        )

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.REVERSIBLE, mutate))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        actions,
        BuiltinExecutorRegistry(),
        accepts(),
        FakeAssessor(SkillAssessment(AssessmentDisposition.PLAN, 0.1, "result_failed")),
        plan_fallback=fallback,
    )

    result = asyncio.run(runtime.execute(request()))

    assert result.status is SkillExecutionStatus.PLAN_FALLBACK
    assert len(fallback.calls) == 1
    assert fallback.calls[0][2] == "result_failed_requires_state_reconciliation"
    assert result.snapshot is not None
    assert result.snapshot.result_assessment is not None
    assert result.snapshot.result_assessment.disposition is AssessmentDisposition.PLAN


def test_workflow_cancellation_compensates_completed_reversible_steps(tmp_path: Path) -> None:
    package_manifest = manifest(side_effect_level="reversible")
    package_manifest["execution"]["steps"] = [
        {"step_id": "mutate", "action": "state.mutate", "depends_on": []},
        {"step_id": "wait", "action": "state.wait", "depends_on": ["mutate"]},
    ]
    write_package(tmp_path, package_manifest)
    waiting = asyncio.Event()
    compensated: list[str] = []

    async def mutate(call):
        return StepActionResult(
            {"summary": "changed"},
            side_effect=SkillSideEffect(
                call.action,
                SideEffectLevel.REVERSIBLE,
                call.idempotency_key,
                compensation_handle="undo-42",
            ),
        )

    async def wait_for_cancel(call):
        del call
        waiting.set()
        await asyncio.Event().wait()

    async def compensate(call):
        compensated.append(call.compensation_handle)

    class FirstThenWaitRunner:
        async def run(self, steps, initial_state, execute_step):
            state = initial_state
            first_update = await execute_step(steps[0], state)
            state = {**state, **dict(first_update)}
            await execute_step(steps[1], state)
            return state

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("state.mutate", SideEffectLevel.REVERSIBLE, mutate))
    actions.register(
        StepActionRegistration("state.wait", SideEffectLevel.READ_ONLY, wait_for_cancel)
    )
    compensations = CompensationRegistry()
    compensations.register("state.mutate", compensate)
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        actions,
        BuiltinExecutorRegistry(),
        accepts(),
        accepts(),
        workflow_runner=FirstThenWaitRunner(),
        compensation_registry=compensations,
        cancellation_sink=SQLiteCancellationStore(tmp_path / "cancellations.sqlite3"),
    )

    async def scenario() -> tuple[ExecutionCancelled, tuple]:
        running = asyncio.create_task(runtime.execute(request()))
        await asyncio.wait_for(waiting.wait(), timeout=1)
        running.cancel()
        try:
            await running
        except ExecutionCancelled as error:
            return error, await SQLiteCancellationStore(
                tmp_path / "cancellations.sqlite3"
            ).list_recent()
        raise AssertionError("Cancellation should carry a compensation snapshot.")

    cancellation, audit_entries = asyncio.run(scenario())

    assert compensated == ["undo-42"]
    assert cancellation.snapshot.scope == "business/summary"
    assert cancellation.snapshot.compensation_records[0].status is CompensationStatus.COMPLETED
    assert cancellation.snapshot.side_effects[0].compensation_handle == "undo-42"
    assert cancellation.snapshot.event_id == audit_entries[0].event_id
    assert audit_entries[0].scope == "business/summary"
    assert audit_entries[0].compensation_records[0]["status"] == "completed"


def test_workflow_cancellation_safely_compensates_prepared_inflight_effect(
    tmp_path: Path,
) -> None:
    write_package(tmp_path, manifest(side_effect_level="reversible"))
    executing = asyncio.Event()
    compensated: list[tuple[str, bool]] = []

    async def prepare(call):
        assert call.action == "text.transform"
        return "snapshot-token"

    async def mutate(call):
        assert call.compensation_handle == "snapshot-token"
        executing.set()
        await asyncio.Event().wait()
        raise AssertionError("Cancelled operation should not continue.")

    async def compensate(call):
        compensated.append((call.compensation_handle, call.uncertain))

    actions = StepActionRegistry()
    actions.register(
        StepActionRegistration(
            "text.transform",
            SideEffectLevel.REVERSIBLE,
            mutate,
            prepare_compensation=prepare,
        )
    )
    compensations = CompensationRegistry()
    compensations.register("text.transform", compensate, safe_for_uncertain_effect=True)
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        actions,
        BuiltinExecutorRegistry(),
        accepts(),
        accepts(),
        compensation_registry=compensations,
    )

    async def scenario() -> ExecutionCancelled:
        task = asyncio.create_task(runtime.execute(request()))
        await asyncio.wait_for(executing.wait(), timeout=1)
        task.cancel()
        try:
            await task
        except ExecutionCancelled as error:
            return error
        raise AssertionError("Cancellation should carry a compensation snapshot.")

    cancellation = asyncio.run(scenario())

    assert compensated == [("snapshot-token", True)]
    assert cancellation.snapshot.side_effects[0].compensation_handle == "snapshot-token"
    assert cancellation.snapshot.compensation_records[0].status is CompensationStatus.COMPLETED


def test_action_risk_cannot_exceed_skill_declaration(tmp_path: Path) -> None:
    write_package(tmp_path, manifest(side_effect_level="read_only"))
    executed = False

    async def mutate(call):
        nonlocal executed
        executed = True
        return StepActionResult({"summary": "changed"})

    actions = StepActionRegistry()
    actions.register(StepActionRegistration("text.transform", SideEffectLevel.IDEMPOTENT, mutate))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path), actions, BuiltinExecutorRegistry(), accepts(), accepts()
    )

    try:
        asyncio.run(runtime.execute(request()))
    except RuntimeError as error:
        assert "risk exceeds" in str(error)
    else:
        raise AssertionError("Underdeclared side effect risk should be rejected.")
    assert not executed


def test_budget_exhaustion_prevents_plan_fallback(tmp_path: Path) -> None:
    write_package(tmp_path)
    fallback = FakeFallback()
    actions = StepActionRegistry()
    actions.register(
        StepActionRegistration("text.transform", SideEffectLevel.READ_ONLY, lambda _: None)
    )
    admission = FakeAssessor(SkillAssessment(AssessmentDisposition.PLAN, 0.2, "not_suitable"))
    runtime = SkillRuntime(
        YamlSkillRegistry(tmp_path),
        actions,
        BuiltinExecutorRegistry(),
        admission,
        accepts(),
        plan_fallback=fallback,
    )
    limited = request(total_tokens=1)
    limited.budget.consume_tokens(1)

    result = asyncio.run(runtime.execute(limited))

    assert result.status is SkillExecutionStatus.FAILED
    assert fallback.calls == []
