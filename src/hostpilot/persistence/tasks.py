"""SQLite persistence for payload-free Runtime TaskRun lifecycle metadata."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from hostpilot.persistence.sqlite_path import prepare_sqlite_database_path

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})


@dataclass(frozen=True)
class StoredTaskRun:
    """Persisted metadata for one Plan task invocation."""

    task_run_id: str
    task_id: str
    turn_id: str
    session_id: str
    runtime_host_id: str
    state: str
    started_at: datetime
    finished_at: datetime | None


class SQLiteTaskStore:
    """Persist TaskRun metadata beside Runtime Host and Turn state."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Create the payload-free TaskRun table."""
        async with self._initialize_lock:
            if self._initialized:
                return
            prepare_sqlite_database_path(self.database_path)
            async with aiosqlite.connect(self.database_path) as connection:
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_task_runs (
                        task_run_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        runtime_host_id TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (
                            state IN ('running', 'completed', 'failed', 'cancelled', 'interrupted')
                        ),
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        FOREIGN KEY (runtime_host_id) REFERENCES runtime_hosts(host_id)
                    )
                    """
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_task_runs_host_state "
                    "ON runtime_task_runs(runtime_host_id, state)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_task_runs_turn "
                    "ON runtime_task_runs(turn_id, started_at)"
                )
                await connection.commit()
            os.chmod(self.database_path, 0o600)
            self._initialized = True

    async def create_task_run(
        self,
        *,
        task_run_id: str,
        task_id: str,
        turn_id: str,
        session_id: str,
        runtime_host_id: str,
        started_at: datetime,
    ) -> None:
        """Create a running record only under a live Host lease."""
        for value, label in (
            (task_run_id, "TaskRun"),
            (task_id, "Task"),
            (turn_id, "Turn"),
            (session_id, "Session"),
            (runtime_host_id, "Runtime Host"),
        ):
            _validate_id(value, label)
        started_value = _timestamp(started_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                INSERT INTO runtime_task_runs (
                    task_run_id, task_id, turn_id, session_id, runtime_host_id,
                    state, started_at
                )
                SELECT ?, ?, ?, ?, host_id, 'running', ? FROM runtime_hosts
                WHERE host_id = ? AND status = 'active' AND lease_expires_at > ?
                """,
                (
                    task_run_id,
                    task_id,
                    turn_id,
                    session_id,
                    started_value,
                    runtime_host_id,
                    started_value,
                ),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise RuntimeError("Cannot start a TaskRun without an active Runtime Host lease.")

    async def finish_task_run(
        self,
        task_run_id: str,
        *,
        runtime_host_id: str,
        state: str,
        finished_at: datetime,
    ) -> None:
        """Set one terminal TaskRun state, scoped to its owning Host."""
        if state not in _TERMINAL_STATES - {"interrupted"}:
            raise ValueError("TaskRun state must be a supported terminal state.")
        finished_value = _timestamp(finished_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                UPDATE runtime_task_runs SET state = ?, finished_at = ?
                WHERE task_run_id = ? AND runtime_host_id = ? AND state = 'running'
                """,
                (state, finished_value, task_run_id, runtime_host_id),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise ValueError("Running TaskRun was not found for this Runtime Host.")

    async def interrupt_inactive_host_tasks(self, *, now: datetime) -> int:
        """Interrupt running tasks owned by expired or cleanly stopped Hosts."""
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                UPDATE runtime_task_runs SET state = 'interrupted', finished_at = ?
                WHERE state = 'running' AND runtime_host_id IN (
                    SELECT host_id FROM runtime_hosts WHERE status IN ('expired', 'stopped')
                )
                """,
                (now_value,),
            )
            await connection.commit()
        return cursor.rowcount

    async def list_task_runs(self, *, turn_id: str | None = None) -> tuple[StoredTaskRun, ...]:
        """Return TaskRun metadata, optionally scoped to a Turn."""
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            if turn_id is None:
                cursor = await connection.execute(
                    """
                    SELECT task_run_id, task_id, turn_id, session_id, runtime_host_id,
                           state, started_at, finished_at
                    FROM runtime_task_runs ORDER BY started_at, task_run_id
                    """
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT task_run_id, task_id, turn_id, session_id, runtime_host_id,
                           state, started_at, finished_at
                    FROM runtime_task_runs WHERE turn_id = ?
                    ORDER BY started_at, task_run_id
                    """,
                    (turn_id,),
                )
            rows = await cursor.fetchall()
        return tuple(_stored_task_run(row) for row in rows)


def _validate_id(value: str, label: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{label} ID must be non-empty and trimmed.")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Task lifecycle timestamps must be timezone-aware.")
    return value.astimezone(UTC).isoformat()


def _stored_task_run(row: sqlite3.Row) -> StoredTaskRun:
    return StoredTaskRun(
        task_run_id=str(row["task_run_id"]),
        task_id=str(row["task_id"]),
        turn_id=str(row["turn_id"]),
        session_id=str(row["session_id"]),
        runtime_host_id=str(row["runtime_host_id"]),
        state=str(row["state"]),
        started_at=datetime.fromisoformat(str(row["started_at"])),
        finished_at=(
            datetime.fromisoformat(str(row["finished_at"]))
            if row["finished_at"] is not None
            else None
        ),
    )
