import asyncio

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    ExecutionEnvironment,
    TimeoutProfile,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.skills.mcp_actions import build_mcp_step_actions
from hostpilot.skills.runtime import StepActionCall
from hostpilot.skills.yaml_registry import SideEffectLevel
from hostpilot.tools.facade import ToolFacade


class FakeRegistry:
    def __init__(self, descriptor: ToolDescriptor) -> None:
        self.descriptor = descriptor

    def descriptors(self):
        return (self.descriptor,)


class FakeProvider:
    def __init__(self, descriptor: ToolDescriptor) -> None:
        self.registry = FakeRegistry(descriptor)
        self.calls = []

    async def call_tool(self, name, arguments, *, budget, confirmation_provider=None):
        self.calls.append((name, arguments, budget, confirmation_provider))
        return "done"


def budget() -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100,
                tool_calls=5,
                retrieval_calls=2,
                timeout_seconds=30,
            )
        )
    )


def descriptor(tool_type: ToolType = ToolType.ACTION) -> ToolDescriptor:
    return ToolDescriptor(
        name="write_text_file",
        description="Write a text file",
        tool_type=tool_type,
        trust_level=TrustLevel.HOST,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=10),
    )


def test_mcp_action_passes_budget_and_confirmation_through_governed_provider() -> None:
    async def scenario() -> None:
        provider = FakeProvider(descriptor())
        actions = build_mcp_step_actions(ToolFacade(provider))
        assert actions.get("write_text_file").tool_id == "write_text_file"
        request_budget = budget()
        confirmation = object()
        call = StepActionCall(
            action="write_text_file",
            skill_id="business/write",
            step_id="write",
            inputs={"path": "notes.txt"},
            params={"content": "hello"},
            context=ContextSnapshot(request_id="request-1"),
            budget=request_budget,
            idempotency_key="stable-key",
            confirmation_provider=confirmation,
        )

        result = await actions.get("write_text_file").handler(call)

        assert provider.calls == [
            (
                "write_text_file",
                {"content": "hello", "path": "notes.txt"},
                request_budget,
                confirmation,
            )
        ]
        assert result.output == "done"
        assert result.tool_id == "write_text_file"
        assert result.side_effect is not None
        assert result.side_effect.level is SideEffectLevel.IRREVERSIBLE
        assert result.side_effect.idempotency_key == "stable-key"

    asyncio.run(scenario())


def test_retrieval_mcp_action_is_marked_read_only() -> None:
    actions = build_mcp_step_actions(ToolFacade(FakeProvider(descriptor(ToolType.RETRIEVAL))))

    assert actions.get("write_text_file").side_effect_level is SideEffectLevel.READ_ONLY
