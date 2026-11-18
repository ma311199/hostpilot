import pytest

from hostpilot.tools.routing import MCPToolRoutingTable


def test_routing_table_registers_and_resolves_qualified_remote_tool_names() -> None:
    table = MCPToolRoutingTable()
    table.register_routes({"billing__lookup": ("billing", "lookup")})

    assert table.resolve("billing__lookup") == ("billing", "lookup")
    assert table.matches("billing__lookup", "billing", "lookup")
    assert not table.matches("billing__lookup", "other", "lookup")
    assert table.resolve("unknown__lookup") is None


def test_routing_table_rejects_reserved_and_existing_names_atomically() -> None:
    table = MCPToolRoutingTable()
    table.register_routes({"billing__lookup": ("billing", "lookup")})

    with pytest.raises(ValueError, match="duplicate tool name"):
        table.register_routes(
            {
                "inventory__lookup": ("inventory", "lookup"),
                "local_tool": ("inventory", "local_tool"),
            },
            reserved_names=frozenset({"local_tool"}),
        )

    assert table.routes == {"billing__lookup": ("billing", "lookup")}
    assert table.resolve("inventory__lookup") is None


def test_routing_table_rejects_invalid_route_without_mutation() -> None:
    table = MCPToolRoutingTable()

    with pytest.raises(ValueError, match="route is invalid"):
        table.register_routes({"server__tool": ("server", "")})

    assert table.routes == {}
