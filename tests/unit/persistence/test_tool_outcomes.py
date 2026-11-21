import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import SQLiteToolOutcomeStore, SQLiteTurnStore


async def _register_host(store: SQLiteTurnStore, host_id: str, *, active: bool = True) -> None:
    now = datetime.now(UTC)
    await store.initialize()
    await store.register_host(
        host_id,
        now=now,
        lease_expires_at=now + timedelta(minutes=1),
    )
    if not active:
        await store.release_host(host_id, now=now)


def test_store_tracks_pending_outcome_without_arguments_or_result_text(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        hosts = SQLiteTurnStore(database_path)
        await _register_host(hosts, "host-a")
        store = SQLiteToolOutcomeStore(database_path)
        await store.initialize()
        now = datetime.now(UTC)

        await store.record_pending(
            session_id="session-1",
            turn_id="turn-1",
            task_run_id=None,
            tool_call_id="call-1",
            tool_name="write_text_file",
            runtime_host_id="host-a",
            now=now,
        )

        pending = await store.list_pending("session-1")
        assert len(pending) == 1
        assert pending[0].turn_id == "turn-1"
        assert pending[0].tool_call_id == "call-1"
        assert pending[0].tool_name == "write_text_file"
        assert pending[0].resolving_host_id is None
        assert "secret-argument" not in repr(pending)
        assert "sensitive-result" not in repr(pending)

        await store.clear_pending("session-1", "call-1", runtime_host_id="host-a")
        assert await store.list_pending("session-1") == ()

    asyncio.run(scenario())


def test_store_rejects_duplicate_call_ids_and_inactive_hosts(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        hosts = SQLiteTurnStore(database_path)
        await _register_host(hosts, "host-a")
        await _register_host(hosts, "host-stale", active=False)
        store = SQLiteToolOutcomeStore(database_path)
        await store.initialize()
        now = datetime.now(UTC)
        values = {
            "session_id": "session-1",
            "turn_id": "turn-1",
            "task_run_id": None,
            "tool_call_id": "call-1",
            "tool_name": "write_text_file",
            "now": now,
        }

        with pytest.raises(RuntimeError, match="Host lease"):
            await store.record_pending(**values, runtime_host_id="host-stale")
        await store.record_pending(**values, runtime_host_id="host-a")
        with pytest.raises(ValueError, match="pending outcome"):
            await store.record_pending(**values, runtime_host_id="host-a")

    asyncio.run(scenario())


def test_resolution_claim_is_exclusive_and_expired_host_claim_is_recoverable(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        hosts = SQLiteTurnStore(database_path)
        await _register_host(hosts, "host-call")
        await _register_host(hosts, "host-resolver-a")
        await _register_host(hosts, "host-resolver-b")
        store_a = SQLiteToolOutcomeStore(database_path)
        store_b = SQLiteToolOutcomeStore(database_path)
        await store_a.initialize()
        await store_b.initialize()
        now = datetime.now(UTC)
        await store_a.record_pending(
            session_id="session-1",
            turn_id="turn-1",
            task_run_id="task-1",
            tool_call_id="call-1",
            tool_name="remote_action",
            runtime_host_id="host-call",
            now=now,
        )

        assert await store_a.claim_resolution(
            "session-1", "call-1", runtime_host_id="host-resolver-a", now=now
        )
        assert not await store_b.claim_resolution(
            "session-1", "call-1", runtime_host_id="host-resolver-b", now=now
        )
        await hosts.release_host("host-resolver-a", now=now)
        assert await store_b.claim_resolution(
            "session-1", "call-1", runtime_host_id="host-resolver-b", now=now
        )

    asyncio.run(scenario())


def test_resolution_claim_can_be_retried_by_its_current_host(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        hosts = SQLiteTurnStore(database_path)
        await _register_host(hosts, "host-call")
        await _register_host(hosts, "host-resolver")
        store = SQLiteToolOutcomeStore(database_path)
        await store.initialize()
        now = datetime.now(UTC)
        await store.record_pending(
            session_id="session-1",
            turn_id="turn-1",
            task_run_id=None,
            tool_call_id="call-1",
            tool_name="write_file",
            runtime_host_id="host-call",
            now=now,
        )

        assert await store.claim_resolution(
            "session-1", "call-1", runtime_host_id="host-resolver", now=now
        )
        assert await store.claim_resolution(
            "session-1", "call-1", runtime_host_id="host-resolver", now=now
        )

    asyncio.run(scenario())
