import asyncio
from pathlib import Path

import pytest

from hostpilot.context import ContextBuilder, ConversationNotFoundError
from hostpilot.contracts import ConversationMessage, MessageRole
from hostpilot.persistence import MarkdownMemoryStore, SQLiteConversationStore


class FakeStateReader:
    def __init__(self, messages: tuple[ConversationMessage, ...]) -> None:
        self.messages = messages
        self.requested_ids: list[str] = []

    async def read_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]:
        self.requested_ids.append(conversation_id)
        return self.messages


def test_context_builder_loads_one_session_and_bounds_context(tmp_path: Path) -> None:
    async def scenario() -> None:
        conversations = SQLiteConversationStore(tmp_path / "hostpilot.sqlite3")
        conversation = await conversations.create("Context")
        memory = MarkdownMemoryStore(tmp_path / "memory")
        memory.write_session_summary(conversation.conversation_id, "session summary")
        memory.write_long_term_memory("shared memory")
        messages = tuple(
            ConversationMessage(role=MessageRole.USER, content=f"message {index}")
            for index in range(4)
        )
        reader = FakeStateReader(messages)
        builder = ContextBuilder(
            conversations,
            memory,
            reader,
            max_messages=2,
            max_memory_chars=100,
        )

        context = await builder.build(conversation.conversation_id)

        assert context.conversation_id == conversation.conversation_id
        assert context.session_summary == "session summary"
        assert context.long_term_memory == "shared memory"
        assert [message.content for message in context.messages] == ["message 2", "message 3"]
        assert reader.requested_ids == [conversation.conversation_id]

    asyncio.run(scenario())


def test_context_builder_rejects_unknown_conversations(tmp_path: Path) -> None:
    async def scenario() -> None:
        builder = ContextBuilder(
            SQLiteConversationStore(tmp_path / "hostpilot.sqlite3"),
            MarkdownMemoryStore(tmp_path / "memory"),
            FakeStateReader(()),
        )
        with pytest.raises(ConversationNotFoundError):
            await builder.build("6c442c1e-6e95-4908-88ab-0b3a3c195443")

    asyncio.run(scenario())
