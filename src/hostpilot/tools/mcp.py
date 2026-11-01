"""FastMCP tool hosting and policy-first LangChain MCP integration."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StreamableHttpTransport
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langchain_mcp_adapters.tools import load_mcp_tools

from hostpilot.budget import RequestBudget
from hostpilot.config import RemoteMCPServerSettings, Settings
from hostpilot.contracts import (
    ExecutionEnvironment,
    TimeoutProfile,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.observability import StructuredAuditSink
from hostpilot.policy import PolicyEngine
from hostpilot.retrieval import RagRetrievalClient
from hostpilot.tools.executor import AsyncConfirmationProvider, ToolDeniedError, ToolExecutor
from hostpilot.tools.filesystem import WorkspaceFiles
from hostpilot.tools.registry import (
    ConfirmationSummaryBuilder,
    ToolHandler,
    ToolRegistration,
    ToolRegistry,
    ToolResultVerifier,
)


class MCPToolProvider:
    """Expose a FastMCP server to DeepAgents through governed LangChain tools."""

    def __init__(
        self,
        server: FastMCP,
        registrations: tuple[ToolRegistration, ...],
        *,
        resources: tuple[Any, ...] = (),
        remote_servers: tuple[RemoteMCPServerSettings, ...] = (),
        remote_clients: Mapping[str, Any] | None = None,
    ) -> None:
        self.server = server
        self.resources = resources
        self.client: Any = Client(server)
        self.remote_servers = remote_servers
        if remote_clients is None:
            self.remote_clients: dict[str, Any] = {
                remote.name: Client(
                    StreamableHttpTransport(
                        remote.url,
                        headers={
                            key: value.get_secret_value() for key, value in remote.headers.items()
                        },
                    ),
                    name=remote.name,
                    timeout=remote.timeout_seconds,
                )
                for remote in remote_servers
            }
        else:
            if set(remote_clients) != {remote.name for remote in remote_servers}:
                raise ValueError("Remote MCP clients must match the configured server names.")
            self.remote_clients = dict(remote_clients)
        self._remote_tool_routes: dict[str, tuple[str, str]] = {}
        self._started_remote_clients: list[Any] = []
        self.registry = ToolRegistry()
        for registration in registrations:
            self.registry.register(registration)
        self.executor = ToolExecutor(
            self.registry,
            PolicyEngine(),
            audit=StructuredAuditSink().record,
        )
        self._started = False

    async def start(self) -> list[Any]:
        """Connect configured MCP servers and load policy-intercepted Agent tools."""
        if self._started:
            raise RuntimeError("MCP tool provider is already started.")
        try:
            await self.client.__aenter__()
            self._started = True
            return (
                await load_mcp_tools(
                    self.client.session,
                    tool_interceptors=[self._authorize_and_execute],
                    handle_tool_errors=False,
                )
                + await self._load_remote_tools()
            )
        except Exception:
            await self.aclose()
            raise

    async def aclose(self) -> None:
        """Close the MCP session and any HTTP clients owned by its tools."""
        if self._started:
            await self.client.__aexit__(None, None, None)
            self._started = False
        while self._started_remote_clients:
            client = self._started_remote_clients.pop()
            await client.__aexit__(None, None, None)
        for resource in self.resources:
            close = getattr(resource, "aclose", None)
            if close is not None:
                await close()

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str:
        """Run one router-selected tool through the same policy-first executor."""
        if not self._started:
            raise ToolDeniedError("MCP tool provider is not started.")

        remote_route = self._remote_tool_routes.get(name)

        async def invoke(validated_arguments: Mapping[str, Any]) -> Any:
            if remote_route is None:
                return await self.client.session.call_tool(name, dict(validated_arguments))
            server_name, remote_tool_name = remote_route
            return await self.remote_clients[server_name].session.call_tool(
                remote_tool_name, dict(validated_arguments)
            )

        result = await self.executor.execute(
            name,
            arguments,
            budget=budget,
            confirmation_provider=confirmation_provider,
            _invoke=invoke,
        )
        return _tool_result_text(result)

    async def _authorize_and_execute(self, request: MCPToolCallRequest, handler: Any) -> Any:
        """Route each model-originated MCP call through ToolExecutor."""
        runtime = request.runtime
        runtime_context = getattr(runtime, "context", None)
        budget = _request_budget(runtime_context)
        if budget is None:
            raise ToolDeniedError("MCP tools require an active request budget.")

        async def invoke(arguments: Mapping[str, Any]) -> Any:
            return await handler(request.override(args=dict(arguments)))

        confirmation_provider = _request_confirmation_provider(runtime_context)

        return await self.executor.execute(
            request.name,
            request.args,
            budget=budget,
            confirmation_provider=confirmation_provider,
            _invoke=invoke,
        )

    async def _load_remote_tools(self) -> list[Any]:
        """Discover each remote server and register its tools under unique names."""
        loaded_tools: list[Any] = []
        for remote in self.remote_servers:
            client = self.remote_clients[remote.name]
            await client.__aenter__()
            self._started_remote_clients.append(client)
            remote_tools = (await client.session.list_tools()).tools
            for remote_tool in remote_tools:
                exposed_name = _remote_tool_name(remote.name, remote_tool.name)
                if exposed_name in self._remote_tool_routes:
                    raise ValueError("Remote MCP servers exposed a duplicate tool name.")
                self._remote_tool_routes[exposed_name] = (remote.name, remote_tool.name)
                self.registry.register(_remote_tool_registration(remote, remote_tool, exposed_name))
            loaded_tools.extend(
                await load_mcp_tools(
                    client.session,
                    server_name=remote.name,
                    tool_name_prefix=True,
                    tool_interceptors=[self._authorize_remote_tool],
                    handle_tool_errors=False,
                )
            )
        return loaded_tools

    async def _authorize_remote_tool(self, request: MCPToolCallRequest, handler: Any) -> Any:
        """Govern a remote MCP call using its server-qualified registry entry."""
        runtime_context = getattr(request.runtime, "context", None)
        budget = _request_budget(runtime_context)
        if budget is None:
            raise ToolDeniedError("MCP tools require an active request budget.")
        qualified_name = _remote_tool_name(request.server_name, request.name)
        if self._remote_tool_routes.get(qualified_name) != (
            request.server_name,
            request.name,
        ):
            raise ToolDeniedError("Remote MCP tool is not registered.")

        async def invoke(arguments: Mapping[str, Any]) -> Any:
            return await handler(request.override(args=dict(arguments)))

        return await self.executor.execute(
            qualified_name,
            request.args,
            budget=budget,
            confirmation_provider=_request_confirmation_provider(runtime_context),
            _invoke=invoke,
        )


def build_retrieval_mcp_provider(
    settings: Settings,
    *,
    workspace_root: Path | None = None,
    retrieval_client: RagRetrievalClient | None = None,
) -> MCPToolProvider:
    """Build a query-only RAG tool and bounded workspace text-file tools."""
    server = FastMCP("HostPilot tools")
    root = workspace_root or Path.cwd()
    workspace = WorkspaceFiles(root)
    if retrieval_client is None and all(
        (settings.rag_base_url, settings.rag_user_id, settings.rag_key)
    ):
        assert settings.rag_base_url is not None
        assert settings.rag_user_id is not None
        assert settings.rag_key is not None
        retrieval_client = RagRetrievalClient(
            base_url=settings.rag_base_url,
            retrieval_path=settings.rag_retrieval_path,
            user_id=settings.rag_user_id,
            key=settings.rag_key,
            timeout_seconds=settings.rag_timeout_seconds,
        )

    if retrieval_client is None:

        @server.tool(
            name="retrieve_knowledge",
            description="Knowledge retrieval is not enabled yet.",
            annotations={"readOnlyHint": True, "destructiveHint": False},
        )
        async def retrieve_knowledge(query: str) -> str:
            """Return a clear placeholder until the external RAG service is enabled."""
            del query
            return "知识库暂未开启"

    else:

        @server.tool(
            name="retrieve_knowledge",
            description="Search the locally configured knowledge service for relevant evidence.",
            annotations={"readOnlyHint": True, "destructiveHint": False},
        )
        async def retrieve_knowledge(query: str) -> str:
            """Retrieve validated evidence using credentials held by the backend."""
            evidence_set = await retrieval_client.retrieve(query)
            return evidence_set.model_dump_json()

    @server.tool(
        name="read_text_file",
        description="Read a UTF-8 text file using a path relative to the HostPilot workspace.",
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    async def read_text_file(path: str) -> str:
        """Read one bounded text file from the configured workspace."""
        return workspace.read_text(path)

    @server.tool(
        name="write_text_file",
        description=(
            "Create or replace a UTF-8 text file in the HostPilot workspace. "
            "This operation always requires explicit user approval."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def write_text_file(path: str, content: str) -> str:
        """Write one bounded UTF-8 text file after explicit user approval."""
        byte_count = workspace.write_text(path, content)
        return f"已写入 {path}（{byte_count} 字节）。"

    async def handler(_: Mapping[str, Any]) -> Any:
        raise RuntimeError("MCP tool invocation must pass through the MCP client.")

    def verify(result: Any, _: Mapping[str, Any]) -> None:
        is_error = getattr(result, "isError", getattr(result, "is_error", False))
        if is_error:
            raise ValueError("MCP tool returned an error.")

    def validate_query(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"query"}:
            raise ValueError("retrieve_knowledge accepts only query.")
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip() or len(query) > 8000:
            raise ValueError("query must contain between 1 and 8000 characters.")
        return {"query": query.strip()}

    def validate_read(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"path"}:
            raise ValueError("read_text_file accepts only path.")
        path = arguments.get("path")
        if not isinstance(path, str) or not path.strip() or len(path) > 1024:
            raise ValueError("path must contain between 1 and 1024 characters.")
        workspace.validate_read_path(path.strip())
        return {"path": path.strip()}

    def validate_write(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"path", "content"}:
            raise ValueError("write_text_file accepts path and content.")
        path = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(path, str) or not path.strip() or len(path) > 1024:
            raise ValueError("path must contain between 1 and 1024 characters.")
        workspace.validate_write_path(path.strip())
        if not isinstance(content, str) or len(content.encode("utf-8")) > workspace.max_file_bytes:
            raise ValueError("content exceeds the workspace file size limit.")
        return {"path": path.strip(), "content": content}

    def summarize_write(arguments: Mapping[str, Any]) -> str:
        path = str(arguments["path"])
        byte_count = len(str(arguments["content"]).encode("utf-8"))
        target = workspace.root / path
        operation = "覆盖" if target.exists() else "创建"
        return f"{operation}工作区文件 `{path}`，写入 {byte_count} 字节。"

    registrations = (
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="retrieve_knowledge",
                description="Return a placeholder while knowledge retrieval is disabled.",
                tool_type=ToolType.RETRIEVAL,
                trust_level=TrustLevel.THIRD_PARTY,
                execution_env=ExecutionEnvironment.SERVICE,
                timeout_profile=TimeoutProfile(timeout_seconds=10),
            ),
            validate_arguments=validate_query,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="read_text_file",
                description="Read one UTF-8 text file from the local workspace.",
                tool_type=ToolType.ACTION,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.HOST,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
            ),
            validate_arguments=validate_read,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="write_text_file",
                description="Create or replace a workspace text file after user approval.",
                tool_type=ToolType.ACTION,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.HOST,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
                high_impact=True,
            ),
            validate_arguments=validate_write,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
            confirmation_summary=summarize_write,
        ),
    )
    resources = (retrieval_client,) if retrieval_client is not None else ()
    return MCPToolProvider(
        server,
        registrations,
        resources=resources,
        remote_servers=settings.mcp_servers,
    )


def _remote_tool_name(server_name: str, tool_name: str) -> str:
    """Return the stable Agent-facing name for a remote MCP tool."""
    return f"{server_name}_{tool_name}"


def _remote_tool_registration(
    server: RemoteMCPServerSettings,
    remote_tool: Any,
    exposed_name: str,
) -> ToolRegistration:
    """Create a conservative, confirmation-required registration for a remote tool."""
    description = getattr(remote_tool, "description", None) or "Remote MCP tool."

    async def handler(_: Mapping[str, Any]) -> Any:
        raise RuntimeError("Remote MCP invocation must pass through its MCP client.")

    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if not isinstance(arguments, Mapping) or any(not isinstance(key, str) for key in arguments):
            raise ValueError("Remote MCP arguments must be an object with string keys.")
        return dict(arguments)

    def verify(result: Any, _: Mapping[str, Any]) -> None:
        if getattr(result, "is_error", getattr(result, "isError", False)):
            raise ValueError("Remote MCP server returned an error.")

    def confirmation_summary(_: Mapping[str, Any]) -> str:
        return (
            f"调用远程 MCP Server「{server.name}」上的工具「{remote_tool.name}」。"
            "远程工具可能产生外部影响。"
        )

    return ToolRegistration(
        descriptor=ToolDescriptor(
            name=exposed_name,
            description=f"[{server.name}] {description}",
            tool_type=ToolType.ACTION,
            trust_level=TrustLevel.THIRD_PARTY,
            execution_env=ExecutionEnvironment.SERVICE,
            timeout_profile=TimeoutProfile(timeout_seconds=server.timeout_seconds),
            high_impact=True,
        ),
        validate_arguments=validate,
        handler=cast(ToolHandler, handler),
        verify_result=cast(ToolResultVerifier, verify),
        confirmation_summary=cast(ConfirmationSummaryBuilder, confirmation_summary),
    )


def _request_budget(context: Any) -> RequestBudget | None:
    """Extract the explicit mutable budget from the current Agent runtime context."""
    if isinstance(context, Mapping):
        candidate = context.get("budget")
    else:
        candidate = getattr(context, "budget", None)
    return candidate if isinstance(candidate, RequestBudget) else None


def _request_confirmation_provider(context: Any) -> AsyncConfirmationProvider | None:
    """Read the per-chat confirmation channel injected by the WebSocket handler."""
    if isinstance(context, Mapping):
        candidate = context.get("confirmation_provider")
    else:
        candidate = getattr(context, "confirmation_provider", None)
    if candidate is None or not callable(getattr(candidate, "confirm", None)):
        return None
    return cast(AsyncConfirmationProvider, candidate)


def _tool_result_text(result: Any) -> str:
    """Extract text returned by a successful MCP call for the top-level router path."""
    content = getattr(result, "content", ())
    text_blocks = [getattr(block, "text", "") for block in content]
    combined = "\n".join(value for value in text_blocks if value)
    if combined:
        return combined
    structured = getattr(result, "structuredContent", getattr(result, "structured_content", None))
    if isinstance(structured, Mapping):
        value = structured.get("result")
        if isinstance(value, str):
            return value
    raise RuntimeError("MCP tool returned no displayable text.")


__all__ = ["MCPToolProvider", "build_retrieval_mcp_provider"]
