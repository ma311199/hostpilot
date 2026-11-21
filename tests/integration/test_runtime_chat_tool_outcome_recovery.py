"""Exercise uncertain tool outcome recovery through SQLite and the Chat API."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import MessagesState

from hostpilot.api import create_app
from hostpilot.config import Settings
from hostpilot.models import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelToolDefinition,
)
from hostpilot.orchestration.runtime_chat import RuntimeChatAgentRuntime
from hostpilot.orchestration.runtime_chat_state import LangGraphRuntimeSessionStateAdapter
from hostpilot.runtime import DefaultRuntimeExecutionLoop


class FailingToolResultCheckpoint:
    """Fail once after the tool side effect, then delegate to SQLite."""

    def __init__(self, checkpointer: AsyncSqliteSaver) -> None:
        graph_builder = StateGraph(MessagesState)
        graph_builder.add_node("noop", lambda state: state)
        graph_builder.add_edge(START, "noop")
        graph_builder.add_edge("noop", END)
        self.graph = graph_builder.compile(checkpointer=checkpointer)
        self.adapter = LangGraphRuntimeSessionStateAdapter(self.graph)
        self.fail_tool_result_once = True

    async def load_messages(self, session_id: str) -> tuple[ModelMessage, ...]:
        return await self.adapter.load_messages(session_id)

    async def append_messages(
        self,
        session_id: str,
        messages: tuple[ModelMessage, ...],
    ) -> None:
        if self.fail_tool_result_once and any(
            message.role is ModelRole.TOOL for message in messages
        ):
            self.fail_tool_result_once = False
            raise OSError("injected tool result checkpoint failure")
        await self.adapter.append_messages(session_id, messages)


class SingleToolProposalModel:
    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    async def complete(self, request_context: Any, request: ModelRequest) -> ModelResponse:
        del request_context
        self.calls.append(request)
        return ModelResponse(
            text="",
            tool_calls=(
                ModelToolCall(
                    call_id="side-effect-1",
                    name="record_side_effect",
                    arguments={"value": "once"},
                ),
            ),
        )


class CountingMemoryTool:
    def __init__(self) -> None:
        self.calls: list[ModelToolCall] = []

    async def execute(self, request_context: Any, tool_call: ModelToolCall) -> str:
        del request_context
        self.calls.append(tool_call)
        return "recorded"


def test_chat_api_retry_clarifies_without_repeating_tool_after_sqlite_checkpoint_failure(
    tmp_path: Path,
) -> None:
    model = SingleToolProposalModel()
    tool = CountingMemoryTool()
    runtime_instances: list[RuntimeChatAgentRuntime] = []

    def runtime_factory(checkpointer: AsyncSqliteSaver) -> RuntimeChatAgentRuntime:
        state = FailingToolResultCheckpoint(checkpointer)
        runtime = RuntimeChatAgentRuntime(
            DefaultRuntimeExecutionLoop(model, tool),
            state,
            tools=(
                ModelToolDefinition(
                    name="record_side_effect",
                    description="Record one in-memory side effect.",
                    parameters={"type": "object"},
                ),
            ),
            allowed_tool_ids=frozenset({"record_side_effect"}),
        )
        runtime_instances.append(runtime)
        return runtime

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings, chat_agent_runtime_factory=runtime_factory)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]

    with TestClient(
        create_app(settings, chat_agent_runtime_factory=runtime_factory),
        raise_server_exceptions=False,
    ) as client:
        failed = client.post(
            f"/api/v1/conversations/{session_id}/messages",
            json={"message": "Record this side effect."},
        )

    with TestClient(
        create_app(settings, chat_agent_runtime_factory=runtime_factory),
        raise_server_exceptions=False,
    ) as client:
        retry = client.post(
            f"/api/v1/conversations/{session_id}/messages",
            json={"message": "Please try that again."},
        )

    assert failed.status_code == 500
    assert retry.status_code == 200
    assert retry.json()["route"] == "clarify"
    assert "尚未核实" in retry.json()["assistant_message"]
    assert len(tool.calls) == 1
    assert len(model.calls) == 1
    assert len(runtime_instances) == 3
