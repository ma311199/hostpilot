"""Explicitly approved, policy-checked Markdown memory writes."""

from __future__ import annotations

import asyncio
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5

from hostpilot.contracts import (
    ConversationSummary,
    MemoryImportanceTier,
    MemoryRawArchive,
    MemoryScope,
    MemoryWriteProposal,
)
from hostpilot.memory.controller import MemoryArchiveCandidate, MemoryChunk, MemoryController
from hostpilot.memory.scoring import assess_memory_content
from hostpilot.persistence import MemoryStore


class ConversationLookup(Protocol):
    """Minimal conversation lookup required to scope session-summary writes."""

    async def get(self, session_id: str) -> ConversationSummary | None:
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
        memory: MemoryStore,
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

        assessment = assess_memory_content(proposal.content)
        if assessment.tier is MemoryImportanceTier.LOW:
            raise MemoryWriteDenied("Low-importance memory proposals are discarded.")

        encoded_content = proposal.content.encode("utf-8")
        if len(encoded_content) > self.memory.max_file_bytes:
            raise MemoryWriteDenied("Memory proposal exceeds the configured file size limit.")

        if assessment.tier is MemoryImportanceTier.MEDIUM:
            summary = assessment.summary
            if summary is None:
                raise MemoryWriteDenied("Medium-importance memory requires a summary.")
            archive = MemoryRawArchive(
                archive_id=uuid5(NAMESPACE_URL, f"hostpilot-memory:{proposal.proposal_id}"),
                proposal_id=proposal.proposal_id,
                scope=proposal.scope,
                session_id=proposal.session_id,
                importance_score=assessment.score,
                content=proposal.content,
            )
            try:
                self.memory.archive_raw_memory(archive)
            except ValueError as error:
                raise MemoryWriteDenied(
                    "The original memory archive could not be stored."
                ) from error
            try:
                await self._write_content(proposal, summary)
            except asyncio.CancelledError:
                self.memory.delete_raw_memory_archive(archive.archive_id)
                raise
            except Exception:
                self.memory.delete_raw_memory_archive(archive.archive_id)
                raise
            return

        await self._write_content(proposal, proposal.content)

    async def _write_content(self, proposal: MemoryWriteProposal, content: str) -> None:
        if proposal.scope is MemoryScope.SESSION_SUMMARY:
            session_id = proposal.session_id
            if session_id is None:
                raise MemoryWriteDenied("Session summary proposal has no conversation scope.")
            if await self.conversations.get(session_id) is None:
                raise MemoryWriteDenied("Session summary proposal targets an unknown conversation.")
            self.memory.write_session_summary(session_id, content)
            return

        self.memory.write_long_term_memory(content)


__all__ = [
    "ConversationLookup",
    "MemoryArchiveCandidate",
    "MemoryChunk",
    "MemoryController",
    "MemoryWriteDenied",
    "MemoryWriteService",
]
