"""Context-local access to the active request budget during framework model calls."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from hostpilot.budget import RequestBudget

_active_budget: ContextVar[RequestBudget | None] = ContextVar(
    "hostpilot_request_budget", default=None
)


def current_request_budget() -> RequestBudget | None:
    """Return the budget active in this async context, if any."""
    return _active_budget.get()


@contextmanager
def request_budget_scope(budget: RequestBudget) -> Iterator[None]:
    """Expose one request budget to nested LangChain model calls."""
    token: Token[RequestBudget | None] = _active_budget.set(budget)
    try:
        yield
    finally:
        _active_budget.reset(token)


__all__ = ["current_request_budget", "request_budget_scope"]
