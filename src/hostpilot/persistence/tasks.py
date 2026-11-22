"""SQLite persistence for Runtime TaskRuns and successful Plan task results."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from hostpilot.persistence.sqlite_path import prepare_sqlite_database_path
from hostpilot.task_results import (
    CompletedPlanTaskResult,
    FailedPlanTaskSummary,
    StoredPlanTaskFailure,
    StoredPlanTaskResult,
)

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_MAX_RESULT_BYTES = 65_536
_MAX_RESULT_HISTORY = 32
_MAX_RESULT_HISTORY_BYTES = 65_536
_MAX_FAILURE_HISTORY = 16
_MAX_FAILURE_HISTORY_BYTES = 16_384
_SAFE_CACHE_VALUE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


@dataclass(frozen=True)
class StoredTaskRun:
    """Persisted metadata for one Plan task invocation."""

    task_run_id: str
    task_id: str
    turn_id: str
    session_id: str
    runtime_host_id: str
    state: str
    started_at: datetime
    finished_at: datetime | None


class SQLiteTaskStore:
    """Persist TaskRun lifecycle metadata and Session-scoped successful outputs."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()
        self._initialize_lock = asyncio.Lock()
        self._initialized = False

    async def initialize(self) -> None:
        """Create TaskRun lifecycle and Plan result tables."""
        async with self._initialize_lock:
            if self._initialized:
                return
            prepare_sqlite_database_path(self.database_path)
            async with aiosqlite.connect(self.database_path) as connection:
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_task_runs (
                        task_run_id TEXT PRIMARY KEY,
                        task_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        runtime_host_id TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (
                            state IN ('running', 'completed', 'failed', 'cancelled', 'interrupted')
                        ),
                        started_at TEXT NOT NULL,
                        finished_at TEXT,
                        FOREIGN KEY (runtime_host_id) REFERENCES runtime_hosts(host_id)
                    )
                    """
                )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_plan_task_results (
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
                result_columns_cursor = await connection.execute(
                    "PRAGMA table_info(runtime_plan_task_results)"
                )
                result_columns = {str(row[1]) for row in await result_columns_cursor.fetchall()}
                if "task_fingerprint" not in result_columns:
                    await connection.execute(
                        "ALTER TABLE runtime_plan_task_results ADD COLUMN task_fingerprint TEXT"
                    )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_plan_task_failures (
                        task_run_id TEXT PRIMARY KEY,
                        task_fingerprint TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        description TEXT NOT NULL,
                        input_json TEXT NOT NULL,
                        error_code TEXT NOT NULL,
                        side_effect_count INTEGER NOT NULL,
                        finished_at TEXT NOT NULL
                    )
                    """
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_plan_task_results_session_time "
                    "ON runtime_plan_task_results(session_id, finished_at)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_plan_task_results_fingerprint "
                    "ON runtime_plan_task_results(session_id, task_fingerprint, finished_at)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_plan_task_failures_session_time "
                    "ON runtime_plan_task_failures(session_id, finished_at)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_task_runs_host_state "
                    "ON runtime_task_runs(runtime_host_id, state)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_task_runs_turn "
                    "ON runtime_task_runs(turn_id, started_at)"
                )
                await connection.commit()
            os.chmod(self.database_path, 0o600)
            self._initialized = True

    async def create_task_run(
        self,
        *,
        task_run_id: str,
        task_id: str,
        turn_id: str,
        session_id: str,
        runtime_host_id: str,
        started_at: datetime,
    ) -> None:
        """Create a running record only under a live Host lease."""
        for value, label in (
            (task_run_id, "TaskRun"),
            (task_id, "Task"),
            (turn_id, "Turn"),
            (session_id, "Session"),
            (runtime_host_id, "Runtime Host"),
        ):
            _validate_id(value, label)
        started_value = _timestamp(started_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                INSERT INTO runtime_task_runs (
                    task_run_id, task_id, turn_id, session_id, runtime_host_id,
                    state, started_at
                )
                SELECT ?, ?, ?, ?, host_id, 'running', ? FROM runtime_hosts
                WHERE host_id = ? AND status = 'active' AND lease_expires_at > ?
                """,
                (
                    task_run_id,
                    task_id,
                    turn_id,
                    session_id,
                    started_value,
                    runtime_host_id,
                    started_value,
                ),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise RuntimeError("Cannot start a TaskRun without an active Runtime Host lease.")

    async def finish_task_run(
        self,
        task_run_id: str,
        *,
        runtime_host_id: str,
        state: str,
        finished_at: datetime,
        completed_result: CompletedPlanTaskResult | None = None,
        failure_summary: FailedPlanTaskSummary | None = None,
    ) -> None:
        """Set one terminal TaskRun state, scoped to its owning Host."""
        if state not in _TERMINAL_STATES - {"interrupted"}:
            raise ValueError("TaskRun state must be a supported terminal state.")
        if completed_result is not None and state != "completed":
            raise ValueError("A Plan task result can be persisted only for a completed TaskRun.")
        if failure_summary is not None and state != "failed":
            raise ValueError(
                "A Plan task failure summary can be persisted only for a failed TaskRun."
            )
        if completed_result is not None and failure_summary is not None:
            raise ValueError(
                "A TaskRun cannot persist both a successful result and failure summary."
            )
        finished_value = _timestamp(finished_at)
        serialized_result = (
            _serialize_task_result(completed_result) if completed_result is not None else None
        )
        serialized_failure = (
            _serialize_task_failure(failure_summary) if failure_summary is not None else None
        )
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                """
                UPDATE runtime_task_runs SET state = ?, finished_at = ?
                WHERE task_run_id = ? AND runtime_host_id = ? AND state = 'running'
                """,
                (state, finished_value, task_run_id, runtime_host_id),
            )
            if cursor.rowcount != 1:
                raise ValueError("Running TaskRun was not found for this Runtime Host.")
            if serialized_result is not None:
                task_cursor = await connection.execute(
                    "SELECT task_id, turn_id, session_id FROM runtime_task_runs "
                    "WHERE task_run_id = ? AND runtime_host_id = ?",
                    (task_run_id, runtime_host_id),
                )
                task_run = await task_cursor.fetchone()
                if task_run is None:
                    raise ValueError("Completed TaskRun was not found for this Runtime Host.")
                await connection.execute(
                    """
                    INSERT INTO runtime_plan_task_results (
                        task_run_id, task_fingerprint, task_id, turn_id, session_id, description,
                        input_json, depends_on_json, output_json, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_run_id,
                        serialized_result[0],
                        str(task_run["task_id"]),
                        str(task_run["turn_id"]),
                        str(task_run["session_id"]),
                        *serialized_result[1:],
                        finished_value,
                    ),
                )
            if serialized_failure is not None:
                task_cursor = await connection.execute(
                    "SELECT task_id, turn_id, session_id FROM runtime_task_runs "
                    "WHERE task_run_id = ? AND runtime_host_id = ?",
                    (task_run_id, runtime_host_id),
                )
                task_run = await task_cursor.fetchone()
                if task_run is None:
                    raise ValueError("Failed TaskRun was not found for this Runtime Host.")
                await connection.execute(
                    """
                    INSERT INTO runtime_plan_task_failures (
                        task_run_id, task_fingerprint, task_id, turn_id, session_id,
                        description, input_json, error_code, side_effect_count, finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task_run_id,
                        *serialized_failure[:1],
                        str(task_run["task_id"]),
                        str(task_run["turn_id"]),
                        str(task_run["session_id"]),
                        *serialized_failure[1:],
                        finished_value,
                    ),
                )
            await connection.commit()

    async def interrupt_inactive_host_tasks(self, *, now: datetime) -> int:
        """Interrupt running tasks owned by expired or cleanly stopped Hosts."""
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                UPDATE runtime_task_runs SET state = 'interrupted', finished_at = ?
                WHERE state = 'running' AND runtime_host_id IN (
                    SELECT host_id FROM runtime_hosts WHERE status IN ('expired', 'stopped')
                )
                """,
                (now_value,),
            )
            await connection.commit()
        return cursor.rowcount

    async def list_task_runs(self, *, turn_id: str | None = None) -> tuple[StoredTaskRun, ...]:
        """Return TaskRun metadata, optionally scoped to a Turn."""
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            if turn_id is None:
                cursor = await connection.execute(
                    """
                    SELECT task_run_id, task_id, turn_id, session_id, runtime_host_id,
                           state, started_at, finished_at
                    FROM runtime_task_runs ORDER BY started_at, task_run_id
                    """
                )
            else:
                cursor = await connection.execute(
                    """
                    SELECT task_run_id, task_id, turn_id, session_id, runtime_host_id,
                           state, started_at, finished_at
                    FROM runtime_task_runs WHERE turn_id = ?
                    ORDER BY started_at, task_run_id
                    """,
                    (turn_id,),
                )
            rows = await cursor.fetchall()
        return tuple(_stored_task_run(row) for row in rows)

    async def list_completed_task_results(
        self, *, session_id: str, exclude_turn_id: str, limit: int
    ) -> tuple[StoredPlanTaskResult, ...]:
        """Return recent successful results for one Session, oldest first in the window."""
        _validate_id(session_id, "Session")
        _validate_id(exclude_turn_id, "Turn")
        if limit < 1:
            raise ValueError("Task result history limit must be positive.")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                """
                SELECT r.task_run_id, r.task_id, r.turn_id, r.session_id, r.description,
                       r.task_fingerprint, r.input_json, r.depends_on_json,
                       r.output_json, r.finished_at
                FROM runtime_plan_task_results AS r
                JOIN runtime_task_runs AS t ON t.task_run_id = r.task_run_id
                WHERE r.session_id = ? AND r.turn_id != ? AND t.state = 'completed'
                ORDER BY r.finished_at DESC, r.task_run_id DESC
                LIMIT ?
                """,
                (session_id, exclude_turn_id, min(limit, _MAX_RESULT_HISTORY)),
            )
            rows = list(await cursor.fetchall())
        selected: list[StoredPlanTaskResult] = []
        total_bytes = 0
        for row in rows:
            result_bytes = sum(
                len(str(row[column]).encode("utf-8"))
                for column in (
                    "description",
                    "input_json",
                    "depends_on_json",
                    "output_json",
                )
            )
            if total_bytes + result_bytes > _MAX_RESULT_HISTORY_BYTES:
                break
            selected.append(_stored_task_result(row))
            total_bytes += result_bytes
        return tuple(reversed(selected))

    async def find_completed_task_result(
        self, *, session_id: str, exclude_turn_id: str, task_fingerprint: str
    ) -> StoredPlanTaskResult | None:
        """Return the newest successful result matching one exact Session fingerprint."""
        _validate_id(session_id, "Session")
        _validate_id(exclude_turn_id, "Turn")
        _validate_fingerprint(task_fingerprint)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                """
                SELECT r.task_run_id, r.task_id, r.turn_id, r.session_id,
                       r.task_fingerprint, r.description, r.input_json,
                       r.depends_on_json, r.output_json, r.finished_at
                FROM runtime_plan_task_results AS r
                JOIN runtime_task_runs AS t ON t.task_run_id = r.task_run_id
                WHERE r.session_id = ? AND r.turn_id != ?
                  AND r.task_fingerprint = ? AND t.state = 'completed'
                ORDER BY r.finished_at DESC, r.task_run_id DESC
                LIMIT 1
                """,
                (session_id, exclude_turn_id, task_fingerprint),
            )
            row = await cursor.fetchone()
        return _stored_task_result(row) if row is not None else None

    async def list_recent_task_failures(
        self, *, session_id: str, exclude_turn_id: str, limit: int
    ) -> tuple[StoredPlanTaskFailure, ...]:
        """Return bounded safe failure summaries, never failed output or exception text."""
        _validate_id(session_id, "Session")
        _validate_id(exclude_turn_id, "Turn")
        if limit < 1:
            raise ValueError("Task failure history limit must be positive.")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                """
                SELECT f.task_run_id, f.task_id, f.turn_id, f.session_id,
                       f.task_fingerprint, f.description, f.input_json,
                       f.error_code, f.side_effect_count, f.finished_at
                FROM runtime_plan_task_failures AS f
                JOIN runtime_task_runs AS t ON t.task_run_id = f.task_run_id
                WHERE f.session_id = ? AND f.turn_id != ? AND t.state = 'failed'
                ORDER BY f.finished_at DESC, f.task_run_id DESC LIMIT ?
                """,
                (session_id, exclude_turn_id, min(limit, _MAX_FAILURE_HISTORY)),
            )
            rows = list(await cursor.fetchall())
        selected: list[StoredPlanTaskFailure] = []
        total_bytes = 0
        for row in rows:
            size = sum(
                len(str(row[column]).encode("utf-8"))
                for column in ("description", "input_json", "error_code")
            )
            if total_bytes + size > _MAX_FAILURE_HISTORY_BYTES:
                break
            selected.append(_stored_task_failure(row))
            total_bytes += size
        return tuple(reversed(selected))


def _validate_id(value: str, label: str) -> None:
    if not value or value.strip() != value:
        raise ValueError(f"{label} ID must be non-empty and trimmed.")


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Task lifecycle timestamps must be timezone-aware.")
    return value.astimezone(UTC).isoformat()


def _stored_task_run(row: sqlite3.Row) -> StoredTaskRun:
    return StoredTaskRun(
        task_run_id=str(row["task_run_id"]),
        task_id=str(row["task_id"]),
        turn_id=str(row["turn_id"]),
        session_id=str(row["session_id"]),
        runtime_host_id=str(row["runtime_host_id"]),
        state=str(row["state"]),
        started_at=datetime.fromisoformat(str(row["started_at"])),
        finished_at=(
            datetime.fromisoformat(str(row["finished_at"]))
            if row["finished_at"] is not None
            else None
        ),
    )


def _serialize_task_result(
    result: CompletedPlanTaskResult,
) -> tuple[str, str, str, str, str]:
    _validate_fingerprint(result.task_fingerprint)
    if not result.description.strip():
        raise ValueError("Completed Plan task description cannot be blank.")
    if not isinstance(result.input, Mapping):
        raise ValueError("Completed Plan task input must be a mapping.")
    try:
        input_json = json.dumps(dict(result.input), ensure_ascii=False, allow_nan=False)
        depends_on_json = json.dumps(list(result.depends_on), ensure_ascii=False)
        output_json = json.dumps(result.output, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Completed Plan task result must contain JSON-compatible values."
        ) from error
    total_bytes = len(result.description.encode("utf-8")) + sum(
        len(value.encode("utf-8")) for value in (input_json, depends_on_json, output_json)
    )
    if total_bytes > _MAX_RESULT_BYTES:
        raise ValueError("Completed Plan task result exceeds the 65536-byte persistence limit.")
    return result.task_fingerprint, result.description, input_json, depends_on_json, output_json


def _stored_task_result(row: sqlite3.Row) -> StoredPlanTaskResult:
    task_input: Any = json.loads(str(row["input_json"]))
    dependencies: Any = json.loads(str(row["depends_on_json"]))
    if not isinstance(task_input, dict) or not isinstance(dependencies, list):
        raise ValueError("Stored Plan task result has an invalid JSON shape.")
    return StoredPlanTaskResult(
        task_run_id=str(row["task_run_id"]),
        task_id=str(row["task_id"]),
        turn_id=str(row["turn_id"]),
        session_id=str(row["session_id"]),
        task_fingerprint=str(row["task_fingerprint"] or ""),
        description=str(row["description"]),
        input=task_input,
        depends_on=tuple(str(item) for item in dependencies),
        output=json.loads(str(row["output_json"])),
        finished_at=datetime.fromisoformat(str(row["finished_at"])),
    )


def _serialize_task_failure(
    failure: FailedPlanTaskSummary,
) -> tuple[str, str, str, str, int]:
    _validate_fingerprint(failure.task_fingerprint)
    if not failure.description.strip() or not isinstance(failure.input, Mapping):
        raise ValueError("Failed Plan task summary must have a description and mapping input.")
    if failure.input:
        raise ValueError("Failed Plan task summaries cannot persist task input.")
    if not _SAFE_CACHE_VALUE.fullmatch(failure.error_code):
        raise ValueError("Failed Plan task error code contains unsafe characters.")
    if failure.side_effect_count < 0:
        raise ValueError("Failed Plan task side effect count cannot be negative.")
    try:
        input_json = json.dumps(dict(failure.input), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("Failed Plan task input must contain JSON-compatible values.") from error
    values = (failure.task_fingerprint, failure.description, input_json, failure.error_code)
    if sum(len(value.encode("utf-8")) for value in values) > _MAX_FAILURE_HISTORY_BYTES:
        raise ValueError("Failed Plan task summary exceeds the persistence limit.")
    return (
        failure.task_fingerprint,
        failure.description,
        input_json,
        failure.error_code,
        failure.side_effect_count,
    )


def _stored_task_failure(row: sqlite3.Row) -> StoredPlanTaskFailure:
    task_input: Any = json.loads(str(row["input_json"]))
    if not isinstance(task_input, dict):
        raise ValueError("Stored Plan task failure has an invalid input shape.")
    return StoredPlanTaskFailure(
        task_run_id=str(row["task_run_id"]),
        task_id=str(row["task_id"]),
        turn_id=str(row["turn_id"]),
        session_id=str(row["session_id"]),
        task_fingerprint=str(row["task_fingerprint"]),
        description=str(row["description"]),
        input=task_input,
        error_code=str(row["error_code"]),
        side_effect_count=int(row["side_effect_count"]),
        finished_at=datetime.fromisoformat(str(row["finished_at"])),
    )


def _validate_fingerprint(value: str) -> None:
    if not value or not re.fullmatch(r"v[0-9]+:[0-9a-f]{64}", value):
        raise ValueError("Plan task fingerprint must be a versioned SHA-256 value.")
