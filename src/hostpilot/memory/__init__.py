"""Explicitly approved, policy-checked Markdown memory writes."""

from __future__ import annotations

from typing import Protocol

from hostpilot.contracts import ConversationSummary, MemoryScope, MemoryWriteProposal
from hostpilot.persistence import MarkdownMemoryStore


class ConversationLookup(Protocol):
    """Minimal conversation lookup required to scope session-summary writes."""

    async def get(self, conversation_id: str) -> ConversationSummary | None:
        """Return a conversation when it exists."""


class MemoryWriteDenied(PermissionError):
    """Raised when a proposal fails an approval or scope check."""


class MemoryWriteService:
    """Apply complete memory replacements only after explicit user approval.

    Proposal content remains untrusted reference data. Approval permits storage only; it
    never grants tool authorization or promotes the content to system instructions.
    """

    def __init__(
        self,
        memory: MarkdownMemoryStore,
        conversations: ConversationLookup,
    ) -> None:
        self.memory = memory
        self.conversations = conversations

    async def apply(
        self,
        proposal: MemoryWriteProposal,
        *,
        user_approved: bool,
    ) -> None:
        """Validate a proposal and write it only after an explicit user decision."""
        if not user_approved:
            raise MemoryWriteDenied("Memory proposal was not explicitly approved by the user.")

        encoded_content = proposal.content.encode("utf-8")
        if len(encoded_content) > self.memory.max_file_bytes:
            raise MemoryWriteDenied("Memory proposal exceeds the configured file size limit.")

        if proposal.scope is MemoryScope.SESSION_SUMMARY:
            conversation_id = proposal.conversation_id
            if conversation_id is None:
                raise MemoryWriteDenied("Session summary proposal has no conversation scope.")
            if await self.conversations.get(conversation_id) is None:
                raise MemoryWriteDenied("Session summary proposal targets an unknown conversation.")
            self.memory.write_session_summary(conversation_id, proposal.content)
            return

        self.memory.write_long_term_memory(proposal.content)


__all__ = [
    "ConversationLookup",
    "MemoryWriteDenied",
    "MemoryWriteService",
]
