import time

import pytest

from hostpilot.budget import BudgetExceededError, RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits


def _limits(*, tokens: int = 10, tools: int = 1, retrieval: int = 1, timeout: float = 1):
    return BudgetLimits(
        total_tokens=tokens,
        tool_calls=tools,
        retrieval_calls=retrieval,
        timeout_seconds=timeout,
    )


def test_request_budget_tracks_tokens_and_tool_calls() -> None:
    budget = RequestBudget.start(BudgetContext(limits=_limits()))

    budget.consume_tokens(4)
    budget.consume_tool_call(retrieval=True)

    assert budget.snapshot().usage.total_tokens == 4
    assert budget.snapshot().usage.tool_calls == 1
    assert budget.snapshot().usage.retrieval_calls == 1
    with pytest.raises(BudgetExceededError, match="tool_calls"):
        budget.consume_tool_call()


def test_zero_tool_budget_does_not_block_non_tool_request() -> None:
    budget = RequestBudget.start(BudgetContext(limits=_limits(tools=0, retrieval=0)))

    budget.consume_tokens(1)
    with pytest.raises(BudgetExceededError, match="tool_calls"):
        budget.consume_tool_call()


def test_request_budget_rejects_token_overrun_and_expired_deadline() -> None:
    budget = RequestBudget.start(BudgetContext(limits=_limits(tokens=2)))
    with pytest.raises(BudgetExceededError, match="total_tokens"):
        budget.consume_tokens(3)

    expired = RequestBudget(
        context=BudgetContext(limits=_limits(timeout=0.001)),
        started_at=time.monotonic() - 1,
    )
    with pytest.raises(BudgetExceededError, match="timeout_seconds"):
        expired.ensure_available()


def test_request_budget_rejects_negative_token_usage() -> None:
    budget = RequestBudget.start(BudgetContext(limits=_limits()))
    with pytest.raises(ValueError, match="cannot be negative"):
        budget.consume_tokens(-1)
