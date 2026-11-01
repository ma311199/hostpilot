"""Typed observability events that exclude raw prompts and credentials."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class EventModel(BaseModel):
    """Immutable event model that rejects undeclared fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class TraceContext(EventModel):
    """Identifiers used to correlate related request and tool events."""

    trace_id: UUID = Field(default_factory=uuid4)
    span_id: UUID = Field(default_factory=uuid4)
    parent_span_id: UUID | None = None


class TraceEvent(EventModel):
    """Structured trace record; attributes must be explicit low-risk metadata."""

    context: TraceContext
    name: NonEmptyString
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    attributes: dict[str, str | int | float | bool] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone.")
        return value


class AuditOutcome(StrEnum):
    """Result category recorded for a governed action."""

    ALLOWED = "allowed"
    DENIED = "denied"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AuditEvent(EventModel):
    """Minimal audit record without raw arguments or sensitive payloads."""

    event_id: UUID = Field(default_factory=uuid4)
    trace_id: UUID | None = None
    action: NonEmptyString
    outcome: AuditOutcome
    resource_type: NonEmptyString
    resource_id: NonEmptyString | None = None
    reason_code: NonEmptyString | None = None
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("occurred_at")
    @classmethod
    def audit_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone.")
        return value


class CostUnit(StrEnum):
    """Units suitable for recording usage without provider-specific coupling."""

    TOKEN = "token"
    TOOL_CALL = "tool_call"
    RETRIEVAL_CALL = "retrieval_call"
    USD = "usd"


class CostEvent(EventModel):
    """Usage measurement associated with a request trace."""

    trace_id: UUID
    component: NonEmptyString
    unit: CostUnit
    quantity: Decimal = Field(ge=0)
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("occurred_at")
    @classmethod
    def cost_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone.")
        return value


__all__ = [
    "AuditEvent",
    "AuditOutcome",
    "CostEvent",
    "CostUnit",
    "TraceContext",
    "TraceEvent",
]
