import asyncio
from types import SimpleNamespace

import pytest

from hostpilot.contracts import ModelTokenUsage
from hostpilot.models import (
    ModelMessage,
    ModelProviderError,
    ModelProviderErrorCategory,
    ModelRequest,
    ModelRole,
    ModelToolDefinition,
)
from hostpilot.models.langchain_openai import OpenAICompatibleChatProvider


class FakeChatModel:
    def __init__(self, response=None, error=None) -> None:
        self.response = response or SimpleNamespace(
            content=[{"type": "text", "text": "Hello"}],
            usage_metadata={"input_tokens": 4, "output_tokens": 2, "total_tokens": 6},
            response_metadata={"finish_reason": "stop"},
            tool_calls=[],
        )
        self.error = error
        self.messages = None
        self.bound_tools = None
        self.bound_options = None

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = tools
        self.bound_options = kwargs
        return self

    def bind(self, **options):
        self.bound_options = options
        return self

    async def ainvoke(self, messages):
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.response


def test_provider_normalizes_stream_aggregated_text_and_usage() -> None:
    model = FakeChatModel()
    provider = OpenAICompatibleChatProvider(model, model_id="test-model")

    result = asyncio.run(
        provider.complete(
            ModelRequest(
                messages=(
                    ModelMessage(ModelRole.SYSTEM, "Follow system rules."),
                    ModelMessage(ModelRole.USER, "Hello"),
                )
            )
        )
    )

    assert result.text == "Hello"
    assert result.finish_reason == "stop"
    assert result.provider_id == "openai-compatible-chat"
    assert result.model_id == "test-model"
    assert result.token_usage == ModelTokenUsage(input_tokens=4, output_tokens=2, total_tokens=6)
    assert [message.type for message in model.messages] == ["system", "human"]


def test_provider_normalizes_tool_calls_and_model_options() -> None:
    response = SimpleNamespace(
        content="",
        usage_metadata=None,
        response_metadata={"finish_reason": "tool_calls"},
        tool_calls=[{"id": "call-1", "name": "lookup", "args": {"query": "x"}}],
    )
    model = FakeChatModel(response=response)
    provider = OpenAICompatibleChatProvider(model, model_id="test-model")

    result = asyncio.run(
        provider.complete(
            ModelRequest(
                messages=(ModelMessage(ModelRole.USER, "lookup x"),),
                tools=(
                    ModelToolDefinition(
                        name="lookup",
                        description="Look up a value.",
                        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
                    ),
                ),
                temperature=0.2,
                max_output_tokens=64,
            )
        )
    )

    assert model.bound_tools[0]["name"] == "lookup"
    assert model.bound_options == {"temperature": 0.2, "max_tokens": 64}
    assert result.tool_calls[0].call_id == "call-1"
    assert result.tool_calls[0].arguments == {"query": "x"}


def test_provider_maps_authentication_error_without_exposing_provider_text() -> None:
    class AuthenticationError(Exception):
        status_code = 401
        code = "invalid_api_key"

    model = FakeChatModel(error=AuthenticationError("secret-token rejected"))
    provider = OpenAICompatibleChatProvider(model, model_id="test-model")

    with pytest.raises(ModelProviderError) as error:
        asyncio.run(
            provider.complete(ModelRequest(messages=(ModelMessage(ModelRole.USER, "Hello"),)))
        )

    assert error.value.category is ModelProviderErrorCategory.AUTHENTICATION
    assert error.value.status_code == 401
    assert "secret-token" not in str(error.value)


def test_provider_enforces_timeout_and_cancellation() -> None:
    class SlowChatModel(FakeChatModel):
        async def ainvoke(self, messages):
            self.messages = messages
            await asyncio.sleep(1)

    provider = OpenAICompatibleChatProvider(SlowChatModel(), model_id="test-model")

    async def scenario() -> None:
        base_messages = (ModelMessage(ModelRole.USER, "Hello"),)
        with pytest.raises(ModelProviderError) as timeout_error:
            await provider.complete(ModelRequest(messages=base_messages, timeout_seconds=0.01))
        assert timeout_error.value.category is ModelProviderErrorCategory.TIMEOUT

        cancelled = asyncio.Event()
        task = asyncio.create_task(
            provider.complete(ModelRequest(messages=base_messages, cancellation_event=cancelled))
        )
        await asyncio.sleep(0.01)
        cancelled.set()
        with pytest.raises(ModelProviderError) as cancellation_error:
            await task
        assert cancellation_error.value.category is ModelProviderErrorCategory.CANCELLED

    asyncio.run(scenario())
