"""Chat Runtime adapter driven by the Runtime-owned Model/Tool execution loop."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from hostpilot.contracts import (
    ConversationMessage,
    MessageRole,
    Route,
    ToolRetryClass,
    ToolSideEffect,
)
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
    ToolInvocationRecoveryAction,
    decide_tool_invocation_recovery,
    runtime_tool_call_key,
    stable_tool_invocation_identity,
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
        tool_retry_classes: Mapping[str, ToolRetryClass] | None = None,
        idempotency_windows: Mapping[str, int] | None = None,
        idempotency_business_fields: Mapping[str, tuple[str, ...]] | None = None,
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
        self.tool_retry_classes = dict(tool_retry_classes or {})
        self.idempotency_windows = dict(idempotency_windows or {})
        self.idempotency_business_fields = dict(idempotency_business_fields or {})
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
            (
                ModelMessage(
                    role=ModelRole.USER,
                    content=message,
                    turn_id=request_context.snapshot.request_id,
                ),
            ),
        )

        async def persist_message_delta(messages: tuple[ModelMessage, ...]) -> None:
            await self.session_state.append_messages(
                session_id,
                tuple(
                    message
                    if message.turn_id is not None
                    else _associate_turn(message, request_context)
                    for message in messages
                ),
            )

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
                tool_invocations=request_context.task_lifecycle,
                tool_retry_classes=self.tool_retry_classes,
                idempotency_windows=self.idempotency_windows,
                idempotency_business_fields=self.idempotency_business_fields,
                require_tool_invocation_journal=request_context.task_lifecycle is not None,
                message_sink=persist_message_delta,
            )
        )
        if result.status is RuntimeExecutionStatus.MAX_STEPS_REACHED:
            raise RuntimeError("Runtime chat reached the maximum execution steps.")
        if result.response_text is None:
            raise RuntimeError("Runtime chat completed without a response text.")

        return _response_from_execution(result)

    async def recoverability_reason(self, session_id: str, turn_id: str) -> str | None:
        """Return a safe reason when the latest checkpoint cannot identify this Turn."""
        history = await self.session_state.load_messages(session_id)
        associated = tuple(message for message in history if message.turn_id is not None)
        if not associated or not any(message.turn_id == turn_id for message in associated):
            return "checkpoint_turn_missing"
        if associated[-1].turn_id != turn_id:
            return "checkpoint_superseded"
        last_turn_message_index = max(
            index for index, message in enumerate(history) if message.turn_id == turn_id
        )
        if any(
            message.turn_id is None
            for message in history[last_turn_message_index + 1 :]
            if message.role is not ModelRole.SYSTEM
        ):
            return "checkpoint_superseded"
        if not any(
            message.role is ModelRole.USER for message in associated if message.turn_id == turn_id
        ):
            return "checkpoint_user_message_missing"
        return None

    async def recoverable_user_message(self, session_id: str, turn_id: str) -> str:
        """Load the original user message after validating the checkpoint Turn boundary."""
        reason = await self.recoverability_reason(session_id, turn_id)
        if reason is not None:
            raise ValueError(f"Runtime Chat checkpoint cannot resume this Turn: {reason}.")
        history = await self.session_state.load_messages(session_id)
        for message in history:
            if message.turn_id == turn_id and message.role is ModelRole.USER:
                return message.content
        raise ValueError("Runtime Chat checkpoint does not contain the original user message.")

    async def recovery_tool_reason(
        self,
        session_id: str,
        turn_id: str,
        *,
        task_lifecycle: Any | None,
    ) -> str | None:
        """Preflight invocation recovery without claiming gates or dispatching work."""
        reason = await self.recoverability_reason(session_id, turn_id)
        if reason is not None:
            return reason
        history = await self.session_state.load_messages(session_id)
        turn_messages = tuple(message for message in history if message.turn_id == turn_id)
        try:
            await self._resume_tool_state(
                session_id,
                turn_id,
                turn_messages,
                None,
                task_lifecycle=task_lifecycle,
                claim_outcomes=False,
            )
        except ValueError:
            return "tool_invocation_reconciliation_required"
        return None

    async def resume_turn(
        self,
        turn_id: str,
        request_context: RequestContext,
    ) -> ChatAgentResponse:
        """Resume from the checkpoint messages associated with the original logical Turn."""
        session_id = request_context.snapshot.session_id
        if session_id is None:
            raise ValueError("Chat recovery context must contain a session_id.")
        original_user_message = await self.recoverable_user_message(session_id, turn_id)
        history = await self.session_state.load_messages(session_id)
        turn_messages = tuple(message for message in history if message.turn_id == turn_id)
        if (
            turn_messages
            and turn_messages[-1].role is ModelRole.ASSISTANT
            and not turn_messages[-1].tool_calls
        ):
            return ChatAgentResponse(content=turn_messages[-1].content, budget_usage_accounted=True)
        replays, _, _ = _interrupted_tool_replays(turn_messages, self.tool_side_effects)
        (
            recovery_messages,
            call_ids,
            claimed_outcome_ids,
            call_index_start,
        ) = await self._resume_tool_state(session_id, turn_id, turn_messages, request_context)
        system_message = self.prompt_builder.build(
            request_context.snapshot,
            (),
            original_user_message,
            self.system_prompt,
        )[0]
        prompt_messages = (system_message, *turn_messages, *recovery_messages)

        async def persist_message_delta(messages: tuple[ModelMessage, ...]) -> None:
            await self.session_state.append_messages(
                session_id,
                tuple(_associate_turn(message, request_context) for message in messages),
            )

        try:
            result = await self.execution_loop.run(
                RuntimeExecutionRequest(
                    request_context=request_context,
                    messages=prompt_messages,
                    tools=self.tools,
                    allowed_tool_ids=self.allowed_tool_ids,
                    max_steps=self.max_steps,
                    tool_replays=replays,
                    tool_side_effects=self.tool_side_effects,
                    tool_outcomes=self.tool_outcomes,
                    tool_invocations=request_context.task_lifecycle,
                    tool_retry_classes=self.tool_retry_classes,
                    idempotency_windows=self.idempotency_windows,
                    idempotency_business_fields=self.idempotency_business_fields,
                    tool_call_index_start=call_index_start,
                    recovery_tool_call_ids=call_ids,
                    claimed_recovery_outcome_ids=frozenset(claimed_outcome_ids),
                    require_tool_invocation_journal=request_context.task_lifecycle is not None,
                    message_sink=persist_message_delta,
                )
            )
        finally:
            if self.tool_outcomes is not None:
                for tool_call_id in claimed_outcome_ids:
                    await self.tool_outcomes.release_resolution_claim(session_id, tool_call_id)
        if result.status is RuntimeExecutionStatus.MAX_STEPS_REACHED:
            raise RuntimeError("Runtime chat recovery reached the maximum execution steps.")
        if result.response_text is None:
            raise RuntimeError("Runtime chat recovery completed without a response text.")
        return _response_from_execution(result)

    async def _resume_tool_state(
        self,
        session_id: str,
        turn_id: str,
        turn_messages: tuple[ModelMessage, ...],
        request_context: RequestContext | None,
        *,
        task_lifecycle: Any | None = None,
        claim_outcomes: bool = True,
    ) -> tuple[
        tuple[ModelMessage, ...],
        dict[tuple[str, str], tuple[str, ...]],
        set[str],
        int,
    ]:
        """Validate uncertain calls and claim safe idempotent outcome gates before model work."""
        if request_context is not None:
            task_lifecycle = request_context.task_lifecycle
        list_invocations = getattr(task_lifecycle, "list_runtime_tool_invocations", None)
        records = await list_invocations(session_id, turn_id) if callable(list_invocations) else ()
        records_by_index = {record.call_index: record for record in records}
        pending_outcomes: tuple[Any, ...] = ()
        if self.tool_outcomes is not None:
            pending_reader = getattr(self.tool_outcomes, "pending", None)
            if callable(pending_reader):
                pending_outcomes = await pending_reader(session_id)
        pending_by_call = {
            item.tool_call_id: item for item in pending_outcomes if item.turn_id == turn_id
        }
        if any(item.turn_id != turn_id for item in pending_outcomes):
            raise ValueError("Another Chat Turn has an unresolved tool outcome.")

        tool_results = {
            message.tool_call_id
            for message in turn_messages
            if message.role is ModelRole.TOOL and message.tool_call_id is not None
        }
        proposed_calls = tuple(
            call
            for message in turn_messages
            if message.role is ModelRole.ASSISTANT
            for call in message.tool_calls
        )
        recovery_messages: list[ModelMessage] = []
        recovery_call_ids: dict[tuple[str, str], list[str]] = {}
        claimed_outcome_ids: set[str] = set()
        outcomes_to_claim: list[str] = []
        unresolved_indices: list[int] = []
        for call_index, tool_call in enumerate(proposed_calls, start=1):
            if tool_call.call_id in tool_results:
                continue
            key = runtime_tool_call_key(tool_call)
            recovery_call_ids.setdefault(key, []).append(tool_call.call_id)
            record = records_by_index.get(call_index)
            pending = pending_by_call.get(tool_call.call_id)
            if record is not None:
                current_key, current_fingerprint = stable_tool_invocation_identity(
                    plan_run_id=turn_id,
                    task_id="runtime_chat",
                    call_index=call_index,
                    tool_id=tool_call.name,
                    arguments=tool_call.arguments,
                    business_fields=self.idempotency_business_fields.get(tool_call.name, ()),
                )
                expected_idempotency_key = (
                    current_key
                    if call_index in records_by_index and record.idempotency_key is not None
                    else None
                )
                if (
                    record.session_id != session_id
                    or record.turn_id != turn_id
                    or record.plan_run_id is not None
                    or record.task_id != "runtime_chat"
                    or record.tool_id != tool_call.name
                    or record.retry_class
                    != self.tool_retry_classes.get(
                        tool_call.name,
                        (
                            ToolRetryClass.PURE_READ
                            if self.tool_side_effects.get(tool_call.name, ToolSideEffect.POSSIBLE)
                            is ToolSideEffect.NONE
                            else ToolRetryClass.UNKNOWN
                        ),
                    )
                    or record.request_fingerprint != current_fingerprint
                    or (
                        record.idempotency_key is not None
                        and record.idempotency_key != expected_idempotency_key
                    )
                ):
                    raise ValueError("Runtime Chat recovery tool invocation identity changed.")
                if record.state == "completed":
                    raise ValueError("Completed tool invocation is missing its checkpoint result.")
                if record.state != "prepared":
                    raise ValueError("Runtime Chat tool invocation requires reconciliation.")
                unresolved_indices.append(call_index)
                if pending is not None:
                    try:
                        retry_class = ToolRetryClass(record.retry_class)
                    except ValueError:
                        retry_class = ToolRetryClass.UNKNOWN
                    decision = decide_tool_invocation_recovery(
                        retry_class=retry_class,
                        prepared_record_exists=True,
                        request_fingerprint=current_fingerprint,
                        prior_request_fingerprint=record.request_fingerprint,
                        idempotency_key=record.idempotency_key,
                        idempotency_expires_at=record.idempotency_expires_at,
                        idempotency_supported=callable(
                            getattr(
                                getattr(self.execution_loop, "tools", None),
                                "execute_idempotent",
                                None,
                            )
                        ),
                        now=datetime.now(UTC),
                    )
                    if decision.action not in {
                        ToolInvocationRecoveryAction.RETRY_READ,
                        ToolInvocationRecoveryAction.REPLAY_WITH_KEY,
                    }:
                        raise ValueError(
                            "Runtime Chat tool invocation requires reconciliation: "
                            f"{decision.reason_code}."
                        )
                    if decision.action is ToolInvocationRecoveryAction.REPLAY_WITH_KEY and (
                        self.idempotency_windows.get(tool_call.name) is None
                        or record.idempotency_key != current_key
                    ):
                        raise ValueError("Runtime Chat idempotency configuration changed.")
                    if pending is not None:
                        outcomes_to_claim.append(tool_call.call_id)
            elif pending is not None:
                raise ValueError(
                    "Runtime Chat pending tool outcome has no matching invocation record."
                )
            recovery_messages.append(
                ModelMessage(
                    role=ModelRole.TOOL,
                    content=(
                        "The previous tool call has no durable result. "
                        "Resume only if the current policy permits it."
                    ),
                    tool_call_id=tool_call.call_id,
                    turn_id=turn_id,
                )
            )
        proposal_ids = {tool_call.call_id for tool_call in proposed_calls}
        if set(pending_by_call).difference(proposal_ids):
            raise ValueError("Runtime Chat pending tool outcome has no checkpointed tool proposal.")
        next_index = (
            min(unresolved_indices) if unresolved_indices else max(records_by_index, default=0) + 1
        )
        if outcomes_to_claim and claim_outcomes:
            if self.tool_outcomes is None:
                raise ValueError("Runtime Chat pending tool outcome lifecycle is unavailable.")
            try:
                for tool_call_id in outcomes_to_claim:
                    if not await self.tool_outcomes.claim_resolution(session_id, tool_call_id):
                        raise ValueError("Runtime Chat pending tool outcome is already claimed.")
                    claimed_outcome_ids.add(tool_call_id)
            except BaseException:
                for tool_call_id in claimed_outcome_ids:
                    await self.tool_outcomes.release_resolution_claim(session_id, tool_call_id)
                raise
        return (
            tuple(recovery_messages),
            {key: tuple(call_ids) for key, call_ids in recovery_call_ids.items()},
            claimed_outcome_ids,
            next_index,
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

    async def record_associated_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        *,
        turn_id: str,
    ) -> None:
        """Persist a non-model turn with its durable logical Turn association."""
        await self.session_state.append_messages(
            session_id,
            (
                ModelMessage(role=ModelRole.USER, content=user_message, turn_id=turn_id),
                ModelMessage(role=ModelRole.ASSISTANT, content=assistant_message, turn_id=turn_id),
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


def _associate_turn(message: ModelMessage, request_context: RequestContext) -> ModelMessage:
    """Attach the logical Turn ID to a normalized checkpoint message."""
    return ModelMessage(
        role=message.role,
        content=message.content,
        tool_call_id=message.tool_call_id,
        tool_calls=message.tool_calls,
        turn_id=request_context.snapshot.request_id,
    )


def _response_from_execution(result: Any) -> ChatAgentResponse:
    """Map a completed Runtime execution to the existing chat boundary."""
    return ChatAgentResponse(
        content=result.response_text,
        token_usage=result.token_usage,
        budget_usage_accounted=True,
        route=(
            Route.CLARIFY if result.status is RuntimeExecutionStatus.CLARIFICATION else Route.DIRECT
        ),
        clarification_prompt=(
            result.response_text if result.status is RuntimeExecutionStatus.CLARIFICATION else None
        ),
    )


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
