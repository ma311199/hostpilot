import asyncio
from collections.abc import Mapping
from typing import Any

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ExecutionEnvironment,
    TimeoutProfile,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.observability import AuditEvent, AuditOutcome
from hostpilot.policy import PolicyEngine
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.tools import (
    ToolDeniedError,
    ToolExecutionError,
    ToolExecutor,
    ToolRegistration,
    ToolRegistry,
)
from hostpilot.verification import TaskVerificationError


def _budget(*, calls: int = 2, retrieval: int = 1) -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100,
                tool_calls=calls,
                retrieval_calls=retrieval,
                timeout_seconds=2,
            )
        )
    )


class FakeConfirmation:
    def __init__(self, decision: ConfirmationDecision) -> None:
        self.decision = decision
        self.requests: list[ConfirmationRequest] = []

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        self.requests.append(request)
        return self.decision


def _registration(
    handler,
    *,
    high_impact: bool = False,
    capabilities: frozenset[str] = frozenset(),
    trust: TrustLevel = TrustLevel.HOST,
    tool_type: ToolType = ToolType.ACTION,
    timeout_seconds: float = 1,
    verifier=None,
) -> ToolRegistration:
    return ToolRegistration(
        descriptor=ToolDescriptor(
            name="sample",
            description="Perform the sample operation",
            tool_type=tool_type,
            trust_level=trust,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=timeout_seconds),
            capabilities=capabilities,
            high_impact=high_impact,
        ),
        validate_arguments=lambda args: args,
        handler=handler,
        verify_result=verifier or (lambda result, args: None),
        confirmation_summary=(lambda args: "Write the sample data") if high_impact else None,
    )


def test_policy_denial_has_no_tool_side_effect() -> None:
    async def scenario() -> None:
        calls: list[Mapping[str, Any]] = []
        audit: list[AuditEvent] = []

        async def handler(arguments: Mapping[str, Any]) -> str:
            calls.append(arguments)
            return "done"

        registry = ToolRegistry()
        registry.register(_registration(handler, capabilities=frozenset({"write"})))
        executor = ToolExecutor(registry, PolicyEngine(), audit=audit.append)

        with pytest.raises(ToolDeniedError, match="capability_not_granted"):
            await executor.execute("sample", {}, budget=_budget())

        assert calls == []
        assert audit[-1].outcome is AuditOutcome.DENIED
        assert audit[-1].reason_code == "capability_not_granted"

    asyncio.run(scenario())


def test_unregistered_tool_is_denied_and_audited() -> None:
    async def scenario() -> None:
        audit: list[AuditEvent] = []
        executor = ToolExecutor(ToolRegistry(), PolicyEngine(), audit=audit.append)

        with pytest.raises(ToolDeniedError, match="not registered"):
            await executor.execute("missing", {}, budget=_budget())

        assert audit[-1].outcome is AuditOutcome.DENIED
        assert audit[-1].reason_code == "tool_not_registered"

    asyncio.run(scenario())


def test_third_party_host_tool_requires_an_execution_boundary() -> None:
    descriptor = ToolDescriptor(
        name="third_party",
        description="Third party local tool",
        tool_type=ToolType.ACTION,
        trust_level=TrustLevel.THIRD_PARTY,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=1),
    )

    decision = PolicyEngine().authorize(descriptor, {})

    assert not decision.allowed
    assert decision.reason_code == "third_party_tool_requires_boundary"


def test_high_impact_operation_requires_explicit_confirmation() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        audit: list[AuditEvent] = []

        async def handler(arguments: Mapping[str, Any]) -> str:
            calls.append("ran")
            return "done"

        registry = ToolRegistry()
        registry.register(_registration(handler, high_impact=True))
        executor = ToolExecutor(registry, PolicyEngine(), audit=audit.append)

        with pytest.raises(ToolDeniedError, match="requires user confirmation"):
            await executor.execute("sample", {}, budget=_budget())

        assert calls == []
        assert audit[-1].reason_code == "confirmation_provider_unavailable"

    asyncio.run(scenario())


def test_approved_high_impact_tool_is_budgeted_and_verified() -> None:
    async def scenario() -> None:
        audit: list[AuditEvent] = []
        confirmation = FakeConfirmation(ConfirmationDecision.APPROVED)

        async def handler(arguments: Mapping[str, Any]) -> dict[str, bool]:
            return {"ok": True}

        def verify(result: Any, arguments: Mapping[str, Any]) -> None:
            if result.get("ok") is not True:
                raise ValueError("Operation did not complete.")

        registry = ToolRegistry()
        registry.register(
            _registration(
                handler,
                high_impact=True,
                capabilities=frozenset({"write"}),
                verifier=verify,
            )
        )
        executor = ToolExecutor(
            registry,
            PolicyEngine(),
            audit=audit.append,
            confirmation=confirmation,
        )
        budget = _budget()

        result = await executor.execute(
            "sample",
            {},
            budget=budget,
            granted_capabilities=frozenset({"write"}),
        )

        assert result == {"ok": True}
        assert confirmation.requests[0].tool_name == "sample"
        assert confirmation.requests[0].summary == "Write the sample data"
        assert budget.snapshot().usage.tool_calls == 1
        assert audit[0].outcome is AuditOutcome.ALLOWED
        assert audit[-1].outcome is AuditOutcome.COMPLETED

    asyncio.run(scenario())


def test_tool_call_budget_exhaustion_prevents_handler_execution() -> None:
    async def scenario() -> None:
        calls: list[str] = []
        audit: list[AuditEvent] = []

        async def handler(arguments: Mapping[str, Any]) -> str:
            calls.append("ran")
            return "done"

        registry = ToolRegistry()
        registry.register(_registration(handler))
        executor = ToolExecutor(registry, PolicyEngine(), audit=audit.append)

        with pytest.raises(ToolDeniedError, match="budget exhausted"):
            await executor.execute("sample", {}, budget=_budget(calls=0))

        assert calls == []
        assert audit[-1].reason_code == "tool_calls"

    asyncio.run(scenario())


def test_failed_verification_is_reported_and_never_returned_as_success() -> None:
    async def scenario() -> None:
        audit: list[AuditEvent] = []

        async def handler(arguments: Mapping[str, Any]) -> str:
            return "not verified"

        def verify(result: Any, arguments: Mapping[str, Any]) -> None:
            raise ValueError("Result did not match the requested operation.")

        registry = ToolRegistry()
        registry.register(_registration(handler, verifier=verify))
        executor = ToolExecutor(registry, PolicyEngine(), audit=audit.append)

        with pytest.raises(ToolExecutionError, match="failed verification") as error:
            await executor.execute("sample", {}, budget=_budget())

        assert isinstance(error.value.__cause__, TaskVerificationError)
        assert audit[-1].outcome is AuditOutcome.FAILED
        assert audit[-1].reason_code == "verification_failed"

    asyncio.run(scenario())


def test_tool_handler_timeout_is_a_failed_operation() -> None:
    async def scenario() -> None:
        audit: list[AuditEvent] = []

        async def handler(arguments: Mapping[str, Any]) -> str:
            await asyncio.sleep(0.05)
            return "late"

        registry = ToolRegistry()
        registry.register(_registration(handler, timeout_seconds=0.01))
        executor = ToolExecutor(registry, PolicyEngine(), audit=audit.append)

        with pytest.raises(ToolExecutionError, match="timed out"):
            await executor.execute("sample", {}, budget=_budget())

        assert audit[-1].outcome is AuditOutcome.FAILED
        assert audit[-1].reason_code == "tool_timeout"

    asyncio.run(scenario())


def test_cancelling_tool_execution_is_audited_and_propagated() -> None:
    async def scenario() -> None:
        audit: list[AuditEvent] = []

        async def handler(arguments: Mapping[str, Any]) -> str:
            raise asyncio.CancelledError

        registry = ToolRegistry()
        registry.register(_registration(handler))
        executor = ToolExecutor(registry, PolicyEngine(), audit=audit.append)

        with pytest.raises(asyncio.CancelledError):
            await executor.execute("sample", {}, budget=_budget())

        assert audit[-1].outcome is AuditOutcome.CANCELLED
        assert audit[-1].reason_code == "execution_cancelled"

    asyncio.run(scenario())
