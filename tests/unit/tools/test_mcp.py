import asyncio
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastmcp import Client, FastMCP
from jsonschema import ValidationError
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from pydantic import SecretStr

from hostpilot.budget import RequestBudget
from hostpilot.config import RemoteMCPServerSettings, Settings
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ExecutionEnvironment,
    SandboxMode,
    TimeoutProfile,
    ToolConnectionState,
    ToolDescriptor,
    ToolSideEffect,
    ToolType,
    TrustLevel,
)
from hostpilot.retrieval import RagRetrievalClient
from hostpilot.sandbox import (
    LinuxSandboxBackendFactory,
    SandboxBackendSet,
)
from hostpilot.sandbox.linux import (
    LinuxJavaScriptSandbox,
    LinuxPythonSandbox,
    LinuxShellSandbox,
    SandboxRunResult,
)
from hostpilot.tools.executor import (
    RemoteToolOutcomeUnknownError,
    ToolDeniedError,
)
from hostpilot.tools.governance import DefaultToolExecutorFactory
from hostpilot.tools.mcp import (
    MCPToolProvider,
    _remote_tool_registration,
    build_retrieval_mcp_provider,
)
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


class RecordingExecutionScope:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.registry = inner.registry
        self.calls: list[str] = []

    async def execute(self, name, arguments, **options):
        self.calls.append(name)
        return await self.inner.execute(name, arguments, **options)


class RecordingExecutionScopeFactory:
    def __init__(self) -> None:
        self.calls = []
        self.scopes: list[RecordingExecutionScope] = []
        self.coordinators: list[RecordingInvocationCoordinator] = []

    def create_scope(self, registry, **configuration):
        self.calls.append((registry, configuration))
        inner = DefaultToolExecutorFactory().create_scope(registry, **configuration)
        scope = RecordingExecutionScope(inner)
        self.scopes.append(scope)
        return scope

    def create_coordinator(self, primary_scope, compensation_scope):
        inner = DefaultToolExecutorFactory().create_coordinator(primary_scope, compensation_scope)
        coordinator = RecordingInvocationCoordinator(inner)
        self.coordinators.append(coordinator)
        return coordinator


class RecordingInvocationCoordinator:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.primary_calls: list[str] = []
        self.compensation_calls: list[str] = []

    async def execute(self, name, arguments, **options):
        self.primary_calls.append(name)
        return await self.inner.execute(name, arguments, **options)

    async def execute_compensation(self, name, arguments, **options):
        self.compensation_calls.append(name)
        return await self.inner.execute_compensation(name, arguments, **options)


def test_mcp_provider_uses_runtime_coordinator_for_both_registries() -> None:
    factory = RecordingExecutionScopeFactory()
    sandboxed_tools = frozenset({"lookup", "_restore_existing_text_file"})
    sandboxed_environments = frozenset({ExecutionEnvironment.NATIVE_SANDBOX})
    provider = MCPToolProvider(
        FastMCP("test"),
        (_registration(),),
        executor_factory=factory,
        sandboxed_environments=sandboxed_environments,
        sandboxed_tools=sandboxed_tools,
    )

    assert len(factory.calls) == 2
    primary_registry, primary_configuration = factory.calls[0]
    compensation_registry, compensation_configuration = factory.calls[1]
    assert primary_registry is provider.registry
    assert compensation_registry is provider._compensation_registry
    assert primary_registry is not compensation_registry
    assert primary_configuration["sandboxed_environments"] == sandboxed_environments
    assert primary_configuration["sandboxed_tools"] == sandboxed_tools
    assert compensation_configuration["sandboxed_environments"] == sandboxed_environments
    assert compensation_configuration["sandboxed_tools"] == sandboxed_tools
    assert provider._tool_coordinator is factory.coordinators[0]


def test_fastmcp_calls_use_runtime_coordinator_and_share_request_budget() -> None:
    async def scenario() -> None:
        server = FastMCP("test")

        @server.tool
        async def lookup(query: str) -> dict[str, str]:
            assert query == "question"
            return {"answer": "ok"}

        factory = RecordingExecutionScopeFactory()
        provider = MCPToolProvider(server, (_registration(),), executor_factory=factory)
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
        assert factory.coordinators[0].primary_calls == ["lookup"]
        assert budget.snapshot().usage.tool_calls == 1
        assert budget.snapshot().usage.retrieval_calls == 1
        local_result = await provider.call_tool(
            "lookup",
            {"query": "question"},
            budget=_budget(),
        )
        assert "ok" in local_result
        assert factory.coordinators[0].primary_calls == ["lookup", "lookup"]
        await provider.aclose()

    asyncio.run(scenario())


def test_fastmcp_policy_denial_prevents_protocol_handler_call() -> None:
    async def scenario() -> None:
        server = FastMCP("test")
        provider = MCPToolProvider(server, (_registration(capabilities=frozenset({"admin"})),))
        assert provider._tool_coordinator is not None
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
            "read_text_range",
            "list_directory",
            "search_files",
            "write_text_file",
            "write_existing_text_file",
            "run_python_code",
            "run_javascript_code",
            "run_shell_script",
        ]
        schema = next(tool for tool in tools if tool.name == "retrieve_knowledge").args_schema
        assert schema["properties"] == {"query": {"type": "string"}}
        assert "private-key" not in str(schema)
        assert "configured-user" not in str(schema)
        await provider.aclose()

    asyncio.run(scenario())


def test_builtin_tool_descriptors_mark_only_reads_as_side_effect_free() -> None:
    async def scenario() -> None:
        provider = build_retrieval_mcp_provider(Settings())
        await provider.start()
        effects = {
            descriptor.name: descriptor.side_effect
            for descriptor in provider.registry.descriptors()
        }
        assert effects["retrieve_knowledge"] is ToolSideEffect.NONE
        assert effects["read_text_file"] is ToolSideEffect.NONE
        assert effects["read_text_range"] is ToolSideEffect.NONE
        assert effects["list_directory"] is ToolSideEffect.NONE
        assert effects["search_files"] is ToolSideEffect.NONE
        assert effects["write_text_file"] is ToolSideEffect.POSSIBLE
        assert effects["write_existing_text_file"] is ToolSideEffect.POSSIBLE
        assert effects["run_python_code"] is ToolSideEffect.POSSIBLE
        assert effects["run_javascript_code"] is ToolSideEffect.POSSIBLE
        assert effects["run_shell_script"] is ToolSideEffect.POSSIBLE
        await provider.aclose()

    asyncio.run(scenario())


def test_bounded_workspace_discovery_and_range_tools_use_governed_provider(tmp_path) -> None:
    async def scenario() -> None:
        (tmp_path / "large.txt").write_text("line-1\nline-2\n", encoding="utf-8")
        (tmp_path / "nested").mkdir()
        (tmp_path / "nested" / "notes.md").write_text("notes", encoding="utf-8")
        provider = build_retrieval_mcp_provider(Settings(), workspace_root=tmp_path)
        await provider.start()
        try:
            listed = json.loads(await provider.call_tool("list_directory", {}, budget=_budget()))
            searched = json.loads(
                await provider.call_tool("search_files", {"pattern": "*.md"}, budget=_budget())
            )
            section = await provider.call_tool(
                "read_text_range",
                {"path": "large.txt", "offset": 7, "length": 7},
                budget=_budget(),
            )
        finally:
            await provider.aclose()

        assert {entry["name"] for entry in listed["entries"]} == {"large.txt", "nested"}
        assert searched["matches"] == ["nested/notes.md"]
        assert section == "line-2\n"

    asyncio.run(scenario())


@pytest.mark.skipif(
    not LinuxJavaScriptSandbox().host_supported or not LinuxShellSandbox().host_supported,
    reason="Linux bubblewrap, Node.js and POSIX shell runtimes are required.",
)
def test_javascript_and_shell_tools_require_approval_and_run_in_sandboxes(tmp_path) -> None:
    from hostpilot.policy.confirmation import ConfirmationDecision

    class Approve:
        async def confirm(self, request):
            assert request.tool_name in {"run_javascript_code", "run_shell_script"}
            assert "无网络" in request.summary
            return ConfirmationDecision.APPROVED

    async def scenario() -> None:
        provider = build_retrieval_mcp_provider(Settings(), workspace_root=tmp_path)
        await provider.start()
        try:
            javascript = json.loads(
                await provider.call_tool(
                    "run_javascript_code",
                    {"code": "console.log('javascript-isolated')"},
                    budget=_budget(),
                    confirmation_provider=Approve(),
                )
            )
            shell = json.loads(
                await provider.call_tool(
                    "run_shell_script",
                    {"script": "printf 'shell-isolated\\n'"},
                    budget=_budget(),
                    confirmation_provider=Approve(),
                )
            )
        finally:
            await provider.aclose()

        assert javascript["status"] == "completed"
        assert javascript["stdout"] == "javascript-isolated\n"
        assert shell["status"] == "completed"
        assert shell["stdout"] == "shell-isolated\n"

    asyncio.run(scenario())


@pytest.mark.skipif(
    not LinuxPythonSandbox().host_supported,
    reason="Linux bubblewrap and prlimit are not installed on this host.",
)
def test_python_code_tool_requires_confirmation_and_runs_through_native_sandbox(tmp_path) -> None:
    from hostpilot.policy.confirmation import ConfirmationDecision

    class Approve:
        async def confirm(self, _request):
            return ConfirmationDecision.APPROVED

    async def scenario() -> None:
        provider = build_retrieval_mcp_provider(Settings(), workspace_root=tmp_path)
        tools = await provider.start()
        assert "run_python_code" in {tool.name for tool in tools}

        try:
            result = await provider.call_tool(
                "run_python_code",
                {"code": "print('isolated')"},
                budget=_budget(),
                confirmation_provider=Approve(),
            )
        finally:
            await provider.aclose()

        response = json.loads(result)
        assert response["status"] == "completed"
        assert response["stdout"] == "isolated\n"

    asyncio.run(scenario())


def test_sandbox_backend_injection_reaches_filesystem_and_code_handlers(tmp_path) -> None:
    class RecordingFilesystemBackend:
        host_supported = True
        unavailable_reason = None

        def __init__(self) -> None:
            self.calls = []

        async def execute(self, workspace, operation, arguments):
            self.calls.append((workspace, operation, arguments))
            return "injected file result"

    class RecordingCodeBackend:
        host_supported = True
        unavailable_reason = None
        max_source_bytes = 65_536

        def __init__(self, label: str) -> None:
            self.label = label
            self.sources = []

        async def run(self, source: str) -> SandboxRunResult:
            self.sources.append(source)
            return SandboxRunResult("completed", 0, self.label, "", 1)

    class Approve:
        async def confirm(self, _request):
            from hostpilot.policy.confirmation import ConfirmationDecision

            return ConfirmationDecision.APPROVED

    async def scenario() -> None:
        filesystem = RecordingFilesystemBackend()
        python = RecordingCodeBackend("python backend")
        javascript = RecordingCodeBackend("javascript backend")
        shell = RecordingCodeBackend("shell backend")
        provider = build_retrieval_mcp_provider(
            Settings(),
            workspace_root=tmp_path,
            sandbox_backends=SandboxBackendSet(filesystem, python, javascript, shell),
        )
        await provider.start()
        try:
            file_result = await provider.call_tool(
                "read_text_file", {"path": "notes.txt"}, budget=_budget()
            )
            code_results = {
                "python": json.loads(
                    await provider.call_tool(
                        "run_python_code",
                        {"code": "print('p')"},
                        budget=_budget(),
                        confirmation_provider=Approve(),
                    )
                ),
                "javascript": json.loads(
                    await provider.call_tool(
                        "run_javascript_code",
                        {"code": "console.log('j')"},
                        budget=_budget(),
                        confirmation_provider=Approve(),
                    )
                ),
                "shell": json.loads(
                    await provider.call_tool(
                        "run_shell_script",
                        {"script": "echo s"},
                        budget=_budget(),
                        confirmation_provider=Approve(),
                    )
                ),
            }
        finally:
            await provider.aclose()

        assert file_result == "injected file result"
        assert filesystem.calls[0][1:] == ("read_text_file", {"path": "notes.txt"})
        assert code_results["python"]["stdout"] == "python backend"
        assert code_results["javascript"]["stdout"] == "javascript backend"
        assert code_results["shell"]["stdout"] == "shell backend"
        assert python.sources == ["print('p')"]
        assert javascript.sources == ["console.log('j')"]
        assert shell.sources == ["echo s"]

    asyncio.run(scenario())


def test_direct_builder_uses_default_linux_backend_factory(monkeypatch) -> None:
    calls = []
    default_create = LinuxSandboxBackendFactory.create

    def create(self):
        backends = default_create(self)
        calls.append(backends)
        return backends

    monkeypatch.setattr(LinuxSandboxBackendFactory, "create", create)

    provider = build_retrieval_mcp_provider(Settings())

    assert len(calls) == 1
    assert provider.filesystem_sandbox is calls[0].filesystem


def test_existing_file_write_compensation_uses_runtime_coordinator(tmp_path) -> None:
    from hostpilot.policy.confirmation import ConfirmationDecision

    class Approve:
        async def confirm(self, request):
            assert request.tool_name in {"write_existing_text_file", "_restore_existing_text_file"}
            return ConfirmationDecision.APPROVED

    async def scenario() -> None:
        target = tmp_path / "notes.txt"
        target.write_text("before", encoding="utf-8")
        factory = RecordingExecutionScopeFactory()
        provider = build_retrieval_mcp_provider(
            Settings(), workspace_root=tmp_path, executor_factory=factory
        )
        provider.set_sandbox_mode(SandboxMode.WORKSPACE_WRITE)
        await provider.start()
        request_budget = _budget(calls=5)
        token = await provider.prepare_existing_file_write(
            {"path": "notes.txt", "content": "after"},
            budget=request_budget,
            confirmation_provider=Approve(),
        )
        await provider.call_tool(
            "write_existing_text_file",
            {"path": "notes.txt", "content": "after"},
            budget=request_budget,
            confirmation_provider=Approve(),
        )
        assert target.read_text(encoding="utf-8") == "after"
        await provider.compensate_existing_file_write(
            token, budget=request_budget, confirmation_provider=Approve()
        )
        assert target.read_text(encoding="utf-8") == "before"
        assert factory.coordinators[0].compensation_calls == ["_restore_existing_text_file"]
        await provider.aclose()

    asyncio.run(scenario())


def test_existing_file_restore_is_noop_if_write_never_happened_and_rejects_conflicts(
    tmp_path,
) -> None:
    async def scenario() -> None:
        from hostpilot.tools.filesystem import WorkspaceFileError, WorkspaceFiles

        target = tmp_path / "notes.txt"
        target.write_text("before", encoding="utf-8")
        workspace = WorkspaceFiles(tmp_path)
        workspace.restore_text("notes.txt", expected_current="after", original="before")
        assert target.read_text(encoding="utf-8") == "before"
        target.write_text("external edit", encoding="utf-8")
        try:
            workspace.restore_text("notes.txt", expected_current="after", original="before")
        except WorkspaceFileError as error:
            assert "changed after" in str(error)
        else:
            raise AssertionError("A concurrent edit must not be overwritten.")

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
        provider.set_sandbox_mode(SandboxMode.WORKSPACE_WRITE)
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
        provider.set_sandbox_mode(SandboxMode.WORKSPACE_WRITE)
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
        factory = RecordingExecutionScopeFactory()
        provider = MCPToolProvider(
            FastMCP("local"),
            (),
            executor_factory=factory,
            remote_servers=(remote_config, calendar_config),
            remote_clients={
                "knowledge": Client(knowledge_server),
                "calendar": Client(calendar_server),
            },
        )
        tools = await provider.start()

        assert [tool.name for tool in tools] == ["knowledge_search", "calendar_search"]
        connection_status = {item.connection_id: item for item in provider.connections()}
        assert connection_status["builtin/local"].state.value == "connected"
        assert connection_status["knowledge"].state.value == "connected"
        assert connection_status["knowledge"].discovered_tool_count == 1
        assert connection_status["calendar"].discovered_tool_count == 1
        descriptor = provider.registry.get("knowledge_search").descriptor
        assert descriptor.trust_level is TrustLevel.THIRD_PARTY
        assert descriptor.execution_env is ExecutionEnvironment.SERVICE
        assert descriptor.high_impact is True

        try:
            await provider.call_tool("knowledge_search", {}, budget=_budget())
        except ToolDeniedError as error:
            assert "failed validation" in str(error)
        else:
            raise AssertionError("Remote MCP schema must reject missing required arguments.")

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
        assert factory.coordinators[0].primary_calls == [
            "knowledge_search",
            "knowledge_search",
            "knowledge_search",
            "calendar_search",
        ]
        await provider.aclose()
        assert all(item.state.value == "disconnected" for item in provider.connections())

    asyncio.run(scenario())


def test_remote_connection_failure_exposes_only_a_safe_error_code() -> None:
    class FailingClient:
        async def __aenter__(self):
            raise RuntimeError("Bearer private-token at https://private.example/mcp")

        async def __aexit__(self, *_args):
            return None

    async def scenario() -> None:
        config = RemoteMCPServerSettings(
            name="builtin",
            url="https://private.example/mcp",
            headers={"Authorization": "Bearer private-token"},
        )
        provider = MCPToolProvider(
            FastMCP("local"),
            (),
            remote_servers=(config,),
            remote_clients={"builtin": FailingClient()},
        )

        await provider.start()

        statuses = {item.connection_id: item for item in provider.connections()}
        assert "builtin/local" in statuses
        status = statuses["builtin"]
        assert status.connection_id == "builtin"
        assert status.state.value == "failed"
        assert status.error_code == "RuntimeError"
        assert status.discovered_tool_count == 0
        assert "private-token" not in status.model_dump_json()
        assert "private.example" not in status.model_dump_json()
        await provider.aclose()

    asyncio.run(scenario())


def test_remote_mcp_registration_enforces_bounded_input_schema() -> None:
    from hostpilot.config import RemoteMCPServerSettings

    server = RemoteMCPServerSettings(name="remote", url="https://mcp.example/mcp")
    registration = _remote_tool_registration(
        server,
        SimpleNamespace(
            name="search",
            description="Search records",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string", "minLength": 1}},
                "required": ["query"],
                "additionalProperties": False,
            },
        ),
        "remote_search",
    )

    assert registration.validate_arguments({"query": "policy"}) == {"query": "policy"}
    for arguments in ({}, {"query": ""}, {"query": "policy", "admin": True}):
        with pytest.raises(ValidationError):
            registration.validate_arguments(arguments)


def test_remote_mcp_registration_rejects_unbounded_or_external_input_schema() -> None:
    from hostpilot.config import RemoteMCPServerSettings

    server = RemoteMCPServerSettings(name="remote", url="https://mcp.example/mcp")
    nested: dict[str, Any] = {"type": "object"}
    for _ in range(20):
        nested = {"type": "object", "properties": {"child": nested}}
    invalid_schemas = (
        {"type": "object", "$ref": "https://schemas.example/tool.json"},
        {"type": "object", "$dynamicRef": "https://schemas.example/tool.json"},
        {"type": "object", "$ref": "#/definitions/missing"},
        nested,
    )
    for schema in invalid_schemas:
        with pytest.raises(ValueError):
            _remote_tool_registration(
                server,
                SimpleNamespace(name="search", description=None, inputSchema=schema),
                "remote_search",
            )


def test_remote_server_registration_is_atomic_for_invalid_tool_schema(monkeypatch) -> None:
    class Session:
        async def list_tools(self):
            return SimpleNamespace(
                tools=(
                    SimpleNamespace(
                        name="valid",
                        description="Valid tool",
                        inputSchema={"type": "object", "properties": {}},
                    ),
                    SimpleNamespace(
                        name="invalid",
                        description="Invalid tool",
                        inputSchema={"type": "object", "$ref": "https://example.com/schema"},
                    ),
                )
            )

    class FakeClient:
        session = Session()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    async def load_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr("hostpilot.tools.mcp.load_mcp_tools", load_tools)
    server = RemoteMCPServerSettings(name="remote", url="https://mcp.example/mcp")
    provider = MCPToolProvider(
        FastMCP("local"),
        (),
        remote_servers=(server,),
        remote_clients={"remote": FakeClient()},
    )

    async def scenario() -> None:
        await provider.start()

        assert provider.registry.descriptors() == ()
        assert provider.routing_table.routes == {}
        assert provider.connections()[1].state.value == "failed"

        await provider.aclose()

    asyncio.run(scenario())


def test_remote_tool_loading_timeout_closes_client_and_isolates_server(monkeypatch) -> None:
    class Session:
        async def list_tools(self):
            return SimpleNamespace(
                tools=(
                    SimpleNamespace(
                        name="lookup",
                        description="Lookup",
                        inputSchema={"type": "object", "properties": {}},
                    ),
                )
            )

    class FakeClient:
        session = Session()

        def __init__(self):
            self.close_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.close_calls += 1

    async def load_tools(*_args, **kwargs):
        if kwargs.get("server_name") is not None:
            await asyncio.Event().wait()
        return []

    monkeypatch.setattr("hostpilot.tools.mcp.load_mcp_tools", load_tools)
    server = RemoteMCPServerSettings(
        name="slow", url="https://mcp.example/mcp", timeout_seconds=0.02
    )
    remote_client = FakeClient()
    provider = MCPToolProvider(
        FastMCP("local"),
        (),
        remote_servers=(server,),
        remote_clients={"slow": remote_client},
    )

    async def scenario() -> None:
        tools = await provider.start()

        assert tools == []
        assert remote_client.close_calls == 1
        assert provider._started_remote_clients == []
        assert provider.connections()[1].state.value == "failed"
        assert provider.connections()[1].error_code == "TimeoutError"

        await provider.aclose()

    asyncio.run(scenario())


def test_cancelling_remote_tool_loading_closes_all_started_clients(monkeypatch) -> None:
    class Session:
        async def list_tools(self):
            return SimpleNamespace(tools=())

    class FakeClient:
        session = Session()

        def __init__(self):
            self.close_calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.close_calls += 1

    loading_started = asyncio.Event()

    async def load_tools(*_args, **kwargs):
        if kwargs.get("server_name") == "slow":
            loading_started.set()
            await asyncio.Event().wait()
        return []

    monkeypatch.setattr("hostpilot.tools.mcp.load_mcp_tools", load_tools)
    servers = (
        RemoteMCPServerSettings(name="fast", url="https://fast.example/mcp"),
        RemoteMCPServerSettings(name="slow", url="https://slow.example/mcp"),
    )
    fast_client = FakeClient()
    slow_client = FakeClient()
    provider = MCPToolProvider(
        FastMCP("local"),
        (),
        remote_servers=servers,
        remote_clients={"fast": fast_client, "slow": slow_client},
    )

    async def scenario() -> None:
        startup = asyncio.create_task(provider.start())
        await loading_started.wait()
        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup

        assert fast_client.close_calls == 1
        assert slow_client.close_calls == 1
        assert provider._started_remote_clients == []
        statuses = {item.connection_id: item for item in provider.connections()}
        assert statuses["fast"].state.value == "disconnected"
        assert statuses["slow"].state.value == "disconnected"

        await provider.aclose()

    asyncio.run(scenario())


def test_provider_close_bounds_remote_client_cleanup(monkeypatch) -> None:
    class FakeClient:
        async def __aexit__(self, *_args):
            await asyncio.Event().wait()

    monkeypatch.setattr("hostpilot.tools.mcp._MCP_CLEANUP_TIMEOUT_SECONDS", 0.01)
    server = RemoteMCPServerSettings(name="slow", url="https://mcp.example/mcp")
    client = FakeClient()
    provider = MCPToolProvider(
        FastMCP("local"),
        (),
        remote_servers=(server,),
        remote_clients={"slow": client},
    )
    provider._started_remote_clients.append(client)
    provider._set_connection_status("slow", ToolConnectionState.CONNECTED)

    asyncio.run(provider.aclose())

    assert provider._started_remote_clients == []
    status = {item.connection_id: item for item in provider.connections()}["slow"]
    assert status.state.value == "failed"
    assert status.error_code == "TimeoutError"


def test_local_tool_discovery_timeout_closes_provider(monkeypatch) -> None:
    async def load_tools(*_args, **_kwargs):
        await asyncio.Event().wait()

    monkeypatch.setattr("hostpilot.tools.mcp.load_mcp_tools", load_tools)
    monkeypatch.setattr("hostpilot.tools.mcp._MCP_LOCAL_DISCOVERY_TIMEOUT_SECONDS", 0.01)
    provider = MCPToolProvider(FastMCP("local"), ())

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            await provider.start()

        assert provider._started is False
        assert provider.connections()[0].state.value == "failed"
        assert provider.connections()[0].error_code == "TimeoutError"

    asyncio.run(scenario())


def test_failed_remote_server_does_not_block_other_configured_servers() -> None:
    class FailingClient:
        async def __aenter__(self):
            raise RuntimeError("unavailable")

        async def __aexit__(self, *_args):
            return None

    async def scenario() -> None:
        working_server = FastMCP("working")

        @working_server.tool(name="status")
        async def status() -> str:
            return "ok"

        configs = (
            RemoteMCPServerSettings(name="broken", url="https://broken.example/mcp"),
            RemoteMCPServerSettings(name="working", url="https://working.example/mcp"),
        )
        provider = MCPToolProvider(
            FastMCP("local"),
            (),
            remote_servers=configs,
            remote_clients={
                "broken": FailingClient(),
                "working": Client(working_server),
            },
        )

        tools = await provider.start()
        assert [tool.name for tool in tools] == ["working_status"]
        statuses = {item.connection_id: item for item in provider.connections()}
        assert statuses["broken"].state.value == "failed"
        assert statuses["working"].state.value == "connected"
        await provider.aclose()

    asyncio.run(scenario())


def test_remote_tool_call_timeout_cancels_transport_wait_and_keeps_provider_closable(
    monkeypatch,
) -> None:
    from hostpilot.policy.confirmation import ConfirmationDecision

    started = asyncio.Event()
    cancelled = asyncio.Event()

    class Session:
        def __init__(self):
            self.call_count = 0

        async def list_tools(self):
            return SimpleNamespace(
                tools=(
                    SimpleNamespace(
                        name="slow",
                        description="Slow tool",
                        inputSchema={"type": "object", "properties": {}},
                    ),
                )
            )

        async def call_tool(self, _name, _arguments):
            self.call_count += 1
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

    class FakeClient:
        def __init__(self):
            self.session = Session()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Approve:
        async def confirm(self, _request):
            return ConfirmationDecision.APPROVED

    async def load_tools(*_args, **_kwargs):
        return []

    monkeypatch.setattr("hostpilot.tools.mcp.load_mcp_tools", load_tools)

    async def scenario() -> None:
        configuration = RemoteMCPServerSettings(
            name="slow", url="https://mcp.example/mcp", timeout_seconds=0.05
        )
        remote_client = FakeClient()
        provider = MCPToolProvider(
            FastMCP("local"),
            (),
            remote_servers=(configuration,),
            remote_clients={"slow": remote_client},
        )
        await provider.start()

        try:
            await provider.call_tool(
                "slow_slow",
                {},
                budget=_budget(),
                confirmation_provider=Approve(),
            )
        except RemoteToolOutcomeUnknownError as error:
            assert "timed out" in str(error)
        else:
            raise AssertionError("Remote MCP call should be bounded by its timeout profile.")

        assert started.is_set()
        assert cancelled.is_set()
        assert remote_client.session.call_count == 1
        assert provider.connections()[1].state.value == "connected"
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


def test_stdio_transport_uses_argument_array_and_filters_parent_secrets(monkeypatch) -> None:
    import os

    from fastmcp.client.transports import StdioTransport

    from hostpilot.tools.mcp import _remote_transport

    monkeypatch.setenv("PATH", "/usr/bin")
    monkeypatch.setenv("HOSTPILOT_MODEL_API_KEY", "parent-secret")
    server = RemoteMCPServerSettings(
        name="filesystem",
        transport="stdio",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "/tmp/project"),
        env={"MCP_ACCESS_TOKEN": "stdio-secret"},
    )

    transport = _remote_transport(server)

    assert isinstance(transport, StdioTransport)
    assert transport.command == "npx"
    assert transport.args == [
        "-y",
        "@modelcontextprotocol/server-filesystem",
        "/tmp/project",
    ]
    assert transport.env["PATH"] == "/usr/bin"
    assert transport.env["MCP_ACCESS_TOKEN"] == "stdio-secret"
    assert "HOSTPILOT_MODEL_API_KEY" not in transport.env
    assert os.environ["HOSTPILOT_MODEL_API_KEY"] == "parent-secret"


def test_stdio_mcp_server_starts_discovers_tools_and_stops(tmp_path) -> None:
    import sys

    script = tmp_path / "stdio_server.py"
    script.write_text(
        "from fastmcp import FastMCP\n"
        "server = FastMCP('test-stdio')\n"
        "@server.tool(name='ping')\n"
        "def ping() -> str:\n"
        "    return 'pong'\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    provider = MCPToolProvider(
        FastMCP("local"),
        (),
        remote_servers=(
            RemoteMCPServerSettings(
                name="local_process",
                transport="stdio",
                command=sys.executable,
                args=(str(script),),
                cwd=tmp_path,
            ),
        ),
    )

    async def scenario() -> None:
        tools = await asyncio.wait_for(provider.start(), timeout=15)
        status = {item.connection_id: item for item in provider.connections()}["local_process"]
        assert status.transport.value == "stdio"
        assert status.state.value == "connected", status.error_code
        assert status.discovered_tool_count == 1
        assert [tool.name for tool in tools] == ["local_process_ping"]
        await provider.aclose()
        assert not provider._started_remote_clients

    asyncio.run(scenario())
