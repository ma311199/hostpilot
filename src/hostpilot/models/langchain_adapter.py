"""Expose the HostPilot model provider through LangChain's chat-model port."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.utils.function_calling import convert_to_openai_tool
from pydantic import ConfigDict

from hostpilot.budget.context import current_request_budget
from hostpilot.models.budget import complete_with_budget
from hostpilot.models.contracts import (
    ModelMessage,
    ModelRequest,
    ModelRole,
    ModelToolCall,
    ModelToolDefinition,
)


class ProviderChatModel(BaseChatModel):
    """Adapt LangChain agent graph calls to the framework-neutral provider port."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: Any
    model_id: str

    accounts_request_budget: ClassVar[bool] = True

    @property
    def _llm_type(self) -> str:
        return "hostpilot-model-provider"

    @property
    def _identifying_params(self) -> dict[str, Any]:
        return {"model_id": self.model_id}

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: str | Mapping[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        """Keep LangChain's normal tool binding while passing schemas to the provider."""
        schemas = [convert_to_openai_tool(tool) for tool in tools]
        return self.bind(tools=schemas, tool_choice=tool_choice, **kwargs)

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        tools = tuple(_tool_definition(tool) for tool in kwargs.get("tools", ()))
        request = ModelRequest(
            messages=tuple(_model_message(message) for message in messages),
            tools=tools,
            tool_choice=_optional_tool_choice(kwargs.get("tool_choice")),
            stop=tuple(stop or ()),
            temperature=_optional_float(kwargs.get("temperature")),
            max_output_tokens=_optional_int(
                kwargs.get("max_tokens", kwargs.get("max_output_tokens"))
            ),
        )
        budget = current_request_budget()
        response = (
            await complete_with_budget(self.provider, request, budget)
            if budget is not None
            else await self.provider.complete(request)
        )
        usage = response.token_usage
        message = AIMessage(
            content=response.text,
            tool_calls=[
                {
                    "name": call.name,
                    "args": dict(call.arguments),
                    "id": call.call_id,
                    "type": "tool_call",
                }
                for call in response.tool_calls
            ],
            usage_metadata=(
                {
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                }
                if usage is not None
                else None
            ),
            response_metadata={"finish_reason": response.finish_reason}
            if response.finish_reason
            else {},
        )
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Support sync callers while keeping provider I/O async-native."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._agenerate(messages, stop=stop, **kwargs))
        raise RuntimeError("ProviderChatModel sync calls cannot run inside an active event loop.")


def _model_message(message: BaseMessage) -> ModelMessage:
    if isinstance(message, SystemMessage):
        role = ModelRole.SYSTEM
    elif isinstance(message, HumanMessage):
        role = ModelRole.USER
    elif isinstance(message, ToolMessage):
        role = ModelRole.TOOL
    elif isinstance(message, AIMessage):
        role = ModelRole.ASSISTANT
    else:
        raise TypeError(f"Unsupported LangChain message type: {type(message).__name__}")
    calls = tuple(
        ModelToolCall(call_id=call["id"], name=call["name"], arguments=dict(call["args"]))
        for call in getattr(message, "tool_calls", ())
        if isinstance(call.get("id"), str)
        and isinstance(call.get("name"), str)
        and isinstance(call.get("args"), Mapping)
    )
    return ModelMessage(
        role=role,
        content=_message_text(message.content),
        tool_call_id=getattr(message, "tool_call_id", None),
        tool_calls=calls,
    )


def _tool_definition(schema: Any) -> ModelToolDefinition:
    if not isinstance(schema, Mapping):
        raise TypeError("Bound model tools must be mappings.")
    function = schema.get("function", schema)
    if not isinstance(function, Mapping):
        raise TypeError("Bound model tool schema has no function definition.")
    name = function.get("name")
    description = function.get("description", "")
    parameters = function.get("parameters", {"type": "object", "properties": {}})
    if not isinstance(name, str) or not isinstance(description, str):
        raise TypeError("Bound model tool schema has invalid name or description.")
    if not isinstance(parameters, Mapping):
        raise TypeError("Bound model tool parameters must be a JSON schema object.")
    return ModelToolDefinition(name=name, description=description, parameters=parameters)


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            item["text"]
            for item in content
            if isinstance(item, Mapping)
            and item.get("type") == "text"
            and isinstance(item.get("text"), str)
        )
    return ""


def _optional_float(value: Any) -> float | None:
    return value if isinstance(value, (int, float)) else None


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def _optional_tool_choice(value: Any) -> str | Mapping[str, Any] | None:
    if isinstance(value, str) or isinstance(value, Mapping):
        return value
    return None


__all__ = ["ProviderChatModel"]
