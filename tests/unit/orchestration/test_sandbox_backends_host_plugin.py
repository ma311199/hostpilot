"""Runtime lifecycle for the shared Sandbox backend set."""

from __future__ import annotations

import asyncio

import pytest

from hostpilot.orchestration.sandbox_backends_host_plugin import (
    SandboxBackendsHostPlugin,
)
from hostpilot.runtime import RuntimeContext, RuntimeHost, RuntimeHostState, RuntimePluginError
from hostpilot.sandbox import LinuxSandboxBackendFactory, SandboxBackendSet


class RecordingFactory:
    def __init__(self) -> None:
        self.calls = 0
        self.backends = LinuxSandboxBackendFactory().create()

    def create(self) -> SandboxBackendSet:
        self.calls += 1
        return self.backends


class FailingFactory:
    def create(self) -> SandboxBackendSet:
        raise RuntimeError("backend construction failed")


def test_plugin_registers_one_factory_result_and_host_close_removes_service() -> None:
    async def scenario():
        context = RuntimeContext()
        host = RuntimeHost(context)
        factory = RecordingFactory()
        plugin = SandboxBackendsHostPlugin(factory)
        host.register_plugin(plugin)

        await host.start()
        registered = context.services.resolve(plugin.service_id)
        plugin_backends = plugin.backends
        await host.close()

        return context, factory, plugin, registered, plugin_backends

    context, factory, plugin, registered, plugin_backends = asyncio.run(scenario())

    assert factory.calls == 1
    assert registered is plugin_backends
    assert context.services.snapshot() == {}
    assert plugin.backends is None


def test_factory_failure_prevents_runtime_readiness_and_leaves_no_service() -> None:
    async def scenario():
        context = RuntimeContext()
        host = RuntimeHost(context)
        plugin = SandboxBackendsHostPlugin(FailingFactory())
        host.register_plugin(plugin)

        with pytest.raises(RuntimePluginError, match="Required runtime plugin failed"):
            await host.start()
        return host, context

    host, context = asyncio.run(scenario())

    assert host.state is RuntimeHostState.FAILED
    assert context.services.snapshot() == {}
