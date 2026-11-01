"""Replaceable interfaces for exporting observability events."""

from __future__ import annotations

from typing import Protocol

from hostpilot.observability.events import AuditEvent, CostEvent, TraceEvent


class TraceSink(Protocol):
    """Export trace events to a configured backend."""

    def record(self, event: TraceEvent) -> None: ...


class CostSink(Protocol):
    """Export usage and cost events to a configured backend."""

    def record(self, event: CostEvent) -> None: ...


class AuditSink(Protocol):
    """Export security-relevant decisions to an audit backend."""

    def record(self, event: AuditEvent) -> None: ...


__all__ = ["AuditSink", "CostSink", "TraceSink"]
