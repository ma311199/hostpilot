import asyncio

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ExecutionEnvironment,
    TimeoutProfile,
    ToolDescriptor,
    ToolRetryClass,
    ToolSideEffect,
    ToolType,
    TrustLevel,
)
from hostpilot.tools.facade import ToolFacade


class FakeRegistry:
    def __init__(self, descriptor: ToolDescriptor) -> None:
        self._descriptor = descriptor

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        return (self._descriptor,)

    def get(self, name: str):
        assert name == self._descriptor.name

        class Registration:
            descriptor = self._descriptor

        return Registration()


class FakeProvider:
    def __init__(self, descriptor: ToolDescriptor) -> None:
        self.registry = FakeRegistry(descriptor)
        self.calls = []

    async def call_tool(self, name, arguments, *, budget, confirmation_provider=None):
        self.calls.append((name, arguments, budget, confirmation_provider))
        return "result"


def test_tool_retry_classification_is_separate_from_side_effect_level() -> None:
    read_tool = ToolDescriptor(
        name="read_text_file",
        description="Read a workspace file",
        tool_type=ToolType.ACTION,
        side_effect=ToolSideEffect.NONE,
        trust_level=TrustLevel.HOST,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=5),
    )
    write_tool = ToolDescriptor(
        name="send_message",
        description="Send a message",
        tool_type=ToolType.ACTION,
        side_effect=ToolSideEffect.POSSIBLE,
        trust_level=TrustLevel.THIRD_PARTY,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=5),
    )

    assert read_tool.effective_retry_class is ToolRetryClass.PURE_READ
    assert write_tool.effective_retry_class is ToolRetryClass.UNKNOWN


def test_tool_retry_classification_does_not_derive_from_high_impact() -> None:
    tool = ToolDescriptor(
        name="repeatable_update",
        description="Update one resource to a requested value",
        tool_type=ToolType.ACTION,
        trust_level=TrustLevel.THIRD_PARTY,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=5),
        high_impact=True,
        retry_class=ToolRetryClass.IDEMPOTENT_WRITE,
        idempotency_window_seconds=600,
        rejects_idempotency_body_conflict=True,
    )

    assert tool.effective_retry_class is ToolRetryClass.IDEMPOTENT_WRITE


def test_facade_forwards_idempotency_key_only_to_declared_provider_capability() -> None:
    descriptor = ToolDescriptor(
        name="repeatable_update",
        description="Update one resource to a requested value",
        tool_type=ToolType.ACTION,
        trust_level=TrustLevel.THIRD_PARTY,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=5),
        retry_class=ToolRetryClass.IDEMPOTENT_WRITE,
        idempotency_window_seconds=600,
        rejects_idempotency_body_conflict=True,
    )

    class IdempotentProvider(FakeProvider):
        async def call_tool_with_idempotency_key(
            self,
            name,
            arguments,
            *,
            idempotency_key,
            budget,
            confirmation_provider=None,
        ):
            self.calls.append((name, arguments, idempotency_key, budget, confirmation_provider))
            return "deduplicated result"

    async def scenario() -> None:
        provider = IdempotentProvider(descriptor)
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
        result = await facade.call_tool_with_idempotency_key(
            "repeatable_update",
            {"resource_id": "resource-1", "value": "ready"},
            idempotency_key="hostpilot:v1:stable-key",
            budget=budget,
        )
        assert result == "deduplicated result"
        assert provider.calls == [
            (
                "repeatable_update",
                {"resource_id": "resource-1", "value": "ready"},
                "hostpilot:v1:stable-key",
                budget,
                None,
            )
        ]

    asyncio.run(scenario())


def test_facade_rejects_key_when_generic_provider_has_no_idempotency_contract() -> None:
    descriptor = ToolDescriptor(
        name="send_message",
        description="Send a message",
        tool_type=ToolType.ACTION,
        trust_level=TrustLevel.THIRD_PARTY,
        execution_env=ExecutionEnvironment.HOST,
        timeout_profile=TimeoutProfile(timeout_seconds=5),
    )
    provider = FakeProvider(descriptor)
    facade = ToolFacade(provider)

    async def scenario() -> None:
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
        with pytest.raises(RuntimeError, match="does not declare an idempotency contract"):
            await facade.call_tool_with_idempotency_key(
                "send_message",
                {"body": "hello"},
                idempotency_key="hostpilot:v1:stable-key",
                budget=budget,
            )

    asyncio.run(scenario())
    assert provider.calls == []


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
