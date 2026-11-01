"""HTTP request and response schemas for conversation chat."""

from __future__ import annotations

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


class ConversationHistoryResponse(BaseModel):
    """Conversation metadata and persisted messages."""

    conversation: ConversationSummary
    messages: tuple[ConversationMessage, ...]


__all__ = ["ChatRequest", "ChatResponse", "ConversationHistoryResponse"]
