"""Build bounded request context from a conversation checkpoint and Markdown memory."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from hostpilot.contracts import ContextSnapshot, ConversationMessage
from hostpilot.persistence import MarkdownMemoryStore, SQLiteConversationStore


class ConversationStateReader(Protocol):
    async def read_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]: ...


class ConversationNotFoundError(KeyError):
    """Raised when context is requested for a conversation that does not exist."""


class ContextBuilder:
    """Load one conversation's history and relevant local memory for an agent turn."""

    def __init__(
        self,
        conversations: SQLiteConversationStore,
        memory: MarkdownMemoryStore,
        runtime: ConversationStateReader,
        *,
        max_messages: int = 40,
        max_memory_chars: int = 16_000,
    ) -> None:
        if max_messages < 1:
            raise ValueError("max_messages must be positive.")
        if max_memory_chars < 1:
            raise ValueError("max_memory_chars must be positive.")
        self.conversations = conversations
        self.memory = memory
        self.runtime = runtime
        self.max_messages = max_messages
        self.max_memory_chars = max_memory_chars

    async def build(self, conversation_id: str) -> ContextSnapshot:
        """Validate the session, then load bounded history and scoped Markdown memory."""
        conversation = await self.conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)

        messages = await self.runtime.read_messages(conversation.conversation_id)
        session_summary = self.memory.read_session_summary(conversation.conversation_id)
        long_term_memory = self.memory.read_long_term_memory()
        session_summary, long_term_memory = self._bound_memory(
            session_summary,
            long_term_memory,
        )
        return ContextSnapshot(
            request_id=str(uuid4()),
            conversation_id=conversation.conversation_id,
            messages=messages[-self.max_messages :],
            session_summary=session_summary,
            long_term_memory=long_term_memory,
        )

    def _bound_memory(
        self,
        session_summary: str | None,
        long_term_memory: str | None,
    ) -> tuple[str | None, str | None]:
        if session_summary is None:
            return None, _truncate(long_term_memory, self.max_memory_chars)
        if long_term_memory is None:
            return _truncate(session_summary, self.max_memory_chars), None
        per_memory_limit = self.max_memory_chars // 2
        return (
            _truncate(session_summary, per_memory_limit),
            _truncate(long_term_memory, self.max_memory_chars - per_memory_limit),
        )


def _truncate(content: str | None, max_chars: int) -> str | None:
    if content is None or len(content) <= max_chars:
        return content
    marker = "\n[Memory truncated to fit the request context.]"
    if max_chars <= len(marker):
        return content[:max_chars]
    return content[: max_chars - len(marker)] + marker


__all__ = ["ContextBuilder", "ConversationNotFoundError"]
