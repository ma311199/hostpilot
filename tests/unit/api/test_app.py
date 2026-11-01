import asyncio
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hostpilot.api import create_app
from hostpilot.config import Settings
from hostpilot.contracts import (
    ContextSnapshot,
    ConversationMessage,
    MessageRole,
    ModelTokenUsage,
    Route,
    RouteDecision,
)
from hostpilot.orchestration import AgentResponse
from hostpilot.persistence import MarkdownMemoryStore
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.tools.mcp import build_retrieval_mcp_provider


def test_local_api_manages_conversations_and_deletes_checkpoints(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://127.0.0.1:5173",),
    )

    with TestClient(create_app(settings)) as client:
        response = client.post("/api/v1/conversations", json={"title": "Planning"})
        assert response.status_code == 201
        conversation = response.json()

        listed = client.get("/api/v1/conversations")
        assert [item["conversation_id"] for item in listed.json()] == [
            conversation["conversation_id"]
        ]
        assert (
            client.get(f"/api/v1/conversations/{conversation['conversation_id']}").status_code
            == 200
        )

        deleted = client.delete(f"/api/v1/conversations/{conversation['conversation_id']}")
        assert deleted.status_code == 204
        assert client.get("/api/v1/conversations").json() == []


def test_api_rejects_unknown_conversations_and_enforces_configured_cors(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://127.0.0.1:5173",),
    )

    with TestClient(create_app(settings)) as client:
        missing = client.get("/api/v1/conversations/not-a-uuid")
        assert missing.status_code == 404
        preflight = client.options(
            "/api/v1/conversations",
            headers={
                "Origin": "http://127.0.0.1:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert preflight.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
        rejected_preflight = client.options(
            "/api/v1/conversations",
            headers={
                "Origin": "http://localhost:9000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "access-control-allow-origin" not in rejected_preflight.headers


def test_tool_catalog_reports_safe_configuration_and_approval_status(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        rag_base_url="https://rag.example/api/",
        rag_user_id="private-user",
        rag_key="private-key",
    )

    with TestClient(create_app(settings)) as client:
        response = client.get("/api/v1/tools")

    assert response.status_code == 200
    payload = response.json()
    assert payload["runtime_configured"] is False
    tools = {tool["name"]: tool for tool in payload["tools"]}
    assert set(tools) == {"retrieve_knowledge", "read_text_file", "write_text_file"}
    assert tools["retrieve_knowledge"]["configured"] is True
    assert tools["retrieve_knowledge"]["status_message"].startswith("请先配置模型")
    assert tools["write_text_file"]["high_impact"] is True
    assert tools["write_text_file"]["confirmation_required"] is True
    assert "private-user" not in response.text
    assert "private-key" not in response.text


class FakeChatRuntime:
    def __init__(self) -> None:
        self.messages_by_id: dict[str, list[ConversationMessage]] = {}
        self.contexts: list[ContextSnapshot] = []
        self.closed = False
        self.token_usage: ModelTokenUsage | None = None
        self.request_budget = None

    async def aclose(self) -> None:
        self.closed = True

    async def read_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]:
        return tuple(self.messages_by_id.get(conversation_id, ()))

    async def respond(
        self, message: str, context: ContextSnapshot, *, budget=None
    ) -> AgentResponse:
        self.contexts.append(context)
        self.request_budget = budget
        messages = self.messages_by_id.setdefault(context.conversation_id, [])
        messages.extend(
            (
                ConversationMessage(role=MessageRole.USER, content=message),
                ConversationMessage(role=MessageRole.ASSISTANT, content="Local test reply"),
            )
        )
        return AgentResponse("Local test reply", self.token_usage)

    async def record_turn(
        self,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        self.messages_by_id.setdefault(conversation_id, []).extend(
            (
                ConversationMessage(role=MessageRole.USER, content=user_message),
                ConversationMessage(role=MessageRole.ASSISTANT, content=assistant_message),
            )
        )


def test_chat_api_builds_scoped_context_and_returns_runtime_reply(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://127.0.0.1:5173",),
    )
    runtime = FakeChatRuntime()
    runtime.token_usage = ModelTokenUsage(input_tokens=8, output_tokens=3, total_tokens=11)
    memory = MarkdownMemoryStore(settings.memory_dir)

    with TestClient(create_app(settings, runtime_factory=lambda _: runtime)) as client:
        conversation = client.post(
            "/api/v1/conversations",
            json={"title": "Context test"},
        ).json()
        memory.write_session_summary(conversation["conversation_id"], "Session facts")
        memory.write_long_term_memory("Shared preference")

        response = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/messages",
            json={"message": "Hello"},
        )

        assert response.status_code == 200
        assert response.json()["assistant_message"] == "Local test reply"
        assert response.json()["token_usage"]["total_tokens"] == 11
        assert runtime.request_budget.snapshot().usage.total_tokens == 11
        assert runtime.contexts[0].session_summary == "Session facts"
        assert runtime.contexts[0].long_term_memory == "Shared preference"
        assert runtime.contexts[0].budget.limits.total_tokens == settings.request_token_budget

        history = client.get(f"/api/v1/conversations/{conversation['conversation_id']}/messages")
        assert [message["role"] for message in history.json()["messages"]] == [
            "user",
            "assistant",
        ]

    assert runtime.closed


def test_chat_api_reports_provider_tokens_that_exceed_the_request_budget(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        request_token_budget=5,
    )
    runtime = FakeChatRuntime()
    runtime.token_usage = ModelTokenUsage(input_tokens=4, output_tokens=3, total_tokens=7)

    with TestClient(create_app(settings, runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/messages",
            json={"message": "Hello"},
        )

    assert response.status_code == 429
    assert response.json()["detail"] == "Request budget exhausted for total_tokens."
    assert runtime.request_budget.snapshot().usage.total_tokens == 0


def test_chat_api_rejects_messages_over_the_configured_character_limit(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        max_request_chars=3,
    )
    runtime = FakeChatRuntime()

    with TestClient(create_app(settings, runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/messages",
            json={"message": "four"},
        )

    assert response.status_code == 413
    assert not runtime.contexts


def test_chat_api_returns_gateway_timeout_when_request_budget_expires(tmp_path) -> None:
    class SlowRuntime(FakeChatRuntime):
        async def respond(
            self, message: str, context: ContextSnapshot, *, budget=None
        ) -> AgentResponse:
            await asyncio.sleep(0.05)
            return await super().respond(message, context)

    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        request_timeout_seconds=0.01,
    )
    runtime = SlowRuntime()

    with TestClient(create_app(settings, runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/messages",
            json={"message": "Hello"},
        )

    assert response.status_code == 504


def test_chat_api_rejects_an_oversized_agent_response(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        max_response_chars=5,
    )
    runtime = FakeChatRuntime()

    with TestClient(create_app(settings, runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/messages",
            json={"message": "Hello"},
        )

    assert response.status_code == 502


def test_chat_api_returns_clarification_from_an_injected_router(tmp_path) -> None:
    class ClarifyingRouter:
        def decide(self, message: str, context: ContextSnapshot) -> RouteDecision:
            return RouteDecision(
                request_id=context.request_id,
                route=Route.CLARIFY,
                clarification_prompt="Which project do you mean?",
            )

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = FakeChatRuntime()

    with TestClient(
        create_app(settings, runtime_factory=lambda _: runtime, agent_router=ClarifyingRouter())
    ) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/messages",
            json={"message": "Continue that"},
        )

    assert response.status_code == 200
    assert response.json()["route"] == "clarify"
    assert response.json()["assistant_message"] == "Which project do you mean?"
    assert not runtime.contexts
    history = client.get(f"/api/v1/conversations/{conversation['conversation_id']}/messages")
    assert [item["content"] for item in history.json()["messages"]] == [
        "Continue that",
        "Which project do you mean?",
    ]


def test_chat_api_fails_closed_for_routes_without_an_execution_path(tmp_path) -> None:
    class ToolRouter:
        def decide(self, message: str, context: ContextSnapshot) -> RouteDecision:
            return RouteDecision(
                request_id=context.request_id,
                route=Route.TOOL_CALL,
                tool_name="unregistered",
            )

    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = FakeChatRuntime()

    with TestClient(
        create_app(settings, runtime_factory=lambda _: runtime, agent_router=ToolRouter())
    ) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/messages",
            json={"message": "Use a tool"},
        )

    assert response.status_code == 501
    assert not runtime.contexts


def test_chat_api_reports_missing_agent_runtime(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        response = client.post(
            f"/api/v1/conversations/{conversation['conversation_id']}/messages",
            json={"message": "Hello"},
        )

    assert response.status_code == 503
    assert "model provider" in response.json()["detail"]


def test_memory_proposal_is_written_only_after_explicit_approval(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        submitted = client.post(
            "/api/v1/memory-proposals",
            json={"scope": "long_term", "content": "Approved preference"},
        )
        assert submitted.status_code == 202
        proposal_id = submitted.json()["proposal_id"]
        assert client.get("/api/v1/memory-proposals").json()[0]["proposal_id"] == proposal_id

        approval = client.post(f"/api/v1/memory-proposals/{proposal_id}/approve")

        assert approval.status_code == 204
        assert client.get("/api/v1/memory-proposals").json() == []
        assert (
            MarkdownMemoryStore(settings.memory_dir).read_long_term_memory()
            == "Approved preference"
        )


def test_rejected_memory_proposal_has_no_file_side_effect(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        submitted = client.post(
            "/api/v1/memory-proposals",
            json={
                "scope": "session_summary",
                "conversation_id": conversation["conversation_id"],
                "content": "Must not be saved",
            },
        )
        proposal_id = submitted.json()["proposal_id"]

        rejection = client.delete(f"/api/v1/memory-proposals/{proposal_id}")

        assert rejection.status_code == 204
        assert (
            MarkdownMemoryStore(settings.memory_dir).read_session_summary(
                conversation["conversation_id"]
            )
            is None
        )


def test_memory_proposals_for_deleted_conversations_are_discarded(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        client.post(
            "/api/v1/memory-proposals",
            json={
                "scope": "session_summary",
                "conversation_id": conversation["conversation_id"],
                "content": "Session proposal",
            },
        )

        assert (
            client.delete(f"/api/v1/conversations/{conversation['conversation_id']}").status_code
            == 204
        )
        assert client.get("/api/v1/memory-proposals").json() == []


def test_api_rejects_write_requests_from_untrusted_origins(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://127.0.0.1:5173",),
    )

    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/api/v1/conversations",
            headers={"Origin": "http://localhost:9000"},
            json={"title": "blocked"},
        )

    assert response.status_code == 403


def test_status_websocket_sends_health_and_answers_ping(tmp_path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data",
        memory_dir=tmp_path / "memory",
        web_origins=("http://localhost:5173",),
    )

    with TestClient(create_app(settings)) as client:
        with client.websocket_connect(
            "/api/v1/ws/status",
            headers={"Origin": "http://localhost:5173"},
        ) as websocket:
            snapshot = websocket.receive_json()
            websocket.send_json({"type": "ping", "request_id": "heartbeat-1"})
            pong = websocket.receive_json()

    assert snapshot == {
        "type": "status.snapshot",
        "data": {"api": "ok", "protocol_version": 1},
    }
    assert pong == {"type": "pong", "request_id": "heartbeat-1"}


def test_status_websocket_rejects_untrusted_origin(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        try:
            with client.websocket_connect(
                "/api/v1/ws/status",
                headers={"Origin": "http://attacker.example"},
            ):
                raise AssertionError("Untrusted WebSocket origin was accepted.")
        except WebSocketDisconnect as error:
            assert error.code == 1008


def test_status_websocket_rejects_malformed_and_oversized_messages(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")

    with TestClient(create_app(settings)) as client:
        with client.websocket_connect("/api/v1/ws/status") as websocket:
            websocket.receive_json()
            websocket.send_text("not-json")
            assert websocket.receive_json()["data"]["code"] == "invalid_message"
            websocket.send_text("x" * 1025)
            assert websocket.receive_json()["data"]["code"] == "message_too_large"


class ConfirmingChatRuntime(FakeChatRuntime):
    async def respond(
        self, message, context, *, budget=None, confirmation_provider=None
    ) -> AgentResponse:
        assert confirmation_provider is not None
        decision = await confirmation_provider.confirm(
            ConfirmationRequest(
                request_id="confirmation-1",
                tool_name="write_text_file",
                summary="写入 approval-demo.txt（15 字节）",
            )
        )
        assert decision is ConfirmationDecision.APPROVED
        return await super().respond(message, context, budget=budget)


def test_chat_websocket_waits_for_explicit_tool_confirmation(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    runtime = ConfirmingChatRuntime()

    with TestClient(create_app(settings, runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "conversation_id": conversation["conversation_id"],
                    "message": "Write an approval demo",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation == {
                "type": "tool.confirmation.request",
                "data": {
                    "request_id": "confirmation-1",
                    "tool_name": "write_text_file",
                    "summary": "写入 approval-demo.txt（15 字节）",
                },
            }
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": "confirmation-1",
                    "decision": "approved",
                }
            )
            response = websocket.receive_json()

    assert response["type"] == "chat.response"
    assert response["data"]["assistant_message"] == "Local test reply"
    assert runtime.contexts


class MCPWriteRuntime(FakeChatRuntime):
    """Exercise the real MCP provider from a deterministic model-runtime substitute."""

    def __init__(self, provider) -> None:
        super().__init__()
        self.provider = provider

    async def respond(
        self, message, context, *, budget=None, confirmation_provider=None
    ) -> AgentResponse:
        assert budget is not None
        if not self.provider._started:
            await self.provider.start()
        result = await self.provider.call_tool(
            "write_text_file",
            {"path": "approval-demo.txt", "content": "private content"},
            budget=budget,
            confirmation_provider=confirmation_provider,
        )
        self.contexts.append(context)
        self.messages_by_id.setdefault(context.conversation_id, []).extend(
            (
                ConversationMessage(role=MessageRole.USER, content=message),
                ConversationMessage(role=MessageRole.ASSISTANT, content=result),
            )
        )
        return AgentResponse(result)

    async def aclose(self) -> None:
        await self.provider.aclose()
        self.closed = True


def test_chat_websocket_mcp_write_requires_browser_approval(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    provider = build_retrieval_mcp_provider(settings, workspace_root=tmp_path)
    runtime = MCPWriteRuntime(provider)

    with TestClient(create_app(settings, runtime_factory=lambda _: runtime)) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "conversation_id": conversation["conversation_id"],
                    "message": "Write the approval demo",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation["type"] == "tool.confirmation.request"
            assert confirmation["data"]["tool_name"] == "write_text_file"
            assert "private content" not in confirmation["data"]["summary"]
            websocket.send_json(
                {
                    "type": "tool.confirmation.decision",
                    "request_id": confirmation["data"]["request_id"],
                    "decision": "approved",
                }
            )
            response = websocket.receive_json()

    assert response["type"] == "chat.response"
    assert (tmp_path / "approval-demo.txt").read_text(encoding="utf-8") == "private content"


def test_chat_websocket_mcp_write_rejection_and_cancellation_do_not_write(tmp_path) -> None:
    for decision in ("rejected", "cancelled"):
        settings = Settings(
            data_dir=tmp_path / decision / "data",
            memory_dir=tmp_path / decision / "memory",
        )
        workspace = tmp_path / decision / "workspace"
        workspace.mkdir(parents=True)
        provider = build_retrieval_mcp_provider(settings, workspace_root=workspace)
        runtime = MCPWriteRuntime(provider)

        with TestClient(
            create_app(settings, runtime_factory=lambda _, runtime=runtime: runtime)
        ) as client:
            conversation = client.post("/api/v1/conversations", json={}).json()
            with client.websocket_connect("/api/v1/ws/chat") as websocket:
                websocket.send_json(
                    {
                        "type": "chat.send",
                        "conversation_id": conversation["conversation_id"],
                        "message": "Write the approval demo",
                    }
                )
                confirmation = websocket.receive_json()
                websocket.send_json(
                    {
                        "type": "tool.confirmation.decision",
                        "request_id": confirmation["data"]["request_id"],
                        "decision": decision,
                    }
                )
                response = websocket.receive_json()

        assert response == {
            "type": "chat.error",
            "data": {"code": f"tool_confirmation_{decision}"},
        }
        assert not (workspace / "approval-demo.txt").exists()


def test_chat_websocket_mcp_write_confirmation_timeout_does_not_write(tmp_path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    provider = build_retrieval_mcp_provider(settings, workspace_root=workspace)
    runtime = MCPWriteRuntime(provider)

    with (
        patch("hostpilot.api.app.CHAT_CONFIRMATION_TIMEOUT_SECONDS", 0.01),
        TestClient(create_app(settings, runtime_factory=lambda _: runtime)) as client,
    ):
        conversation = client.post("/api/v1/conversations", json={}).json()
        with client.websocket_connect("/api/v1/ws/chat") as websocket:
            websocket.send_json(
                {
                    "type": "chat.send",
                    "conversation_id": conversation["conversation_id"],
                    "message": "Write the approval demo",
                }
            )
            confirmation = websocket.receive_json()
            assert confirmation["type"] == "tool.confirmation.request"
            response = websocket.receive_json()

    assert response == {
        "type": "chat.error",
        "data": {"code": "tool_confirmation_timed_out"},
    }
    assert not (workspace / "approval-demo.txt").exists()
