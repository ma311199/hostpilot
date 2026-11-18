"""Trace, cost, and audit instrumentation boundaries."""

from hostpilot.observability.context import (
    child_trace_context,
    current_trace_context,
    trace_context_scope,
)
from hostpilot.observability.events import (
    AuditEvent,
    AuditOutcome,
    CostEvent,
    CostUnit,
    TraceContext,
    TraceEvent,
)
from hostpilot.observability.interfaces import AuditSink, CompositeAuditSink, CostSink, TraceSink
from hostpilot.observability.logging import (
    StructuredAuditSink,
    StructuredCostSink,
    StructuredTraceSink,
    configure_logging,
    get_logger,
)

__all__ = [
    "AuditEvent",
    "AuditOutcome",
    "AuditSink",
    "CompositeAuditSink",
    "CostEvent",
    "CostSink",
    "CostUnit",
    "TraceContext",
    "TraceEvent",
    "TraceSink",
    "child_trace_context",
    "current_trace_context",
    "trace_context_scope",
    "StructuredAuditSink",
    "StructuredCostSink",
    "StructuredTraceSink",
    "configure_logging",
    "get_logger",
]
