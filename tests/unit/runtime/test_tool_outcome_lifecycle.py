import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import SQLiteToolOutcomeStore, SQLiteTurnStore
from hostpilot.runtime import ToolOutcomeLifecycleManager


def test_runtime_manager_binds_host_and_tracks_dispatch_until_checkpoint(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        hosts = SQLiteTurnStore(database_path)
        await hosts.initialize()
        now = datetime.now(UTC)
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteToolOutcomeStore(database_path)
        await store.initialize()
        manager = ToolOutcomeLifecycleManager()
        manager.configure_persistence(store, "host-a")

        await manager.begin_invocation(
            session_id="session-1",
            turn_id="turn-1",
            task_run_id=None,
            tool_call_id="call-1",
            tool_name="write_text_file",
        )
        assert [item.tool_call_id for item in await manager.pending("session-1")] == ["call-1"]

        await manager.result_checkpointed("session-1", "call-1")
        assert await manager.pending("session-1") == ()

    asyncio.run(scenario())


def test_runtime_manager_requires_persistence_before_dispatch(tmp_path) -> None:
    async def scenario() -> None:
        manager = ToolOutcomeLifecycleManager()
        with pytest.raises(RuntimeError, match="not configured"):
            await manager.begin_invocation(
                session_id="session-1",
                turn_id="turn-1",
                task_run_id=None,
                tool_call_id="call-1",
                tool_name="write_text_file",
            )

    asyncio.run(scenario())
