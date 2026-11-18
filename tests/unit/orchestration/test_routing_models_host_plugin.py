import asyncio

from hostpilot.orchestration.routing_models_host_plugin import RoutingModelsHostPlugin
from hostpilot.runtime import RuntimeContext, RuntimeHost


class FakeRoutingRuntime:
    def __init__(self, name: str, closed: list[str]) -> None:
        self.name = name
        self.closed = closed

    async def aclose(self) -> None:
        self.closed.append(self.name)


def test_routing_model_plugin_retains_replaced_runtimes_until_host_shutdown() -> None:
    async def scenario():
        closed: list[str] = []
        first = FakeRoutingRuntime("first", closed)
        second = FakeRoutingRuntime("second", closed)
        third = FakeRoutingRuntime("third", closed)
        context = RuntimeContext()
        host = RuntimeHost(context)
        plugin = RoutingModelsHostPlugin()
        host.register_plugin(plugin)

        await host.start()
        assert context.services.resolve(plugin.service_id) is plugin
        await plugin.replace(first)
        await plugin.replace(second)
        await plugin.replace(third)
        assert plugin.runtime is third
        assert closed == []
        await host.close()
        return closed, context, plugin

    closed, context, plugin = asyncio.run(scenario())
    assert closed == ["third", "second", "first"]
    assert plugin.runtime is None
    assert context.services.snapshot() == {}
