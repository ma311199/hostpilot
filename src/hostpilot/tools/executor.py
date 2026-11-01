"""Policy-first tool execution with confirmation, budgets, timeouts, and verification."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol
from uuid import uuid4

from hostpilot.budget import BudgetExceededError, RequestBudget
from hostpilot.contracts import ToolType
from hostpilot.observability import AuditEvent, AuditOutcome
from hostpilot.policy import PolicyEngine
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.tools.registry import ToolRegistration, ToolRegistrationError, ToolRegistry
from hostpilot.verification import TaskVerificationError, verify_tool_result


class AsyncConfirmationProvider(Protocol):
    """Obtain explicit user approval for a high-impact operation."""

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision: ...


class ToolExecutionError(RuntimeError):
    """Raised when a tool cannot be safely executed or verified."""


class ToolDeniedError(PermissionError):
    """Raised when policy or confirmation does not permit a tool call."""


class ToolConfirmationDeniedError(ToolDeniedError):
    """Raised when a user rejects, cancels, or times out a confirmation request."""

    def __init__(self, decision: ConfirmationDecision) -> None:
        self.decision = decision
        super().__init__(f"User confirmation was {decision.value}.")


class ToolExecutor:
    """Validate, authorize, confirm, execute, and verify one tool operation."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy: PolicyEngine,
        *,
        audit: Callable[[AuditEvent], None],
        confirmation: AsyncConfirmationProvider | None = None,
        confirmation_timeout_seconds: float = 120,
    ) -> None:
        if confirmation_timeout_seconds <= 0:
            raise ValueError("Confirmation timeout must be positive.")
        self.registry = registry
        self.policy = policy
        self.audit = audit
        self.confirmation = confirmation
        self.confirmation_timeout_seconds = confirmation_timeout_seconds

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        granted_capabilities: frozenset[str] = frozenset(),
        confirmation_provider: AsyncConfirmationProvider | None = None,
        _invoke: Callable[[Mapping[str, Any]], Awaitable[Any]] | None = None,
    ) -> Any:
        """Execute only after schema validation, policy approval, and user confirmation."""
        try:
            registration = self.registry.get(name)
        except ToolRegistrationError as error:
            audit_name = name if name.strip() else "unknown"
            self._record("tool.execute", AuditOutcome.DENIED, audit_name, "tool_not_registered")
            raise ToolDeniedError("Tool is not registered.") from error
        descriptor = registration.descriptor
        try:
            validated_arguments = dict(registration.validate_arguments(arguments))
        except Exception as error:
            self._record("tool.execute", AuditOutcome.DENIED, name, "invalid_arguments")
            raise ToolDeniedError("Tool arguments failed validation.") from error

        decision = self.policy.authorize(
            descriptor,
            validated_arguments,
            granted_capabilities=granted_capabilities,
        )
        if not decision.allowed:
            self._record("tool.execute", AuditOutcome.DENIED, name, decision.reason_code)
            raise ToolDeniedError(f"Policy Engine denied the operation: {decision.reason_code}.")

        if descriptor.high_impact:
            confirmation = confirmation_provider or self.confirmation
            if confirmation is None:
                self._record(
                    "tool.confirmation",
                    AuditOutcome.DENIED,
                    name,
                    "confirmation_provider_unavailable",
                )
                raise ToolDeniedError("High-impact operation requires user confirmation.")
            if registration.confirmation_summary is None:
                self._record(
                    "tool.confirmation",
                    AuditOutcome.DENIED,
                    name,
                    "confirmation_summary_unavailable",
                )
                raise ToolDeniedError("High-impact operation has no safe confirmation summary.")
            try:
                summary = registration.confirmation_summary(validated_arguments).strip()
            except Exception as error:
                self._record(
                    "tool.confirmation",
                    AuditOutcome.DENIED,
                    name,
                    "confirmation_summary_failed",
                )
                raise ToolDeniedError("Could not prepare a safe confirmation summary.") from error
            if not summary:
                self._record(
                    "tool.confirmation",
                    AuditOutcome.DENIED,
                    name,
                    "confirmation_summary_empty",
                )
                raise ToolDeniedError("High-impact operation has no safe confirmation summary.")
            confirmation_decision = await self._request_confirmation(
                registration, summary, confirmation
            )
            if confirmation_decision is not ConfirmationDecision.APPROVED:
                if confirmation_decision is not None:
                    raise ToolConfirmationDeniedError(confirmation_decision)
                raise ToolDeniedError("User did not approve the operation.")

        try:
            budget.consume_tool_call(retrieval=descriptor.tool_type is ToolType.RETRIEVAL)
        except BudgetExceededError as error:
            self._record("tool.execute", AuditOutcome.DENIED, name, error.resource)
            raise ToolDeniedError(f"Tool call budget exhausted: {error.resource}.") from error

        elapsed = budget.snapshot().usage.elapsed_seconds
        request_remaining = budget.context.limits.timeout_seconds - elapsed
        timeout_seconds = min(descriptor.timeout_profile.timeout_seconds, request_remaining)
        if timeout_seconds <= 0:
            self._record("tool.execute", AuditOutcome.DENIED, name, "timeout_seconds")
            raise ToolDeniedError("Request time budget is exhausted.")

        try:
            async with asyncio.timeout(timeout_seconds):
                handler = _invoke or registration.handler
                result = await handler(validated_arguments)
            verify_tool_result(registration.verify_result, result, validated_arguments)
        except TimeoutError as error:
            self._record("tool.execute", AuditOutcome.FAILED, name, "tool_timeout")
            raise ToolExecutionError("Tool execution timed out.") from error
        except asyncio.CancelledError:
            self._record("tool.execute", AuditOutcome.CANCELLED, name, "execution_cancelled")
            raise
        except TaskVerificationError as error:
            self._record("tool.execute", AuditOutcome.FAILED, name, "verification_failed")
            raise ToolExecutionError("Tool result failed verification.") from error
        except Exception as error:
            self._record("tool.execute", AuditOutcome.FAILED, name, "tool_failed")
            raise ToolExecutionError("Tool execution failed.") from error

        self._record("tool.execute", AuditOutcome.COMPLETED, name, None)
        return result

    async def _request_confirmation(
        self,
        registration: ToolRegistration,
        summary: str,
        confirmation: AsyncConfirmationProvider,
    ) -> ConfirmationDecision | None:
        name = registration.descriptor.name
        request_id = str(uuid4())
        request = ConfirmationRequest(
            request_id=request_id,
            tool_name=name,
            summary=summary,
        )
        try:
            async with asyncio.timeout(self.confirmation_timeout_seconds):
                decision = await confirmation.confirm(request)
        except TimeoutError:
            self._record(
                "tool.confirmation", AuditOutcome.CANCELLED, name, "confirmation_timed_out"
            )
            return ConfirmationDecision.TIMED_OUT
        except asyncio.CancelledError:
            self._record(
                "tool.confirmation", AuditOutcome.CANCELLED, name, "confirmation_cancelled"
            )
            raise
        except Exception:
            self._record("tool.confirmation", AuditOutcome.FAILED, name, "confirmation_failed")
            return None

        outcome = (
            AuditOutcome.ALLOWED
            if decision is ConfirmationDecision.APPROVED
            else AuditOutcome.CANCELLED
            if decision in {ConfirmationDecision.CANCELLED, ConfirmationDecision.TIMED_OUT}
            else AuditOutcome.DENIED
        )
        self._record("tool.confirmation", outcome, name, decision.value)
        return decision

    def _record(
        self,
        action: str,
        outcome: AuditOutcome,
        tool_name: str,
        reason_code: str | None,
    ) -> None:
        self.audit(
            AuditEvent(
                action=action,
                outcome=outcome,
                resource_type="tool",
                resource_id=tool_name,
                reason_code=reason_code,
            )
        )


__all__ = [
    "AsyncConfirmationProvider",
    "ToolConfirmationDeniedError",
    "ToolDeniedError",
    "ToolExecutionError",
    "ToolExecutor",
]
