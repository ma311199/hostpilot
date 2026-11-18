import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from hostpilot.observability import AuditEvent, AuditOutcome
from hostpilot.persistence import SQLiteAuditStore


def test_audit_store_filters_events_and_persists_only_typed_metadata(tmp_path) -> None:
    store = SQLiteAuditStore(tmp_path / "private" / "hostpilot.sqlite3")
    trace_id = uuid4()
    first = AuditEvent(
        trace_id=trace_id,
        action="tool.call",
        outcome=AuditOutcome.COMPLETED,
        resource_type="tool",
        resource_id="read_text_file",
        occurred_at=datetime.now(UTC),
    )
    other = AuditEvent(
        action="memory.proposal.rejected",
        outcome=AuditOutcome.CANCELLED,
        resource_type="memory_proposal",
    )

    async def scenario() -> tuple[AuditEvent, ...]:
        await store.initialize()
        store.record(first)
        store.record(other)
        return await store.list_events(trace_id=trace_id)

    entries = asyncio.run(scenario())

    assert entries == (first,)
    with sqlite3.connect(store.database_path) as connection:
        raw_values = " ".join(str(row) for row in connection.execute("SELECT * FROM audit_events"))
    assert "private conversation" not in raw_values


def test_audit_store_enforces_retention_count_and_time_filters(tmp_path) -> None:
    store = SQLiteAuditStore(tmp_path / "audits.sqlite3", retention_days=1, max_events=2)
    old = AuditEvent(
        action="old",
        outcome=AuditOutcome.ALLOWED,
        resource_type="tool",
        occurred_at=datetime.now(UTC) - timedelta(days=2),
    )

    async def scenario() -> tuple[AuditEvent, ...]:
        await store.initialize()
        store.record(old)
        for action in ("first", "second", "last"):
            store.record(
                AuditEvent(action=action, outcome=AuditOutcome.ALLOWED, resource_type="tool")
            )
        return await store.list_events(
            limit=1,
            offset=1,
            since=datetime.now(UTC) - timedelta(minutes=1),
        )

    entries = asyncio.run(scenario())

    assert [event.action for event in entries] == ["second"]


def test_audit_store_rejects_invalid_filter_ranges(tmp_path) -> None:
    store = SQLiteAuditStore(tmp_path / "audits.sqlite3")
    now = datetime.now(UTC)

    with pytest.raises(ValueError, match="timezone"):
        asyncio.run(store.list_events(since=now.replace(tzinfo=None)))
    with pytest.raises(ValueError, match="later than"):
        asyncio.run(store.list_events(since=now, until=now - timedelta(seconds=1)))
