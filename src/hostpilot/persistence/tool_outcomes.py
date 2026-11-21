"""SQLite persistence for unresolved side-effecting tool calls."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from hostpilot.persistence.ports import PendingToolOutcome
from hostpilot.persistence.sqlite_path import prepare_sqlite_database_path


class SQLiteToolOutcomeStore:
    """Keep a fail-closed record until a side-effecting tool result is durable."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Create the pending-call table after Runtime Host storage is ready."""
        async with self._initialize_lock:
            if self._initialized:
                return
            prepare_sqlite_database_path(self.database_path)
            async with aiosqlite.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_tool_outcomes (
                        session_id TEXT NOT NULL,
                        tool_call_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        task_run_id TEXT,
                        tool_name TEXT NOT NULL,
                        dispatched_host_id TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        resolving_host_id TEXT,
                        PRIMARY KEY (session_id, tool_call_id)
                    )
                    """
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_tool_outcomes_session "
                    "ON runtime_tool_outcomes(session_id, created_at)"
                )
                await connection.commit()
            os.chmod(self.database_path, 0o600)
            self._initialized = True

    async def record_pending(
        self,
        *,
        session_id: str,
        turn_id: str,
        task_run_id: str | None,
        tool_call_id: str,
        tool_name: str,
        runtime_host_id: str,
        now: datetime,
    ) -> None:
        """Durably register intent before a possibly side-effecting tool dispatch."""
        for value, label in (
            (session_id, "Session"),
            (turn_id, "Turn"),
            (task_run_id, "TaskRun"),
            (tool_call_id, "Tool call"),
            (tool_name, "Tool"),
            (runtime_host_id, "Runtime Host"),
        ):
            if value is not None:
                _validate_id(value, label)
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            await connection.execute("BEGIN IMMEDIATE")
            host_cursor = await connection.execute(
                "SELECT 1 FROM runtime_hosts WHERE host_id = ? AND status = 'active' "
                "AND lease_expires_at > ?",
                (runtime_host_id, now_value),
            )
            if await host_cursor.fetchone() is None:
                await connection.rollback()
                raise RuntimeError("Runtime Host lease is not active.")
            try:
                await connection.execute(
                    """
                    INSERT INTO runtime_tool_outcomes (
                        session_id, tool_call_id, turn_id, task_run_id, tool_name,
                        dispatched_host_id, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id,
                        tool_call_id,
                        turn_id,
                        task_run_id,
                        tool_name,
                        runtime_host_id,
                        now_value,
                    ),
                )
            except sqlite3.IntegrityError as error:
                await connection.rollback()
                raise ValueError("Tool call already has a pending outcome.") from error
            await connection.commit()

    async def list_pending(self, session_id: str) -> tuple[PendingToolOutcome, ...]:
        """List unresolved calls without arguments, credentials, or result bodies."""
        _validate_id(session_id, "Session")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                """
                SELECT session_id, turn_id, task_run_id, tool_call_id, tool_name,
                    dispatched_host_id, created_at, resolving_host_id
                FROM runtime_tool_outcomes WHERE session_id = ?
                ORDER BY created_at, tool_call_id
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()
        return tuple(
            PendingToolOutcome(
                session_id=str(row["session_id"]),
                turn_id=str(row["turn_id"]),
                task_run_id=(str(row["task_run_id"]) if row["task_run_id"] is not None else None),
                tool_call_id=str(row["tool_call_id"]),
                tool_name=str(row["tool_name"]),
                dispatched_host_id=str(row["dispatched_host_id"]),
                created_at=_parse_timestamp(str(row["created_at"])),
                resolving_host_id=(
                    str(row["resolving_host_id"]) if row["resolving_host_id"] is not None else None
                ),
            )
            for row in rows
        )

    async def claim_resolution(
        self, session_id: str, tool_call_id: str, *, runtime_host_id: str, now: datetime
    ) -> bool:
        """Claim one pending call unless another live Runtime Host owns resolution."""
        _validate_id(session_id, "Session")
        _validate_id(tool_call_id, "Tool call")
        _validate_id(runtime_host_id, "Runtime Host")
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            await connection.execute("BEGIN IMMEDIATE")
            host_cursor = await connection.execute(
                "SELECT 1 FROM runtime_hosts WHERE host_id = ? AND status = 'active' "
                "AND lease_expires_at > ?",
                (runtime_host_id, now_value),
            )
            if await host_cursor.fetchone() is None:
                await connection.rollback()
                raise RuntimeError("Runtime Host lease is not active.")
            row_cursor = await connection.execute(
                "SELECT resolving_host_id FROM runtime_tool_outcomes "
                "WHERE session_id = ? AND tool_call_id = ?",
                (session_id, tool_call_id),
            )
            row = await row_cursor.fetchone()
            if row is None:
                await connection.commit()
                return False
            owner = row["resolving_host_id"]
            if owner is not None and owner != runtime_host_id:
                owner_cursor = await connection.execute(
                    "SELECT 1 FROM runtime_hosts WHERE host_id = ? AND status = 'active' "
                    "AND lease_expires_at > ?",
                    (owner, now_value),
                )
                if await owner_cursor.fetchone() is not None:
                    await connection.commit()
                    return False
            if owner == runtime_host_id:
                await connection.commit()
                return True
            await connection.execute(
                "UPDATE runtime_tool_outcomes SET resolving_host_id = ? "
                "WHERE session_id = ? AND tool_call_id = ?",
                (runtime_host_id, session_id, tool_call_id),
            )
            await connection.commit()
        return True

    async def release_resolution_claim(
        self, session_id: str, tool_call_id: str, *, runtime_host_id: str
    ) -> None:
        """Release a failed resolution attempt but keep the outcome pending."""
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                "UPDATE runtime_tool_outcomes SET resolving_host_id = NULL "
                "WHERE session_id = ? AND tool_call_id = ? AND resolving_host_id = ?",
                (session_id, tool_call_id, runtime_host_id),
            )
            await connection.commit()

    async def clear_pending(
        self, session_id: str, tool_call_id: str, *, runtime_host_id: str
    ) -> None:
        """Clear only after a durable result, by the dispatcher or resolution owner."""
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                DELETE FROM runtime_tool_outcomes
                WHERE session_id = ? AND tool_call_id = ?
                    AND (dispatched_host_id = ? OR resolving_host_id = ?)
                """,
                (session_id, tool_call_id, runtime_host_id, runtime_host_id),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise ValueError("Pending tool outcome was not found for this Runtime Host.")


def _validate_id(value: str, label: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{label} ID must be non-empty and trimmed.")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Tool outcome timestamps must be timezone-aware.")
    return value.astimezone(UTC).isoformat()


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Stored tool outcome timestamps must be timezone-aware.")
    return parsed.astimezone(UTC)


__all__ = ["SQLiteToolOutcomeStore"]
