"""Tests for adapters from Runtime Ports to existing model/tool services."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot, ModelTokenUsage
from hostpilot.models import ModelMessage, ModelRequest, ModelResponse, ModelRole, ModelToolCall
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.runtime import RequestContext
from hostpilot.runtime.adapters import (
    ModelProviderRuntimeAdapter,
    ToolFacadeRuntimeAdapter,
)


def request_context(*, confirmation_provider: Any = None) -> RequestContext:
    return RequestContext(
        ContextSnapshot(request_id="adapter-test", session_id="session-1"),
        RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=50,
                    tool_calls=4,
                    retrieval_calls=2,
                    timeout_seconds=30,
                )
            )
        ),
        confirmation_provider=confirmation_provider,
    )


class FakeModelProvider:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.requests: list[ModelRequest] = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return self.response


def test_model_provider_adapter_uses_request_budget_and_accounts_usage_once() -> None:
    context = request_context()
    usage = ModelTokenUsage(input_tokens=4, output_tokens=3, total_tokens=7)
    provider = FakeModelProvider(ModelResponse(text="answer", token_usage=usage))
    adapter = ModelProviderRuntimeAdapter(provider)
    request = ModelRequest(
        messages=(ModelMessage(role=ModelRole.USER, content="question"),),
        max_output_tokens=80,
    )

    result = asyncio.run(adapter.complete(context, request))

    assert result.text == "answer"
    assert result.budget_usage_accounted is True
    assert provider.requests[0].max_output_tokens == 50
    assert context.budget.snapshot().usage.total_tokens == 7


class ConfirmationStub:
    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        del request
        return ConfirmationDecision.APPROVED


class FakeToolFacade:
    def __init__(self) -> None:
        self.call: tuple[str, Mapping[str, Any], RequestBudget, Any] | None = None

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: Any = None,
    ) -> str:
        self.call = (name, arguments, budget, confirmation_provider)
        return "tool result"


def test_tool_facade_adapter_forwards_shared_budget_and_confirmation() -> None:
    confirmation = ConfirmationStub()
    context = request_context(confirmation_provider=confirmation)
    facade = FakeToolFacade()
    adapter = ToolFacadeRuntimeAdapter(facade)
    proposal = ModelToolCall(call_id="call-1", name="search_files", arguments={"pattern": "*.py"})

    result = asyncio.run(adapter.execute(context, proposal))

    assert result == "tool result"
    assert facade.call is not None
    name, arguments, budget, forwarded_confirmation = facade.call
    assert name == "search_files"
    assert arguments == {"pattern": "*.py"}
    assert budget is context.budget
    assert forwarded_confirmation is context.confirmation_provider
