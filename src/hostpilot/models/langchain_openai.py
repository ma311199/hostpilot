"""OpenAI-compatible Chat API adapter implemented with LangChain."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from hostpilot.contracts import ModelTokenUsage
from hostpilot.models.contracts import (
    ModelMessage,
    ModelProviderError,
    ModelProviderErrorCategory,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
)


class OpenAICompatibleChatProvider:
    """Normalize a LangChain chat model configured for an OpenAI-compatible API."""

    provider_id = "openai-compatible-chat"

    def __init__(self, chat_model: Any, *, model_id: str) -> None:
        self.chat_model = chat_model
        self.model_id = model_id

    async def complete(self, request: ModelRequest) -> ModelResponse:
        messages = [_to_langchain_message(message) for message in request.messages]
        model = self.chat_model
        if request.tools:
            model = model.bind_tools(
                [
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.parameters),
                    }
                    for tool in request.tools
                ],
                tool_choice=request.tool_choice,
            )
        if request.temperature is not None or request.max_output_tokens is not None:
            options: dict[str, Any] = {}
            if request.temperature is not None:
                options["temperature"] = request.temperature
            if request.max_output_tokens is not None:
                options["max_tokens"] = request.max_output_tokens
            model = model.bind(**options)
        if request.stop:
            model = model.bind(stop=list(request.stop))

        try:
            response = await _invoke_with_controls(
                model,
                messages,
                timeout_seconds=request.timeout_seconds,
                cancellation_event=request.cancellation_event,
            )
        except ModelProviderError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:
            raise _normalize_error(error) from None

        return ModelResponse(
            text=_response_text(getattr(response, "content", "")),
            tool_calls=_response_tool_calls(response),
            finish_reason=_finish_reason(response),
            token_usage=_token_usage(getattr(response, "usage_metadata", None)),
            provider_id=self.provider_id,
            model_id=self.model_id,
        )


def _to_langchain_message(message: ModelMessage) -> Any:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    if message.role is ModelRole.SYSTEM:
        return SystemMessage(content=message.content)
    if message.role is ModelRole.USER:
        return HumanMessage(content=message.content)
    if message.role is ModelRole.ASSISTANT:
        return AIMessage(
            content=message.content,
            tool_calls=[
                {
                    "name": call.name,
                    "args": dict(call.arguments),
                    "id": call.call_id,
                    "type": "tool_call",
                }
                for call in message.tool_calls
            ],
        )
    return ToolMessage(content=message.content, tool_call_id=message.tool_call_id or "")


async def _invoke_with_controls(
    model: Any,
    messages: list[Any],
    *,
    timeout_seconds: float | None,
    cancellation_event: asyncio.Event | None,
) -> Any:
    if cancellation_event is not None and cancellation_event.is_set():
        raise ModelProviderError(ModelProviderErrorCategory.CANCELLED)
    call_task = asyncio.create_task(model.ainvoke(messages))
    cancellation_task = (
        asyncio.create_task(cancellation_event.wait()) if cancellation_event is not None else None
    )
    tasks = {call_task}
    if cancellation_task is not None:
        tasks.add(cancellation_task)
    try:
        done, _ = await asyncio.wait(
            tasks,
            timeout=timeout_seconds,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if call_task in done:
            return await call_task
        call_task.cancel()
        await asyncio.gather(call_task, return_exceptions=True)
        if cancellation_task is not None and cancellation_task in done:
            raise ModelProviderError(ModelProviderErrorCategory.CANCELLED)
        raise ModelProviderError(ModelProviderErrorCategory.TIMEOUT)
    except asyncio.CancelledError:
        call_task.cancel()
        await asyncio.gather(call_task, return_exceptions=True)
        raise
    finally:
        if cancellation_task is not None:
            cancellation_task.cancel()
            await asyncio.gather(cancellation_task, return_exceptions=True)


def _response_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            item["text"]
            for item in content
            if isinstance(item, Mapping)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        ).strip()
    return ""


def _response_tool_calls(response: Any) -> tuple[ModelToolCall, ...]:
    raw_calls = getattr(response, "tool_calls", ())
    calls: list[ModelToolCall] = []
    for index, call in enumerate(raw_calls):
        if not isinstance(call, Mapping):
            continue
        name = call.get("name")
        arguments = call.get("args")
        if not isinstance(name, str) or not isinstance(arguments, Mapping):
            continue
        call_id = call.get("id")
        calls.append(
            ModelToolCall(
                call_id=call_id if isinstance(call_id, str) else f"call-{index}",
                name=name,
                arguments=dict(arguments),
            )
        )
    return tuple(calls)


def _finish_reason(response: Any) -> str | None:
    metadata = getattr(response, "response_metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    reason = metadata.get("finish_reason")
    return reason if isinstance(reason, str) else None


def _token_usage(metadata: Any) -> ModelTokenUsage | None:
    if not isinstance(metadata, Mapping):
        return None
    input_tokens = metadata.get("input_tokens")
    output_tokens = metadata.get("output_tokens")
    total_tokens = metadata.get("total_tokens")
    if (
        not isinstance(input_tokens, int)
        or not isinstance(output_tokens, int)
        or input_tokens < 0
        or output_tokens < 0
    ):
        return None
    return ModelTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=(
            total_tokens
            if isinstance(total_tokens, int) and total_tokens >= 0
            else input_tokens + output_tokens
        ),
    )


def _normalize_error(error: Exception) -> ModelProviderError:
    status = getattr(error, "status_code", None)
    status_code = status if isinstance(status, int) else None
    provider_code = getattr(error, "code", None)
    safe_code = provider_code if isinstance(provider_code, str) else None
    name = type(error).__name__.casefold()
    if status_code == 401 or "authentication" in name:
        category = ModelProviderErrorCategory.AUTHENTICATION
    elif status_code == 404 or "modelnotfound" in name:
        category = ModelProviderErrorCategory.MODEL_NOT_FOUND
    elif status_code == 400 or "badrequest" in name or "invalidrequest" in name:
        category = ModelProviderErrorCategory.INVALID_REQUEST
    elif status_code == 429 or "ratelimit" in name:
        category = ModelProviderErrorCategory.RATE_LIMITED
    elif status_code is not None and status_code >= 500:
        category = ModelProviderErrorCategory.UNAVAILABLE
    elif isinstance(error, TimeoutError) or "timeout" in name:
        category = ModelProviderErrorCategory.TIMEOUT
    else:
        category = ModelProviderErrorCategory.UNKNOWN
    return ModelProviderError(
        category,
        status_code=status_code,
        provider_code=safe_code,
    )


__all__ = ["OpenAICompatibleChatProvider"]
