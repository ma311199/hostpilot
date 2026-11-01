"""Process-local review queue for explicitly approved memory writes."""

from __future__ import annotations

import asyncio

from hostpilot.contracts import MemoryScope, MemoryWriteProposal
from hostpilot.memory import MemoryWriteDenied, MemoryWriteService
from hostpilot.observability import AuditEvent, AuditOutcome, AuditSink


class PendingProposalNotFound(KeyError):
    """Raised when a review action references no pending proposal."""


class DuplicatePendingProposal(ValueError):
    """Raised when another pending proposal already targets the same memory file."""


class MemoryProposalReview:
    """Queue proposals and apply a write only through an explicit approval call.

    The queue is intentionally process-local for this first HTTP workflow. A restart
    discards pending proposals, which cannot cause an unapproved write.
    """

    def __init__(self, writer: MemoryWriteService, *, audit: AuditSink | None = None) -> None:
        self.writer = writer
        self.audit = audit
        self._proposals: dict[str, MemoryWriteProposal] = {}
        self._lock = asyncio.Lock()

    async def submit(self, proposal: MemoryWriteProposal) -> MemoryWriteProposal:
        """Validate and enqueue a proposal without changing the Markdown files."""
        encoded_size = len(proposal.content.encode("utf-8"))
        if encoded_size > self.writer.memory.max_file_bytes:
            raise MemoryWriteDenied("Memory proposal exceeds the configured file size limit.")
        if proposal.scope is MemoryScope.SESSION_SUMMARY:
            conversation_id = proposal.conversation_id
            if (
                conversation_id is None
                or await self.writer.conversations.get(conversation_id) is None
            ):
                raise MemoryWriteDenied("Session summary proposal targets an unknown conversation.")

        async with self._lock:
            if proposal.proposal_id in self._proposals:
                raise DuplicatePendingProposal("Proposal ID is already pending.")
            target = _target_key(proposal)
            if any(_target_key(item) == target for item in self._proposals.values()):
                raise DuplicatePendingProposal(
                    "A proposal for this memory target is already pending."
                )
            self._proposals[proposal.proposal_id] = proposal
            try:
                self._record(
                    "memory.proposal.submitted", AuditOutcome.ALLOWED, proposal.proposal_id
                )
            except Exception:
                del self._proposals[proposal.proposal_id]
                raise
        return proposal

    async def list_pending(self) -> tuple[MemoryWriteProposal, ...]:
        """Return pending proposals in submission order."""
        async with self._lock:
            return tuple(self._proposals.values())

    async def approve(self, proposal_id: str) -> None:
        """Apply the selected proposal and remove it only after a successful write."""
        async with self._lock:
            proposal = self._proposals.get(proposal_id)
            if proposal is None:
                raise PendingProposalNotFound(proposal_id)
            self._record("memory.proposal.approval", AuditOutcome.ALLOWED, proposal_id)
            try:
                await self.writer.apply(proposal, user_approved=True)
            except Exception:
                self._record("memory.proposal.write", AuditOutcome.FAILED, proposal_id)
                raise
            del self._proposals[proposal_id]
            self._record("memory.proposal.write", AuditOutcome.COMPLETED, proposal_id)

    async def reject(self, proposal_id: str) -> None:
        """Discard a proposal without changing persisted memory."""
        async with self._lock:
            if self._proposals.pop(proposal_id, None) is None:
                raise PendingProposalNotFound(proposal_id)
            self._record("memory.proposal.rejected", AuditOutcome.CANCELLED, proposal_id)

    async def remove_for_conversation(self, conversation_id: str) -> None:
        """Discard pending session-summary proposals when their session is deleted."""
        async with self._lock:
            proposal_ids = [
                proposal_id
                for proposal_id, proposal in self._proposals.items()
                if proposal.conversation_id == conversation_id
            ]
            for proposal_id in proposal_ids:
                del self._proposals[proposal_id]
                self._record(
                    "memory.proposal.discarded",
                    AuditOutcome.CANCELLED,
                    proposal_id,
                    reason_code="conversation_deleted",
                )

    def _record(
        self,
        action: str,
        outcome: AuditOutcome,
        proposal_id: str,
        *,
        reason_code: str | None = None,
    ) -> None:
        if self.audit is None:
            return
        self.audit.record(
            AuditEvent(
                action=action,
                outcome=outcome,
                resource_type="memory_proposal",
                resource_id=proposal_id,
                reason_code=reason_code,
            )
        )


def _target_key(proposal: MemoryWriteProposal) -> tuple[MemoryScope, str | None]:
    return proposal.scope, proposal.conversation_id


__all__ = [
    "DuplicatePendingProposal",
    "MemoryProposalReview",
    "PendingProposalNotFound",
]
