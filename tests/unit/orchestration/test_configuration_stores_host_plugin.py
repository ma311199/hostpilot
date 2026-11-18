import asyncio

import pytest

from hostpilot.orchestration.configuration_stores_host_plugin import (
    ConfigurationStoresHostPlugin,
)
from hostpilot.runtime import RuntimeContext, RuntimeHost


class FakeConfigurationStore:
    def __init__(self, name: str, events: list[str], *, fail: bool = False) -> None:
        self.name = name
        self.events = events
        self.fail = fail

    async def initialize(self) -> None:
        self.events.append(f"initialize:{self.name}")
        if self.fail:
            raise RuntimeError(f"failed:{self.name}")

    async def aclose(self) -> None:
        self.events.append(f"close:{self.name}")


def make_plugin(tmp_path, events: list[str], *, fail_at: str | None = None):
    stores = {
        name: FakeConfigurationStore(name, events, fail=name == fail_at)
        for name in ("model", "routing", "mcp", "harness", "workspace", "skill")
    }
    factories = {name: lambda _path, store=store: store for name, store in stores.items()}
    plugin = ConfigurationStoresHostPlugin(
        tmp_path / "hostpilot.sqlite3",
        model_configuration_store_factory=factories["model"],
        routing_model_configuration_store_factory=factories["routing"],
        mcp_configuration_store_factory=factories["mcp"],
        harness_configuration_store_factory=factories["harness"],
        workspace_configuration_store_factory=factories["workspace"],
        skill_execution_store_factory=factories["skill"],
    )
    return plugin, stores


def test_configuration_store_plugin_creates_registers_and_closes_stores(tmp_path) -> None:
    async def scenario():
        events: list[str] = []
        plugin, stores = make_plugin(tmp_path, events)
        context = RuntimeContext()
        host = RuntimeHost(context)
        host.register_plugin(plugin)

        await host.start()
        service_ids = (
            plugin.model_configuration_service_id,
            plugin.routing_model_configuration_service_id,
            plugin.mcp_configuration_service_id,
            plugin.harness_configuration_service_id,
            plugin.workspace_configuration_service_id,
            plugin.skill_execution_service_id,
        )
        assert all(
            context.services.resolve(service_id) in stores.values() for service_id in service_ids
        )
        await host.close()
        return events, context

    events, context = asyncio.run(scenario())
    assert events[:6] == [
        "initialize:model",
        "initialize:routing",
        "initialize:mcp",
        "initialize:harness",
        "initialize:workspace",
        "initialize:skill",
    ]
    assert events[6:] == [
        "close:skill",
        "close:workspace",
        "close:harness",
        "close:mcp",
        "close:routing",
        "close:model",
    ]
    assert context.services.snapshot() == {}


def test_configuration_store_plugin_closes_all_stores_after_start_failure(tmp_path) -> None:
    async def scenario():
        events: list[str] = []
        plugin, _ = make_plugin(tmp_path, events, fail_at="mcp")
        host = RuntimeHost(RuntimeContext())
        host.register_plugin(plugin)
        with pytest.raises(RuntimeError, match="Required runtime plugin failed"):
            await host.start()
        return events

    events = asyncio.run(scenario())
    assert events == [
        "initialize:model",
        "initialize:routing",
        "initialize:mcp",
        "close:skill",
        "close:workspace",
        "close:harness",
        "close:mcp",
        "close:routing",
        "close:model",
    ]
