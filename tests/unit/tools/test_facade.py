import asyncio

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
from hostpilot.tools.facade import ToolFacade


class FakeRegistry:
    def __init__(self, descriptor: ToolDescriptor) -> None:
        self._descriptor = descriptor

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self._descriptor,)


class FakeProvider:
    def __init__(self, descriptor: ToolDescriptor) -> None:
        self.registry = FakeRegistry(descriptor)
        self.calls = []

    async def call_tool(self, name, arguments, *, budget, confirmation_provider=None):
        self.calls.append((name, arguments, budget, confirmation_provider))
        return "result"


def test_tool_facade_exposes_catalog_and_delegates_governed_invocation() -> None:
    async def scenario() -> None:
        descriptor = ToolDescriptor(
            name="read_text_file",
            description="Read a workspace file",
            tool_type=ToolType.ACTION,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=5),
        )
        provider = FakeProvider(descriptor)
        facade = ToolFacade(provider)
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
        confirmation = object()

        assert facade.descriptors() == (descriptor,)
        result = await facade.call_tool(
            "read_text_file",
            {"path": "README.md"},
            budget=budget,
            confirmation_provider=confirmation,
        )

        assert result == "result"
        assert provider.calls == [("read_text_file", {"path": "README.md"}, budget, confirmation)]

    asyncio.run(scenario())
