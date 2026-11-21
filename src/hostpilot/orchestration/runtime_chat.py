"""Chat Runtime adapter driven by the Runtime-owned Model/Tool execution loop."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from hostpilot.contracts import ConversationMessage, MessageRole, Route, ToolSideEffect
from hostpilot.models import ModelMessage, ModelRole, ModelToolDefinition
from hostpilot.orchestration.runtime import (
    ChatAgentResponse,
    ChatAgentRuntimeUnavailable,
)
from hostpilot.runtime import (
    RequestContext,
    RuntimeChatPromptBuilder,
    RuntimeExecutionLoop,
    RuntimeExecutionRequest,
    RuntimeExecutionStatus,
    RuntimeSessionStatePort,
    RuntimeToolOutcomePort,
    RuntimeToolReplay,
    runtime_tool_call_key,
)

_UNCERTAIN_TOOL_OUTCOME_RESPONSE = (
    "上次工具调用结果不确定：它可能已经执行，但执行结果未能保存，当前无法确认是否生效。"
    "为避免重复操作，本次恢复不会调用模型或工具。请先核实目标状态，确认后再继续。"
)


class RuntimeChatAgentRuntime:
    """Run chat turns through Runtime while delegating selected Skills to LangGraph."""

    def __init__(
        self,
        execution_loop: RuntimeExecutionLoop,
        session_state: RuntimeSessionStatePort,
        *,
        system_prompt: str = "You are a helpful assistant.",
        prompt_builder: RuntimeChatPromptBuilder | None = None,
        tools: Sequence[ModelToolDefinition] = (),
        allowed_tool_ids: frozenset[str] | None = None,
        max_steps: int = 8,
        skill_runtime: Any | None = None,
        tool_side_effects: Mapping[str, ToolSideEffect] | None = None,
    ) -> None:
        self.execution_loop = execution_loop
        self.session_state = session_state
        self.system_prompt = system_prompt
        self.prompt_builder = prompt_builder or RuntimeChatPromptBuilder()
        self.tools = tuple(tools)
        self.allowed_tool_ids = (
            frozenset(tool.name for tool in self.tools)
            if allowed_tool_ids is None
            else frozenset(allowed_tool_ids)
        )
        self.max_steps = max_steps
        self.skill_runtime = skill_runtime
        self.tool_side_effects = dict(tool_side_effects or {})
        self.tool_outcomes: RuntimeToolOutcomePort | None = None

    def configure_tool_outcomes(self, lifecycle: RuntimeToolOutcomePort) -> None:
        """Attach the Host-owned outcome lifecycle before Runtime readiness."""
        if self.tool_outcomes is lifecycle:
            return
        if self.tool_outcomes is not None:
            raise RuntimeError("Tool outcome lifecycle is already configured.")
        self.tool_outcomes = lifecycle

    async def read_messages(self, session_id: str) -> tuple[ConversationMessage, ...]:
        """Expose stored model history through the existing Chat Runtime contract."""
        messages = await self.session_state.load_messages(session_id)
        return tuple(
            ConversationMessage(role=MessageRole(message.role.value), content=message.content)
            for message in messages
        )

    async def respond(
        self,
        message: str,
        request_context: RequestContext,
    ) -> ChatAgentResponse:
        """Execute one Runtime turn and persist only a successful terminal response."""
        session_id = request_context.snapshot.session_id
        if session_id is None:
            raise ValueError("Chat context must contain a session_id.")
        history = await self.session_state.load_messages(session_id)
        tool_replays, recovery_messages, has_uncertain_side_effect = _interrupted_tool_replays(
            history,
            self.tool_side_effects,
        )
        if has_uncertain_side_effect:
            await self.session_state.append_messages(
                session_id,
                (ModelMessage(role=ModelRole.USER, content=message),),
            )
            await self.session_state.append_messages(
                session_id,
                (ModelMessage(role=ModelRole.ASSISTANT, content=_UNCERTAIN_TOOL_OUTCOME_RESPONSE),),
            )
            return ChatAgentResponse(
                content=_UNCERTAIN_TOOL_OUTCOME_RESPONSE,
                budget_usage_accounted=True,
                route=Route.CLARIFY,
                clarification_prompt=_UNCERTAIN_TOOL_OUTCOME_RESPONSE,
            )
        prompt_messages = self.prompt_builder.build(
            request_context.snapshot,
            (*history, *recovery_messages),
            message,
            self.system_prompt,
        )

        await self.session_state.append_messages(
            session_id,
            (ModelMessage(role=ModelRole.USER, content=message),),
        )

        async def persist_message_delta(messages: tuple[ModelMessage, ...]) -> None:
            await self.session_state.append_messages(session_id, messages)

        result = await self.execution_loop.run(
            RuntimeExecutionRequest(
                request_context=request_context,
                messages=prompt_messages,
                tools=self.tools,
                allowed_tool_ids=self.allowed_tool_ids,
                max_steps=self.max_steps,
                tool_replays=tool_replays,
                tool_side_effects=self.tool_side_effects,
                tool_outcomes=self.tool_outcomes,
                message_sink=persist_message_delta,
            )
        )
        if result.status is RuntimeExecutionStatus.MAX_STEPS_REACHED:
            raise RuntimeError("Runtime chat reached the maximum execution steps.")
        if result.response_text is None:
            raise RuntimeError("Runtime chat completed without a response text.")

        return ChatAgentResponse(
            content=result.response_text,
            token_usage=result.token_usage,
            budget_usage_accounted=True,
            route=(
                Route.CLARIFY
                if result.status is RuntimeExecutionStatus.CLARIFICATION
                else Route.DIRECT
            ),
            clarification_prompt=(
                result.response_text
                if result.status is RuntimeExecutionStatus.CLARIFICATION
                else None
            ),
        )

    async def respond_with_skill(
        self,
        message: str,
        request_context: RequestContext,
        *,
        skill_id: str,
    ) -> ChatAgentResponse:
        """Keep DeepAgents Skills on their existing supported executor."""
        respond = getattr(self.skill_runtime, "respond_with_skill", None)
        if not callable(respond):
            raise ChatAgentRuntimeUnavailable(
                "The configured Runtime Chat adapter has no DeepAgents Skill executor."
            )
        response = await respond(message, request_context, skill_id=skill_id)
        if not isinstance(response, ChatAgentResponse):
            raise TypeError("DeepAgents Skill runtime must return a ChatAgentResponse.")
        return response

    async def record_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        """Persist non-model turns through the same normalized Session State Port."""
        await self.session_state.append_messages(
            session_id,
            (
                ModelMessage(role=ModelRole.USER, content=user_message),
                ModelMessage(role=ModelRole.ASSISTANT, content=assistant_message),
            ),
        )

    async def resolve_tool_outcome(
        self,
        session_id: str,
        tool_call_id: str,
        *,
        result_text: str,
    ) -> None:
        """Append a verified result to its original call and then clear pending state."""
        if self.tool_outcomes is None:
            raise RuntimeError("Tool outcome persistence is not configured.")
        if not await self.tool_outcomes.claim_resolution(session_id, tool_call_id):
            raise ValueError("Tool outcome is missing or is being resolved by another Host.")
        try:
            history = await self.session_state.load_messages(session_id)
            existing = tuple(
                message
                for message in history
                if message.role is ModelRole.TOOL and message.tool_call_id == tool_call_id
            )
            if existing:
                if any(message.content != result_text for message in existing):
                    raise ValueError(
                        "A different result is already checkpointed for this tool call."
                    )
            else:
                await self.session_state.append_messages(
                    session_id,
                    (
                        ModelMessage(
                            role=ModelRole.TOOL,
                            content=result_text,
                            tool_call_id=tool_call_id,
                        ),
                    ),
                )
            await self.tool_outcomes.result_checkpointed(session_id, tool_call_id)
        except BaseException:
            await self.tool_outcomes.release_resolution_claim(session_id, tool_call_id)
            raise

    async def aclose(self) -> None:
        """Release the delegated Skill runtime when it owns external model clients."""
        close = getattr(self.skill_runtime, "aclose", None)
        if callable(close):
            await close()


__all__ = ["RuntimeChatAgentRuntime"]


def _interrupted_tool_replays(
    history: tuple[ModelMessage, ...],
    side_effects: Mapping[str, ToolSideEffect],
) -> tuple[tuple[RuntimeToolReplay, ...], tuple[ModelMessage, ...], bool]:
    """Find tools proposed since the latest completed assistant turn for replay fencing."""
    call_keys: dict[str, tuple[str, str]] = {}
    results: dict[tuple[str, str], str | None] = {}
    unresolved_calls: dict[str, tuple[str, str]] = {}
    has_uncertain_side_effect = False

    for message in history:
        if message.role is ModelRole.ASSISTANT:
            if not message.tool_calls:
                call_keys.clear()
                results.clear()
                unresolved_calls.clear()
                has_uncertain_side_effect = False
                continue
            for tool_call in message.tool_calls:
                key = runtime_tool_call_key(tool_call)
                call_keys[tool_call.call_id] = key
                unresolved_calls[tool_call.call_id] = key
                results.setdefault(key, None)
        elif message.role is ModelRole.TOOL and message.tool_call_id in call_keys:
            key = call_keys[message.tool_call_id]
            results[key] = message.content
            unresolved_calls.pop(message.tool_call_id, None)

    has_uncertain_side_effect = any(
        side_effects.get(name, ToolSideEffect.POSSIBLE) is not ToolSideEffect.NONE
        for name, _ in unresolved_calls.values()
    )

    replays = tuple(
        RuntimeToolReplay(tool_name=name, arguments_json=arguments_json, result=result)
        for (name, arguments_json), result in results.items()
        if result is not None
    )
    recovery_messages = tuple(
        ModelMessage(
            role=ModelRole.TOOL,
            content=(
                f"The previous read-only {name} call did not save a result. "
                "It is safe to repeat this read operation if needed."
                if side_effects.get(name, ToolSideEffect.POSSIBLE) is ToolSideEffect.NONE
                else (
                    f"The previous {name} call was interrupted and its outcome is uncertain. "
                    "It was not repeated. Ask the user to verify whether it took effect "
                    "before proceeding."
                )
            ),
            tool_call_id=call_id,
        )
        for call_id, (name, arguments_json) in unresolved_calls.items()
        if results[(name, arguments_json)] is None
    )
    return replays, recovery_messages, has_uncertain_side_effect
