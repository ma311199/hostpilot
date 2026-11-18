"""Bounded SQLite storage for sanitized security audit events."""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import aiosqlite

from hostpilot.observability import AuditEvent, AuditOutcome


class SQLiteAuditStore:
    """Persist typed audit decisions with retention and total-row bounds."""

    def __init__(
        self,
        database_path: Path | str,
        *,
        retention_days: int = 90,
        max_events: int = 100_000,
    ) -> None:
        if retention_days < 1 or max_events < 1:
            raise ValueError("Audit retention and event limits must be positive.")
        self.database_path = Path(database_path).expanduser()
        self.retention_days = retention_days
        self.max_events = max_events

    async def initialize(self) -> None:
        """Create the audit table and time and trace indexes."""
        if self.database_path.is_symlink():
            raise ValueError("Audit database cannot be a symbolic link.")
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.database_path.parent.is_symlink():
            raise ValueError("Audit database directory cannot be a symbolic link.")
        os.chmod(self.database_path.parent, 0o700)
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS audit_events (
                    event_id TEXT PRIMARY KEY,
                    trace_id TEXT,
                    action TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT,
                    reason_code TEXT,
                    occurred_at TEXT NOT NULL
                )"""
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_trace_time "
                "ON audit_events(trace_id, occurred_at DESC)"
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_events(occurred_at DESC)"
            )
            await connection.commit()
        os.chmod(self.database_path, 0o600)

    def record(self, event: AuditEvent) -> None:
        """Store a typed event; only its declared, bounded metadata is written."""
        occurred_at = event.occurred_at.astimezone(UTC)
        cutoff = datetime.now(UTC) - timedelta(days=self.retention_days)
        with sqlite3.connect(self.database_path, timeout=5) as connection:
            connection.execute(
                """INSERT INTO audit_events
                   (event_id, trace_id, action, outcome, resource_type, resource_id,
                    reason_code, occurred_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(event.event_id),
                    str(event.trace_id) if event.trace_id else None,
                    event.action[:160],
                    event.outcome.value,
                    event.resource_type[:160],
                    event.resource_id[:256] if event.resource_id else None,
                    event.reason_code[:160] if event.reason_code else None,
                    occurred_at.isoformat(),
                ),
            )
            connection.execute(
                "DELETE FROM audit_events WHERE occurred_at < ?", (cutoff.isoformat(),)
            )
            connection.execute(
                """DELETE FROM audit_events WHERE event_id IN (
                       SELECT event_id FROM audit_events ORDER BY occurred_at DESC
                       LIMIT -1 OFFSET ?
                   )""",
                (self.max_events,),
            )
            connection.commit()

    async def list_events(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        trace_id: UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[AuditEvent, ...]:
        """Return newest matching events, with optional trace and time filters."""
        if not 1 <= limit <= 1000:
            raise ValueError("Audit list limit must be between 1 and 1000.")
        if not 0 <= offset <= 1_000_000:
            raise ValueError("Audit offset must be between 0 and 1000000.")
        for value in (since, until):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("Audit time filters must include a timezone.")
        if since is not None and until is not None and since > until:
            raise ValueError("Audit since filter cannot be later than until.")
        clauses: list[str] = []
        parameters: list[str | int] = []
        if trace_id is not None:
            clauses.append("trace_id = ?")
            parameters.append(str(trace_id))
        if since is not None:
            clauses.append("occurred_at >= ?")
            parameters.append(since.astimezone(UTC).isoformat())
        if until is not None:
            clauses.append("occurred_at <= ?")
            parameters.append(until.astimezone(UTC).isoformat())
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                f"""SELECT event_id, trace_id, action, outcome, resource_type,
                           resource_id, reason_code, occurred_at
                    FROM audit_events {where}
                    ORDER BY occurred_at DESC, event_id DESC LIMIT ? OFFSET ?""",
                (*parameters, limit, offset),
            )
            rows = await cursor.fetchall()
        return tuple(
            AuditEvent(
                event_id=UUID(row["event_id"]),
                trace_id=UUID(row["trace_id"]) if row["trace_id"] else None,
                action=row["action"],
                outcome=AuditOutcome(row["outcome"]),
                resource_type=row["resource_type"],
                resource_id=row["resource_id"],
                reason_code=row["reason_code"],
                occurred_at=datetime.fromisoformat(row["occurred_at"]),
            )
            for row in rows
        )


__all__ = ["SQLiteAuditStore"]
