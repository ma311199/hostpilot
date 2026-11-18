import asyncio

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot
from hostpilot.skills.compensation import CompensationRegistry, CompensationStatus
from hostpilot.skills.runtime import SkillSideEffect
from hostpilot.skills.yaml_registry import SideEffectLevel


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


def test_compensation_runs_reversible_effects_in_reverse_completion_order() -> None:
    calls: list[str] = []
    registry = CompensationRegistry()

    async def compensate(call):
        calls.append(call.compensation_handle)

    registry.register("state.write", compensate)
    effects = (
        SkillSideEffect("state.write", SideEffectLevel.REVERSIBLE, "key-1", "undo-1"),
        SkillSideEffect("state.write", SideEffectLevel.REVERSIBLE, "key-2", "undo-2"),
    )

    records = asyncio.run(
        registry.compensate(
            request_id="request-1",
            context=ContextSnapshot(request_id="request-1"),
            budget=budget(),
            effects=effects,
            confirmation_provider=None,
        )
    )

    assert calls == ["undo-2", "undo-1"]
    assert [record.status for record in records] == [
        CompensationStatus.COMPLETED,
        CompensationStatus.COMPLETED,
    ]


def test_missing_compensator_and_irreversible_effect_fail_closed() -> None:
    effects = (
        SkillSideEffect("unknown", SideEffectLevel.REVERSIBLE, "key-1", "undo-1"),
        SkillSideEffect("write", SideEffectLevel.IRREVERSIBLE, "key-2"),
    )
    records = asyncio.run(
        CompensationRegistry().compensate(
            request_id="request-1",
            context=ContextSnapshot(request_id="request-1"),
            budget=budget(),
            effects=effects,
            confirmation_provider=None,
        )
    )

    assert [record.status for record in records] == [
        CompensationStatus.NOT_REVERSIBLE,
        CompensationStatus.UNAVAILABLE,
    ]
    assert records[0].error_code == "effect_not_reversible"
    assert records[1].error_code == "compensation_handler_unavailable"


def test_uncertain_effect_requires_explicitly_safe_compensator() -> None:
    effect = SkillSideEffect("state.write", SideEffectLevel.REVERSIBLE, "key", "undo")
    calls: list[bool] = []

    async def compensate(call):
        calls.append(call.uncertain)

    unsafe = CompensationRegistry()
    unsafe.register("state.write", compensate)
    safe = CompensationRegistry()
    safe.register("state.write", compensate, safe_for_uncertain_effect=True)

    async def run(registry):
        return await registry.compensate(
            request_id="request-1",
            context=ContextSnapshot(request_id="request-1"),
            budget=budget(),
            effects=(),
            uncertain_effects=(effect,),
            confirmation_provider=None,
        )

    unsafe_records = asyncio.run(run(unsafe))
    safe_records = asyncio.run(run(safe))

    assert unsafe_records[0].status is CompensationStatus.UNAVAILABLE
    assert unsafe_records[0].error_code == "uncertain_compensation_not_safe"
    assert safe_records[0].status is CompensationStatus.COMPLETED
    assert calls == [True]
