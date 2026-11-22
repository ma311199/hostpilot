"""Request-scoped turn tracking and cancellation coordination."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Literal, cast
from uuid import uuid4

if TYPE_CHECKING:
    from hostpilot.persistence.ports import (
        PendingToolOutcome,
        SessionLifecycleStore,
        TaskStore,
        ToolOutcomeStore,
        TurnStore,
    )
    from hostpilot.task_results import (
        CompletedPlanTaskResult,
        FailedPlanTaskSummary,
        PlanTaskExecutionSummary,
        StoredPlanTaskFailure,
        StoredPlanTaskResult,
    )


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


class SessionLifecycleManager:
    """Coordinate Runtime-owned Session operations and cross-Host deletion."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        poll_interval_seconds: float = 0.05,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("Session lifecycle polling interval must be positive.")
        self._condition = asyncio.Condition()
        self._close_lock = asyncio.Lock()
        self._store: SessionLifecycleStore | None = None
        self._runtime_host_id: str | None = None
        self._clock = clock
        self._poll_interval_seconds = poll_interval_seconds
        self._active_operations = 0
        self._closing = False
        self._closed = False

    def configure_persistence(self, store: SessionLifecycleStore, runtime_host_id: str) -> None:
        """Bind durable state before the Runtime Host accepts requests."""
        if self._active_operations:
            raise RuntimeError("Session persistence cannot change during active operations.")
        if self._store is not None:
            raise RuntimeError("Session persistence is already configured.")
        if not runtime_host_id or runtime_host_id.strip() != runtime_host_id:
            raise ValueError("Runtime Host ID must be non-empty and trimmed.")
        self._store = store
        self._runtime_host_id = runtime_host_id

    async def register_session(self, session_id: str) -> None:
        """Register a Session with the injected persistence provider."""
        store, _ = self._require_persistence()
        async with self._condition:
            self._ensure_accepting()
            self._active_operations += 1
        try:
            await store.register_session(session_id)
        finally:
            await self._finish_operation()

    @asynccontextmanager
    async def lease(self, session_id: str) -> AsyncIterator[None]:
        """Hold a durable Session lease for one complete Runtime operation."""
        store, host_id = self._require_persistence()
        lease_id = str(uuid4())
        async with self._condition:
            self._ensure_accepting()
            self._active_operations += 1
        acquired = False
        try:
            await store.acquire_lease(session_id, lease_id, host_id, self._clock())
            acquired = True
            yield
        finally:
            try:
                if acquired:
                    await store.release_lease(lease_id, host_id)
            finally:
                await self._finish_operation()

    async def close_session(self, session_id: str, cleanup: Callable[[], Awaitable[None]]) -> bool:
        """Reject new leases, drain active work, then run retryable cleanup once."""
        store, host_id = self._require_persistence()
        async with self._condition:
            self._ensure_accepting()
            self._active_operations += 1
        try:
            async with self._close_lock:
                waited_for_close = False
                while True:
                    claim = await store.begin_close(session_id, host_id)
                    if claim is None:
                        return waited_for_close
                    if claim:
                        break
                    waited_for_close = True
                    await asyncio.sleep(self._poll_interval_seconds)

                try:
                    while await store.active_lease_count(session_id, self._clock()):
                        await asyncio.sleep(self._poll_interval_seconds)
                    await cleanup()
                    await store.finish_delete(session_id)
                    return True
                except BaseException:
                    await store.release_close_claim(session_id, host_id)
                    raise
        finally:
            await self._finish_operation()

    async def begin_shutdown(self) -> None:
        """Reject new Session operations before Runtime Host cancellation begins."""
        async with self._condition:
            self._closing = True

    async def aclose(self) -> None:
        """Reject new operations and drain local leases before Host ownership ends."""
        async with self._condition:
            self._closing = True
            while self._active_operations:
                await self._condition.wait()
            self._closed = True

    def _require_persistence(self) -> tuple[SessionLifecycleStore, str]:
        store = self._store
        host_id = self._runtime_host_id
        if store is None or host_id is None:
            raise RuntimeError("Session lifecycle persistence is not configured.")
        return store, host_id

    def _ensure_accepting(self) -> None:
        if self._closing or self._closed:
            raise RuntimeError("Session lifecycle manager is shutting down.")

    async def _finish_operation(self) -> None:
        async with self._condition:
            self._active_operations -= 1
            if self._active_operations == 0:
                self._condition.notify_all()


class ToolOutcomeLifecycleManager:
    """Track side-effecting tool results until their checkpoint commit is durable."""

    def __init__(self, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self._store: ToolOutcomeStore | None = None
        self._runtime_host_id: str | None = None
        self._clock = clock

    def configure_persistence(self, store: ToolOutcomeStore, runtime_host_id: str) -> None:
        """Bind the durable pending-call Store before Runtime readiness."""
        if self._store is not None:
            raise RuntimeError("Tool outcome persistence is already configured.")
        if not runtime_host_id or runtime_host_id.strip() != runtime_host_id:
            raise ValueError("Runtime Host ID must be non-empty and trimmed.")
        self._store = store
        self._runtime_host_id = runtime_host_id

    async def begin_invocation(
        self,
        *,
        session_id: str,
        turn_id: str,
        task_run_id: str | None,
        tool_call_id: str,
        tool_name: str,
    ) -> None:
        """Persist a pending record before dispatching a side-effecting tool."""
        store, host_id = self._require_persistence()
        await store.record_pending(
            session_id=session_id,
            turn_id=turn_id,
            task_run_id=task_run_id,
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            runtime_host_id=host_id,
            now=self._clock(),
        )

    async def pending(self, session_id: str) -> tuple[PendingToolOutcome, ...]:
        """Return sanitized pending calls for a Session."""
        store, _ = self._require_persistence()
        return await store.list_pending(session_id)

    async def claim_resolution(self, session_id: str, tool_call_id: str) -> bool:
        """Claim a pending call before applying a human-verified checkpoint result."""
        store, host_id = self._require_persistence()
        return await store.claim_resolution(
            session_id,
            tool_call_id,
            runtime_host_id=host_id,
            now=self._clock(),
        )

    async def release_resolution_claim(self, session_id: str, tool_call_id: str) -> None:
        """Keep the Session blocked when checkpoint resolution fails."""
        store, host_id = self._require_persistence()
        await store.release_resolution_claim(
            session_id,
            tool_call_id,
            runtime_host_id=host_id,
        )

    async def result_checkpointed(self, session_id: str, tool_call_id: str) -> None:
        """Clear a pending call only after its ToolMessage has been persisted."""
        store, host_id = self._require_persistence()
        await store.clear_pending(session_id, tool_call_id, runtime_host_id=host_id)

    def _require_persistence(self) -> tuple[ToolOutcomeStore, str]:
        if self._store is None or self._runtime_host_id is None:
            raise RuntimeError("Tool outcome persistence is not configured.")
        return self._store, self._runtime_host_id


@dataclass
class _AgentGeneration:
    runtime: object
    active_leases: int = 0


class AgentLifecycleManager:
    """Lease Chat Agent generations and close them only after active Turns drain."""

    def __init__(self, runtime: object | None = None) -> None:
        self._condition = asyncio.Condition()
        self._replacement_lock = asyncio.Lock()
        self._current = _AgentGeneration(runtime) if runtime is not None else None
        self._retirements: set[asyncio.Task[None]] = set()
        self._closing = False
        self._closed = False

    @property
    def current(self) -> object | None:
        """Expose the current generation for synchronous capability inspection."""
        generation = self._current
        return generation.runtime if generation is not None else None

    @asynccontextmanager
    async def lease(self) -> AsyncIterator[object]:
        """Lease the current Agent generation for one complete request."""
        async with self._condition:
            if self._closing:
                raise RuntimeError("Agent lifecycle manager is shutting down.")
            generation = self._current
            if generation is None:
                raise RuntimeError("The configured Chat Agent is not available.")
            generation.active_leases += 1
        try:
            yield generation.runtime
        finally:
            async with self._condition:
                generation.active_leases -= 1
                if generation.active_leases == 0:
                    self._condition.notify_all()

    async def replace(self, runtime: object | None) -> None:
        """Publish a new Agent immediately, then close the old one after its leases drain."""
        async with self._replacement_lock:
            async with self._condition:
                if self._closing:
                    raise RuntimeError("Cannot replace an Agent while shutting down.")
                previous = self._current
                if previous is not None and previous.runtime is runtime:
                    return
                self._current = _AgentGeneration(runtime) if runtime is not None else None
                retirement = self._start_retirement(previous)
            if retirement is not None:
                try:
                    await asyncio.shield(retirement)
                except asyncio.CancelledError:
                    raise
                finally:
                    if retirement.done():
                        self._retirements.discard(retirement)

    async def aclose(self) -> None:
        """Reject new leases, drain current and retired Agents, then close them once."""
        async with self._replacement_lock:
            async with self._condition:
                if self._closed:
                    return
                self._closing = True
                current = self._current
                self._current = None
                self._start_retirement(current)
                retirements = tuple(self._retirements)
            results = await asyncio.gather(*retirements, return_exceptions=True)
            self._closed = True
            failures = [result for result in results if isinstance(result, BaseException)]
            if failures:
                raise failures[0]

    async def begin_shutdown(self) -> None:
        """Reject new leases before the Host starts cancelling active Turns."""
        async with self._condition:
            self._closing = True

    def _start_retirement(self, generation: _AgentGeneration | None) -> asyncio.Task[None] | None:
        if generation is None:
            return None
        task = asyncio.create_task(self._retire(generation))
        self._retirements.add(task)
        return task

    async def _retire(self, generation: _AgentGeneration) -> None:
        async with self._condition:
            await self._condition.wait_for(lambda: generation.active_leases == 0)
        close = getattr(generation.runtime, "aclose", None)
        if callable(close):
            result = close()
            if hasattr(result, "__await__"):
                await result


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
    """Capture the terminal state and optional successful result for a TaskRun."""

    def __init__(self) -> None:
        self.terminal_state = TaskState.COMPLETED
        self.completed_result: CompletedPlanTaskResult | None = None
        self.failure_summary: FailedPlanTaskSummary | None = None

    def fail(
        self,
        *,
        task_fingerprint: str | None = None,
        description: str | None = None,
        input: Mapping[str, object] | None = None,
        error_code: str | None = None,
        side_effect_count: int = 0,
    ) -> None:
        self.terminal_state = TaskState.FAILED
        self.completed_result = None
        self.failure_summary = None
        if all((task_fingerprint, description, input is not None, error_code)):
            from hostpilot.task_results import FailedPlanTaskSummary

            self.failure_summary = FailedPlanTaskSummary(
                task_fingerprint=task_fingerprint or "",
                description=description or "",
                input=input or {},
                error_code=error_code or "",
                side_effect_count=side_effect_count,
            )

    def complete(
        self,
        *,
        description: str,
        input: Mapping[str, object],
        depends_on: tuple[str, ...],
        output: object,
        task_fingerprint: str,
        execution_summary: PlanTaskExecutionSummary | None = None,
    ) -> None:
        """Persist a successful Plan result with its task context."""
        from hostpilot.task_results import CompletedPlanTaskResult

        self.terminal_state = TaskState.COMPLETED
        self.completed_result = CompletedPlanTaskResult(
            description=description,
            input=input,
            depends_on=depends_on,
            output=output,
            task_fingerprint=task_fingerprint,
            execution_summary=execution_summary,
        )
        self.failure_summary = None


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

    async def list_completed_task_results(
        self, session_id: str, *, exclude_turn_id: str, limit: int = 32
    ) -> tuple[StoredPlanTaskResult, ...]:
        """Load bounded prior results for a new Plan in this Session."""
        if self._store is None:
            return ()
        return await self._store.list_completed_task_results(
            session_id=session_id,
            exclude_turn_id=exclude_turn_id,
            limit=limit,
        )

    async def find_completed_task_result(
        self, session_id: str, *, exclude_turn_id: str, task_fingerprint: str
    ) -> StoredPlanTaskResult | None:
        """Look up one exact successful task candidate in the current Session."""
        if self._store is None:
            return None
        return await self._store.find_completed_task_result(
            session_id=session_id,
            exclude_turn_id=exclude_turn_id,
            task_fingerprint=task_fingerprint,
        )

    async def list_recent_task_failures(
        self, session_id: str, *, exclude_turn_id: str, limit: int = 16
    ) -> tuple[StoredPlanTaskFailure, ...]:
        """Load safe failed-task references for a new planning attempt."""
        if self._store is None:
            return ()
        return await self._store.list_recent_task_failures(
            session_id=session_id,
            exclude_turn_id=exclude_turn_id,
            limit=limit,
        )

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
                        completed_result=(
                            handle.completed_result
                            if terminal_state is TaskState.COMPLETED
                            else None
                        ),
                        failure_summary=(
                            handle.failure_summary if terminal_state is TaskState.FAILED else None
                        ),
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
