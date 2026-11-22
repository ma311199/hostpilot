import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import SQLiteTaskStore, SQLiteTurnStore
from hostpilot.runtime import TaskLifecycleManager, TaskState


def test_task_lifecycle_persists_outcomes_and_unique_attempt_ids(tmp_path) -> None:
    async def scenario():
        database_path = tmp_path / "runtime.sqlite3"
        turn_store = SQLiteTurnStore(database_path)
        await turn_store.initialize()
        now = datetime.now(UTC)
        await turn_store.register_host(
            "host-1",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        store = SQLiteTaskStore(database_path)
        manager = TaskLifecycleManager(store=store, runtime_host_id="host-1")

        async with manager.track("summarize", "turn-1", "session-1") as attempt:
            assert manager.active_task_runs[0].state is TaskState.RUNNING
            attempt.fail()
        async with manager.track("summarize", "turn-1", "session-1") as attempt:
            attempt.complete(
                description="Summarize the source",
                input={"source": "about.txt"},
                depends_on=(),
                output={"summary": "HostPilot"},
                task_fingerprint=f"v1:{'a' * 64}",
            )

        records = await store.list_task_runs(turn_id="turn-1")
        prior_results = await store.list_completed_task_results(
            session_id="session-1",
            exclude_turn_id="turn-2",
            limit=32,
        )
        return manager, records, prior_results

    manager, records, prior_results = asyncio.run(scenario())

    assert len(records) == 2
    assert records[0].task_id == records[1].task_id == "summarize"
    assert records[0].task_run_id != records[1].task_run_id
    assert [record.state for record in records] == ["failed", "completed"]
    assert all(record.finished_at is not None for record in records)
    assert [item.state for item in manager.history] == [TaskState.FAILED, TaskState.COMPLETED]
    assert len(prior_results) == 1
    assert prior_results[0].output == {"summary": "HostPilot"}


def test_task_lifecycle_persists_cancellation(tmp_path) -> None:
    async def scenario():
        database_path = tmp_path / "runtime.sqlite3"
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        now = datetime.now(UTC)
        await turns.register_host(
            "host-1",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        store = SQLiteTaskStore(database_path)
        manager = TaskLifecycleManager(store=store, runtime_host_id="host-1")
        started = asyncio.Event()

        async def execute_task() -> None:
            async with manager.track("write_report", "turn-1", "session-1"):
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(execute_task())
        await started.wait()
        assert await manager.cancel_all() == 1
        with pytest.raises(asyncio.CancelledError):
            await task
        return await store.list_task_runs()

    records = asyncio.run(scenario())

    assert records[0].state == "cancelled"
    assert records[0].finished_at is not None


def test_task_lifecycle_removes_active_record_when_terminal_write_fails(tmp_path) -> None:
    async def scenario():
        database_path = tmp_path / "runtime.sqlite3"
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        now = datetime.now(UTC)
        await turns.register_host(
            "host-1",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        store = SQLiteTaskStore(database_path)

        class FailingFinishStore:
            async def create_task_run(self, **kwargs) -> None:
                await store.create_task_run(**kwargs)

            async def finish_task_run(self, *args, **kwargs) -> None:
                del args, kwargs
                raise OSError("task terminal write failed")

        manager = TaskLifecycleManager(store=FailingFinishStore(), runtime_host_id="host-1")
        with pytest.raises(OSError, match="task terminal write failed"):
            async with manager.track("summarize", "turn-1", "session-1"):
                pass
        return manager

    manager = asyncio.run(scenario())

    assert manager.active_task_runs == ()
    assert manager.history[0].state is TaskState.COMPLETED
