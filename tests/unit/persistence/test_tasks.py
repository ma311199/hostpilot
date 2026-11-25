import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.contracts import ToolRetryClass
from hostpilot.persistence import (
    CompletedPlanTaskResult,
    SQLiteTaskStore,
    SQLiteTurnStore,
    StoredPlanTaskResult,
)
from hostpilot.task_results import (
    FailedPlanTaskSummary,
    PlanTaskExecutionSummary,
    PlanTaskToolUsage,
)


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
        await store.create_plan_run(
            plan_run_id="stale-plan",
            turn_id="turn-stale",
            session_id="session-1",
            runtime_host_id="stale-host",
            goal="Prepare a report",
            snapshot={"version": 1, "plan": {"tasks": []}},
            started_at=now - timedelta(minutes=1),
        )
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
        plan_run = await store.get_plan_run("stale-plan")
        assert records["stale-run"].state == "interrupted"
        assert records["stale-run"].finished_at == now
        assert records["live-run"].state == "running"
        assert records["live-run"].finished_at is None
        assert plan_run is not None and plan_run.state == "interrupted"

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


def test_task_store_persists_plan_run_snapshot_and_lists_recoverable_session_runs(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    later = now + timedelta(seconds=5)
    snapshot = {
        "version": 1,
        "goal": "Inspect two API contracts",
        "tasks": [{"task_id": "inspect-api", "depends_on": []}],
        "results": {},
    }

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(seconds=30))
        store = SQLiteTaskStore(database_path)
        await store.create_plan_run(
            plan_run_id="plan-run-1",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            goal="Inspect two API contracts",
            snapshot=snapshot,
            started_at=now,
        )
        await store.update_plan_run(
            "plan-run-1",
            runtime_host_id="host-a",
            state="interrupted",
            snapshot={**snapshot, "completed_task_ids": ["inspect-api"]},
            updated_at=later,
        )

    asyncio.run(scenario())

    async def reopen() -> None:
        store = SQLiteTaskStore(database_path)
        loaded = await store.get_plan_run("plan-run-1")
        assert loaded is not None
        assert loaded.session_id == "session-1"
        assert loaded.state == "interrupted"
        assert loaded.snapshot["completed_task_ids"] == ["inspect-api"]
        assert loaded.updated_at == later
        assert [run.plan_run_id for run in await store.list_recoverable_plan_runs("session-1")] == [
            "plan-run-1"
        ]
        assert await store.list_recoverable_plan_runs("session-2") == ()

    asyncio.run(reopen())


def test_task_store_claims_one_plan_resume_until_owner_releases_it(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    snapshot = {"version": 1, "goal": "Read README", "tasks": []}

    async def scenario() -> tuple[bool, bool, bool]:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        for host_id in ("host-a", "host-b"):
            await turns.register_host(
                host_id, now=now, lease_expires_at=now + timedelta(seconds=30)
            )
        store = SQLiteTaskStore(database_path)
        await store.create_plan_run(
            plan_run_id="plan-run-1",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            goal="Read README",
            snapshot=snapshot,
            started_at=now,
        )
        await store.update_plan_run(
            "plan-run-1",
            runtime_host_id="host-a",
            state="interrupted",
            snapshot=snapshot,
            updated_at=now + timedelta(seconds=1),
        )
        first = await store.claim_plan_run(
            "plan-run-1", runtime_host_id="host-a", now=now + timedelta(seconds=2)
        )
        concurrent = await store.claim_plan_run(
            "plan-run-1", runtime_host_id="host-b", now=now + timedelta(seconds=2)
        )
        await store.release_plan_run_claim("plan-run-1", runtime_host_id="host-a")
        after_release = await store.claim_plan_run(
            "plan-run-1", runtime_host_id="host-b", now=now + timedelta(seconds=3)
        )
        if after_release:
            await store.update_plan_run(
                "plan-run-1",
                runtime_host_id="host-b",
                state="running",
                snapshot=snapshot,
                updated_at=now + timedelta(seconds=4),
            )
        recovered = await store.get_plan_run("plan-run-1")
        assert recovered is not None and recovered.runtime_host_id == "host-b"
        return first, concurrent, after_release

    assert asyncio.run(scenario()) == (True, False, True)


def test_task_store_journals_tool_invocation_before_dispatch_and_records_completion(
    tmp_path,
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(seconds=30))
        store = SQLiteTaskStore(database_path)
        await store.create_plan_run(
            plan_run_id="plan-run-1",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            goal="Send invoice",
            snapshot={"version": 1, "tasks": []},
            started_at=now,
        )
        await store.record_tool_invocation(
            invocation_id="invocation-1",
            plan_run_id="plan-run-1",
            task_run_id="task-run-1",
            task_id="send-invoice",
            call_index=1,
            tool_id="billing/send_invoice",
            retry_class="non_idempotent_write",
            idempotency_key="plan-run-1:send-invoice:1:invoice-42",
            request_fingerprint="sha256:request-1",
            arguments_json='{"invoice_id":"invoice-42"}',
            created_at=now,
            runtime_host_id="host-a",
            idempotency_window_seconds=3600,
        )
        pending = await store.list_tool_invocations("plan-run-1")
        assert len(pending) == 1
        assert pending[0].state == "prepared"
        assert pending[0].idempotency_key == "plan-run-1:send-invoice:1:invoice-42"
        assert pending[0].arguments_json == '{"invoice_id":"invoice-42"}'
        assert pending[0].idempotency_expires_at == now + timedelta(seconds=3600)

        await store.finish_tool_invocation(
            "invocation-1",
            plan_run_id="plan-run-1",
            runtime_host_id="host-a",
            state="completed",
            finished_at=now + timedelta(seconds=1),
        )
        completed = await SQLiteTaskStore(database_path).list_tool_invocations("plan-run-1")
        assert completed[0].state == "completed"
        assert completed[0].finished_at == now + timedelta(seconds=1)

    asyncio.run(scenario())


def test_task_store_journals_runtime_chat_tool_calls_without_plan_run(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(seconds=30))
        store = SQLiteTaskStore(database_path)
        await store.record_runtime_tool_invocation(
            invocation_id="invocation-chat-1",
            session_id="session-1",
            turn_id="turn-1",
            runtime_host_id="host-a",
            task_run_id=None,
            task_id="runtime_chat",
            call_index=1,
            tool_id="search_files",
            retry_class=ToolRetryClass.PURE_READ,
            idempotency_key=None,
            request_fingerprint="sha256:search-request",
            arguments_json='{"pattern":"*.py"}',
            idempotency_window_seconds=None,
            created_at=now,
        )
        await store.finish_tool_invocation(
            "invocation-chat-1",
            plan_run_id=None,
            runtime_host_id="host-a",
            state="completed",
            finished_at=now + timedelta(seconds=1),
        )
        loaded = await SQLiteTaskStore(database_path).list_tool_invocations(session_id="session-1")
        assert len(loaded) == 1
        assert loaded[0].plan_run_id is None
        assert loaded[0].arguments_json == '{"pattern":"*.py"}'
        assert loaded[0].request_fingerprint == "sha256:search-request"
        assert loaded[0].state == "completed"

    asyncio.run(scenario())


def test_task_store_lists_runtime_tool_invocations_by_turn(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime.now(UTC)

    async def scenario() -> None:
        hosts = SQLiteTurnStore(database_path)
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        for turn_id, invocation_id in (("turn-a", "call-a"), ("turn-b", "call-b")):
            await hosts.create_turn(
                turn_id=turn_id,
                session_id="session-1",
                runtime_host_id="host-a",
                started_at=now,
            )
            await store.record_runtime_tool_invocation(
                invocation_id=invocation_id,
                session_id="session-1",
                turn_id=turn_id,
                runtime_host_id="host-a",
                task_run_id=None,
                task_id="runtime_chat",
                call_index=1,
                tool_id="search_files",
                retry_class=ToolRetryClass.PURE_READ,
                idempotency_key=None,
                request_fingerprint=f"fingerprint-{turn_id}",
                arguments_json="{}",
                idempotency_window_seconds=None,
                created_at=now,
            )

        return await store.list_runtime_tool_invocations("session-1", "turn-a")

    records = asyncio.run(scenario())
    assert [record.invocation_id for record in records] == ["call-a"]


def test_task_store_claims_prepared_invocations_only_for_current_turn_owner(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime.now(UTC)

    async def scenario() -> None:
        hosts = SQLiteTurnStore(database_path)
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(seconds=1))
        store = SQLiteTaskStore(database_path)
        await hosts.create_turn(
            turn_id="turn-1", session_id="session-1", runtime_host_id="host-a", started_at=now
        )
        await store.record_runtime_tool_invocation(
            invocation_id="call-1",
            session_id="session-1",
            turn_id="turn-1",
            runtime_host_id="host-a",
            task_run_id=None,
            task_id="runtime_chat",
            call_index=1,
            tool_id="write_text_file",
            retry_class="idempotent_write",
            idempotency_key="stable-1",
            request_fingerprint="fingerprint-1",
            arguments_json='{"text":"a"}',
            idempotency_window_seconds=3600,
            created_at=now,
        )
        await hosts.register_host(
            "host-b", now=now + timedelta(seconds=2), lease_expires_at=now + timedelta(minutes=1)
        )
        assert await hosts.claim_interrupted_turn(
            "turn-1", "session-1", "host-b", claimed_at=now + timedelta(seconds=2)
        )
        claimed = await store.claim_runtime_tool_invocations(
            "session-1", "turn-1", runtime_host_id="host-b", now=now + timedelta(seconds=2)
        )
        wrong_host_claim = await store.claim_runtime_tool_invocations(
            "session-1", "turn-1", runtime_host_id="host-a", now=now + timedelta(seconds=2)
        )
        wrong_session_claim = await store.claim_runtime_tool_invocations(
            "session-other", "turn-1", runtime_host_id="host-b", now=now + timedelta(seconds=2)
        )
        return (
            claimed,
            wrong_host_claim,
            wrong_session_claim,
            await store.list_runtime_tool_invocations("session-1", "turn-1"),
        )

    claimed, wrong_host_claim, wrong_session_claim, records = asyncio.run(scenario())
    assert claimed is True
    assert wrong_host_claim is False
    assert wrong_session_claim is False
    assert records[0].runtime_host_id == "host-b"
    assert records[0].state == "prepared"


def test_task_store_reuses_only_exact_prepared_runtime_invocation(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime.now(UTC)

    async def scenario() -> None:
        hosts = SQLiteTurnStore(database_path)
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await hosts.create_turn(
            turn_id="turn-1", session_id="session-1", runtime_host_id="host-a", started_at=now
        )
        values = dict(
            invocation_id="call-1",
            session_id="session-1",
            turn_id="turn-1",
            runtime_host_id="host-a",
            task_run_id=None,
            task_id="runtime_chat",
            call_index=1,
            tool_id="write_text_file",
            retry_class="idempotent_write",
            idempotency_key="stable-1",
            request_fingerprint="fingerprint-1",
            arguments_json='{"text":"a"}',
            idempotency_window_seconds=3600,
            created_at=now,
        )
        await store.record_runtime_tool_invocation(**values)
        await store.record_runtime_tool_invocation(**values)
        return store

    store = asyncio.run(scenario())
    assert len(asyncio.run(store.list_runtime_tool_invocations("session-1", "turn-1"))) == 1


def test_task_store_rejects_reused_runtime_invocation_with_changed_fingerprint(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime.now(UTC)

    async def scenario() -> None:
        hosts = SQLiteTurnStore(database_path)
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await hosts.create_turn(
            turn_id="turn-1", session_id="session-1", runtime_host_id="host-a", started_at=now
        )
        values = dict(
            invocation_id="call-1",
            session_id="session-1",
            turn_id="turn-1",
            runtime_host_id="host-a",
            task_run_id=None,
            task_id="runtime_chat",
            call_index=1,
            tool_id="write_text_file",
            retry_class="idempotent_write",
            idempotency_key="stable-1",
            request_fingerprint="fingerprint-1",
            arguments_json='{"text":"a"}',
            idempotency_window_seconds=3600,
            created_at=now,
        )
        await store.record_runtime_tool_invocation(**values)
        with pytest.raises((ValueError, RuntimeError), match="match|reuse|invocation"):
            await store.record_runtime_tool_invocation(
                **{**values, "request_fingerprint": "fingerprint-changed"}
            )

    asyncio.run(scenario())


def test_task_completion_completes_its_tool_journal_with_the_result_checkpoint(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(seconds=30))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="task-run-1",
            task_id="inspect-files",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        await store.record_runtime_tool_invocation(
            invocation_id="invocation-plan-1",
            session_id="session-1",
            turn_id="turn-1",
            runtime_host_id="host-a",
            task_run_id="task-run-1",
            task_id="inspect-files",
            call_index=1,
            tool_id="read_text_file",
            retry_class=ToolRetryClass.PURE_READ,
            idempotency_key=None,
            request_fingerprint="sha256:read-request",
            arguments_json='{"path":"README.md"}',
            idempotency_window_seconds=None,
            created_at=now,
        )
        assert (await store.list_tool_invocations(session_id="session-1"))[0].state == "prepared"
        await store.finish_task_run(
            "task-run-1",
            runtime_host_id="host-a",
            state="completed",
            finished_at=now + timedelta(seconds=2),
            completed_result=CompletedPlanTaskResult(
                description="Inspect files",
                input={},
                depends_on=(),
                output={"files": []},
                task_fingerprint=f"v1:{'b' * 64}",
            ),
        )
        loaded = await SQLiteTaskStore(database_path).list_tool_invocations(session_id="session-1")
        assert loaded[0].state == "completed"

    asyncio.run(scenario())


def test_plan_task_result_and_progress_snapshot_commit_with_tool_completion(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)
    initial_snapshot = {"version": 1, "states": []}
    completed_snapshot = {
        "version": 1,
        "states": [{"task_id": "inspect", "status": "completed", "output": {"ok": True}}],
    }

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(seconds=30))
        store = SQLiteTaskStore(database_path)
        await store.create_plan_run(
            plan_run_id="plan-run-atomic",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            goal="Inspect files",
            snapshot=initial_snapshot,
            started_at=now,
        )
        await store.create_task_run(
            task_run_id="task-run-atomic",
            task_id="inspect",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        await store.record_tool_invocation(
            invocation_id="invocation-atomic",
            plan_run_id="plan-run-atomic",
            task_run_id="task-run-before-restart",
            task_id="inspect",
            call_index=1,
            tool_id="read_text_file",
            retry_class=ToolRetryClass.PURE_READ,
            idempotency_key=None,
            request_fingerprint="sha256:read",
            arguments_json='{"path":"README.md"}',
            created_at=now,
            runtime_host_id="host-a",
        )
        await store.finish_task_run(
            "task-run-atomic",
            runtime_host_id="host-a",
            state="completed",
            finished_at=now + timedelta(seconds=1),
            completed_result=CompletedPlanTaskResult(
                description="Inspect files",
                input={},
                depends_on=(),
                output={"ok": True},
                task_fingerprint=f"v1:{'b' * 64}",
            ),
            plan_run_id="plan-run-atomic",
            plan_snapshot=completed_snapshot,
        )
        persisted_plan = await store.get_plan_run("plan-run-atomic")
        persisted_invocation = (await store.list_tool_invocations("plan-run-atomic"))[0]
        assert persisted_plan is not None
        assert persisted_plan.snapshot == completed_snapshot
        assert persisted_invocation.state == "completed"

    asyncio.run(scenario())


def test_task_store_migrates_legacy_result_table_with_nullable_execution_summary(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE runtime_plan_task_results (
                task_run_id TEXT PRIMARY KEY,
                task_fingerprint TEXT,
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
            row[1]: row[3]
            for row in connection.execute("PRAGMA table_info(runtime_plan_task_results)").fetchall()
        }
    assert columns["execution_summary_json"] == 0


def test_task_store_persists_execution_summary_across_reopen(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)
    summary = PlanTaskExecutionSummary(
        tools=(
            PlanTaskToolUsage("read_text_file", 2, "read_only"),
            PlanTaskToolUsage("write_text_file", 1, "irreversible"),
        ),
        confirmation_decisions=("approved", "timed_out"),
    )

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="run-summary",
            task_id="prepare_report",
            turn_id="turn-old",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        await store.finish_task_run(
            "run-summary",
            runtime_host_id="host-a",
            state="completed",
            finished_at=now + timedelta(seconds=1),
            completed_result=CompletedPlanTaskResult(
                task_fingerprint=f"v1:{'b' * 64}",
                description="Prepare report",
                input={"source": "notes.txt"},
                depends_on=(),
                output={"ready": True},
                execution_summary=summary,
            ),
        )
        results = await SQLiteTaskStore(database_path).list_completed_task_results(
            session_id="session-1", exclude_turn_id="turn-new", limit=32
        )
        assert results[0].execution_summary == summary

    asyncio.run(scenario())


def test_task_store_rolls_back_terminal_state_if_summary_insert_fails(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="run-atomic",
            task_id="summarize",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        with sqlite3.connect(database_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER reject_task_result BEFORE INSERT ON runtime_plan_task_results
                BEGIN SELECT RAISE(ABORT, 'forced result insert failure'); END
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="forced result insert failure"):
            await store.finish_task_run(
                "run-atomic",
                runtime_host_id="host-a",
                state="completed",
                finished_at=now + timedelta(seconds=1),
                completed_result=CompletedPlanTaskResult(
                    task_fingerprint=f"v1:{'d' * 64}",
                    description="Summarize",
                    input={},
                    depends_on=(),
                    output="done",
                    execution_summary=PlanTaskExecutionSummary(),
                ),
            )
        records = await store.list_task_runs(turn_id="turn-1")
        assert records[0].state == "running"
        assert records[0].finished_at is None
        with sqlite3.connect(database_path) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM runtime_plan_task_results").fetchone()[0]
                == 0
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "summary",
    [
        PlanTaskExecutionSummary(
            tools=(PlanTaskToolUsage("read_text_file", 0, "read_only"),),
        ),
        PlanTaskExecutionSummary(
            tools=(PlanTaskToolUsage("read_text_file", 1, "assume_safe"),),
        ),
        PlanTaskExecutionSummary(confirmation_decisions=("approval from Alice",)),
        PlanTaskExecutionSummary(
            tools=tuple(
                PlanTaskToolUsage(f"tool_{index}", 1, "read_only") for index in range(5000)
            ),
        ),
    ],
)
def test_task_store_rejects_invalid_execution_summary_without_finishing_run(
    tmp_path, summary
) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        await store.create_task_run(
            task_run_id="run-invalid-summary",
            task_id="summarize",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=now,
        )
        with pytest.raises(ValueError):
            await store.finish_task_run(
                "run-invalid-summary",
                runtime_host_id="host-a",
                state="completed",
                finished_at=now + timedelta(seconds=1),
                completed_result=CompletedPlanTaskResult(
                    task_fingerprint=f"v1:{'c' * 64}",
                    description="Summarize",
                    input={},
                    depends_on=(),
                    output="done",
                    execution_summary=summary,
                ),
            )
        records = await store.list_task_runs(turn_id="turn-1")
        assert records[0].state == "running"
        assert records[0].finished_at is None

    asyncio.run(scenario())


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
        for index in range(34):
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
            limit=33,
        )

    results = asyncio.run(scenario())
    assert [result.task_id for result in results] == [f"task-{index}" for index in range(1, 34)]


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


def test_task_store_result_history_budget_counts_unicode_characters(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 22, 11, 0, tzinfo=UTC)

    async def scenario() -> tuple[StoredPlanTaskResult, ...]:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteTaskStore(database_path)
        for index in range(2):
            started_at = now + timedelta(seconds=index)
            run_id = f"unicode-run-{index}"
            await store.create_task_run(
                task_run_id=run_id,
                task_id=f"unicode-task-{index}",
                turn_id=f"unicode-turn-{index}",
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
                    description=f"任务 {index}",
                    input={},
                    depends_on=(),
                    output={"text": "中" * 20_000},
                ),
            )
        return await store.list_completed_task_results(
            session_id="session-1",
            exclude_turn_id="current",
            limit=2,
            max_chars=42_000,
        )

    results = asyncio.run(scenario())
    assert [result.task_id for result in results] == ["unicode-task-0", "unicode-task-1"]


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
