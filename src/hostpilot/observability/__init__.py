"""Trace, cost, and audit instrumentation boundaries."""

from hostpilot.observability.events import (
    AuditEvent,
    AuditOutcome,
    CostEvent,
    CostUnit,
    TraceContext,
    TraceEvent,
)
from hostpilot.observability.interfaces import AuditSink, CostSink, TraceSink
from hostpilot.observability.logging import (
    StructuredAuditSink,
    StructuredCostSink,
    configure_logging,
    get_logger,
)

__all__ = [
    "AuditEvent",
    "AuditOutcome",
    "AuditSink",
    "CostEvent",
    "CostSink",
    "CostUnit",
    "TraceContext",
    "TraceEvent",
    "TraceSink",
    "StructuredAuditSink",
    "StructuredCostSink",
    "configure_logging",
    "get_logger",
]
