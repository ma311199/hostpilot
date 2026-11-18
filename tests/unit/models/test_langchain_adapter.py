import asyncio

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from hostpilot.budget import BudgetExceededError, RequestBudget
from hostpilot.budget.context import request_budget_scope
from hostpilot.contracts import BudgetContext, BudgetLimits, ModelTokenUsage
from hostpilot.models import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelToolDefinition,
)
from hostpilot.models.budget import complete_with_budget
from hostpilot.models.langchain_adapter import ProviderChatModel


class FakeProvider:
    def __init__(self, response: ModelResponse) -> None:
        self.response = response
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        return self.response


def test_provider_chat_model_maps_tool_loop_and_usage() -> None:
    provider = FakeProvider(
        ModelResponse(
            text="",
            tool_calls=(ModelToolCall(call_id="call-1", name="lookup", arguments={"key": "x"}),),
            finish_reason="tool_calls",
            token_usage=ModelTokenUsage(input_tokens=8, output_tokens=2, total_tokens=10),
        )
    )
    model = ProviderChatModel(provider=provider, model_id="test-model")

    async def invoke() -> None:
        bound = model.bind_tools(
            [
                {
                    "name": "lookup",
                    "description": "Look up a value.",
                    "parameters": {
                        "type": "object",
                        "properties": {"key": {"type": "string"}},
                    },
                }
            ],
            tool_choice="auto",
        )
        response = await bound.ainvoke(
            [
                HumanMessage(content="Look up x"),
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "lookup",
                            "args": {"key": "x"},
                            "id": "previous-call",
                            "type": "tool_call",
                        }
                    ],
                ),
                ToolMessage(content="found", tool_call_id="previous-call"),
            ]
        )

        assert response.tool_calls[0]["name"] == "lookup"
        assert response.usage_metadata == {
            "input_tokens": 8,
            "output_tokens": 2,
            "total_tokens": 10,
        }

    asyncio.run(invoke())
    request = provider.requests[0]
    assert [message.role for message in request.messages] == [
        ModelRole.USER,
        ModelRole.ASSISTANT,
        ModelRole.TOOL,
    ]
    assert request.messages[1].tool_calls[0].call_id == "previous-call"
    assert request.messages[2].tool_call_id == "previous-call"
    assert request.tools == (
        ModelToolDefinition(
            name="lookup",
            description="Look up a value.",
            parameters={"type": "object", "properties": {"key": {"type": "string"}}},
        ),
    )
    assert request.tool_choice == "auto"


def test_provider_chat_model_caps_and_accounts_each_scoped_request() -> None:
    provider = FakeProvider(
        ModelResponse(
            text="reply",
            token_usage=ModelTokenUsage(input_tokens=3, output_tokens=2, total_tokens=5),
        )
    )
    model = ProviderChatModel(provider=provider, model_id="test-model")
    budget = RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=6,
                tool_calls=0,
                retrieval_calls=0,
                timeout_seconds=10,
            )
        )
    )

    async def invoke() -> None:
        with request_budget_scope(budget):
            await model.ainvoke([HumanMessage(content="first")])
            assert budget.remaining_tokens == 1
            with pytest.raises(BudgetExceededError, match="total_tokens"):
                await model.ainvoke([HumanMessage(content="second")])

    asyncio.run(invoke())

    assert len(provider.requests) == 2
    assert provider.requests[0].max_output_tokens == 6
    assert provider.requests[1].max_output_tokens == 1
    assert budget.snapshot().usage.total_tokens == 5


def test_parallel_budgeted_model_calls_serialize_and_stop_after_exhaustion() -> None:
    class SlowProvider:
        def __init__(self) -> None:
            self.requests = []
            self.active = 0
            self.max_active = 0

        async def complete(self, request):
            self.requests.append(request)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return ModelResponse(
                text="result",
                token_usage=ModelTokenUsage(input_tokens=4, output_tokens=2, total_tokens=6),
            )

    provider = SlowProvider()
    budget = RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=10,
                tool_calls=0,
                retrieval_calls=0,
                timeout_seconds=10,
            )
        )
    )
    request = ModelRequest(messages=(ModelMessage(ModelRole.USER, "subtask"),))

    async def invoke_all() -> list[object]:
        return list(
            await asyncio.gather(
                *(complete_with_budget(provider, request, budget) for _ in range(3)),
                return_exceptions=True,
            )
        )

    results = asyncio.run(invoke_all())

    assert provider.max_active == 1
    assert len(provider.requests) == 2
    assert [request.max_output_tokens for request in provider.requests] == [10, 4]
    assert sum(isinstance(result, BudgetExceededError) for result in results) == 2
    assert budget.snapshot().usage.total_tokens == 6
