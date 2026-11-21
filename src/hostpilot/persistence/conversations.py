"""SQLite-backed conversation index and LangGraph thread mapping."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite

from hostpilot.contracts import ConversationSummary, SandboxMode
from hostpilot.persistence.checkpointer import session_thread_config
from hostpilot.persistence.sqlite_path import prepare_sqlite_database_path


class ConversationNotFoundError(KeyError):
    """Raised when an operation references an unknown conversation."""


class SQLiteConversationStore:
    """Persist conversation metadata in a local SQLite database.

    Agent messages and execution state are stored by LangGraph's checkpointer
    in the same database, keyed by the conversation ID as the graph thread ID.
    """

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Create the local database and metadata table if needed."""
        async with self._initialize_lock:
            if self._initialized:
                return
            prepare_sqlite_database_path(self.database_path)

            async with aiosqlite.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                cursor = await connection.execute("PRAGMA user_version")
                version_row = await cursor.fetchone()
                if version_row is None:
                    raise RuntimeError("Could not read conversation database schema version.")
                schema_version = int(version_row[0])
                if schema_version > 3:
                    raise RuntimeError("Conversation database schema is newer than this app.")
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversations (
                        session_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        sandbox_mode TEXT NOT NULL DEFAULT 'read-only'
                    )
                    """
                )
                cursor = await connection.execute("PRAGMA table_info(conversations)")
                columns = {str(row[1]) for row in await cursor.fetchall()}
                if "session_id" not in columns and "conversation_id" in columns:
                    await connection.execute(
                        "ALTER TABLE conversations RENAME COLUMN conversation_id TO session_id"
                    )
                    columns.remove("conversation_id")
                    columns.add("session_id")
                if "session_id" not in columns:
                    raise RuntimeError("Conversation database is missing its session_id column.")
                if "sandbox_mode" not in columns:
                    await connection.execute(
                        "ALTER TABLE conversations ADD COLUMN sandbox_mode TEXT "
                        "NOT NULL DEFAULT 'read-only'"
                    )
                if schema_version < 3:
                    await connection.execute("PRAGMA user_version = 3")
                await connection.commit()
            os.chmod(self.database_path, 0o600)
            self._initialized = True

    async def create(self, title: str | None = None) -> ConversationSummary:
        """Create an empty conversation with a stable thread identifier."""
        await self.initialize()
        normalized_title = title.strip() if title is not None else "New conversation"
        if not normalized_title:
            normalized_title = "New conversation"
        if len(normalized_title) > 200:
            raise ValueError("Conversation title cannot exceed 200 characters.")

        now = datetime.now(UTC)
        record = ConversationSummary(
            session_id=str(uuid4()),
            title=normalized_title,
            created_at=now,
            updated_at=now,
        )
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """
                INSERT INTO conversations (session_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.title,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            await connection.commit()
        return record

    async def get(self, session_id: str) -> ConversationSummary | None:
        """Return one conversation summary, if it exists."""
        await self.initialize()
        normalized_id = _normalize_session_id(session_id)
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                "SELECT * FROM conversations WHERE session_id = ?",
                (normalized_id,),
            )
            row = await cursor.fetchone()
        return _record_from_row(row) if row is not None else None

    async def list(self) -> tuple[ConversationSummary, ...]:
        """List conversations by most recently updated."""
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC, session_id"
            )
            rows = await cursor.fetchall()
        return tuple(_record_from_row(row) for row in rows)

    async def touch(self, session_id: str, *, title: str | None = None) -> None:
        """Update activity time and optionally rename a conversation."""
        await self.initialize()
        normalized_id = _normalize_session_id(session_id)
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.database_path) as connection:
            if title is None:
                cursor = await connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE session_id = ?",
                    (now, normalized_id),
                )
            else:
                normalized_title = title.strip()
                if not normalized_title or len(normalized_title) > 200:
                    raise ValueError("Conversation title must contain 1 to 200 characters.")
                cursor = await connection.execute(
                    """
                    UPDATE conversations SET title = ?, updated_at = ?
                    WHERE session_id = ?
                    """,
                    (normalized_title, now, normalized_id),
                )
            await connection.commit()
        if cursor.rowcount == 0:
            raise ConversationNotFoundError(normalized_id)

    async def delete(self, session_id: str) -> bool:
        """Delete a conversation index row; its checkpointer is deleted separately."""
        await self.initialize()
        normalized_id = _normalize_session_id(session_id)
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                "DELETE FROM conversations WHERE session_id = ?",
                (normalized_id,),
            )
            await connection.commit()
        return cursor.rowcount > 0

    async def upgrade_sandbox_mode(
        self, session_id: str, target: SandboxMode
    ) -> ConversationSummary:
        """Atomically apply one strict privilege upgrade to a conversation."""
        await self.initialize()
        normalized_id = _normalize_session_id(session_id)
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT * FROM conversations WHERE session_id = ?", (normalized_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                await connection.rollback()
                raise ConversationNotFoundError(normalized_id)
            current = SandboxMode(row["sandbox_mode"])
            if target.rank <= current.rank:
                await connection.rollback()
                raise ValueError("Sandbox mode can only be upgraded to a higher level.")
            now = datetime.now(UTC).isoformat()
            await connection.execute(
                "UPDATE conversations SET sandbox_mode = ?, updated_at = ? WHERE session_id = ?",
                (target.value, now, normalized_id),
            )
            await connection.commit()
            updated = await connection.execute(
                "SELECT * FROM conversations WHERE session_id = ?", (normalized_id,)
            )
            updated_row = await updated.fetchone()
        if updated_row is None:
            raise RuntimeError("Conversation disappeared after sandbox upgrade.")
        return _record_from_row(updated_row)

    @staticmethod
    def thread_config(session_id: str) -> dict[str, dict[str, str]]:
        """Map a HostPilot conversation ID to LangGraph's persistent thread ID."""
        return session_thread_config(session_id)


def _normalize_session_id(session_id: str) -> str:
    try:
        return str(UUID(session_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("session_id must be a UUID.") from error


def _record_from_row(row: sqlite3.Row) -> ConversationSummary:
    return ConversationSummary(
        session_id=row["session_id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        sandbox_mode=SandboxMode(row["sandbox_mode"]),
    )


__all__ = ["ConversationNotFoundError", "SQLiteConversationStore"]
