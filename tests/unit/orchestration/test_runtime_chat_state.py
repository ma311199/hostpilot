from __future__ import annotations

import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from hostpilot.models import ModelMessage, ModelRole, ModelToolCall
from hostpilot.orchestration.runtime_chat_state import LangGraphRuntimeSessionStateAdapter

SESSION_ID = "09b649f1-2a13-4c99-9e5e-cc86f81ba313"


class FakeGraph:
    def __init__(self, messages=()) -> None:
        self.messages = list(messages)
        self.updated: list[tuple[object, dict[str, object]]] = []

    async def aget_state(self, config):
        del config
        return SimpleNamespace(values={"messages": self.messages})

    async def aupdate_state(self, config, values):
        self.updated.append((config, values))
        self.messages.extend(values["messages"])


def test_session_adapter_preserves_tool_call_and_result_ids() -> None:
    graph = FakeGraph(
        [
            HumanMessage(content="查天气"),
            AIMessage(
                content="",
                tool_calls=[{"id": "call-weather-1", "name": "weather", "args": {"city": "杭州"}}],
            ),
            ToolMessage(content="晴天", tool_call_id="call-weather-1"),
            AIMessage(content="杭州今天晴天。"),
        ]
    )
    adapter = LangGraphRuntimeSessionStateAdapter(graph)

    messages = asyncio.run(adapter.load_messages(SESSION_ID))

    assert messages == (
        ModelMessage(role=ModelRole.USER, content="查天气"),
        ModelMessage(
            role=ModelRole.ASSISTANT,
            content="",
            tool_calls=(ModelToolCall("call-weather-1", "weather", {"city": "杭州"}),),
        ),
        ModelMessage(role=ModelRole.TOOL, content="晴天", tool_call_id="call-weather-1"),
        ModelMessage(role=ModelRole.ASSISTANT, content="杭州今天晴天。"),
    )


def test_session_adapter_appends_normalized_messages_to_the_session() -> None:
    graph = FakeGraph()
    adapter = LangGraphRuntimeSessionStateAdapter(graph)

    asyncio.run(
        adapter.append_messages(
            SESSION_ID,
            (
                ModelMessage(role=ModelRole.USER, content="继续"),
                ModelMessage(
                    role=ModelRole.ASSISTANT,
                    content="",
                    tool_calls=(ModelToolCall("call-1", "weather", {"city": "杭州"}),),
                ),
                ModelMessage(role=ModelRole.TOOL, content="晴天", tool_call_id="call-1"),
                ModelMessage(role=ModelRole.ASSISTANT, content="好的。"),
            ),
        )
    )

    assert [message.type for message in graph.messages] == ["human", "ai", "tool", "ai"]
    assert graph.messages[1].tool_calls[0]["id"] == "call-1"
    assert graph.messages[2].tool_call_id == "call-1"
    assert graph.updated[0][0]["configurable"]["thread_id"] == SESSION_ID
