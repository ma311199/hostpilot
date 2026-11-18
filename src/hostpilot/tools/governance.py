"""Factories for policy-first tool execution used by Runtime and MCP adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from hostpilot.budget import RequestBudget
from hostpilot.contracts import ExecutionEnvironment
from hostpilot.observability import AuditEvent, TraceSink
from hostpilot.policy import PolicyEngine
from hostpilot.tools.executor import AsyncConfirmationProvider, ToolExecutor
from hostpilot.tools.registry import ToolRegistry


class ToolExecutionScope(Protocol):
    """Execute operations through one provider-scoped governance policy."""

    @property
    def registry(self) -> ToolRegistry:
        """Return the provider-owned Registry associated with this scope."""

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        granted_capabilities: frozenset[str] = frozenset(),
        confirmation_provider: AsyncConfirmationProvider | None = None,
        _invoke: Callable[[Mapping[str, Any]], Awaitable[Any]] | None = None,
    ) -> Any: ...


class ToolExecutorFactory(Protocol):
    """Construct a governed execution scope for one provider-owned registry."""

    def create_scope(
        self,
        registry: ToolRegistry,
        *,
        sandboxed_environments: frozenset[ExecutionEnvironment],
        sandboxed_tools: frozenset[str] | None,
        audit: Callable[[AuditEvent], None],
        trace_sink: TraceSink | None,
    ) -> ToolExecutionScope:
        """Build a scope with the supplied policy and observability boundary."""

    def create_coordinator(
        self,
        primary_scope: ToolExecutionScope,
        compensation_scope: ToolExecutionScope,
    ) -> ToolInvocationCoordinator:
        """Create a coordinator for one provider's primary and compensation scopes."""


class ToolInvocationCoordinator(Protocol):
    """Coordinate primary and compensating calls through their governed scopes."""

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        granted_capabilities: frozenset[str] = frozenset(),
        confirmation_provider: AsyncConfirmationProvider | None = None,
        invoke: Callable[[Mapping[str, Any]], Awaitable[Any]] | None = None,
    ) -> Any: ...

    async def execute_compensation(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> Any: ...


class _DefaultToolExecutionScope:
    """Hide the concrete executor behind the Runtime execution contract."""

    def __init__(self, executor: ToolExecutor) -> None:
        self._executor = executor

    @property
    def registry(self) -> ToolRegistry:
        return self._executor.registry

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
        return await self._executor.execute(
            name,
            arguments,
            budget=budget,
            granted_capabilities=granted_capabilities,
            confirmation_provider=confirmation_provider,
            _invoke=_invoke,
        )


class _DefaultToolInvocationCoordinator:
    """Route primary and compensation requests to their provider-scoped guards."""

    def __init__(
        self,
        primary_scope: ToolExecutionScope,
        compensation_scope: ToolExecutionScope,
    ) -> None:
        self._primary_scope = primary_scope
        self._compensation_scope = compensation_scope

    async def execute(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        granted_capabilities: frozenset[str] = frozenset(),
        confirmation_provider: AsyncConfirmationProvider | None = None,
        invoke: Callable[[Mapping[str, Any]], Awaitable[Any]] | None = None,
    ) -> Any:
        return await self._primary_scope.execute(
            name,
            arguments,
            budget=budget,
            granted_capabilities=granted_capabilities,
            confirmation_provider=confirmation_provider,
            _invoke=invoke,
        )

    async def execute_compensation(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> Any:
        return await self._compensation_scope.execute(
            name,
            arguments,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )


class DefaultToolExecutorFactory:
    """Build the project's standard PolicyEngine and ToolExecutor pair."""

    def create_scope(
        self,
        registry: ToolRegistry,
        *,
        sandboxed_environments: frozenset[ExecutionEnvironment],
        sandboxed_tools: frozenset[str] | None,
        audit: Callable[[AuditEvent], None],
        trace_sink: TraceSink | None,
    ) -> ToolExecutionScope:
        """Create one execution scope without taking ownership of registry or sinks."""
        policy = PolicyEngine(
            sandboxed_environments=sandboxed_environments,
            sandboxed_tools=sandboxed_tools,
        )
        executor = ToolExecutor(registry, policy, audit=audit, trace_sink=trace_sink)
        return _DefaultToolExecutionScope(executor)

    def create_coordinator(
        self,
        primary_scope: ToolExecutionScope,
        compensation_scope: ToolExecutionScope,
    ) -> ToolInvocationCoordinator:
        """Create provider-scoped coordination without owning either scope."""
        return _DefaultToolInvocationCoordinator(primary_scope, compensation_scope)


__all__ = [
    "DefaultToolExecutorFactory",
    "ToolExecutionScope",
    "ToolExecutorFactory",
    "ToolInvocationCoordinator",
]
