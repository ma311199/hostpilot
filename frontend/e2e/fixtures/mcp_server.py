"""Local Streamable HTTP MCP server used by browser E2E tests."""

from __future__ import annotations

from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import JSONResponse

server = FastMCP("HostPilot E2E MCP")


@server.tool
def e2e_ping() -> str:
    """Return a fixed response for the local E2E fixture."""
    return "pong"


@server.custom_route("/health", methods=["GET"])
async def health(_request: Request) -> JSONResponse:
    """Expose a readiness endpoint for Playwright's web server manager."""
    return JSONResponse({"status": "ok"})


if __name__ == "__main__":
    server.run(transport="http", host="127.0.0.1", port=8012, path="/mcp")
