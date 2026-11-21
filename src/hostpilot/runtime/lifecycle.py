"""Request-scoped turn tracking and cancellation coordination."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum


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

    def __init__(self, *, history_limit: int = 100) -> None:
        if history_limit < 0:
            raise ValueError("Turn history limit cannot be negative.")
        self._active: dict[str, _ActiveTurn] = {}
        self._history: deque[TurnRecord] = deque(maxlen=history_limit)

    @property
    def active_turns(self) -> tuple[TurnRecord, ...]:
        return tuple(active.record for active in self._active.values())

    @property
    def history(self) -> tuple[TurnRecord, ...]:
        return tuple(self._history)

    @contextmanager
    def track(self, turn_id: str, session_id: str) -> Iterator[TurnRecord]:
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
        self._active[turn_id] = _ActiveTurn(record=record, task=task)
        try:
            yield record
        except asyncio.CancelledError:
            self._finish(turn_id, TurnState.CANCELLED)
            raise
        except BaseException:
            self._finish(turn_id, TurnState.FAILED)
            raise
        else:
            self._finish(turn_id, TurnState.COMPLETED)

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
            await asyncio.gather(*tasks, return_exceptions=True)

    def _finish(self, turn_id: str, state: TurnState) -> None:
        active = self._active.pop(turn_id)
        self._history.append(
            replace(
                active.record,
                state=state,
                finished_at=datetime.now(UTC),
            )
        )
