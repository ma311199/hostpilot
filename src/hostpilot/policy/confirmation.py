"""Policy-first confirmation gate for high-impact tool operations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, TypeVar
from uuid import uuid4


class ConfirmationDecision(StrEnum):
    """Possible human responses to a confirmation request."""

    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


@dataclass(frozen=True)
class ProposedOperation:
    """A safe summary of a proposed operation, without raw arguments."""

    tool_name: str
    summary: str
    requires_confirmation: bool = False
    request_id: str = ""

    def __post_init__(self) -> None:
        if not self.tool_name.strip():
            raise ValueError("Tool name cannot be empty.")
        if not self.summary.strip():
            raise ValueError("Operation summary cannot be empty.")
        if not self.request_id:
            object.__setattr__(self, "request_id", str(uuid4()))


@dataclass(frozen=True)
class ConfirmationRequest:
    """The user-visible information required to approve an operation."""

    request_id: str
    tool_name: str
    summary: str


@dataclass(frozen=True)
class ConfirmationEvent:
    """Audit event for policy and user confirmation decisions."""

    request_id: str
    tool_name: str
    decision: str


class ConfirmationProvider(Protocol):
    """User-interface boundary that obtains an explicit decision."""

    def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision: ...


class OperationDenied(PermissionError):
    """Raised when policy or user confirmation denies an operation."""


ResultT = TypeVar("ResultT")


class ConfirmationGate:
    """Authorize first, ask for confirmation when required, then execute."""

    def execute(
        self,
        operation: ProposedOperation,
        *,
        authorize: Callable[[ProposedOperation], bool],
        confirm: ConfirmationProvider,
        run: Callable[[ProposedOperation], ResultT],
        audit: Callable[[ConfirmationEvent], None],
    ) -> ResultT:
        if not authorize(operation):
            audit(
                ConfirmationEvent(
                    request_id=operation.request_id,
                    tool_name=operation.tool_name,
                    decision="policy_denied",
                )
            )
            raise OperationDenied("Policy Engine denied the operation.")

        if operation.requires_confirmation:
            request = ConfirmationRequest(
                request_id=operation.request_id,
                tool_name=operation.tool_name,
                summary=operation.summary,
            )
            decision = confirm.confirm(request)
            audit(
                ConfirmationEvent(
                    request_id=operation.request_id,
                    tool_name=operation.tool_name,
                    decision=decision.value,
                )
            )
            if decision is not ConfirmationDecision.APPROVED:
                raise OperationDenied(f"User confirmation was {decision.value}.")

        return run(operation)
