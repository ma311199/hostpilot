import asyncio
from datetime import UTC, datetime

import pytest

from hostpilot.contracts import ConversationSummary, MemoryScope, MemoryWriteProposal
from hostpilot.memory import MemoryWriteDenied, MemoryWriteService
from hostpilot.memory.review import (
    DuplicatePendingProposal,
    MemoryProposalReview,
    PendingProposalNotFound,
)
from hostpilot.observability import AuditEvent, AuditOutcome
from hostpilot.persistence import MarkdownMemoryStore

CONVERSATION_ID = "6c442c1e-6e95-4908-88ab-0b3a3c195443"


class FakeConversations:
    async def get(self, conversation_id: str) -> ConversationSummary | None:
        if conversation_id != CONVERSATION_ID:
            return None
        now = datetime.now(UTC)
        return ConversationSummary(
            conversation_id=conversation_id,
            title="Review test",
            created_at=now,
            updated_at=now,
        )


class RecordingAuditSink:
    def __init__(self) -> None:
        self.events: list[AuditEvent] = []

    def record(self, event: AuditEvent) -> None:
        self.events.append(event)


def test_pending_memory_proposal_changes_markdown_only_after_approval(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        writer = MemoryWriteService(memory, FakeConversations())
        audit = RecordingAuditSink()
        review = MemoryProposalReview(writer, audit=audit)
        proposal = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="Saved preference")

        await review.submit(proposal)
        assert memory.read_long_term_memory() is None
        assert await review.list_pending() == (proposal,)

        await review.approve(proposal.proposal_id)

        assert memory.read_long_term_memory() == "Saved preference"
        assert await review.list_pending() == ()
        assert [event.outcome for event in audit.events] == [
            AuditOutcome.ALLOWED,
            AuditOutcome.ALLOWED,
            AuditOutcome.COMPLETED,
        ]
        assert all(event.resource_id == proposal.proposal_id for event in audit.events)
        assert all("Saved preference" not in str(event.model_dump()) for event in audit.events)

    asyncio.run(scenario())


def test_only_one_pending_proposal_can_target_a_memory_file(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        review = MemoryProposalReview(MemoryWriteService(memory, FakeConversations()))
        first = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="First draft")
        second = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="Second draft")

        await review.submit(first)
        with pytest.raises(DuplicatePendingProposal, match="already pending"):
            await review.submit(second)

        assert await review.list_pending() == (first,)

    asyncio.run(scenario())


def test_rejected_proposal_is_removed_without_writing(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        audit = RecordingAuditSink()
        review = MemoryProposalReview(
            MemoryWriteService(memory, FakeConversations()),
            audit=audit,
        )
        proposal = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="Rejected draft")

        await review.submit(proposal)
        await review.reject(proposal.proposal_id)

        assert memory.read_long_term_memory() is None
        assert await review.list_pending() == ()
        assert audit.events[-1].outcome is AuditOutcome.CANCELLED
        with pytest.raises(PendingProposalNotFound):
            await review.approve(proposal.proposal_id)

    asyncio.run(scenario())


def test_submission_for_unknown_session_is_rejected_without_queueing(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        conversations = FakeConversations()
        review = MemoryProposalReview(MemoryWriteService(memory, conversations))
        proposal = MemoryWriteProposal(
            scope=MemoryScope.SESSION_SUMMARY,
            conversation_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            content="Unknown session",
        )

        with pytest.raises(MemoryWriteDenied, match="unknown conversation"):
            await review.submit(proposal)

        assert await review.list_pending() == ()

    asyncio.run(scenario())
