"""SQLite storage for sanitized execution cancellation and compensation summaries."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import aiosqlite

if TYPE_CHECKING:
    from hostpilot.skills.compensation import CancellationSnapshot


@dataclass(frozen=True)
class CancellationAuditEntry:
    event_id: str
    request_id: str
    scope: str
    occurred_at: str
    uncertain_operations: tuple[str, ...]
    side_effects: tuple[Mapping[str, Any], ...]
    compensation_records: tuple[Mapping[str, Any], ...]


class SQLiteCancellationStore:
    """Persist only safe metadata; never store tool arguments, outputs, or handles."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()

    async def initialize(self) -> None:
        if self.database_path.is_symlink():
            raise ValueError("Cancellation database cannot be a symbolic link.")
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.database_path.parent.is_symlink():
            raise ValueError("Cancellation database directory cannot be a symbolic link.")
        os.chmod(self.database_path.parent, 0o700)
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_cancellations (
                    event_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    uncertain_operations_json TEXT NOT NULL,
                    side_effects_json TEXT NOT NULL,
                    compensation_records_json TEXT NOT NULL
                )
                """
            )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_execution_cancellations_occurred_at "
                "ON execution_cancellations (occurred_at DESC)"
            )
            await connection.commit()
        os.chmod(self.database_path, 0o600)

    async def record(self, snapshot: CancellationSnapshot) -> str:
        """Insert a sanitized cancellation entry and return its event identifier."""
        await self.initialize()
        event_id = str(uuid4())
        occurred_at = datetime.now(UTC).isoformat()
        uncertain = [value[:128] for value in snapshot.uncertain_operations[:128]]
        effects = [
            {
                "action": effect.action[:128],
                "level": effect.level.value,
                "idempotency_key": effect.idempotency_key[:128],
                "compensation_handle_available": bool(effect.compensation_handle),
            }
            for effect in snapshot.side_effects[:128]
        ]
        compensations = [
            {
                "action": record.action[:128],
                "idempotency_key": record.idempotency_key[:128],
                "status": record.status.value,
                "error_code": record.error_code[:128] if record.error_code else None,
            }
            for record in snapshot.compensation_records[:128]
        ]
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """
                INSERT INTO execution_cancellations (
                    event_id, request_id, scope, occurred_at,
                    uncertain_operations_json, side_effects_json, compensation_records_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    snapshot.request_id[:128],
                    snapshot.scope[:128],
                    occurred_at,
                    json.dumps(uncertain, ensure_ascii=False),
                    json.dumps(effects, ensure_ascii=False),
                    json.dumps(compensations, ensure_ascii=False),
                ),
            )
            await connection.commit()
        os.chmod(self.database_path, 0o600)
        return event_id

    async def list_recent(self, *, limit: int = 100) -> tuple[CancellationAuditEntry, ...]:
        """List recent sanitized cancellation metadata for local diagnostics."""
        if not 1 <= limit <= 1000:
            raise ValueError("Cancellation history limit must be between 1 and 1000.")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            async with connection.execute(
                """
                SELECT event_id, request_id, scope, occurred_at,
                       uncertain_operations_json, side_effects_json, compensation_records_json
                FROM execution_cancellations
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
        return tuple(
            CancellationAuditEntry(
                event_id=row["event_id"],
                request_id=row["request_id"],
                scope=row["scope"],
                occurred_at=row["occurred_at"],
                uncertain_operations=tuple(json.loads(row["uncertain_operations_json"])),
                side_effects=tuple(json.loads(row["side_effects_json"])),
                compensation_records=tuple(json.loads(row["compensation_records_json"])),
            )
            for row in rows
        )


__all__ = ["CancellationAuditEntry", "SQLiteCancellationStore"]
