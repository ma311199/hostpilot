"""SQLite persistence for Session lifecycle state and Runtime operation leases."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from hostpilot.persistence.sqlite_path import prepare_sqlite_database_path


class SessionUnavailableError(RuntimeError):
    """Raised when a Session is missing, closing, or has no active Host owner."""


class SessionLeasesActiveError(RuntimeError):
    """Raised when deletion is attempted before live Session operations drain."""


class SQLiteSessionLifecycleStore:
    """Persist Session admission state and leases in a shared SQLite database."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Create Session lifecycle tables after Runtime Host storage is ready."""
        async with self._initialize_lock:
            if self._initialized:
                return
            prepare_sqlite_database_path(self.database_path)
            async with aiosqlite.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_sessions (
                        session_id TEXT PRIMARY KEY,
                        state TEXT NOT NULL CHECK (state IN ('active', 'closing', 'deleted')),
                        closing_host_id TEXT
                    )
                    """
                )
                columns_cursor = await connection.execute("PRAGMA table_info(runtime_sessions)")
                columns = {str(row[1]) for row in await columns_cursor.fetchall()}
                if "closing_host_id" not in columns:
                    await connection.execute(
                        "ALTER TABLE runtime_sessions ADD COLUMN closing_host_id TEXT"
                    )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_session_leases (
                        lease_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        runtime_host_id TEXT NOT NULL,
                        acquired_at TEXT NOT NULL,
                        FOREIGN KEY (session_id) REFERENCES runtime_sessions(session_id),
                        FOREIGN KEY (runtime_host_id) REFERENCES runtime_hosts(host_id)
                    )
                    """
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_session_leases_session "
                    "ON runtime_session_leases(session_id)"
                )
                await connection.commit()
            os.chmod(self.database_path, 0o600)
            self._initialized = True

    async def register_session(self, session_id: str) -> None:
        """Register a newly created or legacy Session without reopening closing state."""
        self._validate_id(session_id, "Session")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                "INSERT OR IGNORE INTO runtime_sessions (session_id, state) VALUES (?, 'active')",
                (session_id,),
            )
            await connection.commit()

    async def acquire_lease(
        self, session_id: str, lease_id: str, runtime_host_id: str, now: datetime
    ) -> None:
        """Atomically grant a lease only while the Session and Host are active."""
        self._validate_id(session_id, "Session")
        self._validate_id(lease_id, "Session lease")
        self._validate_id(runtime_host_id, "Runtime Host")
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                """
                INSERT INTO runtime_session_leases (
                    lease_id, session_id, runtime_host_id, acquired_at
                )
                SELECT ?, sessions.session_id, hosts.host_id, ?
                FROM runtime_sessions AS sessions
                JOIN runtime_hosts AS hosts ON hosts.host_id = ?
                WHERE sessions.session_id = ? AND sessions.state = 'active'
                    AND hosts.status = 'active' AND hosts.lease_expires_at > ?
                """,
                (lease_id, now_value, runtime_host_id, session_id, now_value),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise SessionUnavailableError("Session is closing or unavailable.")

    async def release_lease(self, lease_id: str, runtime_host_id: str) -> None:
        """Release a lease only for its owning Runtime Host."""
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                "DELETE FROM runtime_session_leases WHERE lease_id = ? AND runtime_host_id = ?",
                (lease_id, runtime_host_id),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise ValueError("Session lease was not found for this Runtime Host.")

    async def begin_close(self, session_id: str, runtime_host_id: str) -> bool | None:
        """Close admission and claim cleanup, returning None when already deleted."""
        self._validate_id(session_id, "Session")
        self._validate_id(runtime_host_id, "Runtime Host")
        now_value = _timestamp(datetime.now(UTC))
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
                raise SessionUnavailableError("Runtime Host lease is not active.")
            cursor = await connection.execute(
                "SELECT state, closing_host_id FROM runtime_sessions WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
            if row is None or row["state"] == "deleted":
                await connection.commit()
                return None
            current_owner = row["closing_host_id"]
            if row["state"] == "closing" and current_owner not in {None, runtime_host_id}:
                owner_cursor = await connection.execute(
                    "SELECT 1 FROM runtime_hosts WHERE host_id = ? AND status = 'active' "
                    "AND lease_expires_at > ?",
                    (current_owner, now_value),
                )
                if await owner_cursor.fetchone() is not None:
                    await connection.commit()
                    return False
            await connection.execute(
                "UPDATE runtime_sessions SET state = 'closing', closing_host_id = ? "
                "WHERE session_id = ?",
                (runtime_host_id, session_id),
            )
            await connection.commit()
        return True

    async def release_close_claim(self, session_id: str, runtime_host_id: str) -> None:
        """Keep admission closed while allowing another Host to retry failed cleanup."""
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                "UPDATE runtime_sessions SET closing_host_id = NULL "
                "WHERE session_id = ? AND state = 'closing' AND closing_host_id = ?",
                (session_id, runtime_host_id),
            )
            await connection.commit()

    async def active_lease_count(self, session_id: str, now: datetime) -> int:
        """Count leases whose Runtime Host still owns a live durable lease."""
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                SELECT COUNT(*) FROM runtime_session_leases AS leases
                JOIN runtime_hosts AS hosts ON hosts.host_id = leases.runtime_host_id
                WHERE leases.session_id = ? AND hosts.status = 'active'
                    AND hosts.lease_expires_at > ?
                """,
                (session_id, now_value),
            )
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else 0

    async def finish_delete(self, session_id: str) -> None:
        """Delete closing Session state after live leases drain; safe to repeat."""
        self._validate_id(session_id, "Session")
        now_value = _timestamp(datetime.now(UTC))
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                """
                SELECT COUNT(*) FROM runtime_session_leases AS leases
                JOIN runtime_hosts AS hosts ON hosts.host_id = leases.runtime_host_id
                WHERE leases.session_id = ? AND hosts.status = 'active'
                    AND hosts.lease_expires_at > ?
                """,
                (session_id, now_value),
            )
            row = await cursor.fetchone()
            if row is not None and int(row[0]) > 0:
                await connection.rollback()
                raise SessionLeasesActiveError("Session still has active operation leases.")
            await connection.execute(
                "DELETE FROM runtime_session_leases WHERE session_id = ?", (session_id,)
            )
            await connection.execute(
                "UPDATE runtime_sessions SET state = 'deleted', closing_host_id = NULL "
                "WHERE session_id = ? AND state = 'closing'",
                (session_id,),
            )
            await connection.commit()

    @staticmethod
    def _validate_id(value: str, label: str) -> None:
        if not value or value.strip() != value:
            raise ValueError(f"{label} ID must be non-empty and trimmed.")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Session lifecycle timestamps must be timezone-aware.")
    return value.astimezone(UTC).isoformat()
