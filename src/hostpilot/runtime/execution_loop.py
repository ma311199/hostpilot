"""Default bounded Model/Tool execution loop owned by Runtime."""

from __future__ import annotations

from collections.abc import Mapping

from hostpilot.contracts import ModelTokenUsage, ToolRetryClass, ToolSideEffect
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
    stable_tool_invocation_identity,
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
        recovery_call_id_offsets: dict[tuple[str, str], int] = {}
        allowed_tools = tuple(
            tool for tool in request.tools if tool.name in request.allowed_tool_ids
        )
        next_call_index = request.tool_call_index_start

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

            if model_response.tool_calls:
                self._validate_tool_proposals(
                    model_response.tool_calls,
                    request.tools,
                    request.allowed_tool_ids,
                )
            remapped_calls: list[ModelToolCall] = []
            for call in model_response.tool_calls:
                key = runtime_tool_call_key(call)
                recovery_ids = request.recovery_tool_call_ids.get(key, ())
                offset = recovery_call_id_offsets.get(key, 0)
                call_id = recovery_ids[offset] if offset < len(recovery_ids) else call.call_id
                if offset < len(recovery_ids):
                    recovery_call_id_offsets[key] = offset + 1
                remapped_calls.append(ModelToolCall(call_id, call.name, call.arguments))
            tool_calls = tuple(remapped_calls)

            assistant_message = ModelMessage(
                role=ModelRole.ASSISTANT,
                content=model_response.text,
                tool_calls=tool_calls,
            )
            if tool_calls:
                self._validate_tool_proposals(
                    tool_calls,
                    request.tools,
                    request.allowed_tool_ids,
                )
            if tool_calls and step == request.max_steps:
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

            if not tool_calls:
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
            for tool_call in tool_calls:
                replay_key = runtime_tool_call_key(tool_call)
                tool_result: str | None
                dispatched = False
                if replay_key in executed_results:
                    tool_result = executed_results[replay_key]
                elif replay_key in replay_results:
                    tool_result = replay_results[replay_key]
                    if tool_result is None:
                        tool_result = _uncertain_replay_message(tool_call.name)
                else:
                    request_context.budget.ensure_available()
                    side_effect = request.tool_side_effects.get(
                        tool_call.name,
                        ToolSideEffect.POSSIBLE,
                    )
                    retry_class = request.tool_retry_classes.get(
                        tool_call.name,
                        (
                            ToolRetryClass.PURE_READ
                            if side_effect is ToolSideEffect.NONE
                            else ToolRetryClass.UNKNOWN
                        ),
                    )
                    idempotency_window = request.idempotency_windows.get(tool_call.name)
                    if (
                        retry_class
                        in {
                            ToolRetryClass.IDEMPOTENT_WRITE,
                            ToolRetryClass.NON_IDEMPOTENT_WRITE,
                        }
                        and idempotency_window is not None
                        and request.tool_invocations is None
                    ):
                        raise RuntimeError(
                            "Idempotent tool execution requires a durable invocation journal."
                        )
                    invocation_id: str | None = None
                    idempotency_key: str | None = None
                    if request.tool_invocations is not None:
                        session_id = request_context.snapshot.session_id
                        if session_id is None:
                            raise RuntimeError("Tool invocation journaling requires a Session ID.")
                        logical_plan_id = request.plan_run_id or request_context.snapshot.request_id
                        invocation_key, fingerprint = stable_tool_invocation_identity(
                            plan_run_id=logical_plan_id,
                            task_id=request.task_id,
                            call_index=next_call_index,
                            tool_id=tool_call.name,
                            arguments=tool_call.arguments,
                            business_fields=request.idempotency_business_fields.get(
                                tool_call.name, ()
                            ),
                        )
                        invocation_id = f"invocation:{invocation_key}"
                        idempotency_key = (
                            invocation_key
                            if idempotency_window is not None
                            and retry_class
                            in {
                                ToolRetryClass.IDEMPOTENT_WRITE,
                                ToolRetryClass.NON_IDEMPOTENT_WRITE,
                            }
                            else None
                        )
                        _, arguments_json = replay_key
                        await request.tool_invocations.prepare_invocation(
                            invocation_id=invocation_id,
                            session_id=session_id,
                            turn_id=request_context.snapshot.request_id,
                            plan_run_id=request.plan_run_id,
                            task_run_id=request.task_run_id,
                            task_id=request.task_id,
                            call_index=next_call_index,
                            tool_id=tool_call.name,
                            retry_class=retry_class,
                            idempotency_key=idempotency_key,
                            request_fingerprint=fingerprint,
                            arguments_json=arguments_json,
                            idempotency_window_seconds=idempotency_window,
                        )
                    if (
                        side_effect is ToolSideEffect.POSSIBLE
                        and tool_call.call_id not in request.claimed_recovery_outcome_ids
                    ):
                        if request.tool_outcomes is None:
                            raise RuntimeError(
                                "Side-effecting tool execution requires outcome persistence."
                            )
                        session_id = request_context.snapshot.session_id
                        if session_id is None:
                            raise RuntimeError(
                                "Side-effecting tool execution requires a Session ID."
                            )
                        await request.tool_outcomes.begin_invocation(
                            session_id=session_id,
                            turn_id=request_context.snapshot.request_id,
                            task_run_id=None,
                            tool_call_id=tool_call.call_id,
                            tool_name=tool_call.name,
                        )
                    if idempotency_key is not None:
                        execute_idempotent = getattr(self.tools, "execute_idempotent", None)
                        if not callable(execute_idempotent):
                            raise RuntimeError(
                                "Tool declares idempotency support but its Runtime adapter "
                                "cannot execute with an idempotency key."
                            )
                        tool_result = await execute_idempotent(
                            request_context,
                            tool_call,
                            idempotency_key=idempotency_key,
                        )
                    else:
                        tool_result = await self.tools.execute(request_context, tool_call)
                    executed_results[replay_key] = tool_result
                    dispatched = True
                    next_call_index += 1
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
                if dispatched and invocation_id is not None:
                    assert request.tool_invocations is not None
                    await request.tool_invocations.complete_invocation(
                        invocation_id,
                        plan_run_id=request.plan_run_id,
                    )
                if (
                    dispatched
                    and request.tool_side_effects.get(
                        tool_call.name,
                        ToolSideEffect.POSSIBLE,
                    )
                    is ToolSideEffect.POSSIBLE
                ):
                    session_id = request_context.snapshot.session_id
                    if session_id is None or request.tool_outcomes is None:
                        raise RuntimeError("Side-effect outcome persistence disappeared mid-turn.")
                    await request.tool_outcomes.result_checkpointed(session_id, tool_call.call_id)

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
