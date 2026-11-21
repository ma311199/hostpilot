"""Own process-scoped runtime context and ordered plugin lifecycle."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol
from uuid import uuid4

from hostpilot.runtime.lifecycle import TaskLifecycleManager, TurnLifecycleManager


class RuntimeHostState(StrEnum):
    CREATED = "created"
    STARTING = "starting"
    READY = "ready"
    STOPPING = "stopping"
    STOPPED = "stopped"
    FAILED = "failed"


class RuntimePluginError(RuntimeError):
    """Raised when a required plugin cannot be loaded or started."""


@dataclass(frozen=True)
class RuntimePluginSpec:
    plugin_id: str
    dependencies: tuple[str, ...] = ()
    api_version: str = "1.0"
    optional: bool = False

    def __post_init__(self) -> None:
        if not self.plugin_id or self.plugin_id.strip() != self.plugin_id:
            raise ValueError("Runtime plugin IDs must be non-empty and trimmed.")
        if not self.api_version or self.api_version.strip() != self.api_version:
            raise ValueError("Runtime plugin API versions must be non-empty and trimmed.")
        if self.plugin_id in self.dependencies:
            raise ValueError("A runtime plugin cannot depend on itself.")
        if len(set(self.dependencies)) != len(self.dependencies):
            raise ValueError("Runtime plugin dependencies must be unique.")


class RuntimeServiceRegistry:
    """Resolve explicitly registered in-process services by stable service ID."""

    def __init__(self) -> None:
        self._services: dict[str, tuple[str, Any]] = {}

    def register(self, service_id: str, service: Any, *, owner: str) -> None:
        if not service_id or service_id.strip() != service_id:
            raise ValueError("Runtime service IDs must be non-empty and trimmed.")
        if not owner or owner.strip() != owner:
            raise ValueError("Runtime service owners must be non-empty and trimmed.")
        if service_id in self._services:
            raise ValueError(f"Runtime service is already registered: {service_id}.")
        self._services[service_id] = (owner, service)

    def resolve(self, service_id: str) -> Any:
        registration = self._services.get(service_id)
        if registration is None:
            raise KeyError(f"Runtime service is unavailable: {service_id}.")
        return registration[1]

    def unregister_owner(self, owner: str) -> None:
        for service_id, (service_owner, _) in tuple(self._services.items()):
            if service_owner == owner:
                del self._services[service_id]

    def snapshot(self) -> Mapping[str, str]:
        """Return service ownership without exposing service instances."""
        return {service_id: owner for service_id, (owner, _) in self._services.items()}


@dataclass
class RuntimeContext:
    """Shared process context; request and conversation state belongs elsewhere."""

    services: RuntimeServiceRegistry = field(default_factory=RuntimeServiceRegistry)
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)
    runtime_host_id: str = field(default_factory=lambda: str(uuid4()))
    turns: TurnLifecycleManager = field(default_factory=TurnLifecycleManager)
    tasks: TaskLifecycleManager = field(default_factory=TaskLifecycleManager)


class RuntimePlugin(Protocol):
    """Trusted in-process module managed by RuntimeHost."""

    spec: RuntimePluginSpec

    async def setup(self, context: RuntimeContext) -> None:
        """Register services and validate dependencies before starting resources."""

    async def start(self, context: RuntimeContext) -> None:
        """Start plugin-owned resources after dependencies are ready."""

    async def stop(self, context: RuntimeContext) -> None:
        """Stop plugin tasks and release plugin-owned resources."""


class RuntimeHost:
    """Start plugins in dependency order and stop them in reverse order."""

    def __init__(self, context: RuntimeContext) -> None:
        self.context = context
        self.state = RuntimeHostState.CREATED
        self._plugins: dict[str, RuntimePlugin] = {}
        self._ordered_plugins: list[RuntimePlugin] = []
        self._started_plugins: list[RuntimePlugin] = []
        self._plugin_errors: dict[str, str] = {}
        self._tasks: dict[str, set[asyncio.Task[Any]]] = {}

    @property
    def plugin_errors(self) -> Mapping[str, str]:
        return dict(self._plugin_errors)

    def register_plugin(self, plugin: RuntimePlugin) -> None:
        if self.state is not RuntimeHostState.CREATED:
            raise RuntimeError("Runtime plugins must be registered before startup.")
        if plugin.spec.plugin_id in self._plugins:
            raise ValueError(f"Runtime plugin is already registered: {plugin.spec.plugin_id}.")
        self._plugins[plugin.spec.plugin_id] = plugin

    def create_task(
        self,
        plugin_id: str,
        coroutine: Coroutine[Any, Any, Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]:
        if self.state is not RuntimeHostState.READY:
            coroutine.close()
            raise RuntimeError("Runtime background tasks can start only after the host is ready.")
        if plugin_id not in {plugin.spec.plugin_id for plugin in self._started_plugins}:
            coroutine.close()
            raise ValueError(f"Runtime plugin is not running: {plugin_id}.")
        task = asyncio.create_task(coroutine, name=name or f"runtime:{plugin_id}")
        self._tasks.setdefault(plugin_id, set()).add(task)
        task.add_done_callback(lambda completed: self._forget_task(plugin_id, completed))
        return task

    async def start(self) -> None:
        if self.state is not RuntimeHostState.CREATED:
            raise RuntimeError(f"Runtime host cannot start from state {self.state.value}.")
        self.state = RuntimeHostState.STARTING
        try:
            self._ordered_plugins = self._resolve_order()
            failed_ids: set[str] = set()
            for plugin in self._ordered_plugins:
                spec = plugin.spec
                unavailable = set(spec.dependencies) & failed_ids
                if unavailable:
                    error: Exception | None = RuntimePluginError(
                        f"Dependencies failed to start: {', '.join(sorted(unavailable))}."
                    )
                else:
                    error = None
                if error is None:
                    try:
                        await plugin.setup(self.context)
                        await plugin.start(self.context)
                    except asyncio.CancelledError:
                        self.context.services.unregister_owner(spec.plugin_id)
                        raise
                    except Exception as plugin_error:
                        error = plugin_error
                if error is not None:
                    failed_ids.add(spec.plugin_id)
                    self._plugin_errors[spec.plugin_id] = type(error).__name__
                    self.context.services.unregister_owner(spec.plugin_id)
                    if not spec.optional:
                        raise RuntimePluginError(
                            f"Required runtime plugin failed: {spec.plugin_id}."
                        ) from error
                    continue
                self._started_plugins.append(plugin)
            self.state = RuntimeHostState.READY
        except BaseException:
            await self._stop_started_plugins()
            self.state = RuntimeHostState.FAILED
            raise

    async def close(self) -> None:
        if self.state in {RuntimeHostState.STOPPING, RuntimeHostState.STOPPED}:
            return
        if self.state is RuntimeHostState.CREATED:
            self.context.shutdown_event.set()
            self.state = RuntimeHostState.STOPPED
            return
        self.state = RuntimeHostState.STOPPING
        self.context.shutdown_event.set()
        try:
            await self.context.turns.cancel_all()
        finally:
            try:
                await self.context.tasks.cancel_all()
            finally:
                await self._stop_started_plugins()
                self.state = RuntimeHostState.STOPPED

    def _resolve_order(self) -> list[RuntimePlugin]:
        for plugin in self._plugins.values():
            missing = set(plugin.spec.dependencies) - self._plugins.keys()
            if missing:
                raise RuntimePluginError(
                    f"Plugin {plugin.spec.plugin_id} has missing dependencies: "
                    f"{', '.join(sorted(missing))}."
                )
        ordered: list[RuntimePlugin] = []
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(plugin_id: str) -> None:
            if plugin_id in permanent:
                return
            if plugin_id in temporary:
                raise RuntimePluginError("Runtime plugin dependencies contain a cycle.")
            temporary.add(plugin_id)
            plugin = self._plugins[plugin_id]
            for dependency in sorted(plugin.spec.dependencies):
                visit(dependency)
            temporary.remove(plugin_id)
            permanent.add(plugin_id)
            ordered.append(plugin)

        for plugin_id in sorted(self._plugins):
            visit(plugin_id)
        return ordered

    async def _stop_started_plugins(self) -> None:
        for plugin in reversed(self._started_plugins):
            plugin_id = plugin.spec.plugin_id
            tasks = tuple(self._tasks.pop(plugin_id, ()))
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            try:
                await plugin.stop(self.context)
            except Exception as error:
                self._plugin_errors[plugin_id] = type(error).__name__
            finally:
                self.context.services.unregister_owner(plugin_id)
        self._started_plugins.clear()

    def _forget_task(self, plugin_id: str, task: asyncio.Task[Any]) -> None:
        tasks = self._tasks.get(plugin_id)
        if tasks is not None:
            tasks.discard(task)
            if not tasks:
                self._tasks.pop(plugin_id, None)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                self._plugin_errors[plugin_id] = type(error).__name__
