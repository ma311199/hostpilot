import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from hostpilot.persistence import SQLiteTurnStore


def test_turn_store_persists_lifecycle_records_across_reopen(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    started_at = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    finished_at = started_at + timedelta(seconds=3)

    async def scenario() -> None:
        store = SQLiteTurnStore(database_path)
        await store.initialize()
        await store.register_host(
            "host-a",
            now=started_at,
            lease_expires_at=started_at + timedelta(seconds=30),
        )
        await store.create_turn(
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="host-a",
            started_at=started_at,
        )
        await store.finish_turn(
            "turn-1",
            runtime_host_id="host-a",
            state="completed",
            finished_at=finished_at,
        )

        reopened = SQLiteTurnStore(database_path)
        records = await reopened.list_turns()

        assert len(records) == 1
        assert records[0].turn_id == "turn-1"
        assert records[0].session_id == "session-1"
        assert records[0].runtime_host_id == "host-a"
        assert records[0].state == "completed"
        assert records[0].started_at == started_at
        assert records[0].finished_at == finished_at

    asyncio.run(scenario())


def test_register_host_interrupts_only_turns_of_expired_hosts(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)

    async def scenario() -> None:
        store = SQLiteTurnStore(database_path)
        await store.initialize()
        await store.register_host(
            "stale-host",
            now=now - timedelta(minutes=1),
            lease_expires_at=now - timedelta(seconds=1),
        )
        await store.create_turn(
            turn_id="stale-turn",
            session_id="session-stale",
            runtime_host_id="stale-host",
            started_at=now - timedelta(minutes=1),
        )
        await store.register_host(
            "live-host",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        await store.create_turn(
            turn_id="live-turn",
            session_id="session-live",
            runtime_host_id="live-host",
            started_at=now,
        )

        await store.register_host(
            "new-host",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )

        records = {record.turn_id: record for record in await store.list_turns()}
        assert records["stale-turn"].state == "interrupted"
        assert records["stale-turn"].finished_at == now
        assert records["live-turn"].state == "running"
        assert records["live-turn"].finished_at is None

    asyncio.run(scenario())


def test_turn_store_renews_and_releases_host_lease(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    now = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)

    async def scenario() -> None:
        store = SQLiteTurnStore(database_path)
        await store.initialize()
        await store.register_host(
            "host-a",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        await store.heartbeat_host(
            "host-a",
            now=now + timedelta(seconds=5),
            lease_expires_at=now + timedelta(seconds=35),
        )
        await store.release_host("host-a", now=now + timedelta(seconds=6))
        await store.register_host(
            "new-host",
            now=now + timedelta(seconds=7),
            lease_expires_at=now + timedelta(seconds=37),
        )

        records = await store.list_turns()
        assert records == ()

    asyncio.run(scenario())


def test_turn_store_rejects_unknown_terminal_state(tmp_path) -> None:
    async def scenario() -> None:
        store = SQLiteTurnStore(tmp_path / "runtime.sqlite3")
        await store.initialize()
        with pytest.raises(ValueError, match="terminal"):
            await store.finish_turn(
                "turn-1",
                runtime_host_id="host-a",
                state="running",
                finished_at=datetime.now(UTC),
            )

    asyncio.run(scenario())


def test_turn_store_schema_contains_no_request_payload_columns(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"

    async def scenario() -> None:
        await SQLiteTurnStore(database_path).initialize()

    asyncio.run(scenario())
    with sqlite3.connect(database_path) as connection:
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(runtime_turns)").fetchall()
        }
    assert columns == {
        "turn_id",
        "session_id",
        "runtime_host_id",
        "state",
        "started_at",
        "finished_at",
    }


def test_list_interrupted_turns_is_session_scoped(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    started_at = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)

    async def scenario() -> tuple:
        store = SQLiteTurnStore(database_path)
        await store.initialize()
        await store.register_host(
            "stale-host",
            now=started_at,
            lease_expires_at=started_at + timedelta(seconds=1),
        )
        await store.create_turn(
            turn_id="recoverable-a",
            session_id="session-a",
            runtime_host_id="stale-host",
            started_at=started_at,
        )
        await store.create_turn(
            turn_id="recoverable-b",
            session_id="session-b",
            runtime_host_id="stale-host",
            started_at=started_at,
        )
        await store.register_host(
            "current-host",
            now=started_at + timedelta(seconds=2),
            lease_expires_at=started_at + timedelta(seconds=32),
        )
        return await store.list_interrupted_turns("session-a")

    turns = asyncio.run(scenario())

    assert len(turns) == 1
    assert turns[0].turn_id == "recoverable-a"
    assert turns[0].session_id == "session-a"
    assert turns[0].state == "interrupted"


def test_claim_interrupted_turn_transfers_owner_once(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    started_at = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
    interrupted_at = started_at + timedelta(seconds=2)

    async def scenario() -> tuple[list[bool], object]:
        store = SQLiteTurnStore(database_path)
        await store.register_host(
            "stale-host",
            now=started_at,
            lease_expires_at=started_at + timedelta(seconds=1),
        )
        await store.create_turn(
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="stale-host",
            started_at=started_at,
        )
        await store.register_host(
            "host-a",
            now=interrupted_at,
            lease_expires_at=interrupted_at + timedelta(seconds=30),
        )
        await store.register_host(
            "host-b",
            now=interrupted_at,
            lease_expires_at=interrupted_at + timedelta(seconds=30),
        )
        claimed_at = interrupted_at + timedelta(seconds=1)
        results = await asyncio.gather(
            store.claim_interrupted_turn("turn-1", "session-1", "host-a", claimed_at=claimed_at),
            store.claim_interrupted_turn("turn-1", "session-1", "host-b", claimed_at=claimed_at),
        )
        return results, (await store.list_turns())[0]

    results, record = asyncio.run(scenario())

    assert sorted(results) == [False, True]
    assert record.state == "running"
    assert record.runtime_host_id in {"host-a", "host-b"}
    assert record.finished_at is None


def test_claim_requires_active_host_and_matching_session(tmp_path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    started_at = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)

    async def scenario() -> tuple[bool, bool]:
        store = SQLiteTurnStore(database_path)
        await store.register_host(
            "stale-host",
            now=started_at,
            lease_expires_at=started_at + timedelta(seconds=1),
        )
        await store.create_turn(
            turn_id="turn-1",
            session_id="session-1",
            runtime_host_id="stale-host",
            started_at=started_at,
        )
        current = started_at + timedelta(seconds=2)
        await store.register_host(
            "active-host",
            now=current,
            lease_expires_at=current + timedelta(seconds=30),
        )
        await store.register_host(
            "stopped-host",
            now=current,
            lease_expires_at=current + timedelta(seconds=30),
        )
        await store.release_host("stopped-host", now=current + timedelta(seconds=1))
        claimed_at = current + timedelta(seconds=2)
        wrong_session = await store.claim_interrupted_turn(
            "turn-1", "other-session", "active-host", claimed_at=claimed_at
        )
        inactive_host = await store.claim_interrupted_turn(
            "turn-1", "session-1", "stopped-host", claimed_at=claimed_at
        )
        return wrong_session, inactive_host

    assert asyncio.run(scenario()) == (False, False)
