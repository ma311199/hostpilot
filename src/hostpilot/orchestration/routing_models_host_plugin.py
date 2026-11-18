"""Own active and replaced routing model provider runtimes."""

from __future__ import annotations

from typing import Any

from hostpilot.runtime import RuntimeContext, RuntimePluginSpec


class RoutingModelsHostPlugin:
    """Keep replaced routing providers alive until Host shutdown, then close them."""

    spec = RuntimePluginSpec("builtin.routing-models")
    service_id = "hostpilot.routing-model-runtime"

    def __init__(self) -> None:
        self._runtime: Any | None = None
        self._retired: list[Any] = []

    @property
    def runtime(self) -> Any | None:
        return self._runtime

    async def setup(self, context: RuntimeContext) -> None:
        context.services.register(self.service_id, self, owner=self.spec.plugin_id)

    async def start(self, context: RuntimeContext) -> None:
        del context

    async def replace(self, runtime: Any | None) -> None:
        previous = self._runtime
        self._runtime = runtime
        if previous is not None and previous is not runtime:
            self._retired.append(previous)

    async def stop(self, context: RuntimeContext) -> None:
        del context
        runtimes, self._retired = self._retired, []
        active, self._runtime = self._runtime, None
        if active is not None:
            runtimes.append(active)
        errors: list[Exception] = []
        for runtime in reversed(runtimes):
            close = getattr(runtime, "aclose", None)
            if callable(close):
                try:
                    await close()
                except Exception as error:
                    errors.append(error)
        if errors:
            raise ExceptionGroup("Failed to close routing model runtimes.", errors)


__all__ = ["RoutingModelsHostPlugin"]
