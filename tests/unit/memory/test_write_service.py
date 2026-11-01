import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hostpilot.contracts import ConversationSummary, MemoryScope, MemoryWriteProposal
from hostpilot.memory import MemoryWriteDenied, MemoryWriteService
from hostpilot.persistence import MarkdownMemoryStore

CONVERSATION_ID = "6c442c1e-6e95-4908-88ab-0b3a3c195443"


class FakeConversations:
    def __init__(self, conversation_id: str | None = CONVERSATION_ID) -> None:
        self.conversation_id = conversation_id

    async def get(self, conversation_id: str) -> ConversationSummary | None:
        if conversation_id != self.conversation_id:
            return None
        now = datetime.now(UTC)
        return ConversationSummary(
            conversation_id=conversation_id,
            title="Memory test",
            created_at=now,
            updated_at=now,
        )


def test_memory_write_requires_explicit_user_approval(tmp_path) -> None:
    memory = MarkdownMemoryStore(tmp_path / "memory")
    service = MemoryWriteService(memory, FakeConversations())
    proposal = MemoryWriteProposal(
        scope=MemoryScope.SESSION_SUMMARY,
        conversation_id=CONVERSATION_ID,
        content="Approved session facts",
    )

    with pytest.raises(MemoryWriteDenied, match="not explicitly approved"):
        asyncio.run(service.apply(proposal, user_approved=False))

    assert memory.read_session_summary(CONVERSATION_ID) is None


def test_approved_session_summary_requires_an_existing_conversation(tmp_path) -> None:
    memory = MarkdownMemoryStore(tmp_path / "memory")
    service = MemoryWriteService(memory, FakeConversations(conversation_id=None))
    proposal = MemoryWriteProposal(
        scope=MemoryScope.SESSION_SUMMARY,
        conversation_id=CONVERSATION_ID,
        content="Session facts",
    )

    with pytest.raises(MemoryWriteDenied, match="unknown conversation"):
        asyncio.run(service.apply(proposal, user_approved=True))

    assert memory.read_session_summary(CONVERSATION_ID) is None


def test_approved_proposals_write_only_to_their_declared_scope(tmp_path) -> None:
    memory = MarkdownMemoryStore(tmp_path / "memory")
    service = MemoryWriteService(memory, FakeConversations())
    session_proposal = MemoryWriteProposal(
        scope=MemoryScope.SESSION_SUMMARY,
        conversation_id=CONVERSATION_ID,
        content="Session summary",
    )
    shared_proposal = MemoryWriteProposal(
        scope=MemoryScope.LONG_TERM,
        content="Shared preference",
    )

    asyncio.run(service.apply(session_proposal, user_approved=True))
    asyncio.run(service.apply(shared_proposal, user_approved=True))

    assert memory.read_session_summary(CONVERSATION_ID) == "Session summary"
    assert memory.read_long_term_memory() == "Shared preference"


def test_memory_proposal_rejects_invalid_scope_and_oversized_content(tmp_path) -> None:
    with pytest.raises(ValidationError, match="require conversation_id"):
        MemoryWriteProposal(scope=MemoryScope.SESSION_SUMMARY, content="Summary")

    with pytest.raises(ValidationError, match="cannot target a conversation"):
        MemoryWriteProposal(
            scope=MemoryScope.LONG_TERM,
            conversation_id=CONVERSATION_ID,
            content="Shared memory",
        )

    memory = MarkdownMemoryStore(tmp_path / "memory", max_file_bytes=5)
    service = MemoryWriteService(memory, FakeConversations())
    proposal = MemoryWriteProposal(scope=MemoryScope.LONG_TERM, content="too long")
    with pytest.raises(MemoryWriteDenied, match="size limit"):
        asyncio.run(service.apply(proposal, user_approved=True))

    assert memory.read_long_term_memory() is None
