"""Build unpublished Chat Runtime candidates from Runtime-managed services."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Protocol

from hostpilot.config import Settings
from hostpilot.models.factory import ModelProviderRegistry
from hostpilot.orchestration.application_stores_host_plugin import ApplicationStoresHostPlugin
from hostpilot.orchestration.runtime_plugins import ChatAgentRuntimeRegistry
from hostpilot.orchestration.state_services_host_plugin import StateServicesHostPlugin
from hostpilot.runtime import RuntimeContext, RuntimePluginSpec
from hostpilot.skills.plan_plugins import PlanComponentRegistry

if TYPE_CHECKING:
    from hostpilot.orchestration.runtime import ChatAgentRuntime

CHAT_RUNTIME_ASSEMBLY_SERVICE_ID = "hostpilot.chat-runtime-assembly"


@dataclass(frozen=True)
class ChatRuntimeAssemblyRequest:
    """Explicit candidate-specific values required to compose one Chat Runtime."""

    settings: Settings
    tools: Sequence[Any] = ()
    tool_provider: Any | None = None
    memory_controller: Any | None = None
    skill_execution_modes: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "settings", deepcopy(self.settings))
        object.__setattr__(self, "tools", tuple(self.tools))
        object.__setattr__(
            self,
            "skill_execution_modes",
            MappingProxyType(dict(self.skill_execution_modes)),
        )


class ChatRuntimeAssembly(Protocol):
    """Build a candidate runtime without publishing it as the active generation."""

    def build_candidate(self, request: ChatRuntimeAssemblyRequest) -> ChatAgentRuntime:
        """Return a fully configured but unpublished ChatAgentRuntime."""


ChatRuntimeAssemblyFactory = Callable[..., Any]


class ChatRuntimeAssemblyHostPlugin:
    """Expose Chat Runtime composition through the Runtime service registry."""

    spec = RuntimePluginSpec(
        "builtin.chat-runtime-assembly",
        dependencies=("builtin.state-services", "builtin.application-stores"),
    )
    service_id = CHAT_RUNTIME_ASSEMBLY_SERVICE_ID

    def __init__(
        self,
        *,
        plan_component_registry: PlanComponentRegistry | None = None,
        chat_agent_runtime_registry: ChatAgentRuntimeRegistry | None = None,
        model_provider_registry: ModelProviderRegistry | None = None,
        assembly_factory: ChatRuntimeAssemblyFactory | None = None,
    ) -> None:
        self._plan_component_registry = plan_component_registry
        self._chat_agent_runtime_registry = chat_agent_runtime_registry
        self._model_provider_registry = model_provider_registry
        self._assembly_factory = assembly_factory
        self._checkpointer: Any | None = None
        self._trace_sink: Any | None = None
        self._cancellation_sink: Any | None = None

    async def setup(self, context: RuntimeContext) -> None:
        checkpointer = context.services.resolve(StateServicesHostPlugin.checkpointer_service_id)
        trace_sink = context.services.resolve(ApplicationStoresHostPlugin.trace_store_service_id)
        cancellation_sink = context.services.resolve(
            ApplicationStoresHostPlugin.cancellation_store_service_id
        )
        context.services.register(self.service_id, self, owner=self.spec.plugin_id)
        self._checkpointer = checkpointer
        self._trace_sink = trace_sink
        self._cancellation_sink = cancellation_sink

    async def start(self, context: RuntimeContext) -> None:
        del context

    async def stop(self, context: RuntimeContext) -> None:
        del context

    def build_candidate(self, request: ChatRuntimeAssemblyRequest) -> ChatAgentRuntime:
        """Build a candidate using Host-owned services and request-specific inputs."""
        if (
            self._checkpointer is None
            or self._trace_sink is None
            or self._cancellation_sink is None
        ):
            raise RuntimeError("Chat Runtime assembly plugin has not completed setup.")
        factory = self._assembly_factory
        if factory is None:
            from hostpilot.orchestration.chat_runtime_composition import (
                build_configured_chat_agent_runtime,
            )

            factory = build_configured_chat_agent_runtime
        return factory(
            request.settings,
            checkpointer=self._checkpointer,
            tools=request.tools,
            tool_provider=request.tool_provider,
            memory_controller=request.memory_controller,
            cancellation_sink=self._cancellation_sink,
            skill_execution_modes=request.skill_execution_modes,
            trace_sink=self._trace_sink,
            plan_component_registry=self._plan_component_registry,
            chat_agent_runtime_registry=self._chat_agent_runtime_registry,
            model_provider_registry=self._model_provider_registry,
        )


__all__ = [
    "ChatRuntimeAssembly",
    "ChatRuntimeAssemblyFactory",
    "ChatRuntimeAssemblyHostPlugin",
    "ChatRuntimeAssemblyRequest",
    "CHAT_RUNTIME_ASSEMBLY_SERVICE_ID",
]
