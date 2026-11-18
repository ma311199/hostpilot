"""Versioned registry for replaceable chat engine adapters."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

CHAT_RUNTIME_PLUGIN_API_VERSION = "1.0"


@dataclass(frozen=True)
class ChatRuntimePluginContext:
    """Framework-facing dependencies supplied to a chat runtime factory."""

    model: Any
    checkpointer: Any
    skills: Any
    tools: Sequence[Any]


ChatRuntimeFactory = Callable[[ChatRuntimePluginContext], Any]


@dataclass(frozen=True)
class _Registration:
    implementation_id: str
    api_version: str
    factory: ChatRuntimeFactory


class ChatRuntimeRegistry:
    """Create a runtime that satisfies HostPilot's stable AgentRuntime contract."""

    def __init__(self) -> None:
        self._registrations: dict[str, _Registration] = {}

    def register(
        self,
        implementation_id: str,
        factory: ChatRuntimeFactory,
        *,
        api_version: str = CHAT_RUNTIME_PLUGIN_API_VERSION,
    ) -> None:
        if not implementation_id or implementation_id.strip() != implementation_id:
            raise ValueError("Chat runtime implementation IDs must be non-empty and trimmed.")
        if api_version != CHAT_RUNTIME_PLUGIN_API_VERSION:
            raise ValueError(
                f"Unsupported chat runtime plugin API version {api_version!r}; "
                f"expected {CHAT_RUNTIME_PLUGIN_API_VERSION!r}."
            )
        if implementation_id in self._registrations:
            raise ValueError(f"Chat runtime already registered: {implementation_id}.")
        self._registrations[implementation_id] = _Registration(
            implementation_id, api_version, factory
        )

    def create(self, implementation_id: str, context: ChatRuntimePluginContext) -> Any:
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
    "CHAT_RUNTIME_PLUGIN_API_VERSION",
    "ChatRuntimePluginContext",
    "ChatRuntimeRegistry",
]
