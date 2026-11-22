import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import (
    CompletedPlanTaskResult,
    SQLiteTaskStore,
    SQLiteTurnStore,
)
from hostpilot.task_results import FailedPlanTaskSummary


def test_task_store_persists_terminal_metadata_across_reopen(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    started_at = datetime(2026, 11, 21, 11, 0, tzinfo=UTC)
    finished_at = started_at + timedelta(seconds=4)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host(
            "host-a",
            now=started_at,
            lease_expires_at=started_at + timedelta(seconds=30),
        )
        store = SQLiteTaskStore(database_path)
        await store.initialize()
        await store.create_task_run(
            task_run_id="task-run-1",
            task_id="summarize",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=started_at,
        )
        await store.finish_task_run(
            "task-run-1",
            runtime_host_id="host-a",
            state="completed",
            finished_at=finished_at,
        )

        records = await SQLiteTaskStore(database_path).list_task_runs(turn_id="turn-1")
        assert len(records) == 1
        assert records[0].task_run_id == "task-run-1"
        assert records[0].task_id == "summarize"
        assert records[0].turn_id == "turn-1"
        assert records[0].session_id == "session-1"
        assert records[0].runtime_host_id == "host-a"
        assert records[0].state == "completed"
        assert records[0].started_at == started_at
        assert records[0].finished_at == finished_at

    asyncio.run(scenario())


def test_task_store_interrupts_only_tasks_owned_by_expired_hosts(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 21, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host(
            "stale-host",
            now=now - timedelta(minutes=1),
            lease_expires_at=now - timedelta(seconds=1),
        )
        store = SQLiteTaskStore(database_path)
        await store.initialize()
        await store.create_task_run(
            task_run_id="stale-run",
            task_id="read_source",
            turn_id="turn-stale",
            session_id="session-1",
            runtime_host_id="stale-host",
            started_at=now - timedelta(minutes=1),
        )
        await turns.register_host(
            "live-host",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        await store.create_task_run(
            task_run_id="live-run",
            task_id="write_report",
            turn_id="turn-live",
            session_id="session-2",
            runtime_host_id="live-host",
            started_at=now,
        )

        assert await store.interrupt_inactive_host_tasks(now=now) == 1
        records = {record.task_run_id: record for record in await store.list_task_runs()}
        assert records["stale-run"].state == "interrupted"
        assert records["stale-run"].finished_at == now
        assert records["live-run"].state == "running"
        assert records["live-run"].finished_at is None

    asyncio.run(scenario())


def test_task_store_interrupts_residual_tasks_from_stopped_hosts(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 21, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host(
            "stopped-host",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="residual-run",
            task_id="write_report",
            turn_id="turn-stopped",
            session_id="session-1",
            runtime_host_id="stopped-host",
            started_at=now,
        )
        await turns.release_host("stopped-host", now=now + timedelta(seconds=1))

        assert await store.interrupt_inactive_host_tasks(now=now + timedelta(seconds=2)) == 1
        record = (await store.list_task_runs())[0]
        assert record.state == "interrupted"
        assert record.finished_at == now + timedelta(seconds=2)

    asyncio.run(scenario())


def test_task_store_rejects_nonterminal_finish_state(tmp_path) -> None:
    async def scenario() -> None:
        store = SQLiteTaskStore(tmp_path / "runtime.sqlite3")
        await store.initialize()
        with pytest.raises(ValueError, match="terminal"):
            await store.finish_task_run(
                "task-run-1",
                runtime_host_id="host-a",
                state="running",
                finished_at=datetime.now(UTC),
            )

    asyncio.run(scenario())


def test_task_store_schema_excludes_task_payload_fields(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"

    async def scenario() -> None:
        await SQLiteTaskStore(database_path).initialize()

    asyncio.run(scenario())
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runtime_task_runs)").fetchall()
        }
    assert columns == {
        "task_run_id",
        "task_id",
        "turn_id",
        "session_id",
        "runtime_host_id",
        "state",
        "started_at",
        "finished_at",
    }


def test_task_store_migrates_legacy_result_table_with_no_fingerprint(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE runtime_plan_task_results (
                task_run_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                description TEXT NOT NULL,
                input_json TEXT NOT NULL,
                depends_on_json TEXT NOT NULL,
                output_json TEXT NOT NULL,
                finished_at TEXT NOT NULL
            )
            """
        )

    asyncio.run(SQLiteTaskStore(database_path).initialize())
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(runtime_plan_task_results)").fetchall()
        }
    assert "task_fingerprint" in columns


def test_task_store_persists_and_lists_completed_results_by_session(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        for host_id in ("host-a", "host-b"):
            await turns.register_host(
                host_id,
                now=now,
                lease_expires_at=now + timedelta(minutes=1),
            )
        store = SQLiteTaskStore(database_path)
        task_runs = (
            ("run-old", "task_old", "turn-old", "session-1", "host-a", 1),
            ("run-new", "task_new", "turn-new", "session-1", "host-a", 2),
            ("run-other-session", "task_other", "turn-other", "session-2", "host-b", 3),
        )
        for run_id, task_id, turn_id, session_id, host_id, offset in task_runs:
            started_at = now + timedelta(seconds=offset)
            await store.create_task_run(
                task_run_id=run_id,
                task_id=task_id,
                turn_id=turn_id,
                session_id=session_id,
                runtime_host_id=host_id,
                started_at=started_at,
            )
            await store.finish_task_run(
                run_id,
                runtime_host_id=host_id,
                state="completed",
                finished_at=started_at + timedelta(seconds=1),
                completed_result=CompletedPlanTaskResult(
                    task_fingerprint=f"v1:{'a' * 64}",
                    description=f"description-{task_id}",
                    input={"source": task_id},
                    depends_on=("prepare",),
                    output={"value": offset},
                ),
            )

        reopened = SQLiteTaskStore(database_path)
        results = await reopened.list_completed_task_results(
            session_id="session-1",
            exclude_turn_id="turn-new",
            limit=10,
        )
        assert len(results) == 1
        assert results[0].task_run_id == "run-old"
        assert results[0].task_id == "task_old"
        assert results[0].description == "description-task_old"
        assert results[0].input == {"source": "task_old"}
        assert results[0].depends_on == ("prepare",)
        assert results[0].output == {"value": 1}

    asyncio.run(scenario())


def test_task_store_result_history_is_bounded_and_newest_first_within_window(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        for index in range(4):
            started_at = now + timedelta(seconds=index)
            run_id = f"run-{index}"
            await store.create_task_run(
                task_run_id=run_id,
                task_id=f"task-{index}",
                turn_id=f"turn-{index}",
                session_id="session-1",
                runtime_host_id="host-a",
                started_at=started_at,
            )
            await store.finish_task_run(
                run_id,
                runtime_host_id="host-a",
                state="completed",
                finished_at=started_at + timedelta(seconds=1),
                completed_result=CompletedPlanTaskResult(
                    task_fingerprint=f"v1:{index:064x}",
                    description=f"task {index}",
                    input={},
                    depends_on=(),
                    output=index,
                ),
            )
        return await store.list_completed_task_results(
            session_id="session-1",
            exclude_turn_id="turn-current",
            limit=2,
        )

    results = asyncio.run(scenario())
    assert [result.task_id for result in results] == ["task-2", "task-3"]


def test_task_store_bounds_total_result_history_payload_size(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        for index in range(3):
            started_at = now + timedelta(seconds=index)
            run_id = f"large-run-{index}"
            await store.create_task_run(
                task_run_id=run_id,
                task_id=f"large-task-{index}",
                turn_id=f"large-turn-{index}",
                session_id="session-1",
                runtime_host_id="host-a",
                started_at=started_at,
            )
            await store.finish_task_run(
                run_id,
                runtime_host_id="host-a",
                state="completed",
                finished_at=started_at + timedelta(seconds=1),
                completed_result=CompletedPlanTaskResult(
                    task_fingerprint=f"v1:{index:064x}",
                    description=f"large task {index}",
                    input={},
                    depends_on=(),
                    output={"text": "x" * 50_000},
                ),
            )
        return await store.list_completed_task_results(
            session_id="session-1", exclude_turn_id="current", limit=32
        )

    results = asyncio.run(scenario())
    assert [result.task_id for result in results] == ["large-task-2"]


def test_task_store_does_not_persist_result_for_noncompleted_task(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="run-failed",
            task_id="task-failed",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        with pytest.raises(ValueError, match="completed"):
            await store.finish_task_run(
                "run-failed",
                runtime_host_id="host-a",
                state="failed",
                finished_at=now + timedelta(seconds=1),
                completed_result=CompletedPlanTaskResult(
                    task_fingerprint=f"v1:{1:064x}",
                    description="failed task",
                    input={},
                    depends_on=(),
                    output={"secret": "x"},
                ),
            )
        assert (
            await store.list_completed_task_results(
                session_id="session-1", exclude_turn_id="turn-next", limit=10
            )
            == ()
        )

    asyncio.run(scenario())


def test_task_store_rejects_oversized_result_without_marking_task_completed(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="run-large",
            task_id="task-large",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        with pytest.raises(ValueError, match="65536-byte"):
            await store.finish_task_run(
                "run-large",
                runtime_host_id="host-a",
                state="completed",
                finished_at=now + timedelta(seconds=1),
                completed_result=CompletedPlanTaskResult(
                    task_fingerprint=f"v1:{2:064x}",
                    description="large result",
                    input={},
                    depends_on=(),
                    output={"text": "x" * 70_000},
                ),
            )
        records = await store.list_task_runs(turn_id="turn-1")
        assert records[0].state == "running"
        assert (
            await store.list_completed_task_results(
                session_id="session-1", exclude_turn_id="turn-2", limit=10
            )
            == ()
        )

    asyncio.run(scenario())


def test_task_store_finds_only_exact_successful_fingerprint_in_same_session(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="fingerprint-run",
            task_id="inspect",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        await store.finish_task_run(
            "fingerprint-run",
            runtime_host_id="host-a",
            state="completed",
            finished_at=now + timedelta(seconds=1),
            completed_result=CompletedPlanTaskResult(
                task_fingerprint=f"v1:{3:064x}",
                description="Inspect README",
                input={"path": "README.md"},
                depends_on=(),
                output={"summary": "HostPilot"},
            ),
        )
        reopened = SQLiteTaskStore(database_path)
        exact = await reopened.find_completed_task_result(
            session_id="session-1",
            exclude_turn_id="turn-2",
            task_fingerprint=f"v1:{3:064x}",
        )
        miss = await reopened.find_completed_task_result(
            session_id="session-1",
            exclude_turn_id="turn-2",
            task_fingerprint=f"v1:{4:064x}",
        )
        other_session = await reopened.find_completed_task_result(
            session_id="session-2",
            exclude_turn_id="turn-2",
            task_fingerprint=f"v1:{3:064x}",
        )
        assert exact is not None
        assert exact.output == {"summary": "HostPilot"}
        assert miss is None
        assert other_session is None

    asyncio.run(scenario())


def test_task_store_persists_bounded_failure_summaries_without_output(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="failed-run",
            task_id="inspect",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        await store.finish_task_run(
            "failed-run",
            runtime_host_id="host-a",
            state="failed",
            finished_at=now + timedelta(seconds=1),
            failure_summary=FailedPlanTaskSummary(
                task_fingerprint=f"v1:{5:064x}",
                description="Inspect README",
                input={},
                error_code="tool_unavailable",
                side_effect_count=0,
            ),
        )
        failures = await SQLiteTaskStore(database_path).list_recent_task_failures(
            session_id="session-1", exclude_turn_id="turn-2", limit=16
        )
        assert len(failures) == 1
        assert failures[0].task_fingerprint == f"v1:{5:064x}"
        assert failures[0].input == {}
        assert failures[0].error_code == "tool_unavailable"
        assert failures[0].side_effect_count == 0

    asyncio.run(scenario())


def test_task_store_rejects_unsafe_failure_code_without_finishing_run(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="unsafe-failure-run",
            task_id="inspect",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        with pytest.raises(ValueError, match="error code"):
            await store.finish_task_run(
                "unsafe-failure-run",
                runtime_host_id="host-a",
                state="failed",
                finished_at=now + timedelta(seconds=1),
                failure_summary=FailedPlanTaskSummary(
                    task_fingerprint=f"v1:{6:064x}",
                    description="Inspect README",
                    input={},
                    error_code="secret:raw exception",
                    side_effect_count=0,
                ),
            )
        assert (await store.list_task_runs(turn_id="turn-1"))[0].state == "running"

    asyncio.run(scenario())


def test_task_store_rejects_failed_task_input_without_finishing_run(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="input-failure-run",
            task_id="inspect",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        with pytest.raises(ValueError, match="cannot persist task input"):
            await store.finish_task_run(
                "input-failure-run",
                runtime_host_id="host-a",
                state="failed",
                finished_at=now + timedelta(seconds=1),
                failure_summary=FailedPlanTaskSummary(
                    task_fingerprint=f"v1:{7:064x}",
                    description="Inspect README",
                    input={"token": "sensitive"},
                    error_code="tool_unavailable",
                    side_effect_count=0,
                ),
            )
        assert (await store.list_task_runs(turn_id="turn-1"))[0].state == "running"

    asyncio.run(scenario())
