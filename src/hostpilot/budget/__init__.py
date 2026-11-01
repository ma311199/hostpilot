"""Request-scoped budget enforcement."""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic

from hostpilot.contracts import BudgetContext, BudgetUsage


class BudgetExceededError(RuntimeError):
    """Raised when an operation would exceed a request's resource limits."""

    def __init__(self, resource: str) -> None:
        self.resource = resource
        super().__init__(f"Request budget exhausted for {resource}.")


@dataclass
class RequestBudget:
    """Track and enforce resource usage for one request."""

    context: BudgetContext
    started_at: float

    @classmethod
    def start(cls, context: BudgetContext) -> RequestBudget:
        """Create a tracker and reject limits already exhausted at request entry."""
        tracker = cls(context=context, started_at=monotonic())
        tracker.ensure_available()
        return tracker

    def snapshot(self) -> BudgetContext:
        """Return current usage including elapsed wall time."""
        elapsed = max(self.context.usage.elapsed_seconds, monotonic() - self.started_at)
        return self.context.model_copy(
            update={"usage": self.context.usage.model_copy(update={"elapsed_seconds": elapsed})}
        )

    def ensure_available(self) -> None:
        """Raise when any resource limit has been reached."""
        snapshot = self.snapshot()
        limits = snapshot.limits
        usage = snapshot.usage
        if usage.total_tokens >= limits.total_tokens:
            raise BudgetExceededError("total_tokens")
        if usage.elapsed_seconds >= limits.timeout_seconds:
            raise BudgetExceededError("timeout_seconds")

    def consume_tokens(self, amount: int) -> None:
        """Record model token usage, rejecting negative or over-budget amounts."""
        if amount < 0:
            raise ValueError("Token usage cannot be negative.")
        self._consume(BudgetUsage(total_tokens=amount), "total_tokens")

    def consume_tool_call(self, *, retrieval: bool = False) -> None:
        """Reserve one tool call and optionally one retrieval call."""
        self.ensure_available()
        usage = self.context.usage
        limits = self.context.limits
        if usage.tool_calls + 1 > limits.tool_calls:
            raise BudgetExceededError("tool_calls")
        if retrieval and usage.retrieval_calls + 1 > limits.retrieval_calls:
            raise BudgetExceededError("retrieval_calls")
        self.context = self.context.model_copy(
            update={
                "usage": usage.model_copy(
                    update={
                        "tool_calls": usage.tool_calls + 1,
                        "retrieval_calls": usage.retrieval_calls + int(retrieval),
                    }
                )
            }
        )

    def _consume(self, delta: BudgetUsage, resource: str) -> None:
        self.ensure_available()
        current = self.context.usage
        total = current.total_tokens + delta.total_tokens
        if total > self.context.limits.total_tokens:
            raise BudgetExceededError(resource)
        self.context = self.context.model_copy(
            update={"usage": current.model_copy(update={"total_tokens": total})}
        )


__all__ = ["BudgetExceededError", "RequestBudget"]
