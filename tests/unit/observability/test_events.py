from datetime import datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from hostpilot.observability import (
    AuditEvent,
    AuditOutcome,
    CostEvent,
    CostUnit,
    TraceContext,
    TraceEvent,
)


def test_trace_context_correlates_events_with_uuid_identifiers() -> None:
    context = TraceContext()
    event = TraceEvent(context=context, name="request.accepted")

    assert isinstance(event.context.trace_id, UUID)
    assert event.occurred_at.tzinfo is not None


def test_trace_event_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        TraceEvent(
            context=TraceContext(),
            name="request.accepted",
            occurred_at=datetime(2026, 10, 29),
        )


def test_audit_and_cost_events_use_explicit_low_risk_fields() -> None:
    audit = AuditEvent(
        action="tool.execute",
        outcome=AuditOutcome.DENIED,
        resource_type="tool",
        resource_id="calendar.write",
        reason_code="missing_confirmation",
    )
    cost = CostEvent(
        trace_id=TraceContext().trace_id,
        component="model",
        unit=CostUnit.TOKEN,
        quantity=12,
    )

    assert audit.outcome is AuditOutcome.DENIED
    assert cost.quantity == 12


def test_audit_and_cost_events_reject_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        AuditEvent(
            action="tool.execute",
            outcome=AuditOutcome.DENIED,
            resource_type="tool",
            occurred_at=datetime(2026, 10, 29),
        )

    with pytest.raises(ValidationError, match="timezone"):
        CostEvent(
            trace_id=TraceContext().trace_id,
            component="model",
            unit=CostUnit.TOKEN,
            quantity=1,
            occurred_at=datetime(2026, 10, 29),
        )
