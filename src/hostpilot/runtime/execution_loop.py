"""Default bounded Model/Tool execution loop owned by Runtime."""

from __future__ import annotations

from collections.abc import Mapping

from hostpilot.contracts import ModelTokenUsage
from hostpilot.models import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelToolDefinition,
)
from hostpilot.runtime.execution import (
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeExecutionStatus,
    RuntimeModelPort,
    RuntimeToolPort,
    runtime_tool_call_key,
)


class RuntimeToolProposalError(RuntimeError):
    """Raised when a model proposes a tool call outside the request contract."""


class DefaultRuntimeExecutionLoop:
    """Run bounded model rounds and dispatch only pre-authorized tool proposals."""

    def __init__(self, model: RuntimeModelPort, tools: RuntimeToolPort) -> None:
        self.model = model
        self.tools = tools

    async def run(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult:
        """Execute one turn and return only messages generated during that turn."""
        request_context = request.request_context
        request_messages = list(request.messages)
        generated_messages: list[ModelMessage] = []
        token_usage_input = 0
        token_usage_output = 0
        token_usage_total = 0
        has_token_usage = False
        replay_results: dict[tuple[str, str], str | None] = {
            (replay.tool_name, replay.arguments_json): replay.result
            for replay in request.tool_replays
        }
        executed_results: dict[tuple[str, str], str] = {}
        allowed_tools = tuple(
            tool for tool in request.tools if tool.name in request.allowed_tool_ids
        )

        for step in range(1, request.max_steps + 1):
            request_context.budget.ensure_available()
            model_response = await self.model.complete(
                request_context,
                ModelRequest(messages=tuple(request_messages), tools=allowed_tools),
            )
            if not isinstance(model_response, ModelResponse):
                raise TypeError("Runtime Model Port must return a ModelResponse.")

            usage = model_response.token_usage
            if usage is not None:
                has_token_usage = True
                token_usage_input += usage.input_tokens
                token_usage_output += usage.output_tokens
                token_usage_total += usage.total_tokens
                if not model_response.budget_usage_accounted:
                    request_context.budget.consume_tokens(usage.total_tokens)

            assistant_message = ModelMessage(
                role=ModelRole.ASSISTANT,
                content=model_response.text,
                tool_calls=model_response.tool_calls,
            )
            if model_response.tool_calls:
                self._validate_tool_proposals(
                    model_response.tool_calls,
                    request.tools,
                    request.allowed_tool_ids,
                )
            if model_response.tool_calls and step == request.max_steps:
                return RuntimeExecutionResult(
                    status=RuntimeExecutionStatus.MAX_STEPS_REACHED,
                    response_text=None,
                    messages=tuple(generated_messages),
                    token_usage=_token_usage(
                        has_token_usage,
                        token_usage_input,
                        token_usage_output,
                        token_usage_total,
                    ),
                    steps=step,
                )

            request_messages.append(assistant_message)
            generated_messages.append(assistant_message)

            if not model_response.tool_calls:
                response_text = model_response.text.strip()
                if not response_text:
                    raise ValueError("Runtime Model Port returned no final text or tool proposal.")
                await _publish_messages(request, (assistant_message,))
                return RuntimeExecutionResult(
                    status=RuntimeExecutionStatus.COMPLETED,
                    response_text=response_text,
                    messages=tuple(generated_messages),
                    token_usage=_token_usage(
                        has_token_usage,
                        token_usage_input,
                        token_usage_output,
                        token_usage_total,
                    ),
                    steps=step,
                )

            await _publish_messages(request, (assistant_message,))
            for tool_call in model_response.tool_calls:
                replay_key = runtime_tool_call_key(tool_call)
                tool_result: str | None
                if replay_key in executed_results:
                    tool_result = executed_results[replay_key]
                elif replay_key in replay_results:
                    tool_result = replay_results[replay_key]
                    if tool_result is None:
                        tool_result = _uncertain_replay_message(tool_call.name)
                else:
                    request_context.budget.ensure_available()
                    tool_result = await self.tools.execute(request_context, tool_call)
                    executed_results[replay_key] = tool_result
                if not isinstance(tool_result, str):
                    raise TypeError("Runtime Tool Port must return text results.")
                tool_message = ModelMessage(
                    role=ModelRole.TOOL,
                    content=tool_result,
                    tool_call_id=tool_call.call_id,
                )
                request_messages.append(tool_message)
                generated_messages.append(tool_message)
                await _publish_messages(request, (tool_message,))

        raise AssertionError("Runtime execution loop exited without a terminal result.")

    @staticmethod
    def _validate_tool_proposals(
        tool_calls: tuple[ModelToolCall, ...],
        definitions: tuple[ModelToolDefinition, ...],
        allowed_tool_ids: frozenset[str],
    ) -> None:
        definition_ids = {tool.name for tool in definitions}
        call_ids: set[str] = set()
        for tool_call in tool_calls:
            if not isinstance(tool_call, ModelToolCall):
                raise RuntimeToolProposalError("Model proposed a malformed tool call.")
            if tool_call.name not in definition_ids or tool_call.name not in allowed_tool_ids:
                raise RuntimeToolProposalError(
                    f"Model proposed a tool outside the request allowlist: {tool_call.name}."
                )
            if not isinstance(tool_call.call_id, str) or not tool_call.call_id.strip():
                raise RuntimeToolProposalError("Model proposed a tool call without a call ID.")
            if tool_call.call_id in call_ids:
                raise RuntimeToolProposalError("Model proposed duplicate tool call IDs.")
            if not isinstance(tool_call.arguments, Mapping):
                raise RuntimeToolProposalError("Model proposed non-object tool arguments.")
            try:
                runtime_tool_call_key(tool_call)
            except ValueError as error:
                raise RuntimeToolProposalError(
                    "Model proposed tool arguments that are not JSON serializable."
                ) from error
            call_ids.add(tool_call.call_id)


def _token_usage(
    has_usage: bool,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
) -> ModelTokenUsage | None:
    if not has_usage:
        return None
    return ModelTokenUsage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
    )


async def _publish_messages(
    request: RuntimeExecutionRequest,
    messages: tuple[ModelMessage, ...],
) -> None:
    if request.message_sink is not None:
        await request.message_sink(messages)


def _uncertain_replay_message(tool_name: str) -> str:
    return (
        f"The previous {tool_name} call was interrupted and its outcome is uncertain. "
        "It was not repeated. Ask the user to verify whether it took effect before proceeding."
    )


__all__ = ["DefaultRuntimeExecutionLoop", "RuntimeToolProposalError"]
