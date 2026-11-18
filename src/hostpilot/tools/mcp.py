"""FastMCP tool hosting and policy-first LangChain MCP integration."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from langchain_mcp_adapters.tools import load_mcp_tools

from hostpilot.budget import RequestBudget
from hostpilot.config import RemoteMCPServerSettings, Settings
from hostpilot.contracts import (
    ExecutionEnvironment,
    SandboxMode,
    TimeoutProfile,
    ToolConnectionState,
    ToolConnectionStatus,
    ToolConnectionTransport,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.observability import AuditSink, StructuredAuditSink, TraceSink
from hostpilot.retrieval import RagRetrievalClient
from hostpilot.sandbox import (
    CodeSandboxBackend,
    LinuxSandboxBackendFactory,
    SandboxBackendSet,
    WorkspaceSandboxBackend,
)
from hostpilot.tools.executor import (
    AsyncConfirmationProvider,
    RemoteToolOutcomeUnknownError,
    ToolDeniedError,
    ToolExecutionTimeoutError,
)
from hostpilot.tools.filesystem import WorkspaceFiles
from hostpilot.tools.governance import (
    DefaultToolExecutorFactory,
    ToolExecutorFactory,
    ToolInvocationCoordinator,
)
from hostpilot.tools.json_schema import json_schema_argument_validator
from hostpilot.tools.registry import (
    ConfirmationSummaryBuilder,
    ToolHandler,
    ToolRegistration,
    ToolRegistry,
    ToolResultVerifier,
)
from hostpilot.tools.routing import MCPToolRoutingTable, RemoteToolRouteTable

_LOCAL_CONNECTION_KEY = "__hostpilot_builtin__"
_MCP_LOCAL_DISCOVERY_TIMEOUT_SECONDS = 15
_MCP_CLEANUP_TIMEOUT_SECONDS = 5


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
        workspace: WorkspaceFiles | None = None,
        routing_table: RemoteToolRouteTable | None = None,
        sandboxed_environments: frozenset[ExecutionEnvironment] = frozenset(),
        sandboxed_tools: frozenset[str] = frozenset(),
        sandbox_unavailability_reasons: Mapping[str, str] | None = None,
        filesystem_sandbox: WorkspaceSandboxBackend | None = None,
        executor_factory: ToolExecutorFactory | None = None,
        trace_sink: TraceSink | None = None,
        audit_sink: AuditSink | None = None,
    ) -> None:
        self.server = server
        self.resources = resources
        self.client: Any = Client(server)
        self.remote_servers = remote_servers
        if remote_clients is None:
            self.remote_clients = {
                remote.name: Client(
                    _remote_transport(remote),
                    name=remote.name,
                    timeout=remote.timeout_seconds,
                )
                for remote in remote_servers
            }
        else:
            if set(remote_clients) != {remote.name for remote in remote_servers}:
                raise ValueError("Remote MCP clients must match the configured server names.")
            self.remote_clients = dict(remote_clients)
        self.routing_table = routing_table if routing_table is not None else MCPToolRoutingTable()
        self.sandboxed_environments = sandboxed_environments
        self.sandboxed_tools = sandboxed_tools
        self.sandbox_unavailability_reasons = dict(sandbox_unavailability_reasons or {})
        self.filesystem_sandbox = filesystem_sandbox
        self._started_remote_clients: list[Any] = []
        self._connection_status: dict[str, ToolConnectionStatus] = {
            _LOCAL_CONNECTION_KEY: ToolConnectionStatus(
                connection_id="builtin/local",
                transport=ToolConnectionTransport.IN_PROCESS,
                state=ToolConnectionState.DISCONNECTED,
                discovered_tool_count=0,
            ),
            **{
                remote.name: ToolConnectionStatus(
                    connection_id=remote.name,
                    transport=ToolConnectionTransport(remote.transport),
                    state=ToolConnectionState.DISCONNECTED,
                    discovered_tool_count=0,
                )
                for remote in remote_servers
            },
        }
        self.registry = ToolRegistry()
        for registration in registrations:
            self.registry.register(registration)
        governance_factory = (
            executor_factory if executor_factory is not None else DefaultToolExecutorFactory()
        )
        audit = (audit_sink or StructuredAuditSink()).record
        primary_scope = governance_factory.create_scope(
            self.registry,
            sandboxed_environments=sandboxed_environments,
            sandboxed_tools=sandboxed_tools,
            audit=audit,
            trace_sink=trace_sink,
        )
        self._compensation_registry = ToolRegistry()
        compensation_scope = governance_factory.create_scope(
            self._compensation_registry,
            sandboxed_environments=(
                frozenset({ExecutionEnvironment.NATIVE_SANDBOX})
                if "_restore_existing_text_file" in sandboxed_tools
                else frozenset()
            ),
            sandboxed_tools=sandboxed_tools,
            audit=audit,
            trace_sink=trace_sink,
        )
        self._tool_coordinator: ToolInvocationCoordinator = governance_factory.create_coordinator(
            primary_scope, compensation_scope
        )
        self.workspace = workspace
        self._compensation_snapshots: dict[str, tuple[str, str, str]] = {}
        if workspace is not None:
            self._register_restore_tool()
        self._started = False

    async def prepare_existing_file_write(
        self,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str:
        """Capture old/new content in memory before a governed existing-file write."""
        if self.workspace is None:
            raise ToolDeniedError("Workspace file compensation is unavailable.")
        path = arguments.get("path")
        content = arguments.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise ValueError("Existing-file writes require path and text content.")
        if not self.workspace.validate_write_path(path).is_file():
            raise ValueError("write_existing_text_file only accepts an existing file.")
        old_content = await self.call_tool(
            "read_text_file",
            {"path": path},
            budget=budget,
            confirmation_provider=confirmation_provider,
        )
        token = str(uuid4())
        if len(self._compensation_snapshots) >= 256:
            oldest_token = next(iter(self._compensation_snapshots))
            self._compensation_snapshots.pop(oldest_token)
        self._compensation_snapshots[token] = (path, old_content, content)
        return token

    async def compensate_existing_file_write(
        self,
        token: str,
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> None:
        """Restore through the Runtime coordinator's compensation Scope."""
        await self._tool_coordinator.execute_compensation(
            "_restore_existing_text_file",
            {"token": token},
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

    def _register_restore_tool(self) -> None:
        assert self.workspace is not None

        def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
            if set(arguments) != {"token"}:
                raise ValueError("Restore accepts only a snapshot token.")
            token = arguments.get("token")
            if not isinstance(token, str) or token not in self._compensation_snapshots:
                raise ValueError("Compensation snapshot is unavailable.")
            return {"token": token}

        async def restore(arguments: Mapping[str, Any]) -> str:
            token = str(arguments["token"])
            path, original, expected_current = self._compensation_snapshots[token]
            assert self.workspace is not None
            if self.filesystem_sandbox is None:
                raise ToolDeniedError("Linux filesystem sandbox is unavailable.")
            await self.filesystem_sandbox.execute(
                self.workspace,
                "restore_text",
                {"path": path, "original": original, "expected_current": expected_current},
            )
            self._compensation_snapshots.pop(token, None)
            return "Existing workspace file was restored."

        def verify(result: Any, _: Mapping[str, Any]) -> None:
            if not isinstance(result, str):
                raise ValueError("Restore result is invalid.")

        self._compensation_registry.register(
            ToolRegistration(
                descriptor=ToolDescriptor(
                    name="_restore_existing_text_file",
                    description="Restore a snapshotted existing workspace file.",
                    tool_type=ToolType.ACTION,
                    trust_level=TrustLevel.HOST,
                    execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                    timeout_profile=TimeoutProfile(timeout_seconds=5),
                    high_impact=True,
                ),
                validate_arguments=validate,
                handler=restore,
                verify_result=cast(ToolResultVerifier, verify),
                confirmation_summary=lambda _: (
                    "恢复此前覆盖的工作区文件；仅当文件仍保持本次写入内容时执行。"
                ),
            )
        )

    async def start(self) -> list[Any]:
        """Connect configured MCP servers and load policy-intercepted Agent tools."""
        if self._started:
            raise RuntimeError("MCP tool provider is already started.")
        self._set_connection_status(_LOCAL_CONNECTION_KEY, ToolConnectionState.CONNECTING)
        try:
            await self.client.__aenter__()
            self._started = True
            try:
                local_tools = await asyncio.wait_for(
                    load_mcp_tools(
                        self.client.session,
                        tool_interceptors=[self._authorize_and_execute],
                        handle_tool_errors=False,
                    ),
                    timeout=_MCP_LOCAL_DISCOVERY_TIMEOUT_SECONDS,
                )
            except Exception as error:
                self._set_connection_status(
                    _LOCAL_CONNECTION_KEY,
                    ToolConnectionState.FAILED,
                    error_code=type(error).__name__,
                )
                raise
            self._set_connection_status(
                _LOCAL_CONNECTION_KEY,
                ToolConnectionState.CONNECTED,
                discovered_tool_count=len(local_tools),
            )
            return local_tools + await self._load_remote_tools()
        except asyncio.CancelledError:
            self._set_connection_status(_LOCAL_CONNECTION_KEY, ToolConnectionState.DISCONNECTED)
            await self.aclose()
            raise
        except Exception as error:
            current = self._connection_status[_LOCAL_CONNECTION_KEY]
            if current.state is ToolConnectionState.CONNECTING:
                self._set_connection_status(
                    _LOCAL_CONNECTION_KEY,
                    ToolConnectionState.FAILED,
                    error_code=type(error).__name__,
                )
            await self.aclose()
            raise

    async def aclose(self) -> None:
        """Close the MCP session and any HTTP clients owned by its tools."""
        self._compensation_snapshots.clear()
        if self._started:
            try:
                await asyncio.wait_for(
                    self.client.__aexit__(None, None, None),
                    timeout=_MCP_CLEANUP_TIMEOUT_SECONDS,
                )
            except Exception as error:
                self._set_connection_status(
                    _LOCAL_CONNECTION_KEY,
                    ToolConnectionState.FAILED,
                    error_code=type(error).__name__,
                )
            finally:
                self._started = False
            if (
                self._connection_status[_LOCAL_CONNECTION_KEY].state
                is not ToolConnectionState.FAILED
            ):
                self._set_connection_status(_LOCAL_CONNECTION_KEY, ToolConnectionState.DISCONNECTED)
        while self._started_remote_clients:
            client = self._started_remote_clients.pop()
            server_name = next(
                name for name, candidate in self.remote_clients.items() if candidate is client
            )
            try:
                await asyncio.wait_for(
                    client.__aexit__(None, None, None),
                    timeout=_MCP_CLEANUP_TIMEOUT_SECONDS,
                )
            except Exception as error:
                self._set_connection_status(
                    server_name,
                    ToolConnectionState.FAILED,
                    error_code=type(error).__name__,
                )
        for name, connection in tuple(self._connection_status.items()):
            if name != _LOCAL_CONNECTION_KEY and connection.state is ToolConnectionState.CONNECTED:
                self._set_connection_status(name, ToolConnectionState.DISCONNECTED)
        for resource in self.resources:
            close = getattr(resource, "aclose", None)
            if close is not None:
                await close()

    def connections(self) -> tuple[ToolConnectionStatus, ...]:
        """Return per-connection state without endpoint or authentication details."""
        return tuple(self._connection_status.values())

    def set_sandbox_mode(self, mode: SandboxMode | None) -> None:
        """Bind the serialized chat mode to in-process MCP filesystem handlers."""
        if self.workspace is not None:
            self.workspace.set_sandbox_mode(mode)

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

        remote_route = self.routing_table.resolve(name)

        async def invoke(validated_arguments: Mapping[str, Any]) -> Any:
            if remote_route is None:
                return await self.client.session.call_tool(name, dict(validated_arguments))
            server_name, remote_tool_name = remote_route
            return await self.remote_clients[server_name].session.call_tool(
                remote_tool_name, dict(validated_arguments)
            )

        if remote_route is not None:
            result = await self._execute_remote_tool(
                name,
                arguments,
                budget=budget,
                confirmation_provider=confirmation_provider,
                invoke=invoke,
            )
        else:
            result = await self._tool_coordinator.execute(
                name,
                arguments,
                budget=budget,
                confirmation_provider=confirmation_provider,
                invoke=invoke,
            )
        return _tool_result_text(result)

    async def _execute_remote_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None,
        invoke: Callable[[Mapping[str, Any]], Awaitable[Any]],
    ) -> Any:
        """Mark remote timeouts as unknown because transport disconnect is not cancellation."""
        try:
            return await self._tool_coordinator.execute(
                name,
                arguments,
                budget=budget,
                confirmation_provider=confirmation_provider,
                invoke=invoke,
            )
        except ToolExecutionTimeoutError as error:
            raise RemoteToolOutcomeUnknownError(
                "Remote MCP call timed out; the remote operation outcome is unknown."
            ) from error

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

        return await self._tool_coordinator.execute(
            request.name,
            request.args,
            budget=budget,
            confirmation_provider=confirmation_provider,
            invoke=invoke,
        )

    async def _load_remote_tools(self) -> list[Any]:
        """Discover each remote server and register its tools under unique names."""
        loaded_tools: list[Any] = []
        for remote in self.remote_servers:
            client: Any = self.remote_clients[remote.name]
            self._set_connection_status(remote.name, ToolConnectionState.CONNECTING)
            try:
                await asyncio.wait_for(client.__aenter__(), timeout=remote.timeout_seconds)
                self._started_remote_clients.append(client)
                remote_tools = (
                    await asyncio.wait_for(
                        client.session.list_tools(), timeout=remote.timeout_seconds
                    )
                ).tools
                pending_registrations = []
                pending_routes: dict[str, tuple[str, str]] = {}
                registered_names = {item.name for item in self.registry.descriptors()}
                for remote_tool in remote_tools:
                    exposed_name = _remote_tool_name(remote.name, remote_tool.name)
                    if exposed_name in pending_routes:
                        raise ValueError("Remote MCP servers exposed a duplicate tool name.")
                    pending_routes[exposed_name] = (remote.name, remote_tool.name)
                    pending_registrations.append(
                        _remote_tool_registration(remote, remote_tool, exposed_name)
                    )
                self.routing_table.validate_routes(
                    pending_routes,
                    reserved_names=frozenset(registered_names),
                )
                server_tools = await asyncio.wait_for(
                    load_mcp_tools(
                        client.session,
                        server_name=remote.name,
                        tool_name_prefix=True,
                        tool_interceptors=[self._authorize_remote_tool],
                        handle_tool_errors=False,
                    ),
                    timeout=remote.timeout_seconds,
                )
                for registration in pending_registrations:
                    self.registry.register(registration)
                self.routing_table.register_routes(
                    pending_routes,
                    reserved_names=frozenset(registered_names),
                )
                loaded_tools.extend(server_tools)
            except asyncio.CancelledError:
                self._set_connection_status(remote.name, ToolConnectionState.DISCONNECTED)
                if client in self._started_remote_clients:
                    self._started_remote_clients.remove(client)
                try:
                    await asyncio.wait_for(
                        client.__aexit__(None, None, None),
                        timeout=_MCP_CLEANUP_TIMEOUT_SECONDS,
                    )
                except Exception:
                    pass
                raise
            except Exception as error:
                self._set_connection_status(
                    remote.name,
                    ToolConnectionState.FAILED,
                    error_code=type(error).__name__,
                )
                if client in self._started_remote_clients:
                    self._started_remote_clients.remove(client)
                try:
                    await asyncio.wait_for(
                        client.__aexit__(None, None, None),
                        timeout=_MCP_CLEANUP_TIMEOUT_SECONDS,
                    )
                except Exception:
                    pass
                continue
            self._set_connection_status(
                remote.name,
                ToolConnectionState.CONNECTED,
                discovered_tool_count=len(remote_tools),
            )
        return loaded_tools

    def _set_connection_status(
        self,
        connection_id: str,
        state: ToolConnectionState,
        *,
        discovered_tool_count: int | None = None,
        error_code: str | None = None,
    ) -> None:
        previous = self._connection_status[connection_id]
        self._connection_status[connection_id] = ToolConnectionStatus(
            connection_id=previous.connection_id,
            transport=previous.transport,
            state=state,
            discovered_tool_count=(
                previous.discovered_tool_count
                if discovered_tool_count is None
                else discovered_tool_count
            ),
            error_code=error_code,
        )

    async def _authorize_remote_tool(self, request: MCPToolCallRequest, handler: Any) -> Any:
        """Govern a remote MCP call using its server-qualified registry entry."""
        runtime_context = getattr(request.runtime, "context", None)
        budget = _request_budget(runtime_context)
        if budget is None:
            raise ToolDeniedError("MCP tools require an active request budget.")
        qualified_name = _remote_tool_name(request.server_name, request.name)
        if not self.routing_table.matches(qualified_name, request.server_name, request.name):
            raise ToolDeniedError("Remote MCP tool is not registered.")

        async def invoke(arguments: Mapping[str, Any]) -> Any:
            return await handler(request.override(args=dict(arguments)))

        return await self._execute_remote_tool(
            qualified_name,
            request.args,
            budget=budget,
            confirmation_provider=_request_confirmation_provider(runtime_context),
            invoke=invoke,
        )


def build_retrieval_mcp_provider(
    settings: Settings,
    *,
    workspace_root: Path | None = None,
    workspace_read_only: bool = False,
    workspace_instance: WorkspaceFiles | None = None,
    sandbox_backends: SandboxBackendSet | None = None,
    executor_factory: ToolExecutorFactory | None = None,
    retrieval_client: RagRetrievalClient | None = None,
    trace_sink: TraceSink | None = None,
    audit_sink: AuditSink | None = None,
) -> MCPToolProvider:
    """Build a query-only RAG tool and bounded workspace text-file tools."""
    server = FastMCP("HostPilot tools")
    selected_backends = sandbox_backends or LinuxSandboxBackendFactory().create()
    python_sandbox = selected_backends.python
    javascript_sandbox = selected_backends.javascript
    shell_sandbox = selected_backends.shell
    filesystem_sandbox = selected_backends.filesystem
    root = workspace_root or Path.cwd()
    workspace = workspace_instance or WorkspaceFiles(root, read_only=workspace_read_only)
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
        return cast(
            str,
            await filesystem_sandbox.execute(workspace, "read_text_file", {"path": path}),
        )

    @server.tool(
        name="read_text_range",
        description=(
            "Read a bounded UTF-8 byte range from a file. The range must align with UTF-8 "
            "character boundaries."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    async def read_text_range(path: str, offset: int, length: int) -> str:
        """Read at most 64 KiB from one offset without loading the entire file."""
        return cast(
            str,
            await filesystem_sandbox.execute(
                workspace,
                "read_text_range",
                {"path": path, "offset": offset, "length": length},
            ),
        )

    @server.tool(
        name="list_directory",
        description=(
            "List up to 100 immediate files and directories under an authorized workspace path; "
            "does not recurse or follow symbolic links."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    async def list_directory(path: str = "", limit: int = 100) -> str:
        """List a bounded number of direct child entries as JSON."""
        result = await filesystem_sandbox.execute(
            workspace, "list_directory", {"path": path, "limit": limit}
        )
        return json.dumps(result, ensure_ascii=False)

    @server.tool(
        name="search_files",
        description=(
            "Search file names using a glob pattern under an authorized path; bounded to "
            "six directory levels and 5,000 inspected entries, without following symbolic links."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False},
    )
    async def search_files(
        pattern: str,
        path: str = "",
        max_results: int = 50,
        max_depth: int = 6,
    ) -> str:
        """Search bounded directory trees for matching file names and return JSON."""
        result = await filesystem_sandbox.execute(
            workspace,
            "search_files",
            {
                "pattern": pattern,
                "path": path,
                "max_results": max_results,
                "max_depth": max_depth,
            },
        )
        return json.dumps(result, ensure_ascii=False)

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
        byte_count = await filesystem_sandbox.execute(
            workspace, "write_text_file", {"path": path, "content": content}
        )
        return f"已写入 {path}（{byte_count} 字节）。"

    @server.tool(
        name="write_existing_text_file",
        description=(
            "Replace a UTF-8 text file that already exists in the HostPilot workspace. "
            "This operation always requires explicit user approval."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def write_existing_text_file(path: str, content: str) -> str:
        """Replace an existing workspace file after explicit user approval."""
        byte_count = await filesystem_sandbox.execute(
            workspace, "write_existing_text_file", {"path": path, "content": content}
        )
        return f"已覆盖 {path}（{byte_count} 字节）。"

    @server.tool(
        name="run_python_code",
        description=(
            "Run Python source in a Linux bubblewrap sandbox with no host workspace, "
            "network, or inherited environment. Requires explicit user approval."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def run_python_code(code: str) -> str:
        """Run bounded Python source in the configured no-network Linux sandbox."""
        result = await python_sandbox.run(code)
        return json.dumps(
            {
                "status": result.status,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
            },
            ensure_ascii=False,
        )

    @server.tool(
        name="run_javascript_code",
        description=(
            "Run JavaScript with Node.js in a Linux bubblewrap sandbox without network or "
            "host workspace access. Requires explicit user approval."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def run_javascript_code(code: str) -> str:
        """Run bounded JavaScript source in the configured Linux sandbox."""
        result = await javascript_sandbox.run(code)
        return json.dumps(
            {
                "status": result.status,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
            },
            ensure_ascii=False,
        )

    @server.tool(
        name="run_shell_script",
        description=(
            "Run a POSIX shell script in a Linux bubblewrap sandbox without network or writable "
            "host filesystem access. System commands may be available read-only. Requires approval."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True},
    )
    async def run_shell_script(script: str) -> str:
        """Run bounded POSIX shell source in the configured Linux sandbox."""
        result = await shell_sandbox.run(script)
        return json.dumps(
            {
                "status": result.status,
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": result.duration_ms,
            },
            ensure_ascii=False,
        )

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

    def validate_read_range(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"path", "offset", "length"}:
            raise ValueError("read_text_range accepts path, offset and length.")
        path = arguments.get("path")
        offset = arguments.get("offset")
        length = arguments.get("length")
        if not isinstance(path, str) or not path.strip() or len(path) > 1024:
            raise ValueError("path must contain between 1 and 1024 characters.")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ValueError("offset must be a non-negative integer.")
        if not isinstance(length, int) or isinstance(length, bool) or not 1 <= length <= 65_536:
            raise ValueError("length must be between 1 and 65536 bytes.")
        workspace.validate_read_path(path.strip())
        return {"path": path.strip(), "offset": offset, "length": length}

    def validate_list_directory(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) - {"path", "limit"}:
            raise ValueError("list_directory accepts only path and limit.")
        path = arguments.get("path", "")
        limit = arguments.get("limit", 100)
        if not isinstance(path, str) or len(path) > 1024:
            raise ValueError("path must not exceed 1024 characters.")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100.")
        workspace.validate_directory_path(path)
        return {"path": path, "limit": limit}

    def validate_search_files(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) - {"pattern", "path", "max_results", "max_depth"}:
            raise ValueError("search_files received unsupported arguments.")
        pattern = arguments.get("pattern")
        path = arguments.get("path", "")
        max_results = arguments.get("max_results", 50)
        max_depth = arguments.get("max_depth", 6)
        if not isinstance(pattern, str) or not pattern.strip() or len(pattern) > 256:
            raise ValueError("pattern must contain between 1 and 256 characters.")
        if not isinstance(path, str) or len(path) > 1024:
            raise ValueError("path must not exceed 1024 characters.")
        if (
            not isinstance(max_results, int)
            or isinstance(max_results, bool)
            or not 1 <= max_results <= 100
        ):
            raise ValueError("max_results must be between 1 and 100.")
        if not isinstance(max_depth, int) or isinstance(max_depth, bool) or not 0 <= max_depth <= 8:
            raise ValueError("max_depth must be between 0 and 8.")
        workspace.validate_directory_path(path)
        return {
            "pattern": pattern,
            "path": path,
            "max_results": max_results,
            "max_depth": max_depth,
        }

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

    def validate_existing_write(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        validated = validate_write(arguments)
        if not workspace.validate_write_path(str(validated["path"])).is_file():
            raise ValueError("write_existing_text_file only accepts an existing file.")
        return validated

    def validate_python(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"code"}:
            raise ValueError("run_python_code accepts only code.")
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip() or "\x00" in code:
            raise ValueError("code must contain non-empty Python source without NUL bytes.")
        if len(code.encode("utf-8")) > python_sandbox.max_source_bytes:
            raise ValueError("code exceeds the sandbox source size limit.")
        return {"code": code}

    def validate_javascript(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"code"}:
            raise ValueError("run_javascript_code accepts only code.")
        code = arguments.get("code")
        if not isinstance(code, str) or not code.strip() or "\x00" in code:
            raise ValueError("code must contain non-empty JavaScript source without NUL bytes.")
        if len(code.encode("utf-8")) > javascript_sandbox.max_source_bytes:
            raise ValueError("code exceeds the sandbox source size limit.")
        return {"code": code}

    def validate_shell(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if set(arguments) != {"script"}:
            raise ValueError("run_shell_script accepts only script.")
        script = arguments.get("script")
        if not isinstance(script, str) or not script.strip() or "\x00" in script:
            raise ValueError("script must contain non-empty shell source without NUL bytes.")
        if len(script.encode("utf-8")) > shell_sandbox.max_source_bytes:
            raise ValueError("script exceeds the sandbox source size limit.")
        return {"script": script}

    def summarize_write(arguments: Mapping[str, Any]) -> str:
        path = str(arguments["path"])
        byte_count = len(str(arguments["content"]).encode("utf-8"))
        target = workspace.root / path
        operation = "覆盖" if target.exists() else "创建"
        return f"{operation}工作区文件 `{path}`，写入 {byte_count} 字节。"

    def summarize_python(arguments: Mapping[str, Any]) -> str:
        code_bytes = len(str(arguments["code"]).encode("utf-8"))
        return (
            f"运行 {code_bytes} 字节 Python 代码（无网络和主机文件访问；"
            "CPU 上限 2 秒、内存上限 256 MiB）；需先审批。"
        )

    def summarize_javascript(arguments: Mapping[str, Any]) -> str:
        code_bytes = len(str(arguments["code"]).encode("utf-8"))
        return (
            f"运行 {code_bytes} 字节 JavaScript（无网络和主机文件访问；CPU 上限 2 秒、"
            "V8 堆上限 256 MiB、墙钟 5 秒）；需先审批。"
        )

    def summarize_shell(arguments: Mapping[str, Any]) -> str:
        script_bytes = len(str(arguments["script"]).encode("utf-8"))
        return (
            f"运行 {script_bytes} 字节 Shell 脚本（无网络和宿主文件写入；"
            "仅挂载只读系统目录，CPU 上限 2 秒、墙钟 5 秒）；需先审批。"
        )

    registrations = (
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="retrieve_knowledge",
                description="Return a placeholder while knowledge retrieval is disabled.",
                tool_type=ToolType.RETRIEVAL,
                trust_level=TrustLevel.THIRD_PARTY,
                execution_env=ExecutionEnvironment.SERVICE,
                timeout_profile=TimeoutProfile(timeout_seconds=10),
                access_summary=("将查询发送到后端配置的 RAG 服务；未配置时只返回占位结果。"),
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string", "minLength": 1}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
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
                timeout_profile=TimeoutProfile(timeout_seconds=5),
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                access_summary=(
                    "读取所选工作区内 UTF-8 文本文件，单文件最多 64 KiB；"
                    "danger-full-access 会话可读取当前 OS 用户有权访问的路径。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Workspace relative file path.",
                        }
                    },
                    "required": ["path"],
                    "additionalProperties": False,
                },
            ),
            validate_arguments=validate_read,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="read_text_range",
                description="Read a bounded UTF-8 byte range from a local text file.",
                tool_type=ToolType.ACTION,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
                access_summary=(
                    "按字节偏移读取 UTF-8 文本文件，每次最多 64 KiB；路径遵循当前会话文件访问范围。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "offset": {"type": "integer", "minimum": 0},
                        "length": {"type": "integer", "minimum": 1, "maximum": 65536},
                    },
                    "required": ["path", "offset", "length"],
                    "additionalProperties": False,
                },
            ),
            validate_arguments=validate_read_range,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="list_directory",
                description="List bounded direct children of an authorized local directory.",
                tool_type=ToolType.ACTION,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
                access_summary=(
                    "仅列出当前目录，最多 100 项；默认不递归并跳过符号链接，"
                    "路径遵循当前会话文件访问范围。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "additionalProperties": False,
                },
            ),
            validate_arguments=validate_list_directory,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="search_files",
                description="Search file names with a bounded glob under an authorized directory.",
                tool_type=ToolType.ACTION,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
                access_summary=(
                    "按文件名 glob 搜索，最多 6 层、检查 5,000 项、返回 100 项；"
                    "不跟随符号链接，路径遵循当前会话文件访问范围。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "pattern": {"type": "string"},
                        "path": {"type": "string"},
                        "max_results": {"type": "integer", "minimum": 1, "maximum": 100},
                        "max_depth": {"type": "integer", "minimum": 0, "maximum": 8},
                    },
                    "required": ["pattern"],
                    "additionalProperties": False,
                },
            ),
            validate_arguments=validate_search_files,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="write_text_file",
                description="Create or replace a workspace text file after user approval.",
                tool_type=ToolType.ACTION,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
                high_impact=True,
                access_summary=(
                    "在工作区内创建或覆盖 UTF-8 文本文件，单文件最多 64 KiB；"
                    "danger-full-access 会话可写入当前 OS 用户有权访问的路径。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "Workspace relative target path.",
                        },
                        "content": {"type": "string", "description": "Text to write."},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            ),
            validate_arguments=validate_write,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
            confirmation_summary=summarize_write,
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="write_existing_text_file",
                description="Replace an existing workspace text file after user approval.",
                tool_type=ToolType.ACTION,
                trust_level=TrustLevel.HOST,
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                timeout_profile=TimeoutProfile(timeout_seconds=5),
                high_impact=True,
                access_summary=(
                    "仅覆盖已存在的 UTF-8 文本文件，单文件最多 64 KiB；"
                    "danger-full-access 会话可写入当前 OS 用户有权访问的路径。"
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                    "additionalProperties": False,
                },
            ),
            validate_arguments=validate_existing_write,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
            confirmation_summary=lambda arguments: (
                f"覆盖既有工作区文件 `{arguments['path']}`，写入 "
                f"{len(str(arguments['content']).encode('utf-8'))} 字节。"
            ),
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="run_python_code",
                description="Execute Python source in a Linux bubblewrap sandbox.",
                tool_type=ToolType.CODE,
                trust_level=TrustLevel.UNTRUSTED,
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                timeout_profile=TimeoutProfile(timeout_seconds=8),
                high_impact=True,
                access_summary=(
                    "执行 Python 源码；无网络和宿主工作区访问，限制 CPU 2 秒、"
                    "内存 256 MiB、单进程、墙钟 5 秒、源码 64 KiB、输出 16 KiB。"
                ),
            ),
            validate_arguments=validate_python,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
            confirmation_summary=summarize_python,
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="run_javascript_code",
                description="Execute JavaScript source in a Linux bubblewrap sandbox.",
                tool_type=ToolType.CODE,
                trust_level=TrustLevel.UNTRUSTED,
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                timeout_profile=TimeoutProfile(timeout_seconds=8),
                high_impact=True,
                access_summary=(
                    "在 Node.js 沙箱运行；无网络和宿主工作区访问，V8 堆上限 256 MiB、"
                    "CPU 2 秒、单次墙钟 5 秒、源码 64 KiB、输出 16 KiB。"
                ),
            ),
            validate_arguments=validate_javascript,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
            confirmation_summary=summarize_javascript,
        ),
        ToolRegistration(
            descriptor=ToolDescriptor(
                name="run_shell_script",
                description="Execute POSIX shell source in a Linux bubblewrap sandbox.",
                tool_type=ToolType.CODE,
                trust_level=TrustLevel.UNTRUSTED,
                execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                timeout_profile=TimeoutProfile(timeout_seconds=8),
                high_impact=True,
                access_summary=(
                    "在 POSIX Shell 沙箱运行；无网络、无宿主可写文件系统，系统目录只读；"
                    "最多 16 个进程、CPU 2 秒、墙钟 5 秒、源码 64 KiB、输出 16 KiB。"
                ),
            ),
            validate_arguments=validate_shell,
            handler=cast(ToolHandler, handler),
            verify_result=cast(ToolResultVerifier, verify),
            confirmation_summary=summarize_shell,
        ),
    )
    resources = (retrieval_client,) if retrieval_client is not None else ()
    sandboxes: tuple[tuple[str, WorkspaceSandboxBackend | CodeSandboxBackend], ...] = (
        ("read_text_file", filesystem_sandbox),
        ("read_text_range", filesystem_sandbox),
        ("list_directory", filesystem_sandbox),
        ("search_files", filesystem_sandbox),
        ("write_text_file", filesystem_sandbox),
        ("write_existing_text_file", filesystem_sandbox),
        ("run_python_code", python_sandbox),
        ("run_javascript_code", javascript_sandbox),
        ("run_shell_script", shell_sandbox),
    )
    sandbox_unavailability_reasons = {
        name: reason
        for name, sandbox in sandboxes
        if (reason := sandbox.unavailable_reason) is not None
    }
    return MCPToolProvider(
        server,
        registrations,
        resources=resources,
        remote_servers=settings.mcp_servers,
        workspace=workspace,
        sandboxed_environments=(
            frozenset({ExecutionEnvironment.NATIVE_SANDBOX})
            if any(
                sandbox.host_supported
                for sandbox in (
                    filesystem_sandbox,
                    python_sandbox,
                    javascript_sandbox,
                    shell_sandbox,
                )
            )
            else frozenset()
        ),
        sandboxed_tools=frozenset(
            name
            for name, sandbox in (
                ("run_python_code", python_sandbox),
                ("run_javascript_code", javascript_sandbox),
                ("run_shell_script", shell_sandbox),
            )
            if sandbox.host_supported
        )
        | (
            frozenset(
                {
                    "read_text_file",
                    "read_text_range",
                    "list_directory",
                    "search_files",
                    "write_text_file",
                    "write_existing_text_file",
                    "_restore_existing_text_file",
                }
            )
            if filesystem_sandbox.host_supported
            else frozenset()
        ),
        sandbox_unavailability_reasons=sandbox_unavailability_reasons,
        filesystem_sandbox=filesystem_sandbox,
        executor_factory=executor_factory,
        trace_sink=trace_sink,
        audit_sink=audit_sink,
    )


def _remote_transport(remote: RemoteMCPServerSettings) -> Any:
    """Build one transport without passing HostPilot secrets into child processes."""
    if remote.transport == "streamable_http":
        assert remote.url is not None
        return StreamableHttpTransport(
            remote.url,
            headers={key: value.get_secret_value() for key, value in remote.headers.items()},
        )

    assert remote.command is not None
    inherited_names = {
        "PATH",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "SYSTEMROOT",
        "TMPDIR",
        "TEMP",
        "TMP",
        "LANG",
        "LC_ALL",
        "NPM_CONFIG_CACHE",
        "NPM_CONFIG_PREFIX",
    }
    child_environment = {name: os.environ[name] for name in inherited_names if name in os.environ}
    child_environment.update({name: value.get_secret_value() for name, value in remote.env.items()})
    return StdioTransport(
        command=remote.command,
        args=list(remote.args),
        env=child_environment,
        cwd=str(remote.cwd) if remote.cwd else None,
        keep_alive=False,
        log_file=Path(os.devnull),
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

    validate_schema = json_schema_argument_validator(getattr(remote_tool, "inputSchema", None))

    def validate(arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return validate_schema(arguments)

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
            transport=ToolConnectionTransport(server.transport),
            access_summary=(
                "MCP Server 自定义能力范围；"
                + (
                    "本机子进程与 HostPilot 共用当前 OS 用户权限，无 OS 沙箱。"
                    if server.transport == "stdio"
                    else "工具在远端运行，HostPilot 无法完整推断其副作用范围。"
                )
            ),
            input_schema=(
                dict(remote_tool.inputSchema)
                if isinstance(getattr(remote_tool, "inputSchema", None), Mapping)
                else {"type": "object"}
            ),
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
