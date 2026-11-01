import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
from fastmcp import Client, FastMCP
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from pydantic import SecretStr

from hostpilot.budget import RequestBudget
from hostpilot.config import RemoteMCPServerSettings, Settings
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ExecutionEnvironment,
    TimeoutProfile,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.retrieval import RagRetrievalClient
from hostpilot.tools.executor import ToolDeniedError
from hostpilot.tools.mcp import MCPToolProvider, build_retrieval_mcp_provider
from hostpilot.tools.registry import ToolRegistration


def _registration(*, capabilities: frozenset[str] = frozenset()) -> ToolRegistration:
    async def unused(_: Mapping[str, Any]) -> Any:
        raise AssertionError("MCP calls should use the intercepted protocol handler.")

    def verify(result: Any, _: Mapping[str, Any]) -> None:
        assert not result.isError
        assert result.structuredContent == {"answer": "ok"}

    return ToolRegistration(
        descriptor=ToolDescriptor(
            name="lookup",
            description="Read a test value",
            tool_type=ToolType.RETRIEVAL,
            trust_level=TrustLevel.THIRD_PARTY,
            execution_env=ExecutionEnvironment.SERVICE,
            timeout_profile=TimeoutProfile(timeout_seconds=2),
            capabilities=capabilities,
        ),
        validate_arguments=lambda arguments: arguments,
        handler=unused,
        verify_result=verify,
    )


def _budget(*, calls: int = 1) -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100, tool_calls=calls, retrieval_calls=calls, timeout_seconds=5
            )
        )
    )


def test_fastmcp_calls_pass_through_tool_executor_and_share_request_budget() -> None:
    async def scenario() -> None:
        server = FastMCP("test")

        @server.tool
        async def lookup(query: str) -> dict[str, str]:
            assert query == "question"
            return {"answer": "ok"}

        provider = MCPToolProvider(server, (_registration(),))
        loaded_tools = await provider.start()
        assert [tool.name for tool in loaded_tools] == ["lookup"]
        assert loaded_tools[0].args_schema["properties"] == {"query": {"type": "string"}}
        budget = _budget()
        request = MCPToolCallRequest(
            name="lookup",
            args={"query": "question"},
            server_name="test",
            runtime=SimpleNamespace(context={"budget": budget}),
        )

        async def handler(call: MCPToolCallRequest) -> Any:
            return await provider.client.session.call_tool(call.name, call.args)

        result = await provider._authorize_and_execute(request, handler)

        assert result.structuredContent == {"answer": "ok"}
        assert budget.snapshot().usage.tool_calls == 1
        assert budget.snapshot().usage.retrieval_calls == 1
        await provider.aclose()

    asyncio.run(scenario())


def test_fastmcp_policy_denial_prevents_protocol_handler_call() -> None:
    async def scenario() -> None:
        server = FastMCP("test")
        provider = MCPToolProvider(server, (_registration(capabilities=frozenset({"admin"})),))
        await provider.start()
        request = MCPToolCallRequest(
            name="lookup",
            args={"query": "question"},
            server_name="test",
            runtime=SimpleNamespace(context={"budget": _budget()}),
        )
        calls: list[str] = []

        async def handler(_: MCPToolCallRequest) -> Any:
            calls.append("called")
            raise AssertionError("Denied calls must not reach MCP.")

        try:
            await provider._authorize_and_execute(request, handler)
        except ToolDeniedError as error:
            assert "capability_not_granted" in str(error)
        else:
            raise AssertionError("Policy must deny a missing capability.")

        assert calls == []
        await provider.aclose()

    asyncio.run(scenario())


def test_rag_mcp_schema_hides_configured_identity_and_secret(tmp_path) -> None:
    async def scenario() -> None:
        settings = Settings(
            rag_base_url="https://rag.example/api/",
            rag_user_id="configured-user",
            rag_key="private-key",
        )
        provider = build_retrieval_mcp_provider(settings)
        tools = await provider.start()
        assert [tool.name for tool in tools] == [
            "retrieve_knowledge",
            "read_text_file",
            "write_text_file",
        ]
        schema = next(tool for tool in tools if tool.name == "retrieve_knowledge").args_schema
        assert schema["properties"] == {"query": {"type": "string"}}
        assert "private-key" not in str(schema)
        assert "configured-user" not in str(schema)
        await provider.aclose()

    asyncio.run(scenario())


def test_mcp_interceptor_fails_closed_without_request_budget() -> None:
    async def scenario() -> None:
        server = FastMCP("test")
        provider = MCPToolProvider(server, (_registration(),))
        request = MCPToolCallRequest(
            name="lookup", args={"query": "question"}, server_name="test", runtime=None
        )
        calls: list[str] = []

        async def handler(_: MCPToolCallRequest) -> Any:
            calls.append("called")
            raise AssertionError("Missing budget must block MCP invocation.")

        try:
            await provider._authorize_and_execute(request, handler)
        except ToolDeniedError as error:
            assert "active request budget" in str(error)
        else:
            raise AssertionError("MCP calls without a request budget must be denied.")
        assert calls == []

    asyncio.run(scenario())


def test_placeholder_rag_tool_returns_disabled_message_without_configuration(tmp_path) -> None:
    async def scenario() -> None:
        provider = build_retrieval_mcp_provider(Settings(), workspace_root=tmp_path)
        await provider.start()
        result = await provider.client.call_tool("retrieve_knowledge", {"query": "find policy"})
        assert result.content[0].text == "知识库暂未开启"
        await provider.aclose()

    asyncio.run(scenario())


def test_configured_rag_mcp_tool_injects_credentials_and_returns_validated_evidence() -> None:
    received: list[httpx.Request] = []
    payload = {
        "evidence": [
            {
                "source_id": "handbook-1",
                "confidence": 0.95,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "retrieval_path": ["vector"],
                "content": "Leave requests require manager approval.",
            }
        ]
    }

    def respond(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json=payload)

    async def scenario() -> str:
        http_client = httpx.AsyncClient(
            base_url="https://rag.example/api/",
            transport=httpx.MockTransport(respond),
        )
        retrieval_client = RagRetrievalClient(
            base_url="https://rag.example/api/",
            retrieval_path="v1/search",
            user_id="local-user",
            key=SecretStr("local-secret"),
            client=http_client,
        )
        settings = Settings(
            rag_base_url="https://rag.example/api/",
            rag_retrieval_path="v1/search",
            rag_user_id="local-user",
            rag_key="local-secret",
        )
        provider = build_retrieval_mcp_provider(settings, retrieval_client=retrieval_client)
        tools = await provider.start()
        retrieval_tool = next(tool for tool in tools if tool.name == "retrieve_knowledge")
        assert retrieval_tool.args_schema["properties"] == {"query": {"type": "string"}}
        assert "local-secret" not in str(retrieval_tool.args_schema)
        assert "local-user" not in str(retrieval_tool.args_schema)
        result = await provider.call_tool(
            "retrieve_knowledge",
            {"query": "leave approval policy"},
            budget=_budget(),
        )
        await provider.aclose()
        await http_client.aclose()
        return result

    result_text = asyncio.run(scenario())
    result = json.loads(result_text)
    assert result["evidence"][0]["source_id"] == "handbook-1"
    assert result["evidence"][0]["content"] == "Leave requests require manager approval."
    assert json.loads(received[0].content) == {
        "user_id": "local-user",
        "key": "local-secret",
        "query": "leave approval policy",
    }


def test_workspace_write_tool_requires_approval_and_uses_executor(tmp_path) -> None:
    from hostpilot.policy.confirmation import ConfirmationDecision

    class ApproveWrite:
        async def confirm(self, request) -> ConfirmationDecision:
            assert request.tool_name == "write_text_file"
            assert "approval-demo.txt" in request.summary
            assert "private content" not in request.summary
            return ConfirmationDecision.APPROVED

    async def scenario() -> None:
        provider = build_retrieval_mcp_provider(Settings(), workspace_root=tmp_path)
        await provider.start()
        arguments = {"path": "approval-demo.txt", "content": "private content"}
        try:
            await provider.call_tool("write_text_file", arguments, budget=_budget())
        except ToolDeniedError as error:
            assert "requires user confirmation" in str(error)
        else:
            raise AssertionError("High-impact file writes must fail closed without approval.")
        assert not (tmp_path / "approval-demo.txt").exists()

        result = await provider.call_tool(
            "write_text_file",
            arguments,
            budget=_budget(),
            confirmation_provider=ApproveWrite(),
        )
        assert result == "已写入 approval-demo.txt（15 字节）。"
        assert (tmp_path / "approval-demo.txt").read_text(encoding="utf-8") == "private content"
        await provider.aclose()

    asyncio.run(scenario())


def test_agent_mcp_interceptor_uses_confirmation_from_runtime_context(tmp_path) -> None:
    from hostpilot.policy.confirmation import ConfirmationDecision

    class ApproveWrite:
        async def confirm(self, request) -> ConfirmationDecision:
            assert request.tool_name == "write_text_file"
            return ConfirmationDecision.APPROVED

    async def scenario() -> None:
        provider = build_retrieval_mcp_provider(Settings(), workspace_root=tmp_path)
        await provider.start()
        budget = _budget()
        request = MCPToolCallRequest(
            name="write_text_file",
            args={"path": "agent-demo.txt", "content": "approved"},
            server_name="test",
            runtime=SimpleNamespace(
                context={"budget": budget, "confirmation_provider": ApproveWrite()}
            ),
        )

        async def handler(call: MCPToolCallRequest) -> Any:
            return await provider.client.session.call_tool(call.name, call.args)

        result = await provider._authorize_and_execute(request, handler)

        assert result.content[0].text == "已写入 agent-demo.txt（8 字节）。"
        assert (tmp_path / "agent-demo.txt").read_text(encoding="utf-8") == "approved"
        await provider.aclose()

    asyncio.run(scenario())


def test_remote_mcp_server_tools_are_namespaced_and_require_approval() -> None:
    from hostpilot.policy.confirmation import ConfirmationDecision

    class ApproveRemoteTool:
        async def confirm(self, request) -> ConfirmationDecision:
            assert request.tool_name in {"knowledge_search", "calendar_search"}
            assert request.tool_name.split("_", maxsplit=1)[0] in request.summary
            return ConfirmationDecision.APPROVED

    async def scenario() -> None:
        knowledge_server = FastMCP("knowledge")

        @knowledge_server.tool(name="search")
        async def search_knowledge(query: str) -> str:
            return f"knowledge: {query}"

        calendar_server = FastMCP("calendar")

        @calendar_server.tool(name="search")
        async def search_calendar(query: str) -> str:
            return f"calendar: {query}"

        remote_config = RemoteMCPServerSettings(name="knowledge", url="https://mcp.example/mcp")
        calendar_config = RemoteMCPServerSettings(
            name="calendar", url="https://calendar.example/mcp"
        )
        provider = MCPToolProvider(
            FastMCP("local"),
            (),
            remote_servers=(remote_config, calendar_config),
            remote_clients={
                "knowledge": Client(knowledge_server),
                "calendar": Client(calendar_server),
            },
        )
        tools = await provider.start()

        assert [tool.name for tool in tools] == ["knowledge_search", "calendar_search"]
        descriptor = provider.registry.get("knowledge_search").descriptor
        assert descriptor.trust_level is TrustLevel.THIRD_PARTY
        assert descriptor.execution_env is ExecutionEnvironment.SERVICE
        assert descriptor.high_impact is True

        try:
            await provider.call_tool("knowledge_search", {"query": "policy"}, budget=_budget())
        except ToolDeniedError as error:
            assert "requires user confirmation" in str(error)
        else:
            raise AssertionError("Remote MCP calls must require approval by default.")

        knowledge_result = await provider.call_tool(
            "knowledge_search",
            {"query": "policy"},
            budget=_budget(),
            confirmation_provider=ApproveRemoteTool(),
        )
        calendar_result = await provider.call_tool(
            "calendar_search",
            {"query": "policy"},
            budget=_budget(),
            confirmation_provider=ApproveRemoteTool(),
        )
        assert knowledge_result == "knowledge: policy"
        assert calendar_result == "calendar: policy"
        await provider.aclose()

    asyncio.run(scenario())


def test_configured_remote_server_uses_streamable_http_and_secret_headers() -> None:
    from fastmcp.client.transports import StreamableHttpTransport

    provider = build_retrieval_mcp_provider(
        Settings(
            mcp_servers=(
                RemoteMCPServerSettings(
                    name="knowledge",
                    url="https://mcp.example/mcp",
                    headers={"Authorization": "Bearer private-token"},
                ),
            )
        )
    )

    client = provider.remote_clients["knowledge"]
    assert isinstance(client.transport, StreamableHttpTransport)
    assert client.transport.url == "https://mcp.example/mcp"
    assert client.transport.headers == {"Authorization": "Bearer private-token"}
    assert "private-token" not in repr(provider.remote_servers)
