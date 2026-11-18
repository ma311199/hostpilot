import asyncio

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ExecutionEnvironment,
    TimeoutProfile,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.orchestration.mcp_provider_host_plugin import MCPProviderHostPlugin
from hostpilot.orchestration.sandbox_backends_host_plugin import (
    SandboxBackendsHostPlugin,
)
from hostpilot.orchestration.tool_governance_host_plugin import ToolGovernanceHostPlugin
from hostpilot.runtime import RuntimeContext, RuntimeHost
from hostpilot.tools.registry import ToolRegistration, ToolRegistry


def descriptor(name: str) -> ToolDescriptor:
    return ToolDescriptor(
        name=name,
        description=f"Run {name}",
        tool_type=ToolType.ACTION,
        trust_level=TrustLevel.HOST,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=5),
    )


class FakeProvider:
    def __init__(self, tool_id: str, reply: str) -> None:
        self.registry = ToolRegistry()
        self.registry.register(
            ToolRegistration(
                descriptor=descriptor(tool_id),
                validate_arguments=lambda arguments: arguments,
                handler=self._handle,
                verify_result=lambda _result, _arguments: None,
            )
        )
        self.reply = reply
        self.calls: list[tuple[str, object]] = []
        self.modes: list[object] = []

    async def _handle(self, _arguments):
        return self.reply

    async def call_tool(self, name, arguments, *, budget, confirmation_provider=None):
        del budget, confirmation_provider
        self.calls.append((name, arguments))
        return self.reply

    def connections(self):
        return (self.reply,)

    def set_sandbox_mode(self, mode) -> None:
        self.modes.append(mode)

    async def prepare_existing_file_write(self, arguments, *, budget, confirmation_provider=None):
        del arguments, budget, confirmation_provider
        return f"prepared:{self.reply}"

    async def compensate_existing_file_write(
        self, token, *, budget, confirmation_provider=None
    ) -> None:
        del budget, confirmation_provider
        self.calls.append(("compensate", token))


def test_runtime_service_facade_forwards_to_bound_provider() -> None:
    async def scenario():
        context = RuntimeContext()
        host = RuntimeHost(context)
        mcp_plugin = MCPProviderHostPlugin(None)
        tool_plugin = ToolGovernanceHostPlugin()
        host.register_plugin(SandboxBackendsHostPlugin())
        host.register_plugin(mcp_plugin)
        host.register_plugin(tool_plugin)
        await host.start()

        facade = context.services.resolve(tool_plugin.service_id)
        provider = FakeProvider("read_text_file", "read result")
        tool_plugin.bind(provider)
        budget = RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=100,
                    tool_calls=2,
                    retrieval_calls=1,
                    timeout_seconds=10,
                )
            )
        )

        assert facade is tool_plugin.facade
        assert context.services.resolve(tool_plugin.governance_service_id) is (
            tool_plugin.executor_factory
        )
        assert [tool.name for tool in facade.descriptors()] == ["read_text_file"]
        assert await facade.call_tool("read_text_file", {"path": "README.md"}, budget=budget) == (
            "read result"
        )
        assert provider.calls == [("read_text_file", {"path": "README.md"})]
        await host.close()

    asyncio.run(scenario())


def test_runtime_service_facade_uses_replacement_provider_without_replacing_facade() -> None:
    async def scenario():
        context = RuntimeContext()
        host = RuntimeHost(context)
        host.register_plugin(SandboxBackendsHostPlugin())
        host.register_plugin(MCPProviderHostPlugin(None))
        tool_plugin = ToolGovernanceHostPlugin()
        host.register_plugin(tool_plugin)
        await host.start()
        facade = context.services.resolve(tool_plugin.service_id)
        first = FakeProvider("old_tool", "old")
        second = FakeProvider("new_tool", "new")
        tool_plugin.bind(first)
        tool_plugin.bind(second)
        budget = RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=100,
                    tool_calls=2,
                    retrieval_calls=1,
                    timeout_seconds=10,
                )
            )
        )

        assert context.services.resolve(tool_plugin.service_id) is facade
        assert [tool.name for tool in facade.descriptors()] == ["new_tool"]
        assert await facade.call_tool("new_tool", {}, budget=budget) == "new"
        assert first.calls == []
        assert second.calls == [("new_tool", {})]
        await host.close()

    asyncio.run(scenario())


def test_runtime_service_facade_clears_provider_on_host_shutdown() -> None:
    async def scenario():
        context = RuntimeContext()
        host = RuntimeHost(context)
        host.register_plugin(SandboxBackendsHostPlugin())
        host.register_plugin(MCPProviderHostPlugin(None))
        tool_plugin = ToolGovernanceHostPlugin()
        host.register_plugin(tool_plugin)
        await host.start()
        tool_plugin.bind(FakeProvider("read_text_file", "result"))
        facade = tool_plugin.facade
        await host.close()

        assert context.services.snapshot() == {}
        with pytest.raises(RuntimeError, match="not bound"):
            facade.descriptors()

    asyncio.run(scenario())


def test_facade_forwards_optional_compensation_and_sandbox_mode() -> None:
    async def scenario():
        context = RuntimeContext()
        host = RuntimeHost(context)
        host.register_plugin(SandboxBackendsHostPlugin())
        host.register_plugin(MCPProviderHostPlugin(None))
        tool_plugin = ToolGovernanceHostPlugin()
        host.register_plugin(tool_plugin)
        await host.start()
        provider = FakeProvider("write_text_file", "result")
        tool_plugin.bind(provider)
        facade = tool_plugin.facade
        budget = RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=100,
                    tool_calls=2,
                    retrieval_calls=1,
                    timeout_seconds=10,
                )
            )
        )

        facade.set_sandbox_mode("workspace_write")
        prepared = await facade.prepare_existing_file_write({}, budget=budget)
        await facade.compensate_existing_file_write("undo-token", budget=budget)

        assert provider.modes == ["workspace_write"]
        assert prepared == "prepared:result"
        assert provider.calls == [("compensate", "undo-token")]
        await host.close()

    asyncio.run(scenario())
