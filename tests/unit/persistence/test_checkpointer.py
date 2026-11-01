import asyncio
import operator
import stat
from pathlib import Path
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph

from hostpilot.persistence import SQLiteConversationStore, open_sqlite_checkpointer


class ConversationState(TypedDict):
    messages: Annotated[list[str], operator.add]


def _build_graph(checkpointer):
    builder = StateGraph(ConversationState)
    builder.add_node("continue", lambda state: {})
    builder.add_edge(START, "continue")
    builder.add_edge("continue", END)
    return builder.compile(checkpointer=checkpointer)


def test_sqlite_checkpointer_restores_each_conversation_after_reopen(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "hostpilot.sqlite3"
    first_id = "6c442c1e-6e95-4908-88ab-0b3a3c195443"
    second_id = "9fcb6ed8-264e-4729-9b0f-f99f7b853e1f"

    async def scenario() -> None:
        async with open_sqlite_checkpointer(database_path) as checkpointer:
            graph = _build_graph(checkpointer)
            store = SQLiteConversationStore(database_path)

            await graph.ainvoke(
                {"messages": ["first conversation"]},
                store.thread_config(first_id),
            )
            await graph.ainvoke(
                {"messages": ["second conversation"]},
                store.thread_config(second_id),
            )

        async with open_sqlite_checkpointer(database_path) as checkpointer:
            graph = _build_graph(checkpointer)
            first_state = await graph.aget_state(SQLiteConversationStore.thread_config(first_id))
            second_state = await graph.aget_state(SQLiteConversationStore.thread_config(second_id))

        assert first_state.values["messages"] == ["first conversation"]
        assert second_state.values["messages"] == ["second conversation"]

    asyncio.run(scenario())
    assert stat.S_IMODE(database_path.stat().st_mode) == 0o600
