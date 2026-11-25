import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import SQLiteTurnStore
from hostpilot.runtime import RuntimeContext, RuntimeHost, TurnLifecycleManager, TurnState


def test_turn_manager_records_completed_and_failed_turns() -> None:
    async def scenario() -> TurnLifecycleManager:
        manager = TurnLifecycleManager()
        async with manager.track("turn-ok", "session-1"):
            assert manager.active_turns[0].state is TurnState.RUNNING

        with pytest.raises(ValueError, match="expected"):
            async with manager.track("turn-failed", "session-1"):
                raise ValueError("expected")
        return manager

    manager = asyncio.run(scenario())

    assert [turn.state for turn in manager.history] == [TurnState.COMPLETED, TurnState.FAILED]
    assert all(turn.finished_at is not None for turn in manager.history)
    assert manager.active_turns == ()


def test_turn_manager_cancels_turns_by_session() -> None:
    async def scenario() -> TurnLifecycleManager:
        manager = TurnLifecycleManager()
        started = asyncio.Event()

        async def run_turn(turn_id: str, session_id: str) -> None:
            async with manager.track(turn_id, session_id):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_turn("turn-1", "session-1"))
        await started.wait()
        assert manager.cancel_session("session-1") == 1
        with pytest.raises(asyncio.CancelledError):
            await task
        return manager

    manager = asyncio.run(scenario())
    assert manager.history[0].state is TurnState.CANCELLED


def test_host_shutdown_cancels_active_turns_before_stopping_plugins() -> None:
    async def scenario() -> tuple[RuntimeContext, asyncio.Task[None]]:
        context = RuntimeContext()
        host = RuntimeHost(context)
        await host.start()
        started = asyncio.Event()

        async def run_turn() -> None:
            async with context.turns.track("turn-1", "session-1"):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_turn())
        await started.wait()
        await host.close()
        return context, task

    context, task = asyncio.run(scenario())

    assert task.cancelled()
    assert context.turns.active_turns == ()
    assert context.turns.history[0].state is TurnState.CANCELLED


def test_turn_manager_persists_lifecycle_transitions(tmp_path) -> None:
    async def scenario():
        store = SQLiteTurnStore(tmp_path / "runtime.sqlite3")
        await store.initialize()
        await store.register_host(
            "host-1",
            now=datetime.now(UTC),
            lease_expires_at=datetime.now(UTC) + timedelta(seconds=30),
        )
        manager = TurnLifecycleManager(store=store, runtime_host_id="host-1")
        async with manager.track("turn-ok", "session-1"):
            assert (await store.list_turns())[0].state == "running"
        with pytest.raises(ValueError, match="expected"):
            async with manager.track("turn-failed", "session-1"):
                raise ValueError("expected")

        started = asyncio.Event()

        async def run_cancelled_turn() -> None:
            async with manager.track("turn-cancelled", "session-1"):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_cancelled_turn())
        await started.wait()
        assert manager.cancel_turn("turn-cancelled")
        with pytest.raises(asyncio.CancelledError):
            await task
        return manager, await store.list_turns()

    manager, records = asyncio.run(scenario())

    assert [record.state for record in records] == ["completed", "failed", "cancelled"]
    assert all(record.finished_at is not None for record in records)
    assert [turn.state for turn in manager.history] == [
        TurnState.COMPLETED,
        TurnState.FAILED,
        TurnState.CANCELLED,
    ]


def test_turn_manager_cleans_active_index_when_terminal_persistence_fails(tmp_path) -> None:
    async def scenario() -> TurnLifecycleManager:
        store = SQLiteTurnStore(tmp_path / "runtime.sqlite3")
        await store.initialize()
        now = datetime.now(UTC)
        await store.register_host(
            "host-1",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )

        class FailingTerminalStore:
            async def create_turn(self, **kwargs) -> None:
                await store.create_turn(**kwargs)

            async def finish_turn(self, *args, **kwargs) -> None:
                del args, kwargs
                raise OSError("terminal write failed")

        manager = TurnLifecycleManager(store=FailingTerminalStore(), runtime_host_id="host-1")
        with pytest.raises(OSError, match="terminal write failed"):
            async with manager.track("turn-1", "session-1"):
                pass
        return manager

    manager = asyncio.run(scenario())

    assert manager.active_turns == ()
    assert manager.history[0].state is TurnState.COMPLETED


def test_cancel_all_reports_terminal_persistence_failure(tmp_path) -> None:
    async def scenario() -> None:
        store = SQLiteTurnStore(tmp_path / "runtime.sqlite3")
        await store.initialize()
        now = datetime.now(UTC)
        await store.register_host(
            "host-1",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )

        class FailingTerminalStore:
            async def create_turn(self, **kwargs) -> None:
                await store.create_turn(**kwargs)

            async def finish_turn(self, *args, **kwargs) -> None:
                del args, kwargs
                raise OSError("terminal write failed")

        manager = TurnLifecycleManager(store=FailingTerminalStore(), runtime_host_id="host-1")
        started = asyncio.Event()

        async def run_turn() -> None:
            async with manager.track("turn-1", "session-1"):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(run_turn())
        await started.wait()
        with pytest.raises(OSError, match="terminal write failed"):
            await manager.cancel_all()
        assert task.done()

    asyncio.run(scenario())


def test_recovery_track_finishes_claimed_turn_on_new_host(tmp_path) -> None:
    async def scenario():
        database = tmp_path / "runtime.sqlite3"
        store = SQLiteTurnStore(database)
        started_at = datetime.now(UTC)
        await store.register_host(
            "old-host",
            now=started_at,
            lease_expires_at=started_at + timedelta(seconds=1),
        )
        await store.create_turn(
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="old-host",
            started_at=started_at,
        )
        now = started_at + timedelta(seconds=2)
        await store.register_host(
            "new-host",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        manager = TurnLifecycleManager(store=store, runtime_host_id="new-host")
        recoverable = await manager.list_recoverable("session-1")
        assert [turn.turn_id for turn in recoverable] == ["turn-1"]
        assert await manager.claim_recovery("turn-1", "session-1") is True
        async with manager.track_recovery("turn-1", "session-1"):
            assert manager.active_turns[0].state is TurnState.RUNNING
        return manager, await store.list_turns()

    manager, records = asyncio.run(scenario())

    assert records[0].state == "completed"
    assert records[0].runtime_host_id == "new-host"
    assert manager.history[0].state is TurnState.COMPLETED


def test_cancelled_recovery_returns_turn_to_interrupted(tmp_path) -> None:
    async def scenario():
        store = SQLiteTurnStore(tmp_path / "runtime.sqlite3")
        started_at = datetime.now(UTC)
        await store.register_host(
            "old-host",
            now=started_at,
            lease_expires_at=started_at + timedelta(seconds=1),
        )
        await store.create_turn(
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="old-host",
            started_at=started_at,
        )
        now = started_at + timedelta(seconds=2)
        await store.register_host(
            "new-host",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        manager = TurnLifecycleManager(store=store, runtime_host_id="new-host")
        assert await manager.claim_recovery("turn-1", "session-1") is True
        with pytest.raises(asyncio.CancelledError):
            async with manager.track_recovery("turn-1", "session-1"):
                raise asyncio.CancelledError
        return await store.list_turns()

    records = asyncio.run(scenario())

    assert records[0].state == "interrupted"
    assert records[0].finished_at is not None
