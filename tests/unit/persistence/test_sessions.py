import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import SQLiteSessionLifecycleStore, SQLiteTurnStore


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
