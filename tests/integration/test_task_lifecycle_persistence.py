"""Exercise TaskRun persistence through the Chat API and app restart."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from hostpilot.api import create_app
from hostpilot.config import Settings
from hostpilot.orchestration.runtime import ChatAgentResponse
from hostpilot.persistence import SQLiteTaskStore, SQLiteTurnStore


class DeterministicChatRuntime:
    async def read_messages(self, session_id: str) -> tuple[()]:
        del session_id
        return ()

    async def respond(self, message, request_context) -> ChatAgentResponse:
        del message
        lifecycle = request_context.task_lifecycle
        if lifecycle is not None:
            async with lifecycle.track(
                "deterministic_task",
                request_context.snapshot.request_id,
                request_context.snapshot.session_id,
            ):
                return ChatAgentResponse("deterministic reply")
        return ChatAgentResponse("deterministic reply")

    async def record_turn(self, session_id, user_message, assistant_message) -> None:
        del session_id, user_message, assistant_message


def test_chat_api_task_state_survives_application_restart(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(
        create_app(settings, chat_agent_runtime_factory=lambda _: DeterministicChatRuntime())
    ) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['session_id']}/messages",
            json={"message": "Please reply."},
        )
        assert response.status_code == 200, response.text
        turn_id = response.json()["request_id"]

    with TestClient(
        create_app(settings, chat_agent_runtime_factory=lambda _: DeterministicChatRuntime())
    ):
        turns = asyncio.run(SQLiteTurnStore(settings.data_dir / "hostpilot.sqlite3").list_turns())
        task_runs = asyncio.run(
            SQLiteTaskStore(settings.data_dir / "hostpilot.sqlite3").list_task_runs(turn_id=turn_id)
        )

    assert len(turns) == 1
    assert turns[0].turn_id == turn_id
    assert turns[0].state == "completed"
    assert len(task_runs) == 1
    assert task_runs[0].turn_id == turn_id
    assert task_runs[0].session_id == conversation["session_id"]
    assert task_runs[0].task_id == "deterministic_task"
    assert task_runs[0].state == "completed"
    assert task_runs[0].finished_at is not None
