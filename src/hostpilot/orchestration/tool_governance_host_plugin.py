"""Expose one stable, Runtime-owned facade over the active tool provider."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, cast

from hostpilot.budget import RequestBudget
from hostpilot.contracts import SandboxMode, ToolConnectionStatus
from hostpilot.runtime import RuntimeContext, RuntimePluginSpec
from hostpilot.tools.executor import AsyncConfirmationProvider
from hostpilot.tools.facade import ToolFacade, ToolInvoker
from hostpilot.tools.governance import DefaultToolExecutorFactory, ToolExecutorFactory
from hostpilot.tools.registry import ToolRegistry


class _ActiveToolInvoker:
    """Delegate facade operations to the provider bound by the Runtime host."""

    def __init__(self) -> None:
        self._provider: ToolInvoker | None = None

    @property
    def registry(self) -> ToolRegistry:
        return self._require_provider().registry

    def bind(self, provider: ToolInvoker | None) -> None:
        self._provider = provider

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str:
        return await self._require_provider().call_tool(
            name,
            arguments,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

    def connections(self) -> tuple[ToolConnectionStatus, ...]:
        inspect_connections = getattr(self._require_provider(), "connections", None)
        if not callable(inspect_connections):
            return ()
        return cast(tuple[ToolConnectionStatus, ...], inspect_connections())

    def set_sandbox_mode(self, mode: SandboxMode | None) -> None:
        set_mode = getattr(self._require_provider(), "set_sandbox_mode", None)
        if callable(set_mode):
            set_mode(mode)

    async def prepare_existing_file_write(
        self,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str:
        prepare = getattr(self._require_provider(), "prepare_existing_file_write", None)
        if not callable(prepare):
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
        compensate = getattr(self._require_provider(), "compensate_existing_file_write", None)
        if not callable(compensate):
            raise RuntimeError("Tool adapter does not support reversible file writes.")
        compensate_call = cast(Callable[..., Awaitable[None]], compensate)
        await compensate_call(
            token,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

    def _require_provider(self) -> ToolInvoker:
        if self._provider is None:
            raise RuntimeError("The Runtime tool provider is not bound.")
        return self._provider


class ToolGovernanceHostPlugin:
    """Register a stable ToolFacade while the MCP plugin owns provider resources."""

    spec = RuntimePluginSpec(
        "builtin.tool-governance",
        dependencies=("builtin.mcp-provider",),
    )
    service_id = "hostpilot.tool-facade"
    governance_service_id = "hostpilot.tool-governance"

    def __init__(self, executor_factory: ToolExecutorFactory | None = None) -> None:
        self._invoker = _ActiveToolInvoker()
        self._facade = ToolFacade(self._invoker)
        self.executor_factory = executor_factory or DefaultToolExecutorFactory()

    @property
    def facade(self) -> ToolFacade:
        return self._facade

    async def setup(self, context: RuntimeContext) -> None:
        context.services.register(self.service_id, self._facade, owner=self.spec.plugin_id)
        context.services.register(
            self.governance_service_id,
            self.executor_factory,
            owner=self.spec.plugin_id,
        )

    async def start(self, context: RuntimeContext) -> None:
        del context

    async def stop(self, context: RuntimeContext) -> None:
        del context
        self.bind(None)

    def bind(self, provider: ToolInvoker | None) -> None:
        """Switch the facade target without taking ownership of provider resources."""
        self._invoker.bind(provider)


__all__ = ["ToolGovernanceHostPlugin"]
