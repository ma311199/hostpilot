import asyncio

import pytest

from hostpilot.orchestration.application_stores_host_plugin import (
    ApplicationStoresHostPlugin,
)
from hostpilot.runtime import RuntimeContext, RuntimeHost


class FakeStore:
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


class FakeMemoryStore:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("close:memory")


def make_plugin(tmp_path, events: list[str], *, fail_at: str | None = None):
    stores = {
        name: FakeStore(name, events, fail=name == fail_at)
        for name in ("trace", "audit", "cancellation")
    }
    memory = FakeMemoryStore(events)
    plugin = ApplicationStoresHostPlugin(
        tmp_path / "data.sqlite3",
        tmp_path / "memory",
        trace_store_factory=lambda _path: stores["trace"],
        audit_store_factory=lambda _path: stores["audit"],
        cancellation_store_factory=lambda _path: stores["cancellation"],
        memory_store_factory=lambda _path: memory,
    )
    return plugin, (*stores.values(), memory)


def test_application_store_plugin_initializes_registers_and_closes_in_reverse(
    tmp_path,
) -> None:
    async def scenario():
        events: list[str] = []
        plugin, stores = make_plugin(tmp_path, events)
        context = RuntimeContext()
        host = RuntimeHost(context)
        host.register_plugin(plugin)
        await host.start()

        service_ids = (
            plugin.trace_store_service_id,
            plugin.audit_store_service_id,
            plugin.cancellation_store_service_id,
            plugin.memory_store_service_id,
        )
        assert all(context.services.resolve(service_id) in stores for service_id in service_ids)
        await host.close()
        return events, context

    events, context = asyncio.run(scenario())
    assert events == [
        "initialize:trace",
        "initialize:audit",
        "initialize:cancellation",
        "close:memory",
        "close:cancellation",
        "close:audit",
        "close:trace",
    ]
    assert context.services.snapshot() == {}


def test_application_store_plugin_closes_stores_after_initialization_failure(tmp_path) -> None:
    async def scenario():
        events: list[str] = []
        plugin, _ = make_plugin(tmp_path, events, fail_at="audit")
        host = RuntimeHost(RuntimeContext())
        host.register_plugin(plugin)
        with pytest.raises(RuntimeError, match="Required runtime plugin failed"):
            await host.start()
        return events

    events = asyncio.run(scenario())
    assert events == [
        "initialize:trace",
        "initialize:audit",
        "close:memory",
        "close:cancellation",
        "close:audit",
        "close:trace",
    ]
