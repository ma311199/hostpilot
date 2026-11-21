from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot
from hostpilot.runtime import RequestContext


def _budget() -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100,
                tool_calls=2,
                retrieval_calls=1,
                timeout_seconds=30,
            )
        )
    )


def test_request_context_retains_snapshot_and_live_budget() -> None:
    snapshot = ContextSnapshot(request_id="request-1", session_id="session-1")
    budget = _budget()

    request = RequestContext(snapshot=snapshot, budget=budget)
    budget.consume_tokens(3)

    assert request.snapshot is snapshot
    assert request.budget is budget
    assert request.budget.snapshot().usage.total_tokens == 3
    assert request.confirmation_provider is None


def test_request_context_keeps_request_data_separate() -> None:
    first_snapshot = ContextSnapshot(request_id="request-1", session_id="session-1")
    second_snapshot = ContextSnapshot(request_id="request-2", session_id="session-2")
    first_budget = _budget()
    second_budget = _budget()

    first = RequestContext(snapshot=first_snapshot, budget=first_budget)
    second = RequestContext(snapshot=second_snapshot, budget=second_budget)

    assert first.snapshot is not second.snapshot
    assert first.budget is not second.budget
    assert first.snapshot.request_id == "request-1"
    assert second.snapshot.request_id == "request-2"
