"""Request-local trace context propagation for nested runtime operations."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from uuid import UUID

from hostpilot.observability.events import TraceContext

_CURRENT_TRACE_CONTEXT: ContextVar[TraceContext | None] = ContextVar(
    "hostpilot_trace_context",
    default=None,
)


def current_trace_context() -> TraceContext | None:
    """Return the trace context active in this async task, if any."""
    return _CURRENT_TRACE_CONTEXT.get()


def child_trace_context(*, trace_id: UUID | None = None) -> TraceContext:
    """Create a child span under the current context or a new request trace."""
    parent = current_trace_context()
    resolved_trace_id = trace_id or (parent.trace_id if parent is not None else None)
    if resolved_trace_id is None:
        return TraceContext()
    return TraceContext(
        trace_id=resolved_trace_id,
        parent_span_id=parent.span_id if parent is not None else None,
    )


@contextmanager
def trace_context_scope(context: TraceContext) -> Iterator[None]:
    """Set one span as the parent for nested operations until the scope exits."""
    token: Token[TraceContext | None] = _CURRENT_TRACE_CONTEXT.set(context)
    try:
        yield
    finally:
        _CURRENT_TRACE_CONTEXT.reset(token)


__all__ = ["child_trace_context", "current_trace_context", "trace_context_scope"]
