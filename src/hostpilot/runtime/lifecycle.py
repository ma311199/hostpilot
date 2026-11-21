"""Request-scoped turn tracking and cancellation coordination."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

if TYPE_CHECKING:
    from hostpilot.persistence.ports import TaskStore, TurnStore


class TurnState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class TurnRecord:
    """Non-sensitive lifecycle metadata for one chat turn."""

    turn_id: str
    session_id: str
    state: TurnState
    started_at: datetime
    finished_at: datetime | None = None


@dataclass
class _ActiveTurn:
    record: TurnRecord
    task: asyncio.Task[object]


class TurnLifecycleManager:
    """Track active turns and cancel them by turn or session identifier."""

    def __init__(
        self,
        *,
        history_limit: int = 100,
        store: TurnStore | None = None,
        runtime_host_id: str | None = None,
    ) -> None:
        if history_limit < 0:
            raise ValueError("Turn history limit cannot be negative.")
        if (store is None) != (runtime_host_id is None):
            raise ValueError("TurnStore and Runtime Host ID must be configured together.")
        self._active: dict[str, _ActiveTurn] = {}
        self._history: deque[TurnRecord] = deque(maxlen=history_limit)
        self._store = store
        self._runtime_host_id = runtime_host_id

    @property
    def active_turns(self) -> tuple[TurnRecord, ...]:
        return tuple(active.record for active in self._active.values())

    @property
    def history(self) -> tuple[TurnRecord, ...]:
        return tuple(self._history)

    def configure_persistence(self, store: TurnStore, runtime_host_id: str) -> None:
        """Bind a durable store before the Runtime Host accepts requests."""
        if self._active:
            raise RuntimeError("Turn persistence cannot change while Turns are active.")
        if self._store is not None:
            raise RuntimeError("Turn persistence is already configured.")
        if not runtime_host_id or runtime_host_id.strip() != runtime_host_id:
            raise ValueError("Runtime Host ID must be non-empty and trimmed.")
        self._store = store
        self._runtime_host_id = runtime_host_id

    @asynccontextmanager
    async def track(self, turn_id: str, session_id: str) -> AsyncIterator[TurnRecord]:
        """Register the current task and record its terminal state on exit."""
        if not turn_id or not session_id:
            raise ValueError("Turn and session IDs must be non-empty.")
        if turn_id in self._active:
            raise ValueError(f"Turn is already active: {turn_id}.")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Turn lifecycle tracking requires an asyncio task.")
        record = TurnRecord(
            turn_id=turn_id,
            session_id=session_id,
            state=TurnState.RUNNING,
            started_at=datetime.now(UTC),
        )
        if self._store is not None:
            await self._store.create_turn(
                turn_id=turn_id,
                session_id=session_id,
                runtime_host_id=self._runtime_host_id or "",
                started_at=record.started_at,
            )
        self._active[turn_id] = _ActiveTurn(record=record, task=task)
        terminal_state: TurnState
        try:
            yield record
        except asyncio.CancelledError:
            terminal_state = TurnState.CANCELLED
            raise
        except BaseException:
            terminal_state = TurnState.FAILED
            raise
        else:
            terminal_state = TurnState.COMPLETED
        finally:
            active = self._active.pop(turn_id, None)
            if active is not None:
                finished_at = datetime.now(UTC)
                self._history.append(
                    replace(
                        active.record,
                        state=terminal_state,
                        finished_at=finished_at,
                    )
                )
                if self._store is not None:
                    await self._store.finish_turn(
                        turn_id,
                        runtime_host_id=self._runtime_host_id or "",
                        state=cast(
                            Literal["completed", "failed", "cancelled"],
                            terminal_state.value,
                        ),
                        finished_at=finished_at,
                    )

    def cancel_turn(self, turn_id: str) -> bool:
        active = self._active.get(turn_id)
        if active is None:
            return False
        return active.task.cancel()

    def cancel_session(self, session_id: str) -> int:
        tasks = [
            active.task
            for active in self._active.values()
            if active.record.session_id == session_id
        ]
        return sum(task.cancel() for task in tasks)

    async def cancel_all(self) -> None:
        current = asyncio.current_task()
        tasks = [active.task for active in self._active.values() if active.task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failures = [
                result
                for result in results
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            ]
            if failures:
                raise failures[0]


class TaskState(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True)
class TaskRecord:
    """Non-sensitive lifecycle metadata for one Plan task attempt."""

    task_run_id: str
    task_id: str
    turn_id: str
    session_id: str
    state: TaskState
    started_at: datetime
    finished_at: datetime | None = None


@dataclass
class _ActiveTaskRun:
    record: TaskRecord
    task: asyncio.Task[object]


class TaskRunHandle:
    """Allow an executor to mark a returned unsuccessful result as failed."""

    def __init__(self) -> None:
        self.terminal_state = TaskState.COMPLETED

    def fail(self) -> None:
        self.terminal_state = TaskState.FAILED


class TaskLifecycleManager:
    """Persist Plan TaskRun lifecycle while retaining local cancellation handles."""

    def __init__(
        self,
        *,
        history_limit: int = 100,
        store: TaskStore | None = None,
        runtime_host_id: str | None = None,
    ) -> None:
        if history_limit < 0:
            raise ValueError("Task history limit cannot be negative.")
        if (store is None) != (runtime_host_id is None):
            raise ValueError("TaskStore and Runtime Host ID must be configured together.")
        self._active: dict[str, _ActiveTaskRun] = {}
        self._history: deque[TaskRecord] = deque(maxlen=history_limit)
        self._store = store
        self._runtime_host_id = runtime_host_id

    @property
    def active_task_runs(self) -> tuple[TaskRecord, ...]:
        return tuple(active.record for active in self._active.values())

    @property
    def history(self) -> tuple[TaskRecord, ...]:
        return tuple(self._history)

    def configure_persistence(self, store: TaskStore, runtime_host_id: str) -> None:
        """Bind the durable TaskStore before the Runtime Host accepts requests."""
        if self._active:
            raise RuntimeError("Task persistence cannot change while TaskRuns are active.")
        if self._store is not None:
            raise RuntimeError("Task persistence is already configured.")
        if not runtime_host_id or runtime_host_id.strip() != runtime_host_id:
            raise ValueError("Runtime Host ID must be non-empty and trimmed.")
        self._store = store
        self._runtime_host_id = runtime_host_id

    @asynccontextmanager
    async def track(
        self,
        task_id: str,
        turn_id: str,
        session_id: str,
    ) -> AsyncIterator[TaskRunHandle]:
        """Persist a TaskRun before work starts and its outcome when work ends."""
        for value, label in ((task_id, "Task"), (turn_id, "Turn"), (session_id, "Session")):
            if not value or value.strip() != value:
                raise ValueError(f"{label} ID must be non-empty and trimmed.")
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("Task lifecycle tracking requires an asyncio task.")
        task_run_id = str(uuid4())
        record = TaskRecord(
            task_run_id=task_run_id,
            task_id=task_id,
            turn_id=turn_id,
            session_id=session_id,
            state=TaskState.RUNNING,
            started_at=datetime.now(UTC),
        )
        if self._store is not None:
            await self._store.create_task_run(
                task_run_id=task_run_id,
                task_id=task_id,
                turn_id=turn_id,
                session_id=session_id,
                runtime_host_id=self._runtime_host_id or "",
                started_at=record.started_at,
            )
        self._active[task_run_id] = _ActiveTaskRun(record, task)
        handle = TaskRunHandle()
        terminal_state: TaskState
        try:
            yield handle
        except asyncio.CancelledError:
            terminal_state = TaskState.CANCELLED
            raise
        except BaseException:
            terminal_state = TaskState.FAILED
            raise
        else:
            terminal_state = handle.terminal_state
        finally:
            active = self._active.pop(task_run_id, None)
            if active is not None:
                finished_at = datetime.now(UTC)
                self._history.append(
                    replace(
                        active.record,
                        state=terminal_state,
                        finished_at=finished_at,
                    )
                )
                if self._store is not None:
                    await self._store.finish_task_run(
                        task_run_id,
                        runtime_host_id=self._runtime_host_id or "",
                        state=cast(
                            Literal["completed", "failed", "cancelled"],
                            terminal_state.value,
                        ),
                        finished_at=finished_at,
                    )

    async def cancel_all(self) -> int:
        current = asyncio.current_task()
        tasks = [active.task for active in self._active.values() if active.task is not current]
        cancelled = sum(task.cancel() for task in tasks)
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            failures = [
                result
                for result in results
                if isinstance(result, BaseException)
                and not isinstance(result, asyncio.CancelledError)
            ]
            if failures:
                raise failures[0]
        return cancelled
