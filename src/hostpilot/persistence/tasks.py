"""SQLite persistence for Runtime TaskRuns and successful Plan task results."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import aiosqlite

from hostpilot.persistence.sqlite_path import prepare_sqlite_database_path
from hostpilot.task_results import (
    CompletedPlanTaskResult,
    FailedPlanTaskSummary,
    PlanRunSnapshot,
    PlanRunState,
    PlanTaskExecutionSummary,
    PlanTaskToolUsage,
    StoredPlanTaskFailure,
    StoredPlanTaskResult,
    ToolInvocationRecord,
    ToolInvocationState,
)

_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_PLAN_RUN_STATES = frozenset(
    {"running", "interrupted", "awaiting_reconciliation", "completed", "failed", "cancelled"}
)
_PLAN_RUN_TRANSITIONS = {
    "running": {
        "running",
        "interrupted",
        "awaiting_reconciliation",
        "completed",
        "failed",
        "cancelled",
    },
    "interrupted": {"interrupted", "running", "awaiting_reconciliation"},
    "awaiting_reconciliation": {"awaiting_reconciliation", "interrupted", "running"},
    "completed": {"completed"},
    "failed": {"failed"},
    "cancelled": {"cancelled"},
}
_INVOCATION_STATES = frozenset(
    {"prepared", "completed", "awaiting_reconciliation", "user_reported", "not_executed"}
)
_INVOCATION_TRANSITIONS = {
    "prepared": {"completed", "awaiting_reconciliation", "user_reported", "not_executed"},
    "awaiting_reconciliation": {"completed", "user_reported", "not_executed"},
    "completed": {"completed"},
    "user_reported": {"user_reported"},
    "not_executed": {"not_executed", "prepared"},
}
_MAX_RESULT_BYTES = 65_536
_MAX_RESULT_HISTORY = 128
_MAX_RESULT_HISTORY_CHARS = 262_144
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
    """Persist Plan recovery state, TaskRun metadata, and Session-scoped results."""

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
                    CREATE TABLE IF NOT EXISTS runtime_plan_runs (
                        plan_run_id TEXT PRIMARY KEY,
                        turn_id TEXT NOT NULL,
                        session_id TEXT NOT NULL,
                        runtime_host_id TEXT NOT NULL,
                        goal TEXT NOT NULL,
                        state TEXT NOT NULL CHECK (state IN (
                            'running', 'interrupted', 'awaiting_reconciliation',
                            'completed', 'failed', 'cancelled'
                        )),
                        snapshot_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        resume_claim_host_id TEXT,
                        FOREIGN KEY (runtime_host_id) REFERENCES runtime_hosts(host_id)
                    )
                    """
                )
                await connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS runtime_tool_invocations (
                        invocation_id TEXT PRIMARY KEY,
                        plan_run_id TEXT,
                        session_id TEXT NOT NULL,
                        turn_id TEXT NOT NULL,
                        runtime_host_id TEXT NOT NULL,
                        task_run_id TEXT,
                        task_id TEXT NOT NULL,
                        call_index INTEGER NOT NULL CHECK (call_index > 0),
                        tool_id TEXT NOT NULL,
                        retry_class TEXT NOT NULL,
                        idempotency_key TEXT,
                        request_fingerprint TEXT NOT NULL,
                        arguments_json TEXT NOT NULL,
                        idempotency_expires_at TEXT,
                        state TEXT NOT NULL CHECK (state IN (
                            'prepared', 'completed', 'awaiting_reconciliation',
                            'user_reported', 'not_executed'
                        )),
                        created_at TEXT NOT NULL,
                        finished_at TEXT,
                        reported_result TEXT,
                        UNIQUE (plan_run_id, task_id, call_index),
                        FOREIGN KEY (plan_run_id) REFERENCES runtime_plan_runs(plan_run_id)
                    )
                    """
                )
                invocation_columns_cursor = await connection.execute(
                    "PRAGMA table_info(runtime_tool_invocations)"
                )
                invocation_columns = {
                    str(row[1]): row for row in await invocation_columns_cursor.fetchall()
                }
                if "turn_id" not in invocation_columns:
                    await connection.execute(
                        "ALTER TABLE runtime_tool_invocations ADD COLUMN turn_id TEXT"
                    )
                if "idempotency_expires_at" not in invocation_columns:
                    await connection.execute(
                        "ALTER TABLE runtime_tool_invocations "
                        "ADD COLUMN idempotency_expires_at TEXT"
                    )
                if "reported_result" not in invocation_columns:
                    await connection.execute(
                        "ALTER TABLE runtime_tool_invocations ADD COLUMN reported_result TEXT"
                    )
                if int(invocation_columns["plan_run_id"][3]) != 0:
                    await connection.execute(
                        "DROP INDEX IF EXISTS runtime_tool_invocations_plan_state"
                    )
                    await connection.execute(
                        "ALTER TABLE runtime_tool_invocations RENAME TO "
                        "runtime_tool_invocations_legacy"
                    )
                    await connection.execute(
                        """
                        CREATE TABLE runtime_tool_invocations (
                            invocation_id TEXT PRIMARY KEY,
                            plan_run_id TEXT,
                            session_id TEXT NOT NULL,
                            turn_id TEXT NOT NULL,
                            runtime_host_id TEXT NOT NULL,
                            task_run_id TEXT,
                            task_id TEXT NOT NULL,
                            call_index INTEGER NOT NULL CHECK (call_index > 0),
                            tool_id TEXT NOT NULL,
                            retry_class TEXT NOT NULL,
                            idempotency_key TEXT,
                            request_fingerprint TEXT NOT NULL,
                            arguments_json TEXT NOT NULL,
                            idempotency_expires_at TEXT,
                            state TEXT NOT NULL CHECK (state IN (
                                'prepared', 'completed', 'awaiting_reconciliation',
                                'user_reported', 'not_executed'
                            )),
                            created_at TEXT NOT NULL,
                            finished_at TEXT,
                            reported_result TEXT,
                            UNIQUE (plan_run_id, task_id, call_index),
                            FOREIGN KEY (plan_run_id) REFERENCES runtime_plan_runs(plan_run_id)
                        )
                        """
                    )
                    await connection.execute(
                        """
                        INSERT INTO runtime_tool_invocations (
                            invocation_id, plan_run_id, session_id, turn_id, runtime_host_id,
                            task_run_id, task_id, call_index, tool_id, retry_class,
                            idempotency_key, request_fingerprint, arguments_json,
                            idempotency_expires_at, state, created_at, finished_at,
                            reported_result
                        )
                        SELECT old.invocation_id, old.plan_run_id, old.session_id,
                            plan.turn_id, old.runtime_host_id, old.task_run_id, old.task_id,
                            old.call_index, old.tool_id, old.retry_class, old.idempotency_key,
                            old.request_fingerprint, old.arguments_json,
                            old.idempotency_expires_at, old.state, old.created_at,
                            old.finished_at, NULL
                        FROM runtime_tool_invocations_legacy AS old
                        JOIN runtime_plan_runs AS plan ON plan.plan_run_id = old.plan_run_id
                        """
                    )
                    await connection.execute("DROP TABLE runtime_tool_invocations_legacy")
                await connection.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS runtime_tool_invocations_chat_order "
                    "ON runtime_tool_invocations(session_id, turn_id, task_id, call_index) "
                    "WHERE plan_run_id IS NULL"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_plan_runs_session_state "
                    "ON runtime_plan_runs(session_id, state, updated_at)"
                )
                await connection.execute(
                    "CREATE INDEX IF NOT EXISTS runtime_tool_invocations_plan_state "
                    "ON runtime_tool_invocations(plan_run_id, state, created_at)"
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
                        execution_summary_json TEXT,
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
                if "execution_summary_json" not in result_columns:
                    await connection.execute(
                        "ALTER TABLE runtime_plan_task_results "
                        "ADD COLUMN execution_summary_json TEXT"
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

    async def create_plan_run(
        self,
        *,
        plan_run_id: str,
        turn_id: str,
        session_id: str,
        runtime_host_id: str,
        goal: str,
        snapshot: Mapping[str, Any],
        started_at: datetime,
    ) -> None:
        """Persist the first validated snapshot while the Host lease is live."""
        for value, label in (
            (plan_run_id, "PlanRun"),
            (turn_id, "Turn"),
            (session_id, "Session"),
            (runtime_host_id, "Runtime Host"),
        ):
            _validate_id(value, label)
        if not goal.strip():
            raise ValueError("PlanRun goal cannot be blank.")
        snapshot_json = _serialize_plan_snapshot(snapshot)
        created_value = _timestamp(started_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                INSERT INTO runtime_plan_runs (
                    plan_run_id, turn_id, session_id, runtime_host_id, goal, state,
                    snapshot_json, created_at, updated_at
                )
                SELECT ?, ?, ?, host_id, ?, 'running', ?, ?, ? FROM runtime_hosts
                WHERE host_id = ? AND status = 'active' AND lease_expires_at > ?
                """,
                (
                    plan_run_id,
                    turn_id,
                    session_id,
                    goal,
                    snapshot_json,
                    created_value,
                    created_value,
                    runtime_host_id,
                    created_value,
                ),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise RuntimeError("Cannot start a PlanRun without an active Runtime Host lease.")

    async def update_plan_run(
        self,
        plan_run_id: str,
        *,
        runtime_host_id: str,
        state: PlanRunState,
        snapshot: Mapping[str, Any],
        updated_at: datetime,
    ) -> None:
        """Update one PlanRun snapshot while enforcing its lifecycle transitions."""
        _validate_id(plan_run_id, "PlanRun")
        _validate_id(runtime_host_id, "Runtime Host")
        if state not in _PLAN_RUN_STATES:
            raise ValueError("PlanRun state is unsupported.")
        snapshot_json = _serialize_plan_snapshot(snapshot)
        updated_value = _timestamp(updated_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT state FROM runtime_plan_runs WHERE plan_run_id = ? "
                "AND (runtime_host_id = ? OR resume_claim_host_id = ?)",
                (plan_run_id, runtime_host_id, runtime_host_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await connection.rollback()
                raise ValueError("PlanRun was not found for this Runtime Host.")
            current_state = str(row[0])
            if state not in _PLAN_RUN_TRANSITIONS[current_state]:
                await connection.rollback()
                raise ValueError(f"PlanRun cannot transition from {current_state} to {state}.")
            await connection.execute(
                "UPDATE runtime_plan_runs SET state = ?, snapshot_json = ?, updated_at = ? "
                "WHERE plan_run_id = ? AND (runtime_host_id = ? OR resume_claim_host_id = ?)",
                (
                    state,
                    snapshot_json,
                    updated_value,
                    plan_run_id,
                    runtime_host_id,
                    runtime_host_id,
                ),
            )
            await connection.commit()

    async def get_plan_run(self, plan_run_id: str) -> PlanRunSnapshot | None:
        """Load a persisted snapshot by its stable PlanRun ID."""
        _validate_id(plan_run_id, "PlanRun")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                "SELECT * FROM runtime_plan_runs WHERE plan_run_id = ?", (plan_run_id,)
            )
            row = await cursor.fetchone()
        return _stored_plan_run(row) if row is not None else None

    async def list_recoverable_plan_runs(self, session_id: str) -> tuple[PlanRunSnapshot, ...]:
        """List interrupted or unresolved PlanRuns for the requested Session only."""
        _validate_id(session_id, "Session")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                "SELECT * FROM runtime_plan_runs WHERE session_id = ? "
                "AND state IN ('interrupted', 'awaiting_reconciliation') "
                "ORDER BY updated_at, plan_run_id",
                (session_id,),
            )
            rows = await cursor.fetchall()
        return tuple(_stored_plan_run(row) for row in rows)

    async def claim_plan_run(
        self, plan_run_id: str, *, runtime_host_id: str, now: datetime
    ) -> bool:
        """Atomically claim a recoverable PlanRun under one active Host lease."""
        _validate_id(plan_run_id, "PlanRun")
        _validate_id(runtime_host_id, "Runtime Host")
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                """
                UPDATE runtime_plan_runs
                SET resume_claim_host_id = ?, runtime_host_id = ?
                WHERE plan_run_id = ?
                  AND state IN ('interrupted', 'awaiting_reconciliation')
                  AND (resume_claim_host_id IS NULL OR resume_claim_host_id = ? OR
                       resume_claim_host_id IN (
                           SELECT host_id FROM runtime_hosts
                           WHERE status != 'active' OR lease_expires_at <= ?
                       ))
                  AND EXISTS (
                      SELECT 1 FROM runtime_hosts WHERE host_id = ?
                        AND status = 'active' AND lease_expires_at > ?
                  )
                """,
                (
                    runtime_host_id,
                    runtime_host_id,
                    plan_run_id,
                    runtime_host_id,
                    now_value,
                    runtime_host_id,
                    now_value,
                ),
            )
            if cursor.rowcount == 1:
                await connection.execute(
                    "UPDATE runtime_tool_invocations SET runtime_host_id = ? WHERE plan_run_id = ?",
                    (runtime_host_id, plan_run_id),
                )
            await connection.commit()
        return cursor.rowcount == 1

    async def release_plan_run_claim(self, plan_run_id: str, *, runtime_host_id: str) -> None:
        """Release only the resume claim owned by the given Host."""
        _validate_id(plan_run_id, "PlanRun")
        _validate_id(runtime_host_id, "Runtime Host")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                "UPDATE runtime_plan_runs SET resume_claim_host_id = NULL "
                "WHERE plan_run_id = ? AND resume_claim_host_id = ?",
                (plan_run_id, runtime_host_id),
            )
            await connection.commit()

    async def record_tool_invocation(
        self,
        *,
        invocation_id: str,
        plan_run_id: str,
        task_run_id: str,
        task_id: str,
        call_index: int,
        tool_id: str,
        retry_class: str,
        idempotency_key: str | None,
        request_fingerprint: str,
        arguments_json: str,
        created_at: datetime,
        runtime_host_id: str,
        idempotency_window_seconds: int | None = None,
    ) -> None:
        """Journal exact invocation data before a tool crosses dispatch boundary."""
        for value, label in (
            (invocation_id, "Invocation"),
            (plan_run_id, "PlanRun"),
            (task_run_id, "TaskRun"),
            (task_id, "Task"),
            (tool_id, "Tool"),
            (request_fingerprint, "Request fingerprint"),
            (runtime_host_id, "Runtime Host"),
        ):
            _validate_id(value, label)
        _validate_invocation_values(
            call_index,
            retry_class,
            idempotency_key,
            arguments_json,
            idempotency_window_seconds,
        )
        created_value = _timestamp(created_at)
        expires_value = _idempotency_expiry(created_at, idempotency_window_seconds)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                """
                INSERT INTO runtime_tool_invocations (
                    invocation_id, plan_run_id, session_id, turn_id, runtime_host_id,
                    task_run_id, task_id, call_index, tool_id, retry_class, idempotency_key,
                    request_fingerprint, arguments_json, idempotency_expires_at, state, created_at
                )
                SELECT ?, p.plan_run_id, p.session_id, p.turn_id, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, 'prepared', ?
                FROM runtime_plan_runs AS p
                WHERE p.plan_run_id = ? AND p.runtime_host_id = ?
                    AND p.state IN ('running', 'interrupted', 'awaiting_reconciliation')
                """,
                (
                    invocation_id,
                    runtime_host_id,
                    task_run_id,
                    task_id,
                    call_index,
                    tool_id,
                    retry_class,
                    idempotency_key,
                    request_fingerprint,
                    arguments_json,
                    expires_value,
                    created_value,
                    plan_run_id,
                    runtime_host_id,
                ),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise RuntimeError("Cannot journal a tool invocation outside its owning PlanRun.")

    async def record_runtime_tool_invocation(
        self,
        *,
        invocation_id: str,
        session_id: str,
        turn_id: str,
        runtime_host_id: str,
        task_run_id: str | None,
        task_id: str,
        call_index: int,
        tool_id: str,
        retry_class: str,
        idempotency_key: str | None,
        request_fingerprint: str,
        arguments_json: str,
        idempotency_window_seconds: int | None,
        created_at: datetime,
    ) -> None:
        """Journal a Chat or Plan invocation without requiring a PlanRun row."""
        for value, label in (
            (invocation_id, "Invocation"),
            (session_id, "Session"),
            (turn_id, "Turn"),
            (runtime_host_id, "Runtime Host"),
            (task_run_id, "TaskRun"),
            (task_id, "Task"),
            (tool_id, "Tool"),
            (request_fingerprint, "Request fingerprint"),
        ):
            if value is not None:
                _validate_id(value, label)
        _validate_invocation_values(
            call_index,
            retry_class,
            idempotency_key,
            arguments_json,
            idempotency_window_seconds,
        )
        created_value = _timestamp(created_at)
        expires_value = _idempotency_expiry(created_at, idempotency_window_seconds)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            await connection.execute("BEGIN IMMEDIATE")
            existing_cursor = await connection.execute(
                "SELECT * FROM runtime_tool_invocations WHERE invocation_id = ? OR "
                "(plan_run_id IS NULL AND session_id = ? AND turn_id = ? "
                "AND task_id = ? AND call_index = ?)",
                (invocation_id, session_id, turn_id, task_id, call_index),
            )
            existing = await existing_cursor.fetchone()
            if existing is not None:
                host_cursor = await connection.execute(
                    "SELECT 1 FROM runtime_hosts WHERE host_id = ? AND status = 'active' "
                    "AND lease_expires_at > ?",
                    (runtime_host_id, created_value),
                )
                turn_cursor = await connection.execute(
                    "SELECT 1 FROM runtime_turns WHERE turn_id = ? AND session_id = ? "
                    "AND runtime_host_id = ? AND state = 'running'",
                    (turn_id, session_id, runtime_host_id),
                )
                original_created_at = str(existing["created_at"])
                original_expiry = _idempotency_expiry(
                    datetime.fromisoformat(original_created_at), idempotency_window_seconds
                )
                expected_expiry = original_expiry
                matches = (
                    existing["invocation_id"] == invocation_id
                    and existing["session_id"] == session_id
                    and existing["turn_id"] == turn_id
                    and existing["runtime_host_id"] == runtime_host_id
                    and existing["task_run_id"] == task_run_id
                    and existing["task_id"] == task_id
                    and existing["call_index"] == call_index
                    and existing["tool_id"] == tool_id
                    and existing["retry_class"] == retry_class
                    and existing["idempotency_key"] == idempotency_key
                    and existing["request_fingerprint"] == request_fingerprint
                    and existing["arguments_json"] == arguments_json
                    and existing["idempotency_expires_at"] == expected_expiry
                    and existing["state"] == "prepared"
                    and await host_cursor.fetchone() is not None
                    and await turn_cursor.fetchone() is not None
                )
                await connection.commit() if matches else await connection.rollback()
                if not matches:
                    raise ValueError(
                        "Existing prepared invocation does not exactly match recovery."
                    )
                return
            cursor = await connection.execute(
                """
                INSERT INTO runtime_tool_invocations (
                    invocation_id, session_id, turn_id, runtime_host_id, task_run_id,
                    task_id, call_index, tool_id, retry_class, idempotency_key,
                    request_fingerprint, arguments_json, idempotency_expires_at,
                    state, created_at
                )
                SELECT ?, ?, ?, host_id, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'prepared', ?
                FROM runtime_hosts
                WHERE host_id = ? AND status = 'active' AND lease_expires_at > ?
                """,
                (
                    invocation_id,
                    session_id,
                    turn_id,
                    task_run_id,
                    task_id,
                    call_index,
                    tool_id,
                    retry_class,
                    idempotency_key,
                    request_fingerprint,
                    arguments_json,
                    expires_value,
                    created_value,
                    runtime_host_id,
                    created_value,
                ),
            )
            await connection.commit()
        if cursor.rowcount != 1:
            raise RuntimeError("Cannot journal a tool invocation without a live Host lease.")

    async def list_runtime_tool_invocations(
        self, session_id: str, turn_id: str
    ) -> tuple[ToolInvocationRecord, ...]:
        """Load private call records for one Runtime Chat Turn."""
        _validate_id(session_id, "Session")
        _validate_id(turn_id, "Turn")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                "SELECT * FROM runtime_tool_invocations WHERE session_id = ? AND turn_id = ? "
                "AND plan_run_id IS NULL ORDER BY task_id, call_index, invocation_id",
                (session_id, turn_id),
            )
            rows = await cursor.fetchall()
        return tuple(_stored_tool_invocation(row) for row in rows)

    async def claim_runtime_tool_invocations(
        self, session_id: str, turn_id: str, *, runtime_host_id: str, now: datetime
    ) -> bool:
        """Transfer prepared Chat call records after this Host owns the running Turn."""
        for value, label in (
            (session_id, "Session"),
            (turn_id, "Turn"),
            (runtime_host_id, "Runtime Host"),
        ):
            _validate_id(value, label)
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            owner_cursor = await connection.execute(
                "SELECT 1 FROM runtime_turns AS turn JOIN runtime_hosts AS host "
                "ON host.host_id = turn.runtime_host_id WHERE turn.turn_id = ? "
                "AND turn.session_id = ? AND turn.runtime_host_id = ? AND turn.state = 'running' "
                "AND host.status = 'active' AND host.lease_expires_at > ?",
                (turn_id, session_id, runtime_host_id, now_value),
            )
            if await owner_cursor.fetchone() is None:
                await connection.rollback()
                return False
            await connection.execute(
                "UPDATE runtime_tool_invocations SET runtime_host_id = ? "
                "WHERE session_id = ? AND turn_id = ? AND plan_run_id IS NULL "
                "AND state = 'prepared'",
                (runtime_host_id, session_id, turn_id),
            )
            await connection.commit()
        return True

    async def finish_tool_invocation(
        self,
        invocation_id: str,
        *,
        plan_run_id: str | None,
        runtime_host_id: str,
        state: ToolInvocationState,
        finished_at: datetime,
    ) -> None:
        """Record one invocation outcome without allowing unsafe state rewrites."""
        if state not in _INVOCATION_STATES:
            raise ValueError("Tool invocation state is unsupported.")
        finished_value = _timestamp(finished_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "SELECT state FROM runtime_tool_invocations WHERE invocation_id = ? "
                "AND plan_run_id IS ? AND runtime_host_id = ?",
                (invocation_id, plan_run_id, runtime_host_id),
            )
            row = await cursor.fetchone()
            if row is None:
                await connection.rollback()
                raise ValueError("Tool invocation was not found for this Runtime Host.")
            current_state = str(row[0])
            if state not in _INVOCATION_TRANSITIONS[current_state]:
                await connection.rollback()
                raise ValueError(
                    f"Tool invocation cannot transition from {current_state} to {state}."
                )
            await connection.execute(
                "UPDATE runtime_tool_invocations SET state = ?, finished_at = ? "
                "WHERE invocation_id = ? AND plan_run_id IS ? AND runtime_host_id = ?",
                (state, finished_value, invocation_id, plan_run_id, runtime_host_id),
            )
            await connection.commit()

    async def list_tool_invocations(
        self, plan_run_id: str | None = None, *, session_id: str | None = None
    ) -> tuple[ToolInvocationRecord, ...]:
        """Read private invocation records by PlanRun or Session."""
        if (plan_run_id is None) == (session_id is None):
            raise ValueError("Provide exactly one PlanRun ID or Session ID.")
        _validate_id(plan_run_id, "PlanRun") if plan_run_id is not None else None
        _validate_id(session_id, "Session") if session_id is not None else None
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            if plan_run_id is not None:
                cursor = await connection.execute(
                    "SELECT * FROM runtime_tool_invocations WHERE plan_run_id = ? "
                    "ORDER BY task_id, call_index, invocation_id",
                    (plan_run_id,),
                )
            else:
                cursor = await connection.execute(
                    "SELECT * FROM runtime_tool_invocations WHERE session_id = ? "
                    "ORDER BY turn_id, task_id, call_index, invocation_id",
                    (session_id,),
                )
            rows = await cursor.fetchall()
        return tuple(_stored_tool_invocation(row) for row in rows)

    async def resolve_tool_invocation(
        self,
        invocation_id: str,
        *,
        plan_run_id: str,
        runtime_host_id: str,
        state: Literal["user_reported", "not_executed"],
        reported_result: str | None,
        resolved_at: datetime,
    ) -> None:
        """Record human reconciliation without claiming the external state was verified."""
        _validate_id(invocation_id, "Invocation")
        _validate_id(plan_run_id, "PlanRun")
        _validate_id(runtime_host_id, "Runtime Host")
        if state == "user_reported":
            if reported_result is None or not reported_result.strip():
                raise ValueError("User-reported completion requires a non-empty result.")
        elif reported_result is not None:
            raise ValueError("An unexecuted invocation cannot contain a result.")
        resolved_value = _timestamp(resolved_at)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                "UPDATE runtime_tool_invocations SET state = ?, reported_result = ?, "
                "finished_at = ? WHERE invocation_id = ? AND plan_run_id = ? "
                "AND runtime_host_id = ? AND state IN ('prepared', 'awaiting_reconciliation')",
                (
                    state,
                    reported_result,
                    resolved_value,
                    invocation_id,
                    plan_run_id,
                    runtime_host_id,
                ),
            )
            if cursor.rowcount != 1:
                await connection.rollback()
                raise ValueError("Pending tool invocation was not found for this Runtime Host.")
            await connection.commit()

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
        plan_run_id: str | None = None,
        plan_snapshot: Mapping[str, Any] | None = None,
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
        if (plan_run_id is None) != (plan_snapshot is None):
            raise ValueError("PlanRun progress requires both a PlanRun ID and snapshot.")
        if plan_snapshot is not None and (state != "completed" or completed_result is None):
            raise ValueError("PlanRun progress can be checkpointed only with a completed result.")
        finished_value = _timestamp(finished_at)
        plan_snapshot_json = (
            _serialize_plan_snapshot(plan_snapshot) if plan_snapshot is not None else None
        )
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
                        input_json, depends_on_json, output_json, execution_summary_json,
                        finished_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            if serialized_result is not None:
                if plan_run_id is not None:
                    task_cursor = await connection.execute(
                        "SELECT task_id FROM runtime_task_runs WHERE task_run_id = ?",
                        (task_run_id,),
                    )
                    task_run = await task_cursor.fetchone()
                    if task_run is None:
                        raise ValueError("Completed TaskRun was not found for this Runtime Host.")
                    plan_cursor = await connection.execute(
                        "UPDATE runtime_plan_runs SET snapshot_json = ?, updated_at = ? "
                        "WHERE plan_run_id = ? AND runtime_host_id = ? AND state = 'running'",
                        (plan_snapshot_json, finished_value, plan_run_id, runtime_host_id),
                    )
                    if plan_cursor.rowcount != 1:
                        raise ValueError("Running PlanRun was not found for this Runtime Host.")
                    await connection.execute(
                        "UPDATE runtime_tool_invocations SET state = 'completed', finished_at = ? "
                        "WHERE plan_run_id = ? AND task_id = ? AND state = 'prepared'",
                        (finished_value, plan_run_id, str(task_run["task_id"])),
                    )
                else:
                    await connection.execute(
                        "UPDATE runtime_tool_invocations SET state = 'completed', finished_at = ? "
                        "WHERE task_run_id = ? AND state = 'prepared'",
                        (finished_value, task_run_id),
                    )
            await connection.commit()

    async def interrupt_inactive_host_tasks(self, *, now: datetime) -> int:
        """Interrupt running Tasks and PlanRuns owned by inactive Runtime Hosts."""
        now_value = _timestamp(now)
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            cursor = await connection.execute(
                """
                UPDATE runtime_task_runs SET state = 'interrupted', finished_at = ?
                WHERE state = 'running' AND runtime_host_id IN (
                    SELECT host_id FROM runtime_hosts WHERE status IN ('expired', 'stopped')
                )
                """,
                (now_value,),
            )
            await connection.execute(
                "UPDATE runtime_plan_runs SET state = 'interrupted', updated_at = ? "
                "WHERE state = 'running' AND runtime_host_id IN ("
                "SELECT host_id FROM runtime_hosts "
                "WHERE status IN ('expired', 'stopped') OR lease_expires_at <= ?) ",
                (now_value, now_value),
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
        self,
        *,
        session_id: str,
        exclude_turn_id: str,
        limit: int,
        max_chars: int = 65_536,
    ) -> tuple[StoredPlanTaskResult, ...]:
        """Return recent successful results for one Session, oldest first in the window."""
        _validate_id(session_id, "Session")
        _validate_id(exclude_turn_id, "Turn")
        if limit < 1:
            raise ValueError("Task result history limit must be positive.")
        if not 1 <= max_chars <= _MAX_RESULT_HISTORY_CHARS:
            raise ValueError("Task result history character limit is out of range.")
        await self.initialize()
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = sqlite3.Row
            cursor = await connection.execute(
                """
                SELECT r.task_run_id, r.task_id, r.turn_id, r.session_id, r.description,
                       r.task_fingerprint, r.input_json, r.depends_on_json,
                       r.output_json, r.execution_summary_json, r.finished_at
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
        total_chars = 0
        for row in rows:
            result_chars = sum(
                len(str(row[column] or ""))
                for column in (
                    "description",
                    "input_json",
                    "depends_on_json",
                    "output_json",
                    "execution_summary_json",
                )
            )
            if total_chars + result_chars > max_chars:
                break
            selected.append(_stored_task_result(row))
            total_chars += result_chars
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
                       r.depends_on_json, r.output_json, r.execution_summary_json, r.finished_at
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


def _serialize_plan_snapshot(snapshot: Mapping[str, Any]) -> str:
    if not isinstance(snapshot, Mapping):
        raise ValueError("PlanRun snapshot must be a mapping.")
    try:
        serialized = json.dumps(dict(snapshot), ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("PlanRun snapshot must contain JSON-compatible values.") from error
    if not serialized.strip() or len(serialized.encode("utf-8")) > _MAX_RESULT_BYTES:
        raise ValueError("PlanRun snapshot is empty or exceeds the supported size.")
    if not isinstance(snapshot.get("version"), int) or snapshot["version"] < 1:
        raise ValueError("PlanRun snapshot must have a positive integer version.")
    return serialized


def _validate_invocation_values(
    call_index: int,
    retry_class: str,
    idempotency_key: str | None,
    arguments_json: str,
    idempotency_window_seconds: int | None,
) -> None:
    if isinstance(call_index, bool) or not isinstance(call_index, int) or call_index < 1:
        raise ValueError("Tool invocation call index must be positive.")
    if retry_class not in {"pure_read", "idempotent_write", "non_idempotent_write", "unknown"}:
        raise ValueError("Tool invocation retry class is unsupported.")
    if retry_class == "idempotent_write" and not idempotency_key:
        raise ValueError("Idempotent writes require a stable idempotency key.")
    if idempotency_key is not None:
        _validate_id(idempotency_key, "Idempotency key")
        if idempotency_window_seconds is None:
            raise ValueError("Idempotency keys require a declared retention window.")
    if idempotency_window_seconds is not None and (
        isinstance(idempotency_window_seconds, bool)
        or not isinstance(idempotency_window_seconds, int)
        or idempotency_window_seconds < 1
    ):
        raise ValueError("Idempotency window must be a positive integer number of seconds.")
    try:
        arguments = json.loads(arguments_json)
        canonical_arguments = json.dumps(
            arguments,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValueError("Tool invocation arguments must be valid JSON.") from error
    if canonical_arguments != arguments_json:
        raise ValueError("Tool invocation arguments must use canonical JSON encoding.")


def _idempotency_expiry(created_at: datetime, window_seconds: int | None) -> str | None:
    if window_seconds is None:
        return None
    return _timestamp(created_at + timedelta(seconds=window_seconds))


def _stored_plan_run(row: sqlite3.Row) -> PlanRunSnapshot:
    snapshot = json.loads(str(row["snapshot_json"]))
    if not isinstance(snapshot, dict):
        raise ValueError("Stored PlanRun snapshot is not a JSON object.")
    return PlanRunSnapshot(
        plan_run_id=str(row["plan_run_id"]),
        turn_id=str(row["turn_id"]),
        session_id=str(row["session_id"]),
        runtime_host_id=str(row["runtime_host_id"]),
        goal=str(row["goal"]),
        state=str(row["state"]),  # type: ignore[arg-type]
        snapshot=snapshot,
        created_at=datetime.fromisoformat(str(row["created_at"])),
        updated_at=datetime.fromisoformat(str(row["updated_at"])),
        resume_claim_host_id=(
            str(row["resume_claim_host_id"]) if row["resume_claim_host_id"] is not None else None
        ),
    )


def _stored_tool_invocation(row: sqlite3.Row) -> ToolInvocationRecord:
    return ToolInvocationRecord(
        invocation_id=str(row["invocation_id"]),
        plan_run_id=(str(row["plan_run_id"]) if row["plan_run_id"] is not None else None),
        session_id=str(row["session_id"]),
        turn_id=str(row["turn_id"]),
        runtime_host_id=str(row["runtime_host_id"]),
        task_run_id=(str(row["task_run_id"]) if row["task_run_id"] is not None else None),
        task_id=str(row["task_id"]),
        call_index=int(row["call_index"]),
        tool_id=str(row["tool_id"]),
        retry_class=str(row["retry_class"]),
        idempotency_key=(
            str(row["idempotency_key"]) if row["idempotency_key"] is not None else None
        ),
        request_fingerprint=str(row["request_fingerprint"]),
        arguments_json=str(row["arguments_json"]),
        state=str(row["state"]),  # type: ignore[arg-type]
        created_at=datetime.fromisoformat(str(row["created_at"])),
        idempotency_expires_at=(
            datetime.fromisoformat(str(row["idempotency_expires_at"]))
            if row["idempotency_expires_at"] is not None
            else None
        ),
        finished_at=(
            datetime.fromisoformat(str(row["finished_at"]))
            if row["finished_at"] is not None
            else None
        ),
        reported_result=(
            str(row["reported_result"]) if row["reported_result"] is not None else None
        ),
    )


def _serialize_task_result(
    result: CompletedPlanTaskResult,
) -> tuple[str, str, str, str, str, str | None]:
    _validate_fingerprint(result.task_fingerprint)
    if not result.description.strip():
        raise ValueError("Completed Plan task description cannot be blank.")
    if not isinstance(result.input, Mapping):
        raise ValueError("Completed Plan task input must be a mapping.")
    try:
        input_json = json.dumps(dict(result.input), ensure_ascii=False, allow_nan=False)
        depends_on_json = json.dumps(list(result.depends_on), ensure_ascii=False)
        output_json = json.dumps(result.output, ensure_ascii=False, allow_nan=False)
        summary_json = _serialize_execution_summary(result.execution_summary)
    except (TypeError, ValueError) as error:
        raise ValueError(
            "Completed Plan task result must contain JSON-compatible values."
        ) from error
    total_bytes = len(result.description.encode("utf-8")) + sum(
        len(value.encode("utf-8"))
        for value in (input_json, depends_on_json, output_json, summary_json or "")
    )
    if total_bytes > _MAX_RESULT_BYTES:
        raise ValueError("Completed Plan task result exceeds the 65536-byte persistence limit.")
    return (
        result.task_fingerprint,
        result.description,
        input_json,
        depends_on_json,
        output_json,
        summary_json,
    )


def _serialize_execution_summary(summary: PlanTaskExecutionSummary | None) -> str | None:
    if summary is None:
        return None
    if not isinstance(summary, PlanTaskExecutionSummary):
        raise ValueError("Plan task execution summary has an invalid type.")
    if not isinstance(summary.tools, tuple) or not isinstance(
        summary.confirmation_decisions, tuple
    ):
        raise ValueError("Plan task execution summary collections must be tuples.")
    tool_ids: set[str] = set()
    tools: list[dict[str, str | int]] = []
    allowed_effects = {"read_only", "idempotent", "reversible", "irreversible", "unknown"}
    for usage in summary.tools:
        if not isinstance(usage, PlanTaskToolUsage):
            raise ValueError("Plan task execution summary contains an invalid tool record.")
        if not isinstance(usage.tool_id, str) or not _SAFE_CACHE_VALUE.fullmatch(usage.tool_id):
            raise ValueError("Plan task execution summary contains an unsafe tool ID.")
        if usage.tool_id in tool_ids:
            raise ValueError("Plan task execution summary must aggregate each tool only once.")
        if isinstance(usage.call_count, bool) or usage.call_count < 1:
            raise ValueError("Plan task tool call count must be positive.")
        if (
            not isinstance(usage.side_effect_level, str)
            or usage.side_effect_level not in allowed_effects
        ):
            raise ValueError("Plan task execution summary contains an unknown effect level.")
        tool_ids.add(usage.tool_id)
        tools.append(
            {
                "tool_id": usage.tool_id,
                "call_count": usage.call_count,
                "side_effect_level": usage.side_effect_level,
            }
        )
    allowed_decisions = {"approved", "rejected", "cancelled", "timed_out"}
    if any(
        not isinstance(decision, str) or decision not in allowed_decisions
        for decision in summary.confirmation_decisions
    ):
        raise ValueError("Plan task execution summary contains an invalid confirmation decision.")
    try:
        serialized = json.dumps(
            {
                "tools": tools,
                "confirmation_decisions": list(summary.confirmation_decisions),
            },
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Plan task execution summary is not JSON-compatible.") from error
    if len(serialized.encode("utf-8")) > _MAX_RESULT_BYTES:
        raise ValueError("Plan task execution summary exceeds the persistence limit.")
    return serialized


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
        execution_summary=_deserialize_execution_summary(row["execution_summary_json"]),
    )


def _deserialize_execution_summary(value: Any) -> PlanTaskExecutionSummary | None:
    if value is None:
        return None
    payload = json.loads(str(value))
    if not isinstance(payload, dict) or set(payload) != {"tools", "confirmation_decisions"}:
        raise ValueError("Stored Plan task execution summary has an invalid shape.")
    tools = payload["tools"]
    decisions = payload["confirmation_decisions"]
    if not isinstance(tools, list) or not isinstance(decisions, list):
        raise ValueError("Stored Plan task execution summary has invalid collections.")
    if any(
        not isinstance(item, dict) or set(item) != {"tool_id", "call_count", "side_effect_level"}
        for item in tools
    ):
        raise ValueError("Stored Plan task execution summary has an invalid tool record.")
    if any(
        not isinstance(item, dict)
        or set(item) != {"tool_id", "call_count", "side_effect_level"}
        or not isinstance(item.get("tool_id"), str)
        or isinstance(item.get("call_count"), bool)
        or not isinstance(item.get("call_count"), int)
        or not isinstance(item.get("side_effect_level"), str)
        for item in tools
    ) or any(not isinstance(item, str) for item in decisions):
        raise ValueError("Stored Plan task execution summary contains invalid values.")
    summary = PlanTaskExecutionSummary(
        tools=tuple(
            PlanTaskToolUsage(
                tool_id=item["tool_id"],
                call_count=item["call_count"],
                side_effect_level=item["side_effect_level"],
            )
            for item in tools
        ),
        confirmation_decisions=tuple(str(item) for item in decisions),
    )
    _serialize_execution_summary(summary)
    return summary


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
