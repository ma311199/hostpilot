"""RuntimeHost adapter for MCP provider resource ownership."""

from __future__ import annotations

from typing import Any

from hostpilot.runtime import RuntimeContext, RuntimePluginSpec


class MCPProviderHostPlugin:
    """Own the active MCP provider and close it when the Host stops."""

    spec = RuntimePluginSpec(
        "builtin.mcp-provider",
        dependencies=("builtin.sandbox-backends",),
    )
    service_id = "hostpilot.mcp-provider"

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def setup(self, context: RuntimeContext) -> None:
        context.services.register(self.service_id, self, owner=self.spec.plugin_id)

    async def start(self, context: RuntimeContext) -> None:
        del context

    async def stop(self, context: RuntimeContext) -> None:
        del context
        await self.replace(None)

    async def replace(self, provider: Any) -> None:
        previous = self._provider
        if previous is provider:
            return
        if previous is not None:
            await previous.aclose()
        self._provider = provider

    @property
    def provider(self) -> Any:
        if self._provider is None:
            raise RuntimeError("The MCP provider is not available.")
        return self._provider
