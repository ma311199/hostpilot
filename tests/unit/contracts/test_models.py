from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    BudgetUsage,
    ContextSnapshot,
    Evidence,
    EvidenceSet,
    MemoryScope,
    MemoryWriteProposal,
    RetrievalToolRequest,
    Route,
    RouteDecision,
    SandboxPolicy,
    SandboxResourceLimits,
)


def test_context_snapshot_is_immutable_and_rejects_unknown_fields() -> None:
    snapshot = ContextSnapshot(request_id="request-1", conversation_id="conversation-1")

    with pytest.raises(ValidationError):
        snapshot.conversation_id = "changed"  # type: ignore[misc]
    with pytest.raises(ValidationError):
        ContextSnapshot(request_id="request-1", unexpected="value")  # type: ignore[call-arg]


def test_budget_context_reports_limits_as_exhausted() -> None:
    budget = BudgetContext(
        limits=BudgetLimits(
            total_tokens=100,
            tool_calls=2,
            retrieval_calls=1,
            timeout_seconds=30,
        ),
        usage=BudgetUsage(tool_calls=2),
    )

    assert budget.is_exhausted


def test_zero_tool_budget_is_exhausted_before_any_call() -> None:
    budget = BudgetContext(
        limits=BudgetLimits(
            total_tokens=100,
            tool_calls=0,
            retrieval_calls=1,
            timeout_seconds=30,
        )
    )

    assert budget.is_exhausted


def test_memory_proposal_id_has_a_bounded_length() -> None:
    with pytest.raises(ValidationError):
        MemoryWriteProposal(
            proposal_id="p" * 65,
            scope=MemoryScope.LONG_TERM,
            content="Preference",
        )


def test_route_decision_requires_route_specific_fields() -> None:
    with pytest.raises(ValidationError, match="require tool_name"):
        RouteDecision(request_id="request-1", route=Route.TOOL_CALL)

    decision = RouteDecision(
        request_id="request-1",
        route=Route.CLARIFY,
        clarification_prompt="Which file should I use?",
    )

    assert decision.route is Route.CLARIFY


def test_retrieval_request_requires_explicit_budget() -> None:
    with pytest.raises(ValidationError):
        RetrievalToolRequest(query="Find the policy", mode="fast")  # type: ignore[call-arg]


def test_evidence_requires_timezone_aware_retrieval_time() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Evidence(
            source_id="doc-1",
            confidence=0.8,
            retrieved_at=datetime(2026, 10, 29),
            retrieval_path=("search",),
            content="Evidence text",
        )

    result = EvidenceSet(
        request_id="request-1",
        evidence=(
            Evidence(
                source_id="doc-1",
                confidence=0.8,
                retrieved_at=datetime(2026, 10, 29, tzinfo=UTC),
                retrieval_path=("search",),
                content="Evidence text",
            ),
        ),
    )

    assert result.evidence[0].source_id == "doc-1"


def test_sandbox_policy_defaults_to_deny_and_rejects_grants_when_denied() -> None:
    limits = SandboxResourceLimits(cpu_seconds=2, memory_bytes=1024, max_processes=1)
    policy = SandboxPolicy(resource_limits=limits)

    assert not policy.allowed
    assert not policy.network_access
    assert not policy.filesystem_access

    with pytest.raises(ValidationError, match="cannot grant capabilities"):
        SandboxPolicy(
            allowed=False,
            capabilities=frozenset({"network"}),
            resource_limits=limits,
        )
