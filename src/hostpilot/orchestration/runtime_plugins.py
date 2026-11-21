"""Versioned registry for replaceable chat engine adapters."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from hostpilot.models import ModelToolDefinition
from hostpilot.runtime import RuntimeExecutionLoop

CHAT_AGENT_RUNTIME_PLUGIN_API_VERSION = "2.0"


@dataclass(frozen=True)
class ChatAgentRuntimePluginContext:
    """Framework-facing dependencies supplied to a chat runtime factory."""

    model: Any
    checkpointer: Any
    skills: Any
    tools: Sequence[Any]
    runtime_execution_loop: RuntimeExecutionLoop | None = None
    runtime_tool_definitions: tuple[ModelToolDefinition, ...] = ()
    runtime_allowed_tool_ids: frozenset[str] = frozenset()


ChatAgentRuntimeFactory = Callable[[ChatAgentRuntimePluginContext], Any]


@dataclass(frozen=True)
class _Registration:
    implementation_id: str
    api_version: str
    factory: ChatAgentRuntimeFactory


class ChatAgentRuntimeRegistry:
    """Create a runtime that satisfies HostPilot's stable ChatAgentRuntime contract."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(
        self,
        implementation_id: str,
        factory: ChatAgentRuntimeFactory,
        *,
        api_version: str = CHAT_AGENT_RUNTIME_PLUGIN_API_VERSION,
    ) -> None:
        if not implementation_id or implementation_id.strip() != implementation_id:
            raise ValueError("Chat runtime implementation IDs must be non-empty and trimmed.")
        if api_version != CHAT_AGENT_RUNTIME_PLUGIN_API_VERSION:
            raise ValueError(
                f"Unsupported chat runtime plugin API version {api_version!r}; "
                f"expected {CHAT_AGENT_RUNTIME_PLUGIN_API_VERSION!r}."
            )
        if implementation_id in self._registrations:
            raise ValueError(f"Chat runtime already registered: {implementation_id}.")
        self._registrations[implementation_id] = _Registration(
            implementation_id, api_version, factory
        )

    def create(self, implementation_id: str, context: ChatAgentRuntimePluginContext) -> Any:
        registration = self._registrations.get(implementation_id)
        if registration is None:
            raise ValueError(f"Unknown chat runtime implementation: {implementation_id}.")
        runtime = registration.factory(context)
        required_methods = ("read_messages", "respond", "record_turn")
        missing = tuple(
            name for name in required_methods if not callable(getattr(runtime, name, None))
        )
        if missing:
            raise TypeError(
                f"Chat runtime {implementation_id} is missing required methods: "
                f"{', '.join(missing)}."
            )
        return runtime

    def implementations(self) -> tuple[str, ...]:
        """Return registered implementation IDs in stable sorted order."""
        return tuple(sorted(self._registrations))


__all__ = [
    "CHAT_AGENT_RUNTIME_PLUGIN_API_VERSION",
    "ChatAgentRuntimePluginContext",
    "ChatAgentRuntimeRegistry",
]
