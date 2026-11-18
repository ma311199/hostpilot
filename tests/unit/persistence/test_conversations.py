import asyncio
import sqlite3
import stat
from pathlib import Path

import pytest

from hostpilot.contracts import SandboxMode
from hostpilot.persistence import ConversationNotFoundError, SQLiteConversationStore


def test_conversation_index_persists_and_isolates_thread_ids(tmp_path: Path) -> None:
    database_path = tmp_path / "data" / "hostpilot.sqlite3"

    async def scenario() -> None:
        first_store = SQLiteConversationStore(database_path)
        first = await first_store.create("Research")
        second = await first_store.create("Planning")

        reopened_store = SQLiteConversationStore(database_path)
        records = await reopened_store.list()

        assert {record.conversation_id for record in records} == {
            first.conversation_id,
            second.conversation_id,
        }
        assert first_store.thread_config(first.conversation_id) == {
            "configurable": {"thread_id": first.conversation_id}
        }
        assert first.conversation_id != second.conversation_id
        assert first.sandbox_mode is SandboxMode.READ_ONLY
        upgraded = await reopened_store.upgrade_sandbox_mode(
            first.conversation_id, SandboxMode.WORKSPACE_WRITE
        )
        assert upgraded.sandbox_mode is SandboxMode.WORKSPACE_WRITE
        assert (
            await SQLiteConversationStore(database_path).get(first.conversation_id)
        ).sandbox_mode is SandboxMode.WORKSPACE_WRITE
        with pytest.raises(ValueError, match="only be upgraded"):
            await reopened_store.upgrade_sandbox_mode(first.conversation_id, SandboxMode.READ_ONLY)
        await reopened_store.touch(first.conversation_id, title="Research notes")
        assert (await reopened_store.get(first.conversation_id)).title == "Research notes"

    asyncio.run(scenario())
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(database_path.parent.stat().st_mode) == 0o700


def test_conversation_store_rejects_invalid_ids_and_reports_missing_records(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        store = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        with pytest.raises(ValueError, match="must be a UUID"):
            await store.get("../outside.md")
        with pytest.raises(ConversationNotFoundError):
            await store.touch("72de5425-9a94-4a18-a012-52fe98e7ed7d")

    asyncio.run(scenario())


def test_conversation_store_rejects_newer_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "hostpilot.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute("PRAGMA user_version = 3")

    async def scenario() -> None:
        store = SQLiteConversationStore(database_path)
        with pytest.raises(RuntimeError, match="newer than this app"):
            await store.initialize()

    asyncio.run(scenario())


def test_conversation_store_migrates_existing_rows_to_read_only(tmp_path: Path) -> None:
    database_path = tmp_path / "hostpilot.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE conversations (
                conversation_id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO conversations VALUES (?, ?, ?, ?)",
            (
                "72de5425-9a94-4a18-a012-52fe98e7ed7d",
                "Existing",
                "2026-01-01T00:00:00+00:00",
                "2026-01-01T00:00:00+00:00",
            ),
        )
        connection.execute("PRAGMA user_version = 1")

    async def scenario() -> None:
        store = SQLiteConversationStore(database_path)
        existing = await store.get("72de5425-9a94-4a18-a012-52fe98e7ed7d")
        assert existing is not None
        assert existing.sandbox_mode is SandboxMode.READ_ONLY

    asyncio.run(scenario())
