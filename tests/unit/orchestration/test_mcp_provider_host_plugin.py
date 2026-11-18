import asyncio

from hostpilot.orchestration.mcp_provider_host_plugin import MCPProviderHostPlugin
from hostpilot.orchestration.sandbox_backends_host_plugin import (
    SandboxBackendsHostPlugin,
)
from hostpilot.runtime import RuntimeContext, RuntimeHost


class FakeMCPProvider:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_host_plugin_replaces_and_closes_mcp_provider() -> None:
    async def scenario() -> tuple[FakeMCPProvider, FakeMCPProvider, RuntimeContext]:
        context = RuntimeContext()
        host = RuntimeHost(context)
        first = FakeMCPProvider()
        second = FakeMCPProvider()
        plugin = MCPProviderHostPlugin(first)
        host.register_plugin(SandboxBackendsHostPlugin())
        host.register_plugin(plugin)

        await host.start()
        assert context.services.resolve(plugin.service_id) is plugin
        await plugin.replace(second)
        assert plugin.provider is second
        assert first.closed
        await host.close()
        return first, second, context

    first, second, context = asyncio.run(scenario())

    assert first.closed
    assert second.closed
    assert context.services.snapshot() == {}
