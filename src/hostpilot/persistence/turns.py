"""SQLite persistence for Turn metadata and Runtime Host leases."""

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
class StoredTurn:
    """Non-sensitive persisted lifecycle metadata for a single Turn."""

    turn_id: str
    session_id: str
    runtime_host_id: str
    state: str
    started_at: datetime
    finished_at: datetime | None


class SQLiteTurnStore:
    """Persist Turn lifecycle and worker leases in a shared SQLite database."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Create lease and Turn tables when they do not already exist."""
        async with self._initialize_lock:
            if self._initialized:
                return
            prepare_sqlite_database_path(self.database_path)
            async with aiosqlite.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_hosts (
                        host_id TEXT PRIMARY KEY,
                        started_at TEXT NOT NULL,
                        last_heartbeat_at TEXT NOT NULL,
                        lease_expires_at TEXT NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('active', 'stopped', 'expired'))
                    )
                    """
                )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_turns (
                        turn_id TEXT PRIMARY KEY,
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
                    "CREATE INDEX IF NOT EXISTS runtime_turns_host_state "
                    "ON runtime_turns(runtime_host_id, state)"
                )
                await connection.commit()
            os.chmod(self.database_path, 0o600)
            self._initialized = True

    async def register_host(
        self, host_id: str, *, now: datetime, lease_expires_at: datetime
    ) -> None:
        """Expire stale owners and register this Host in one write transaction."""
        self._validate_id(host_id, "Host")
        now_value = _timestamp(now)
        lease_value = _timestamp(lease_expires_at)
        if lease_expires_at <= now:
            raise ValueError("Host lease expiration must be later than registration time.")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            await connection.execute(
                """
                UPDATE runtime_turns SET state = 'interrupted', finished_at = ?
                WHERE state = 'running' AND runtime_host_id IN (
                    SELECT host_id FROM runtime_hosts
                    WHERE status = 'active' AND lease_expires_at <= ? AND host_id != ?
                )
                """,
                (now_value, now_value, host_id),
            )
            await connection.execute(
                """
                UPDATE runtime_hosts SET status = 'expired'
                WHERE status = 'active' AND lease_expires_at <= ? AND host_id != ?
                """,
                (now_value, host_id),
            )
            await connection.execute(
                """
                INSERT INTO runtime_hosts (
                    host_id, started_at, last_heartbeat_at, lease_expires_at, status
                ) VALUES (?, ?, ?, ?, 'active')
                ON CONFLICT(host_id) DO UPDATE SET
                    started_at = excluded.started_at,
                    last_heartbeat_at = excluded.last_heartbeat_at,
                    lease_expires_at = excluded.lease_expires_at,
                    status = 'active'
                """,
                (host_id, now_value, now_value, lease_value),
            )
            await connection.commit()

    async def heartbeat_host(
        self, host_id: str, *, now: datetime, lease_expires_at: datetime
    ) -> bool:
        """Extend a still-valid Host lease; an expired lease cannot be revived."""
        await self.initialize()
        now_value = _timestamp(now)
        lease_value = _timestamp(lease_expires_at)
        if lease_expires_at <= now:
            raise ValueError("Host lease expiration must be later than heartbeat time.")
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                UPDATE runtime_hosts
                SET last_heartbeat_at = ?, lease_expires_at = ?
                WHERE host_id = ? AND status = 'active' AND lease_expires_at > ?
                """,
                (now_value, lease_value, host_id, now_value),
            )
            await connection.commit()
        return cursor.rowcount == 1

    async def release_host(self, host_id: str, *, now: datetime) -> None:
        """Mark a Host stopped so it is not treated as a crashed worker."""
        await self.initialize()
        now_value = _timestamp(now)
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """
                UPDATE runtime_hosts
                SET status = 'stopped', last_heartbeat_at = ?, lease_expires_at = ?
                WHERE host_id = ? AND status = 'active'
                """,
                (now_value, now_value, host_id),
            )
            await connection.commit()

    async def create_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        runtime_host_id: str,
        started_at: datetime,
    ) -> None:
        """Insert a running Turn only when its owning Host lease is active."""
        self._validate_id(turn_id, "Turn")
        self._validate_id(session_id, "Session")
        started_value = _timestamp(started_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                INSERT INTO runtime_turns (
                    turn_id, session_id, runtime_host_id, state, started_at
                )
                SELECT ?, ?, host_id, 'running', ? FROM runtime_hosts
                WHERE host_id = ? AND status = 'active' AND lease_expires_at > ?
                """,
                (turn_id, session_id, started_value, runtime_host_id, started_value),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise RuntimeError("Cannot start a Turn without an active Runtime Host lease.")

    async def finish_turn(
        self,
        turn_id: str,
        *,
        runtime_host_id: str,
        state: str,
        finished_at: datetime,
    ) -> None:
        """Record one terminal lifecycle state owned by the same Host."""
        if state not in _TERMINAL_STATES - {"interrupted"}:
            raise ValueError("Turn state must be a supported terminal state.")
        finished_value = _timestamp(finished_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                UPDATE runtime_turns SET state = ?, finished_at = ?
                WHERE turn_id = ? AND runtime_host_id = ? AND state = 'running'
                """,
                (state, finished_value, turn_id, runtime_host_id),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise ValueError("Running Turn was not found for this Runtime Host.")

    async def list_turns(self) -> tuple[StoredTurn, ...]:
        """Return lifecycle metadata in start order for diagnostics and tests."""
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                """
                SELECT turn_id, session_id, runtime_host_id, state, started_at, finished_at
                FROM runtime_turns ORDER BY started_at, turn_id
                """
            )
            rows = await cursor.fetchall()
        return tuple(_stored_turn(row) for row in rows)

    @staticmethod
    def _validate_id(value: str, label: str) -> None:
        if not value or value.strip() != value:
            raise ValueError(f"{label} ID must be non-empty and trimmed.")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Turn lifecycle timestamps must be timezone-aware.")
    return value.astimezone(UTC).isoformat()


def _stored_turn(row: sqlite3.Row) -> StoredTurn:
    return StoredTurn(
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
