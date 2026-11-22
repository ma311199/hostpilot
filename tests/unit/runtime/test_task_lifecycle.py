import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import SQLiteTaskStore, SQLiteTurnStore
from hostpilot.runtime import TaskLifecycleManager, TaskState
from hostpilot.task_results import PlanTaskExecutionSummary, PlanTaskToolUsage


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
                execution_summary=PlanTaskExecutionSummary(
                    tools=(PlanTaskToolUsage("read_text_file", 1, "read_only"),),
                    confirmation_decisions=("approved",),
                ),
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
    assert prior_results[0].execution_summary == PlanTaskExecutionSummary(
        tools=(PlanTaskToolUsage("read_text_file", 1, "read_only"),),
        confirmation_decisions=("approved",),
    )


def test_task_lifecycle_uses_configured_result_history_bounds() -> None:
    class RecordingTaskStore:
        def __init__(self) -> None:
            self.query: dict[str, object] | None = None

        async def list_completed_task_results(self, **query):
            self.query = query
            return ()

    async def scenario():
        store = RecordingTaskStore()
        manager = TaskLifecycleManager(store=store, runtime_host_id="host-1")
        manager.configure_result_history(7, 4321)
        await manager.list_completed_task_results("session-1", exclude_turn_id="turn-2")
        return store.query

    query = asyncio.run(scenario())

    assert query == {
        "session_id": "session-1",
        "exclude_turn_id": "turn-2",
        "limit": 7,
        "max_chars": 4321,
    }


def test_task_lifecycle_exposes_plan_recovery_through_configured_store() -> None:
    class RecoveryTaskStore:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def list_recoverable_plan_runs(self, session_id):
            self.calls.append(("list", {"session_id": session_id}))
            return ("plan-run-1",)

        async def claim_plan_run(self, plan_run_id, *, runtime_host_id, now):
            self.calls.append(
                ("claim", {"plan_run_id": plan_run_id, "runtime_host_id": runtime_host_id})
            )
            return True

        async def release_plan_run_claim(self, plan_run_id, *, runtime_host_id):
            self.calls.append(
                ("release", {"plan_run_id": plan_run_id, "runtime_host_id": runtime_host_id})
            )

    async def scenario():
        store = RecoveryTaskStore()
        manager = TaskLifecycleManager(
            store=store,
            runtime_host_id="host-runtime",
        )
        listed = await manager.list_recoverable_plan_runs("session-1")
        claimed = await manager.claim_plan_run("plan-run-1")
        await manager.release_plan_run_claim("plan-run-1")
        return listed, claimed, store.calls

    assert asyncio.run(scenario()) == (
        ("plan-run-1",),
        True,
        [
            ("list", {"session_id": "session-1"}),
            ("claim", {"plan_run_id": "plan-run-1", "runtime_host_id": "host-runtime"}),
            ("release", {"plan_run_id": "plan-run-1", "runtime_host_id": "host-runtime"}),
        ],
    )


def test_task_lifecycle_exposes_plan_snapshot_persistence() -> None:
    class SnapshotTaskStore:
        def __init__(self) -> None:
            self.calls = []

        async def create_plan_run(self, **values):
            self.calls.append(("create", values))

        async def update_plan_run(self, plan_run_id, **values):
            self.calls.append(("update", {"plan_run_id": plan_run_id, **values}))

        async def get_plan_run(self, plan_run_id):
            self.calls.append(("get", plan_run_id))
            return "stored-plan"

    async def scenario():
        store = SnapshotTaskStore()
        manager = TaskLifecycleManager(store=store, runtime_host_id="host-runtime")
        now = datetime.now(UTC)
        await manager.create_plan_run(
            plan_run_id="plan-1",
            turn_id="turn-1",
            session_id="session-1",
            goal="Prepare report",
            snapshot={"version": 1, "tasks": []},
            started_at=now,
        )
        await manager.update_plan_run(
            "plan-1",
            state="interrupted",
            snapshot={"version": 1, "tasks": [{"task_id": "inspect"}]},
            updated_at=now,
        )
        loaded = await manager.get_plan_run("plan-1")
        return store.calls, loaded

    calls, loaded = asyncio.run(scenario())

    assert loaded == "stored-plan"
    assert [call[0] for call in calls] == ["create", "update", "get"]
    assert calls[0][1]["runtime_host_id"] == "host-runtime"
    assert calls[1][1]["runtime_host_id"] == "host-runtime"


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
