import asyncio
from datetime import UTC, datetime

import pytest

from hostpilot.contracts import (
    ConversationSummary,
    MemoryImportanceTier,
    MemoryScope,
    MemoryWriteProposal,
)
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
    async def get(self, session_id: str) -> ConversationSummary | None:
        if session_id != CONVERSATION_ID:
            return None
        now = datetime.now(UTC)
        return ConversationSummary(
            session_id=session_id,
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

        queued = await review.submit(proposal)
        assert memory.read_long_term_memory() is None
        assert queued.importance_tier is MemoryImportanceTier.MEDIUM
        assert await review.list_pending() == (queued,)

        await review.approve(proposal.proposal_id)

        assert memory.read_long_term_memory() == "Saved preference"
        archives = memory.list_raw_memory_archives("unused")
        assert len(archives) == 1
        assert archives[0].content == "Saved preference"
        assert await review.list_pending() == ()
        assert [event.outcome for event in audit.events] == [
            AuditOutcome.ALLOWED,
            AuditOutcome.ALLOWED,
            AuditOutcome.COMPLETED,
        ]
        assert all(event.resource_id == proposal.proposal_id for event in audit.events)
        assert all("Saved preference" not in str(event.model_dump()) for event in audit.events)

    asyncio.run(scenario())


def test_low_importance_proposal_is_discarded_without_entering_review_queue(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        audit = RecordingAuditSink()
        review = MemoryProposalReview(MemoryWriteService(memory, FakeConversations()), audit=audit)
        proposal = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="好的。")

        decision = await review.submit(proposal)

        assert decision.discarded
        assert decision.importance_tier is MemoryImportanceTier.LOW
        assert await review.list_pending() == ()
        assert memory.read_long_term_memory() is None
        assert audit.events[-1].action == "memory.proposal.discarded"
        assert audit.events[-1].reason_code == "low_importance"

    asyncio.run(scenario())


def test_medium_tier_saves_extractive_summary_and_original_only_after_approval(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        review = MemoryProposalReview(MemoryWriteService(memory, FakeConversations()))
        original = " ".join(
            ["The service stores local metadata in a protected application directory."] * 20
        )
        proposal = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content=original)

        queued = await review.submit(proposal)
        assert queued.importance_tier is MemoryImportanceTier.MEDIUM
        assert queued.summary_content is not None
        assert len(queued.summary_content) < len(original)
        assert memory.read_long_term_memory() is None
        assert memory.list_raw_memory_archives("unused") == ()

        await review.approve(proposal.proposal_id)

        assert memory.read_long_term_memory() == queued.summary_content
        archives = memory.list_raw_memory_archives("unused")
        assert len(archives) == 1
        assert archives[0].content == original
        assert archives[0].importance_score == queued.importance_score

    asyncio.run(scenario())


def test_high_tier_saves_original_without_medium_archive(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        review = MemoryProposalReview(MemoryWriteService(memory, FakeConversations()))
        original = "请记住：我偏好开发答复使用中文，并先说明改动再列验证结果。"
        proposal = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content=original)

        queued = await review.submit(proposal)
        assert queued.importance_tier is MemoryImportanceTier.HIGH
        assert queued.summary_content is None
        await review.approve(proposal.proposal_id)

        assert memory.read_long_term_memory() == original
        assert memory.list_raw_memory_archives("unused") == ()

    asyncio.run(scenario())


def test_archive_capacity_failure_keeps_medium_proposal_pending(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory", max_archives=1)
        review = MemoryProposalReview(MemoryWriteService(memory, FakeConversations()))
        first = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="First saved fact")
        second = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="Second saved fact")

        await review.submit(first)
        await review.approve(first.proposal_id)
        queued = await review.submit(second)

        with pytest.raises(MemoryWriteDenied, match="original memory archive"):
            await review.approve(second.proposal_id)

        assert await review.list_pending() == (queued,)
        assert memory.read_long_term_memory() == first.content
        archives = memory.list_raw_memory_archives("unused")
        assert len(archives) == 1
        assert archives[0].content == first.content

    asyncio.run(scenario())


def test_only_one_pending_proposal_can_target_a_memory_file(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        review = MemoryProposalReview(MemoryWriteService(memory, FakeConversations()))
        first = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="First saved fact")
        second = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="Second saved fact")

        queued_first = await review.submit(first)
        with pytest.raises(DuplicatePendingProposal, match="already pending"):
            await review.submit(second)

        assert await review.list_pending() == (queued_first,)

    asyncio.run(scenario())


def test_rejected_proposal_is_removed_without_writing(tmp_path) -> None:
    async def scenario() -> None:
        memory = MarkdownMemoryStore(tmp_path / "memory")
        audit = RecordingAuditSink()
        review = MemoryProposalReview(
            MemoryWriteService(memory, FakeConversations()),
            audit=audit,
        )
        proposal = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="Rejected saved fact")

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
            session_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            content="Unknown session",
        )

        with pytest.raises(MemoryWriteDenied, match="unknown conversation"):
            await review.submit(proposal)

        assert await review.list_pending() == ()

    asyncio.run(scenario())
