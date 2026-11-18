import asyncio
import sqlite3
import stat
from pathlib import Path

import pytest

from hostpilot.persistence import SQLiteCancellationStore
from hostpilot.skills.compensation import (
    CancellationSnapshot,
    CompensationRecord,
    CompensationStatus,
)
from hostpilot.skills.runtime import SkillSideEffect
from hostpilot.skills.yaml_registry import SideEffectLevel


def test_cancellation_store_persists_sanitized_metadata(tmp_path: Path) -> None:
    database_path = tmp_path / "private" / "hostpilot.sqlite3"
    snapshot = CancellationSnapshot(
        request_id="request-1",
        scope="business/edit-file",
        uncertain_operations=("write_text_file",),
        side_effects=(
            SkillSideEffect(
                "write_text_file",
                SideEffectLevel.REVERSIBLE,
                "idempotency-key",
                "sensitive-backup-token",
            ),
        ),
        compensation_records=(
            CompensationRecord(
                "write_text_file",
                "idempotency-key",
                CompensationStatus.COMPLETED,
            ),
        ),
    )

    async def scenario() -> tuple[str, tuple]:
        event_id = await SQLiteCancellationStore(database_path).record(snapshot)
        entries = await SQLiteCancellationStore(database_path).list_recent()
        return event_id, entries

    event_id, entries = asyncio.run(scenario())

    assert entries[0].event_id == event_id
    assert entries[0].request_id == "request-1"
    assert entries[0].uncertain_operations == ("write_text_file",)
    assert entries[0].side_effects[0]["action"] == "write_text_file"
    assert entries[0].side_effects[0]["compensation_handle_available"] is True
    assert "compensation_handle" not in entries[0].side_effects[0]
    with sqlite3.connect(database_path) as connection:
        stored_values = " ".join(
            str(row)
            for row in connection.execute("SELECT * FROM execution_cancellations").fetchall()
        )
    assert "sensitive-backup-token" not in stored_values
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(database_path.parent.stat().st_mode) == 0o700


def test_cancellation_store_bounds_history_limit(tmp_path: Path) -> None:
    store = SQLiteCancellationStore(tmp_path / "hostpilot.sqlite3")

    with pytest.raises(ValueError, match="between 1 and 1000"):
        asyncio.run(store.list_recent(limit=0))
