"""RuntimeHost plugin owning the process-scoped Sandbox backend set."""

from __future__ import annotations

from hostpilot.runtime import RuntimeContext, RuntimePluginSpec
from hostpilot.sandbox import LinuxSandboxBackendFactory, SandboxBackendFactory, SandboxBackendSet


class SandboxBackendsHostPlugin:
    """Create and expose the backend set shared by a RuntimeHost's Providers."""

    spec = RuntimePluginSpec("builtin.sandbox-backends")
    service_id = "hostpilot.sandbox-backends"

    def __init__(self, factory: SandboxBackendFactory | None = None) -> None:
        self._factory = factory or LinuxSandboxBackendFactory()
        self._backends: SandboxBackendSet | None = None

    @property
    def backends(self) -> SandboxBackendSet | None:
        """Return the set while this plugin is set up or running."""
        return self._backends

    async def setup(self, context: RuntimeContext) -> None:
        backends = self._factory.create()
        context.services.register(self.service_id, backends, owner=self.spec.plugin_id)
        self._backends = backends

    async def start(self, context: RuntimeContext) -> None:
        del context

    async def stop(self, context: RuntimeContext) -> None:
        del context
        self._backends = None
