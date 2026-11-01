"""SQLite-backed conversation index and LangGraph thread mapping."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import aiosqlite

from hostpilot.contracts import ConversationSummary


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
            if self.database_path.is_symlink():
                raise ValueError("Conversation database cannot be a symbolic link.")
            self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.database_path.parent.is_symlink():
                raise ValueError("Conversation data directory cannot be a symbolic link.")
            os.chmod(self.database_path.parent, 0o700)

            async with aiosqlite.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                cursor = await connection.execute("PRAGMA user_version")
                version_row = await cursor.fetchone()
                if version_row is None:
                    raise RuntimeError("Could not read conversation database schema version.")
                schema_version = int(version_row[0])
                if schema_version > 1:
                    raise RuntimeError("Conversation database schema is newer than this app.")
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS conversations (
                        conversation_id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                if schema_version == 0:
                    await connection.execute("PRAGMA user_version = 1")
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
            conversation_id=str(uuid4()),
            title=normalized_title,
            created_at=now,
            updated_at=now,
        )
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """
                INSERT INTO conversations (conversation_id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (
                    record.conversation_id,
                    record.title,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
            await connection.commit()
        return record

    async def get(self, conversation_id: str) -> ConversationSummary | None:
        """Return one conversation summary, if it exists."""
        await self.initialize()
        normalized_id = _normalize_conversation_id(conversation_id)
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                "SELECT * FROM conversations WHERE conversation_id = ?",
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
                "SELECT * FROM conversations ORDER BY updated_at DESC, conversation_id"
            )
            rows = await cursor.fetchall()
        return tuple(_record_from_row(row) for row in rows)

    async def touch(self, conversation_id: str, *, title: str | None = None) -> None:
        """Update activity time and optionally rename a conversation."""
        await self.initialize()
        normalized_id = _normalize_conversation_id(conversation_id)
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.database_path) as connection:
            if title is None:
                cursor = await connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
                    (now, normalized_id),
                )
            else:
                normalized_title = title.strip()
                if not normalized_title or len(normalized_title) > 200:
                    raise ValueError("Conversation title must contain 1 to 200 characters.")
                cursor = await connection.execute(
                    """
                    UPDATE conversations SET title = ?, updated_at = ?
                    WHERE conversation_id = ?
                    """,
                    (normalized_title, now, normalized_id),
                )
            await connection.commit()
        if cursor.rowcount == 0:
            raise ConversationNotFoundError(normalized_id)

    async def delete(self, conversation_id: str) -> bool:
        """Delete a conversation index row; its checkpointer is deleted separately."""
        await self.initialize()
        normalized_id = _normalize_conversation_id(conversation_id)
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                "DELETE FROM conversations WHERE conversation_id = ?",
                (normalized_id,),
            )
            await connection.commit()
        return cursor.rowcount > 0

    @staticmethod
    def thread_config(conversation_id: str) -> dict[str, dict[str, str]]:
        """Map a HostPilot conversation ID to LangGraph's persistent thread ID."""
        return {"configurable": {"thread_id": _normalize_conversation_id(conversation_id)}}


def _normalize_conversation_id(conversation_id: str) -> str:
    try:
        return str(UUID(conversation_id))
    except (ValueError, AttributeError) as error:
        raise ValueError("conversation_id must be a UUID.") from error


def _record_from_row(row: sqlite3.Row) -> ConversationSummary:
    return ConversationSummary(
        conversation_id=row["conversation_id"],
        title=row["title"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


__all__ = ["ConversationNotFoundError", "SQLiteConversationStore"]
