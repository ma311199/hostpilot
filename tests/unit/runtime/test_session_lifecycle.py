import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import (
    SQLiteConversationStore,
    SQLiteSessionLifecycleStore,
    SQLiteTurnStore,
)
from hostpilot.runtime import SessionLifecycleManager


async def configure_manager(database_path, host_id: str) -> SessionLifecycleManager:
    now = datetime.now(UTC)
    hosts = SQLiteTurnStore(database_path)
    sessions = SQLiteSessionLifecycleStore(database_path)
    await hosts.initialize()
    await sessions.initialize()
    await hosts.register_host(host_id, now=now, lease_expires_at=now + timedelta(minutes=1))
    await sessions.register_session("session-1")
    manager = SessionLifecycleManager()
    manager.configure_persistence(sessions, host_id)
    return manager


def test_session_close_gates_new_leases_and_waits_for_existing_lease(tmp_path) -> None:
    async def scenario() -> None:
        manager = await configure_manager(tmp_path / "runtime.sqlite3", "host-a")
        lease_started = asyncio.Event()
        release_lease = asyncio.Event()
        cleanup_started = asyncio.Event()

        async def active_operation() -> None:
            async with manager.lease("session-1"):
                lease_started.set()
                await release_lease.wait()

        async def cleanup() -> None:
            cleanup_started.set()

        operation = asyncio.create_task(active_operation())
        await lease_started.wait()
        closing = asyncio.create_task(manager.close_session("session-1", cleanup))
        await asyncio.sleep(0.02)

        assert not cleanup_started.is_set()
        with pytest.raises(RuntimeError, match="closing"):
            async with manager.lease("session-1"):
                pytest.fail("Closing Session must reject new leases")

        release_lease.set()
        await operation
        await closing
        assert cleanup_started.is_set()

    asyncio.run(scenario())


def test_session_close_failure_keeps_session_closed_and_allows_retry(tmp_path) -> None:
    async def scenario() -> None:
        manager = await configure_manager(tmp_path / "runtime.sqlite3", "host-a")

        async def fail_cleanup() -> None:
            raise OSError("checkpoint deletion failed")

        with pytest.raises(OSError, match="checkpoint deletion failed"):
            await manager.close_session("session-1", fail_cleanup)

        with pytest.raises(RuntimeError, match="closing"):
            async with manager.lease("session-1"):
                pytest.fail("Failed deletion must not reopen the Session")

        cleaned: list[str] = []

        async def retry_cleanup() -> None:
            cleaned.append("done")

        await manager.close_session("session-1", retry_cleanup)
        assert cleaned == ["done"]

    asyncio.run(scenario())


def test_session_cleanup_retry_does_not_require_conversation_row(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        manager = await configure_manager(database_path, "host-a")
        conversations = SQLiteConversationStore(database_path)
        await conversations.initialize()
        conversation = await conversations.create("Retry cleanup")
        await manager.register_session(conversation.session_id)

        async def partial_cleanup() -> None:
            await conversations.delete(conversation.session_id)
            raise OSError("memory cleanup failed after metadata deletion")

        with pytest.raises(OSError, match="memory cleanup failed"):
            await manager.close_session(conversation.session_id, partial_cleanup)
        assert await conversations.get(conversation.session_id) is None

        async def retry_cleanup() -> None:
            assert await conversations.get(conversation.session_id) is None

        assert await manager.close_session(conversation.session_id, retry_cleanup)

    asyncio.run(scenario())


def test_other_runtime_host_close_waits_for_remote_session_lease(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        owner = await configure_manager(database_path, "host-owner")
        deleter = await configure_manager(database_path, "host-deleter")
        operation_started = asyncio.Event()
        release_operation = asyncio.Event()
        cleanup_finished = asyncio.Event()

        async def remote_operation() -> None:
            async with owner.lease("session-1"):
                operation_started.set()
                await release_operation.wait()

        async def cleanup() -> None:
            cleanup_finished.set()

        operation = asyncio.create_task(remote_operation())
        await operation_started.wait()
        deletion = asyncio.create_task(deleter.close_session("session-1", cleanup))
        await asyncio.sleep(0.05)

        assert not cleanup_finished.is_set()
        with pytest.raises(RuntimeError, match="closing"):
            async with owner.lease("session-1"):
                pytest.fail("Other workers must observe the durable closing state")

        release_operation.set()
        await operation
        await deletion
        assert cleanup_finished.is_set()

    asyncio.run(scenario())


def test_concurrent_session_deletes_run_cleanup_once_and_both_complete(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        first = await configure_manager(database_path, "host-first")
        second = await configure_manager(database_path, "host-second")
        cleanup_started = asyncio.Event()
        finish_cleanup = asyncio.Event()
        cleanup_calls = 0

        async def first_cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            cleanup_started.set()
            await finish_cleanup.wait()

        async def second_cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1

        first_delete = asyncio.create_task(first.close_session("session-1", first_cleanup))
        await cleanup_started.wait()
        second_delete = asyncio.create_task(second.close_session("session-1", second_cleanup))
        await asyncio.sleep(0.05)
        assert cleanup_calls == 1

        finish_cleanup.set()
        assert await first_delete
        assert await second_delete
        assert cleanup_calls == 1

    asyncio.run(scenario())
