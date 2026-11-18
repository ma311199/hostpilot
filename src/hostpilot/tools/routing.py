"""Dynamic routing table for qualified remote MCP tools."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol

RemoteToolRoute = tuple[str, str]


class RemoteToolRouteTable(Protocol):
    """Route qualified tool IDs to their owning server and native tool ID."""

    def validate_routes(
        self,
        routes: Mapping[str, RemoteToolRoute],
        *,
        reserved_names: frozenset[str] = frozenset(),
    ) -> None: ...

    def register_routes(
        self,
        routes: Mapping[str, RemoteToolRoute],
        *,
        reserved_names: frozenset[str] = frozenset(),
    ) -> None: ...

    def resolve(self, exposed_name: str) -> RemoteToolRoute | None: ...

    def matches(self, exposed_name: str, server_name: str, tool_name: str) -> bool: ...


class MCPToolRoutingTable:
    """Store validated routes for dynamically discovered remote MCP tools."""

    def __init__(self) -> None:
        self._routes: dict[str, RemoteToolRoute] = {}

    @property
    def routes(self) -> Mapping[str, RemoteToolRoute]:
        """Expose a read-only view for diagnostics and tests."""
        return MappingProxyType(self._routes)

    def validate_routes(
        self,
        routes: Mapping[str, RemoteToolRoute],
        *,
        reserved_names: frozenset[str] = frozenset(),
    ) -> None:
        """Reject invalid or conflicting routes without changing the table."""
        pending_names: set[str] = set()
        for exposed_name, route in routes.items():
            if not exposed_name or exposed_name in pending_names:
                raise ValueError("Remote MCP servers exposed a duplicate tool name.")
            if exposed_name in self._routes or exposed_name in reserved_names:
                raise ValueError("Remote MCP servers exposed a duplicate tool name.")
            if len(route) != 2 or not all(route):
                raise ValueError("Remote MCP tool route is invalid.")
            pending_names.add(exposed_name)

    def register_routes(
        self,
        routes: Mapping[str, RemoteToolRoute],
        *,
        reserved_names: frozenset[str] = frozenset(),
    ) -> None:
        """Validate a complete discovery batch before making it visible."""
        self.validate_routes(routes, reserved_names=reserved_names)
        self._routes.update(routes)

    def resolve(self, exposed_name: str) -> RemoteToolRoute | None:
        """Return the owning server and native tool name, if registered."""
        return self._routes.get(exposed_name)

    def matches(self, exposed_name: str, server_name: str, tool_name: str) -> bool:
        """Verify that an intercepted call matches its discovered route."""
        return self.resolve(exposed_name) == (server_name, tool_name)


__all__ = ["MCPToolRoutingTable", "RemoteToolRoute", "RemoteToolRouteTable"]
