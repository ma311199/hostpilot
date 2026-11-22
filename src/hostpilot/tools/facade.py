"""Stable HostPilot tool interface over transport-specific tool providers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol, cast

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    SandboxMode,
    ToolConnectionStatus,
    ToolDescriptor,
    ToolRetryClass,
)
from hostpilot.tools.executor import AsyncConfirmationProvider
from hostpilot.tools.registry import ToolRegistry


class ToolInvoker(Protocol):
    """Transport adapter contract used by the HostPilot tool facade."""

    @property
    def registry(self) -> ToolRegistry: ...

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str: ...


class IdempotentToolInvoker(Protocol):
    """Optional provider extension that consumes keys outside the tool schema."""

    async def call_tool_with_idempotency_key(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        idempotency_key: str,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str: ...


class ToolFacade:
    """Expose stable tool IDs and governed invocation without exposing MCP clients."""

    def __init__(self, provider: ToolInvoker) -> None:
        self._provider = provider

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        """Return safe descriptors for tools currently registered by the adapter."""
        return self._provider.registry.descriptors()

    def connections(self) -> tuple[ToolConnectionStatus, ...]:
        """Return safe lifecycle details when the adapter exposes connections."""
        inspect_connections = getattr(self._provider, "connections", None)
        if not callable(inspect_connections):
            return ()
        return cast(tuple[ToolConnectionStatus, ...], inspect_connections())

    def set_sandbox_mode(self, mode: SandboxMode | None) -> None:
        """Pass the active conversation mode to in-process filesystem handlers."""
        set_mode = getattr(self._provider, "set_sandbox_mode", None)
        if callable(set_mode):
            set_mode(mode)

    async def call_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str:
        """Invoke a stable tool ID through the adapter's complete governance chain."""
        return await self._provider.call_tool(
            tool_id,
            arguments,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

    async def call_tool_with_idempotency_key(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        idempotency_key: str,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str:
        """Invoke only through an explicitly idempotent, governed provider adapter."""
        descriptor = self._provider.registry.get(tool_id).descriptor
        if (
            descriptor.effective_retry_class
            not in {ToolRetryClass.IDEMPOTENT_WRITE, ToolRetryClass.NON_IDEMPOTENT_WRITE}
            or descriptor.idempotency_window_seconds is None
            or not descriptor.rejects_idempotency_body_conflict
        ):
            raise RuntimeError("Tool does not declare an idempotency contract.")
        if not idempotency_key or idempotency_key.strip() != idempotency_key:
            raise ValueError("Idempotency key must be non-empty and trimmed.")
        execute = getattr(self._provider, "call_tool_with_idempotency_key", None)
        if not callable(execute):
            raise RuntimeError("Tool provider does not implement idempotent invocation.")
        invoke = cast(Callable[..., Awaitable[str]], execute)
        return await invoke(
            tool_id,
            arguments,
            idempotency_key=idempotency_key,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

    async def prepare_existing_file_write(
        self,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str:
        """Delegate optional compensation preparation when supported by the adapter."""
        prepare = getattr(self._provider, "prepare_existing_file_write", None)
        if prepare is None:
            raise RuntimeError("Tool adapter does not support reversible file writes.")
        prepare_call = cast(Callable[..., Awaitable[str]], prepare)
        return await prepare_call(
            arguments,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

    async def compensate_existing_file_write(
        self,
        token: str,
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> None:
        """Delegate an existing-file compensation through the governed adapter."""
        compensate = getattr(self._provider, "compensate_existing_file_write", None)
        if compensate is None:
            raise RuntimeError("Tool adapter does not support reversible file writes.")
        compensate_call = cast(Callable[..., Awaitable[None]], compensate)
        await compensate_call(
            token,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )


__all__ = ["IdempotentToolInvoker", "ToolFacade", "ToolInvoker"]
