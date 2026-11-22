import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import SQLiteSessionLifecycleStore, SQLiteTaskStore, SQLiteTurnStore


def test_session_store_grants_and_releases_lease(tmp_path) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        database_path = tmp_path / "runtime.sqlite3"
        sessions = SQLiteSessionLifecycleStore(database_path)
        hosts = SQLiteTurnStore(database_path)
        await sessions.initialize()
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(seconds=30))
        await sessions.register_session("session-1")

        await sessions.acquire_lease("session-1", "lease-1", "host-a", now)
        assert await sessions.active_lease_count("session-1", now) == 1
        await sessions.release_lease("lease-1", "host-a")
        assert await sessions.active_lease_count("session-1", now) == 0

    asyncio.run(scenario())


def test_session_store_rejects_new_lease_after_close(tmp_path) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        database_path = tmp_path / "runtime.sqlite3"
        sessions = SQLiteSessionLifecycleStore(database_path)
        hosts = SQLiteTurnStore(database_path)
        await sessions.initialize()
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(seconds=30))
        await sessions.register_session("session-1")

        await sessions.begin_close("session-1", "host-a")
        with pytest.raises(RuntimeError, match="closing"):
            await sessions.acquire_lease("session-1", "lease-1", "host-a", now)

    asyncio.run(scenario())


def test_session_store_coordinates_two_instances_and_expired_host(tmp_path) -> None:
    async def scenario() -> None:
        now = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
        database_path = tmp_path / "runtime.sqlite3"
        first = SQLiteSessionLifecycleStore(database_path)
        second = SQLiteSessionLifecycleStore(database_path)
        hosts = SQLiteTurnStore(database_path)
        await hosts.initialize()
        await first.initialize()
        await second.initialize()
        await hosts.register_host(
            "live-host", now=now, lease_expires_at=now + timedelta(seconds=30)
        )
        await hosts.register_host(
            "short-lease-host", now=now, lease_expires_at=now + timedelta(seconds=5)
        )
        await first.register_session("session-1")

        await first.acquire_lease("session-1", "live-lease", "live-host", now)
        await second.acquire_lease("session-1", "stale-lease", "short-lease-host", now)
        assert await second.active_lease_count("session-1", now) == 2
        assert await first.active_lease_count("session-1", now + timedelta(seconds=6)) == 1
        assert await first.active_lease_count("session-1", now + timedelta(seconds=31)) == 0

    asyncio.run(scenario())


def test_session_store_close_completion_is_idempotent(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        now = datetime.now(UTC)
        hosts = SQLiteTurnStore(database_path)
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        store = SQLiteSessionLifecycleStore(database_path)
        await store.initialize()
        await store.register_session("session-1")
        await store.begin_close("session-1", "host-a")

        await store.finish_delete("session-1")
        await store.finish_delete("session-1")
        assert (
            await store.active_lease_count("session-1", datetime(2026, 11, 21, 10, 0, tzinfo=UTC))
            == 0
        )

    asyncio.run(scenario())


def test_deleted_session_tombstone_prevents_legacy_backfill_resurrection(tmp_path) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        database_path = tmp_path / "runtime.sqlite3"
        hosts = SQLiteTurnStore(database_path)
        sessions = SQLiteSessionLifecycleStore(database_path)
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        await sessions.initialize()
        await sessions.register_session("session-1")
        await sessions.begin_close("session-1", "host-a")
        await sessions.finish_delete("session-1")

        await sessions.register_session("session-1")
        with pytest.raises(RuntimeError, match="unavailable"):
            await sessions.acquire_lease("session-1", "lease-1", "host-a", now)

    asyncio.run(scenario())


def test_session_delete_removes_plan_snapshots_and_private_tool_arguments(tmp_path) -> None:
    async def scenario() -> int:
        now = datetime.now(UTC)
        database_path = tmp_path / "runtime.sqlite3"
        hosts = SQLiteTurnStore(database_path)
        await hosts.initialize()
        await hosts.register_host("host-a", now=now, lease_expires_at=now + timedelta(minutes=1))
        sessions = SQLiteSessionLifecycleStore(database_path)
        await sessions.initialize()
        await sessions.register_session("session-1")
        tasks = SQLiteTaskStore(database_path)
        await tasks.create_plan_run(
            plan_run_id="plan-run-1",
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            goal="Send invoice",
            snapshot={"version": 1, "tasks": []},
            started_at=now,
        )
        await tasks.record_tool_invocation(
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
        await sessions.begin_close("session-1", "host-a")
        await sessions.finish_delete("session-1")
        import sqlite3

        with sqlite3.connect(database_path) as connection:
            return int(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_plan_runs WHERE session_id = ?",
                    ("session-1",),
                ).fetchone()[0]
            ) + int(
                connection.execute(
                    "SELECT COUNT(*) FROM runtime_tool_invocations WHERE session_id = ?",
                    ("session-1",),
                ).fetchone()[0]
            )

    assert asyncio.run(scenario()) == 0
