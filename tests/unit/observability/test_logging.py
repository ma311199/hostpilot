import json

from hostpilot.config import Environment, Settings
from hostpilot.observability import (
    AuditEvent,
    AuditOutcome,
    StructuredAuditSink,
    configure_logging,
    get_logger,
)


def test_production_logging_emits_structured_json(capsys) -> None:
    configure_logging(Settings(environment=Environment.PRODUCTION))
    get_logger("hostpilot.test").info("request.accepted", trace_id="trace-1")

    event = json.loads(capsys.readouterr().out)

    assert event["event"] == "request.accepted"
    assert event["trace_id"] == "trace-1"
    assert event["level"] == "info"


def test_audit_sink_emits_typed_metadata_without_payload_content(capsys) -> None:
    configure_logging(Settings(environment=Environment.PRODUCTION))
    event = AuditEvent(
        action="memory.proposal.write",
        outcome=AuditOutcome.COMPLETED,
        resource_type="memory_proposal",
        resource_id="proposal-1",
    )

    StructuredAuditSink().record(event)

    logged = json.loads(capsys.readouterr().out)
    assert logged["event"] == "audit_event"
    assert logged["action"] == "memory.proposal.write"
    assert logged["resource_id"] == "proposal-1"
    assert "content" not in logged
