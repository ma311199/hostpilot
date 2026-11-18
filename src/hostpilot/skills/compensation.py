"""Opt-in compensation hooks and cancellation snapshots for Skill execution."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from hostpilot.budget import RequestBudget
from hostpilot.contracts import ContextSnapshot
from hostpilot.skills.yaml_registry import SideEffectLevel
from hostpilot.tools.executor import AsyncConfirmationProvider


class CompensatableEffect(Protocol):
    @property
    def action(self) -> str: ...

    @property
    def level(self) -> SideEffectLevel: ...

    @property
    def idempotency_key(self) -> str: ...

    @property
    def compensation_handle(self) -> str | None: ...


class CompensationStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"
    NOT_REVERSIBLE = "not_reversible"


@dataclass(frozen=True)
class CompensationCall:
    request_id: str
    action: str
    idempotency_key: str
    compensation_handle: str
    context: ContextSnapshot
    budget: RequestBudget
    confirmation_provider: AsyncConfirmationProvider | None = None
    uncertain: bool = False


@dataclass(frozen=True)
class CompensationRecord:
    action: str
    idempotency_key: str
    status: CompensationStatus
    error_code: str | None = None


CompensationHandler = Callable[[CompensationCall], Awaitable[None]]


@dataclass(frozen=True)
class _CompensationRegistration:
    handler: CompensationHandler
    safe_for_uncertain_effect: bool


@dataclass(frozen=True)
class CancellationSnapshot:
    request_id: str
    scope: str
    uncertain_operations: tuple[str, ...] = ()
    side_effects: tuple[CompensatableEffect, ...] = ()
    compensation_records: tuple[CompensationRecord, ...] = ()
    event_id: str | None = None
    persistence_error: str | None = None


class CancellationSink(Protocol):
    async def record(self, snapshot: CancellationSnapshot) -> str: ...


async def persist_cancellation(
    sink: CancellationSink | None, snapshot: CancellationSnapshot
) -> CancellationSnapshot:
    """Persist a safe cancellation record without masking the original cancellation."""
    if sink is None:
        return snapshot
    try:
        event_id = await sink.record(snapshot)
    except Exception as error:
        return replace(snapshot, persistence_error=type(error).__name__)
    return replace(snapshot, event_id=event_id)


class ExecutionCancelled(asyncio.CancelledError):
    """Propagated cancellation carrying the effects and compensation outcomes."""

    def __init__(
        self, snapshot: CancellationSnapshot, *, runtime_snapshot: object | None = None
    ) -> None:
        self.snapshot = snapshot
        self.runtime_snapshot = runtime_snapshot
        super().__init__(f"Execution cancelled for request {snapshot.request_id}.")


class CompensationRegistry:
    """Allow trusted adapters to register explicitly governed reverse operations."""

    def __init__(self) -> None:
        self._handlers: dict[str, _CompensationRegistration] = {}

    def register(
        self,
        action: str,
        handler: CompensationHandler,
        *,
        safe_for_uncertain_effect: bool = False,
    ) -> None:
        if not action.strip():
            raise ValueError("Compensation action cannot be blank.")
        if action in self._handlers:
            raise ValueError(f"Compensation handler is already registered: {action}")
        self._handlers[action] = _CompensationRegistration(
            handler=handler,
            safe_for_uncertain_effect=safe_for_uncertain_effect,
        )

    def can_compensate(self, effect: CompensatableEffect, *, uncertain: bool = False) -> bool:
        """Return whether a reversible effect has a handle and registered handler."""
        registration = self._handlers.get(effect.action)
        return (
            effect.level is SideEffectLevel.REVERSIBLE
            and bool(effect.compensation_handle)
            and registration is not None
            and (not uncertain or registration.safe_for_uncertain_effect)
        )

    async def compensate(
        self,
        *,
        request_id: str,
        context: ContextSnapshot,
        budget: RequestBudget,
        effects: Sequence[CompensatableEffect],
        uncertain_effects: Sequence[CompensatableEffect] = (),
        confirmation_provider: AsyncConfirmationProvider | None,
    ) -> tuple[CompensationRecord, ...]:
        """Compensate completed reversible effects in reverse completion order."""
        records: list[CompensationRecord] = []
        work = [(effect, False) for effect in effects]
        work.extend((effect, True) for effect in uncertain_effects)
        for effect, uncertain in reversed(work):
            if effect.level is not SideEffectLevel.REVERSIBLE:
                records.append(
                    CompensationRecord(
                        effect.action,
                        effect.idempotency_key,
                        CompensationStatus.NOT_REVERSIBLE,
                        "effect_not_reversible",
                    )
                )
                continue
            if not effect.compensation_handle:
                records.append(
                    CompensationRecord(
                        effect.action,
                        effect.idempotency_key,
                        CompensationStatus.UNAVAILABLE,
                        "compensation_handle_missing",
                    )
                )
                continue
            registration = self._handlers.get(effect.action)
            if registration is None or (uncertain and not registration.safe_for_uncertain_effect):
                records.append(
                    CompensationRecord(
                        effect.action,
                        effect.idempotency_key,
                        CompensationStatus.UNAVAILABLE,
                        (
                            "uncertain_compensation_not_safe"
                            if uncertain and registration is not None
                            else "compensation_handler_unavailable"
                        ),
                    )
                )
                continue
            try:
                budget.ensure_available()
                await registration.handler(
                    CompensationCall(
                        request_id=request_id,
                        action=effect.action,
                        idempotency_key=effect.idempotency_key,
                        compensation_handle=effect.compensation_handle,
                        context=context,
                        budget=budget,
                        confirmation_provider=confirmation_provider,
                        uncertain=uncertain,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                records.append(
                    CompensationRecord(
                        effect.action,
                        effect.idempotency_key,
                        CompensationStatus.FAILED,
                        type(error).__name__,
                    )
                )
            else:
                records.append(
                    CompensationRecord(
                        effect.action,
                        effect.idempotency_key,
                        CompensationStatus.COMPLETED,
                    )
                )
        return tuple(records)


__all__ = [
    "CancellationSnapshot",
    "CancellationSink",
    "CompensationCall",
    "CompensationHandler",
    "CompensationRecord",
    "CompensationRegistry",
    "CompensationStatus",
    "ExecutionCancelled",
    "persist_cancellation",
]
