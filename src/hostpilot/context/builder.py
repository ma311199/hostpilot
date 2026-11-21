"""Build bounded request context from a conversation checkpoint and Markdown memory."""

from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from hostpilot.contracts import ContextSnapshot, ConversationMessage, MemoryScope
from hostpilot.memory import MemoryChunk
from hostpilot.persistence import ConversationStore


class ConversationStateReader(Protocol):
    async def read_messages(self, session_id: str) -> tuple[ConversationMessage, ...]: ...


class MemoryRecallProvider(Protocol):
    async def recall(
        self,
        *,
        session_id: str,
        query: str,
        max_chars: int | None = None,
        request_id: str | None = None,
    ) -> tuple[MemoryChunk, ...]: ...


class ConversationNotFoundError(KeyError):
    """Raised when context is requested for a conversation that does not exist."""


class ContextBuilder:
    """Load one conversation's history and relevant local memory for an agent turn."""

    def __init__(
        self,
        conversations: ConversationStore,
        memory: MemoryRecallProvider,
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

    async def build(
        self,
        session_id: str,
        *,
        query: str | None = None,
        state_reader: ConversationStateReader | None = None,
    ) -> ContextSnapshot:
        """Validate the session, then load bounded history and scoped Markdown memory."""
        conversation = await self.conversations.get(session_id)
        if conversation is None:
            raise ConversationNotFoundError(session_id)

        request_id = str(uuid4())
        messages = await (state_reader or self.runtime).read_messages(conversation.session_id)
        chunks = await self.memory.recall(
            session_id=conversation.session_id,
            query=query or (messages[-1].content if messages else "conversation context"),
            max_chars=self.max_memory_chars,
            request_id=request_id,
        )
        session_summary = _join_memory_chunks(chunks, MemoryScope.SESSION_SUMMARY)
        long_term_memory = _join_memory_chunks(chunks, MemoryScope.LONG_TERM)
        session_summary, long_term_memory = self._bound_memory(
            session_summary,
            long_term_memory,
        )
        return ContextSnapshot(
            request_id=request_id,
            session_id=conversation.session_id,
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


def _join_memory_chunks(chunks: tuple[MemoryChunk, ...], scope: MemoryScope) -> str | None:
    selected = [chunk.content for chunk in chunks if chunk.scope is scope and chunk.content.strip()]
    return "\n\n".join(selected) if selected else None


__all__ = ["ContextBuilder", "ConversationNotFoundError", "MemoryRecallProvider"]
