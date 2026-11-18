"""Exercise timeout cancellation over a real Streamable HTTP MCP connection."""

from __future__ import annotations

import asyncio
import socket
from types import SimpleNamespace

import uvicorn
from fastmcp import FastMCP
from langchain_mcp_adapters.interceptors import MCPToolCallRequest

from hostpilot.budget import RequestBudget
from hostpilot.config import RemoteMCPServerSettings
from hostpilot.contracts import BudgetContext, BudgetLimits
from hostpilot.policy.confirmation import ConfirmationDecision
from hostpilot.tools.executor import RemoteToolOutcomeUnknownError
from hostpilot.tools.mcp import MCPToolProvider


class ApproveRemoteCall:
    """Approve the remote action so the test reaches the HTTP transport."""

    async def confirm(self, _request):
        return ConfirmationDecision.APPROVED


def test_streamable_http_timeout_leaves_remote_tool_task_running() -> None:
    async def scenario() -> None:
        tool_started = asyncio.Event()
        tool_cancelled = asyncio.Event()
        tool_release = asyncio.Event()
        tool_finished = asyncio.Event()
        remote_server = FastMCP("cancellation-e2e")

        @remote_server.tool
        async def wait_forever() -> str:
            """Wait until the test releases the remote operation."""
            tool_started.set()
            try:
                await tool_release.wait()
            except asyncio.CancelledError:
                tool_cancelled.set()
                raise
            finally:
                tool_finished.set()
            return "finished after client timeout"

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.setblocking(False)
        port = listener.getsockname()[1]

        server = uvicorn.Server(
            uvicorn.Config(
                remote_server.http_app(path="/mcp", transport="streamable-http"),
                host="127.0.0.1",
                port=port,
                log_level="critical",
                lifespan="on",
            )
        )
        server_task = asyncio.create_task(server.serve(sockets=[listener]))
        provider: MCPToolProvider | None = None
        try:
            async with asyncio.timeout(5):
                while not server.started:
                    if server_task.done():
                        await server_task
                    await asyncio.sleep(0.01)

            provider = MCPToolProvider(
                FastMCP("local"),
                (),
                remote_servers=(
                    RemoteMCPServerSettings(
                        name="slow",
                        url=f"http://127.0.0.1:{port}/mcp",
                        timeout_seconds=0.4,
                    ),
                ),
            )
            tools = await provider.start()
            assert [tool.name for tool in tools] == ["slow_wait_forever"]

            budget = RequestBudget.start(
                BudgetContext(
                    limits=BudgetLimits(
                        total_tokens=100,
                        tool_calls=1,
                        retrieval_calls=1,
                        timeout_seconds=5,
                    )
                )
            )
            try:
                await provider._authorize_remote_tool(
                    MCPToolCallRequest(
                        name="wait_forever",
                        args={},
                        server_name="slow",
                        runtime=SimpleNamespace(
                            context={
                                "budget": budget,
                                "confirmation_provider": ApproveRemoteCall(),
                            }
                        ),
                    ),
                    lambda request: provider.remote_clients["slow"].session.call_tool(
                        request.name, request.args
                    ),
                )
            except RemoteToolOutcomeUnknownError as error:
                assert "timed out" in str(error)
            else:
                raise AssertionError("The remote call should time out at its Server limit.")

            assert tool_started.is_set()
            await asyncio.sleep(0.2)
            assert not tool_cancelled.is_set()

            # Releasing the remote handler proves it remained active after the
            # HTTP request timed out and was cancelled on the client side.
            tool_release.set()
            async with asyncio.timeout(2):
                await tool_finished.wait()
            assert provider.connections()[1].state.value == "connected"
        finally:
            if provider is not None:
                await provider.aclose()
            server.should_exit = True
            await asyncio.wait_for(server_task, timeout=5)

    asyncio.run(scenario())
