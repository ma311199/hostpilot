"""HTTP request and response schemas for conversation chat."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from hostpilot.contracts import (
    ConversationMessage,
    ConversationSummary,
    ModelTokenUsage,
    Route,
)


class ChatRequest(BaseModel):
    """One new user message submitted to an existing conversation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    message: str = Field(min_length=1, max_length=100_000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message cannot be blank.")
        return value


class ChatResponse(BaseModel):
    """Assistant response associated with a persisted conversation."""

    conversation_id: str
    request_id: str
    assistant_message: str
    route: Route = Route.DIRECT
    clarification_prompt: str | None = None
    token_usage: ModelTokenUsage | None = None


class RoutingEvaluationResponse(BaseModel):
    """Selector result returned without running the selected execution path."""

    request_id: str
    route: Route
    skill_id: str
    reason_code: str
    token_usage: ModelTokenUsage | None = None


class RoutingEvaluationMessage(BaseModel):
    """Ephemeral synthetic history item for isolated routing evaluation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=4_000)


class RoutingEvaluationContext(BaseModel):
    """Bounded synthetic context; never persisted to the conversation or memory store."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    messages: tuple[RoutingEvaluationMessage, ...] = Field(default=(), max_length=8)
    session_summary: str | None = Field(default=None, max_length=4_000)
    long_term_memory: str | None = Field(default=None, max_length=8_000)


class RoutingEvaluationRequest(ChatRequest):
    """Selector-only input with optional bounded synthetic context."""

    evaluation_context: RoutingEvaluationContext | None = None


class ConversationHistoryResponse(BaseModel):
    """Conversation metadata and persisted messages."""

    conversation: ConversationSummary
    messages: tuple[ConversationMessage, ...]


__all__ = [
    "ChatRequest",
    "ChatResponse",
    "ConversationHistoryResponse",
    "RoutingEvaluationResponse",
    "RoutingEvaluationContext",
    "RoutingEvaluationMessage",
    "RoutingEvaluationRequest",
]
