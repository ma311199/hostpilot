import asyncio
from uuid import UUID

import pytest

from hostpilot.contracts import MemoryScope
from hostpilot.memory import MemoryArchiveCandidate, MemoryController, MemoryWriteService
from hostpilot.memory.review import MemoryProposalReview
from hostpilot.observability import TraceEvent
from hostpilot.persistence import MarkdownMemoryStore, SQLiteConversationStore


def test_memory_controller_recalls_only_scoped_and_bounded_items(tmp_path) -> None:
    async def scenario() -> None:
        conversations = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        first = await conversations.create("first")
        second = await conversations.create("second")
        store = MarkdownMemoryStore(tmp_path / "memory")
        store.write_session_summary(first.session_id, "first-session")
        store.write_session_summary(second.session_id, "second-session")
        store.write_long_term_memory("shared-memory")
        review = MemoryProposalReview(MemoryWriteService(store, conversations))
        controller = MemoryController(store, conversations, review, max_recall_chars=20)

        chunks = await controller.recall(
            session_id=first.session_id,
            query="remember this",
        )

        assert [(chunk.scope, chunk.content) for chunk in chunks] == [
            (MemoryScope.SESSION_SUMMARY, "first-session"),
            (MemoryScope.LONG_TERM, "shared-"),
        ]
        assert all(chunk.content != "second-session" for chunk in chunks)

    asyncio.run(scenario())


def test_recall_ranks_matching_chunks_then_keeps_unmatched_fallback(tmp_path) -> None:
    async def scenario() -> None:
        conversations = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        conversation = await conversations.create("hybrid recall")
        store = MarkdownMemoryStore(tmp_path / "memory")
        store.write_session_summary(conversation.session_id, "Ordinary session note.")
        store.write_long_term_memory(
            "# Workspace permissions\n\n"
            "Danger-full-access requires explicit approval before the session is upgraded."
        )
        review = MemoryProposalReview(MemoryWriteService(store, conversations))
        controller = MemoryController(store, conversations, review, max_recall_chars=200)

        chunks = await controller.recall(
            session_id=conversation.session_id,
            query="How does danger full access approval work?",
        )

        assert chunks[0].source == "long_term"
        assert "explicit approval" in chunks[0].content
        assert chunks[1].source == "session_summary"

    asyncio.run(scenario())


def test_memory_controller_rejects_unknown_scope_and_blank_query(tmp_path) -> None:
    async def scenario() -> None:
        conversations = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        store = MarkdownMemoryStore(tmp_path / "memory")
        review = MemoryProposalReview(MemoryWriteService(store, conversations))
        controller = MemoryController(store, conversations, review)

        with pytest.raises(ValueError, match="unknown conversation"):
            await controller.recall(
                session_id="6c442c1e-6e95-4908-88ab-0b3a3c195443",
                query="some query",
            )
        await conversations.create("known")
        conversation = (await conversations.list())[0]
        with pytest.raises(ValueError, match="cannot be blank"):
            await controller.recall(session_id=conversation.session_id, query=" ")

    asyncio.run(scenario())


def test_archive_candidate_waits_for_user_approval_before_storage(tmp_path) -> None:
    async def scenario() -> None:
        conversations = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        conversation = await conversations.create("archive")
        store = MarkdownMemoryStore(tmp_path / "memory")
        review = MemoryProposalReview(MemoryWriteService(store, conversations))
        controller = MemoryController(store, conversations, review)

        proposal = await controller.archive(
            MemoryArchiveCandidate(
                scope=MemoryScope.SESSION_SUMMARY,
                session_id=conversation.session_id,
                source_skill_id="business/meeting-minutes",
                content="Approved summary",
            )
        )

        assert proposal.source_skill_id == "business/meeting-minutes"
        assert store.read_session_summary(conversation.session_id) is None
        assert await review.list_pending() == (proposal,)
        await review.approve(proposal.proposal_id)
        assert store.read_session_summary(conversation.session_id) == "Approved summary"

    asyncio.run(scenario())


def test_trace_events_include_metadata_but_not_memory_text_or_query(tmp_path) -> None:
    class TraceRecorder:
        def __init__(self) -> None:
            self.events: list[TraceEvent] = []

        def record(self, event: TraceEvent) -> None:
            self.events.append(event)

    async def scenario() -> None:
        conversations = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        conversation = await conversations.create("traced")
        store = MarkdownMemoryStore(tmp_path / "memory")
        store.write_session_summary(conversation.session_id, "private memory body")
        review = MemoryProposalReview(MemoryWriteService(store, conversations))
        trace = TraceRecorder()
        request_id = "5d4f23e5-c3b6-4873-bbc4-fdc19592d092"
        controller = MemoryController(store, conversations, review, trace=trace)

        await controller.recall(
            session_id=conversation.session_id,
            query="private query string",
            request_id=request_id,
        )

        assert len(trace.events) == 1
        event = trace.events[0]
        assert event.name == "memory.recall"
        assert event.context.trace_id == UUID(request_id)
        assert event.attributes["status"] == "completed"
        assert event.attributes["chunk_count"] == 1
        assert event.attributes["character_count"] == len("private memory body")
        assert isinstance(event.attributes["duration_ms"], int)
        assert "private memory body" not in str(event.model_dump())
        assert "private query string" not in str(event.model_dump())

    asyncio.run(scenario())


def test_recall_failure_is_traced_without_swallowing_error(tmp_path) -> None:
    class TraceRecorder:
        def __init__(self) -> None:
            self.events: list[TraceEvent] = []

        def record(self, event: TraceEvent) -> None:
            self.events.append(event)

    async def scenario() -> None:
        conversations = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        store = MarkdownMemoryStore(tmp_path / "memory")
        review = MemoryProposalReview(MemoryWriteService(store, conversations))
        trace = TraceRecorder()
        controller = MemoryController(store, conversations, review, trace=trace)
        conversation = await conversations.create("known")

        with pytest.raises(ValueError, match="query cannot be blank"):
            await controller.recall(
                session_id=conversation.session_id,
                query=" ",
                request_id="f8c3a62b-17da-41b2-bbd1-f955e8d35e6d",
            )

        assert trace.events[0].name == "memory.recall"
        assert trace.events[0].attributes["status"] == "failed"
        assert trace.events[0].attributes["reason_code"] == "ValueError"
        assert isinstance(trace.events[0].attributes["duration_ms"], int)

    asyncio.run(scenario())


def test_recall_cancellation_is_traced_and_propagated(tmp_path) -> None:
    class BlockingConversations:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def get(self, session_id: str):
            del session_id
            self.started.set()
            await asyncio.Event().wait()

    class TraceRecorder:
        def __init__(self) -> None:
            self.events: list[TraceEvent] = []

        def record(self, event: TraceEvent) -> None:
            self.events.append(event)

    async def scenario() -> None:
        store = MarkdownMemoryStore(tmp_path / "memory")
        review = MemoryProposalReview(
            MemoryWriteService(store, SQLiteConversationStore(tmp_path / "unused.sqlite3"))
        )
        trace = TraceRecorder()
        conversations = BlockingConversations()
        controller = MemoryController(
            store,
            conversations,
            review,
            trace=trace,
        )
        task = asyncio.create_task(controller.recall(session_id="conversation", query="cancel me"))
        await conversations.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert trace.events[0].attributes["status"] == "cancelled"
        assert isinstance(trace.events[0].attributes["duration_ms"], int)

    asyncio.run(scenario())


def test_trace_sink_failure_does_not_interrupt_recall(tmp_path) -> None:
    class BrokenTraceSink:
        def record(self, event: TraceEvent) -> None:
            del event
            raise RuntimeError("trace backend unavailable")

    async def scenario() -> None:
        conversations = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        conversation = await conversations.create("trace failure")
        store = MarkdownMemoryStore(tmp_path / "memory")
        store.write_long_term_memory("still available")
        review = MemoryProposalReview(MemoryWriteService(store, conversations))
        controller = MemoryController(store, conversations, review, trace=BrokenTraceSink())

        chunks = await controller.recall(
            session_id=conversation.session_id,
            query="read memory",
        )

        assert chunks[0].content == "still available"

    asyncio.run(scenario())
