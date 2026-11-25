"""Framework-independent LLM request, response, and error contracts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from hostpilot.contracts import ModelTokenUsage


class ModelRole(StrEnum):
    """Roles supported by normalized chat messages."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class ModelMessage:
    """One provider-neutral model message."""

    role: ModelRole
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ModelToolCall, ...] = ()
    turn_id: str | None = None


@dataclass(frozen=True)
class ModelToolDefinition:
    """A JSON-schema tool declaration offered to a model."""

    name: str
    description: str
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class ModelToolCall:
    """A normalized tool proposal returned by a model."""

    call_id: str
    name: str
    arguments: Mapping[str, Any]


@dataclass(frozen=True)
class ModelRequest:
    """One text or tool-enabled model request, without credentials."""

    messages: tuple[ModelMessage, ...]
    tools: tuple[ModelToolDefinition, ...] = ()
    tool_choice: str | Mapping[str, Any] | None = None
    stop: tuple[str, ...] = ()
    temperature: float | None = None
    max_output_tokens: int | None = None
    timeout_seconds: float | None = None
    cancellation_event: asyncio.Event | None = None

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("Model requests must contain at least one message.")
        if self.temperature is not None and not 0 <= self.temperature <= 2:
            raise ValueError("Model temperature must be between 0 and 2.")
        if self.max_output_tokens is not None and self.max_output_tokens < 1:
            raise ValueError("max_output_tokens must be positive.")
        if self.timeout_seconds is not None and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if any(message.role is ModelRole.TOOL for message in self.messages) and any(
            message.role is ModelRole.TOOL and not message.tool_call_id for message in self.messages
        ):
            raise ValueError("Tool messages must include tool_call_id.")


@dataclass(frozen=True)
class ModelResponse:
    """Normalized model output and safe provider metadata."""

    text: str
    tool_calls: tuple[ModelToolCall, ...] = ()
    finish_reason: str | None = None
    token_usage: ModelTokenUsage | None = None
    provider_id: str = "unknown"
    model_id: str = "unknown"
    budget_usage_accounted: bool = False


class ModelProviderErrorCategory(StrEnum):
    """Stable provider failure categories independent of SDK exceptions."""

    AUTHENTICATION = "authentication"
    INVALID_REQUEST = "invalid_request"
    MODEL_NOT_FOUND = "model_not_found"
    RATE_LIMITED = "rate_limited"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ModelProviderError(RuntimeError):
    """Sanitized failure raised by an LLM provider adapter."""

    def __init__(
        self,
        category: ModelProviderErrorCategory,
        *,
        status_code: int | None = None,
        provider_code: str | None = None,
    ) -> None:
        self.category = category
        self.status_code = status_code
        self.provider_code = provider_code
        super().__init__(f"Model provider request failed ({category.value}).")


__all__ = [
    "ModelMessage",
    "ModelProviderError",
    "ModelProviderErrorCategory",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "ModelToolCall",
    "ModelToolDefinition",
]
