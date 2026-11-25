from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    MessageRole,
    ModelTokenUsage,
    Route,
    ToolRetryClass,
    ToolSideEffect,
)
from hostpilot.models import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelToolDefinition,
)
from hostpilot.orchestration.runtime import ChatAgentResponse
from hostpilot.orchestration.runtime_chat import RuntimeChatAgentRuntime
from hostpilot.runtime import (
    DefaultRuntimeExecutionLoop,
    RequestContext,
    RuntimeExecutionResult,
    RuntimeExecutionStatus,
    RuntimeToolReplay,
    stable_tool_invocation_identity,
)
from hostpilot.task_results import ToolInvocationRecord


def request_context() -> RequestContext:
    return RequestContext(
        snapshot=ContextSnapshot(
            request_id="request-1",
            session_id="43df14e0-b250-4981-a414-3db49e1ecc3b",
        ),
        budget=RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=500,
                    tool_calls=4,
                    retrieval_calls=1,
                    timeout_seconds=20,
                )
            )
        ),
    )


class FakeState:
    def __init__(self) -> None:
        self.messages: tuple[ModelMessage, ...] = ()
        self.appended: list[tuple[ModelMessage, ...]] = []

    async def load_messages(self, session_id):
        assert session_id == "43df14e0-b250-4981-a414-3db49e1ecc3b"
        return self.messages

    async def append_messages(self, session_id, messages):
        assert session_id == "43df14e0-b250-4981-a414-3db49e1ecc3b"
        self.appended.append(messages)
        self.messages += messages


class FakeExecutionLoop:
    def __init__(self, result=None, error=None) -> None:
        self.result = result
        self.error = error
        self.request = None
        self.tools = None

    async def run(self, request):
        self.request = request
        if self.error is not None:
            raise self.error
        if request.message_sink is not None and self.result.messages:
            await request.message_sink(self.result.messages)
        return self.result


class FakeOutcomeLifecycle:
    async def begin_invocation(self, **kwargs):
        del kwargs

    async def result_checkpointed(self, session_id, tool_call_id):
        del session_id, tool_call_id

    async def pending(self, session_id):
        del session_id
        return ()

    async def claim_resolution(self, session_id, tool_call_id):
        del session_id, tool_call_id
        return True

    async def release_resolution_claim(self, session_id, tool_call_id):
        del session_id, tool_call_id


class FakeSkillRuntime:
    async def read_messages(self, session_id):
        return ()

    async def respond(self, message, request_context):
        return ChatAgentResponse("skill response")

    async def respond_with_skill(self, message, request_context, *, skill_id):
        return ChatAgentResponse(f"skill:{skill_id}")

    async def record_turn(self, session_id, user_message, assistant_message):
        return None

    async def aclose(self):
        return None


def test_runtime_chat_executes_turn_and_persists_the_complete_message_delta() -> None:
    state = FakeState()
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(
            RuntimeExecutionStatus.COMPLETED,
            "你好。",
            (ModelMessage(role=ModelRole.ASSISTANT, content="你好。"),),
            token_usage=ModelTokenUsage(input_tokens=4, output_tokens=2, total_tokens=6),
            steps=1,
        )
    )
    runtime = RuntimeChatAgentRuntime(loop, state, system_prompt="你是 HostPilot 助手。")
    context = request_context()

    response = asyncio.run(runtime.respond("你好", context))

    assert response.content == "你好。"
    assert response.token_usage == ModelTokenUsage(input_tokens=4, output_tokens=2, total_tokens=6)
    assert response.budget_usage_accounted is True
    assert loop.request.request_context is context
    assert loop.request.messages == (
        ModelMessage(role=ModelRole.SYSTEM, content="你是 HostPilot 助手。"),
        ModelMessage(role=ModelRole.USER, content="你好"),
    )
    assert state.appended == [
        (
            ModelMessage(
                role=ModelRole.USER,
                content="你好",
                turn_id=context.snapshot.request_id,
            ),
        ),
        (
            ModelMessage(
                role=ModelRole.ASSISTANT,
                content="你好。",
                turn_id=context.snapshot.request_id,
            ),
        ),
    ]


def test_runtime_chat_messages_carry_turn_id_through_checkpoint() -> None:
    state = FakeState()
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(
            RuntimeExecutionStatus.COMPLETED,
            "完成",
            (ModelMessage(role=ModelRole.ASSISTANT, content="完成"),),
            steps=1,
        )
    )
    context = request_context()
    runtime = RuntimeChatAgentRuntime(loop, state)

    asyncio.run(runtime.respond("执行", context))

    assert [message.turn_id for batch in state.appended for message in batch] == [
        context.snapshot.request_id,
        context.snapshot.request_id,
    ]


def test_runtime_chat_records_non_model_turn_with_association() -> None:
    state = FakeState()
    runtime = RuntimeChatAgentRuntime(FakeExecutionLoop(), state)

    asyncio.run(
        runtime.record_associated_turn(
            "43df14e0-b250-4981-a414-3db49e1ecc3b",
            "clarify this",
            "Which project?",
            turn_id="turn-clarification",
        )
    )

    assert state.appended == [
        (
            ModelMessage(
                role=ModelRole.USER,
                content="clarify this",
                turn_id="turn-clarification",
            ),
            ModelMessage(
                role=ModelRole.ASSISTANT,
                content="Which project?",
                turn_id="turn-clarification",
            ),
        )
    ]


def test_resume_turn_uses_original_user_message_without_duplicate() -> None:
    state = FakeState()
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="previous", turn_id="turn-previous"),
        ModelMessage(role=ModelRole.ASSISTANT, content="old", turn_id="turn-previous"),
        ModelMessage(role=ModelRole.USER, content="continue this task", turn_id="request-1"),
    )
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(
            RuntimeExecutionStatus.COMPLETED,
            "resumed",
            (ModelMessage(role=ModelRole.ASSISTANT, content="resumed"),),
            steps=1,
        )
    )
    runtime = RuntimeChatAgentRuntime(loop, state)
    context = request_context()

    response = asyncio.run(runtime.resume_turn("request-1", context))

    assert response.content == "resumed"
    assert [
        message.content for message in loop.request.messages if message.role is ModelRole.USER
    ] == ["continue this task"]
    assert state.appended == [
        (
            ModelMessage(
                role=ModelRole.ASSISTANT,
                content="resumed",
                turn_id="request-1",
            ),
        )
    ]


def test_resume_reuses_checkpointed_tool_result() -> None:
    tool_call = ModelToolCall("call-read", "search_files", {"pattern": "*.py"})
    state = FakeState()
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="find files", turn_id="request-1"),
        ModelMessage(
            role=ModelRole.ASSISTANT,
            content="",
            turn_id="request-1",
            tool_calls=(tool_call,),
        ),
        ModelMessage(
            role=ModelRole.TOOL,
            content="found.py",
            tool_call_id="call-read",
            turn_id="request-1",
        ),
    )
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(RuntimeExecutionStatus.COMPLETED, "done", (), steps=1)
    )
    runtime = RuntimeChatAgentRuntime(loop, state)

    asyncio.run(runtime.resume_turn("request-1", request_context()))

    assert loop.request.tool_replays == (
        RuntimeToolReplay("search_files", '{"pattern":"*.py"}', "found.py"),
    )


def test_resume_turn_preserves_turn_id_for_idempotency_identity() -> None:
    state = FakeState()
    state.messages = (ModelMessage(role=ModelRole.USER, content="continue", turn_id="request-1"),)
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(RuntimeExecutionStatus.COMPLETED, "done", (), steps=1)
    )
    runtime = RuntimeChatAgentRuntime(loop, state)

    asyncio.run(runtime.resume_turn("request-1", request_context()))

    assert loop.request.request_context.snapshot.request_id == "request-1"


def test_resume_rejects_turn_when_checkpoint_has_newer_turn() -> None:
    state = FakeState()
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="old request", turn_id="request-1"),
        ModelMessage(role=ModelRole.ASSISTANT, content="later request", turn_id="request-2"),
    )
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(RuntimeExecutionStatus.COMPLETED, "unused", (), steps=1)
    )
    runtime = RuntimeChatAgentRuntime(loop, state)

    with pytest.raises(ValueError, match="newer|stale|checkpoint"):
        asyncio.run(runtime.resume_turn("request-1", request_context()))

    assert loop.request is None


def test_recoverability_rejects_newer_unassociated_checkpoint_messages() -> None:
    state = FakeState()
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="old", turn_id="request-1"),
        ModelMessage(role=ModelRole.ASSISTANT, content="later legacy turn"),
    )
    runtime = RuntimeChatAgentRuntime(FakeExecutionLoop(), state)

    reason = asyncio.run(
        runtime.recoverability_reason("43df14e0-b250-4981-a414-3db49e1ecc3b", "request-1")
    )

    assert reason == "checkpoint_superseded"


def test_resume_retries_a_prepared_pure_read_using_original_turn_identity() -> None:
    call = ModelToolCall("provider-call-1", "search_files", {"pattern": "*.py"})
    key, fingerprint = stable_tool_invocation_identity(
        plan_run_id="request-1",
        task_id="runtime_chat",
        call_index=1,
        tool_id=call.name,
        arguments=call.arguments,
    )
    state = FakeState()
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="find files", turn_id="request-1"),
        ModelMessage(role=ModelRole.ASSISTANT, content="", turn_id="request-1", tool_calls=(call,)),
    )
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(RuntimeExecutionStatus.COMPLETED, "done", (), steps=1)
    )

    class Journal:
        async def list_runtime_tool_invocations(self, session_id, turn_id):
            assert (session_id, turn_id) == ("43df14e0-b250-4981-a414-3db49e1ecc3b", "request-1")
            return (
                ToolInvocationRecord(
                    invocation_id=f"invocation:{key}",
                    plan_run_id=None,
                    session_id=session_id,
                    turn_id=turn_id,
                    runtime_host_id="host-new",
                    task_run_id=None,
                    task_id="runtime_chat",
                    call_index=1,
                    tool_id=call.name,
                    retry_class="pure_read",
                    idempotency_key=None,
                    request_fingerprint=fingerprint,
                    arguments_json='{"pattern":"*.py"}',
                    state="prepared",
                    created_at=datetime.now(UTC),
                ),
            )

    context = RequestContext(
        snapshot=request_context().snapshot,
        budget=request_context().budget,
        task_lifecycle=Journal(),
    )
    runtime = RuntimeChatAgentRuntime(
        loop,
        state,
        tools=(ModelToolDefinition("search_files", "Search", {"type": "object"}),),
        tool_side_effects={"search_files": ToolSideEffect.NONE},
    )

    asyncio.run(runtime.resume_turn("request-1", context))

    assert loop.request.request_context.snapshot.request_id == "request-1"
    assert loop.request.tool_call_index_start == 1
    assert loop.request.recovery_tool_call_ids[("search_files", '{"pattern":"*.py"}')] == (
        "provider-call-1",
    )


def test_resume_replays_supported_same_key_write_after_claiming_pending_outcome() -> None:
    call = ModelToolCall("provider-call-write", "send_message", {"message_id": "message-1"})
    key, fingerprint = stable_tool_invocation_identity(
        plan_run_id="request-1",
        task_id="runtime_chat",
        call_index=1,
        tool_id=call.name,
        arguments=call.arguments,
    )
    state = FakeState()
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="send it", turn_id="request-1"),
        ModelMessage(role=ModelRole.ASSISTANT, content="", turn_id="request-1", tool_calls=(call,)),
    )
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(RuntimeExecutionStatus.COMPLETED, "done", (), steps=1)
    )
    loop.tools = SimpleNamespace(execute_idempotent=lambda: None)
    expires_at = datetime.now(UTC) + timedelta(hours=1)

    class Journal:
        async def list_runtime_tool_invocations(self, session_id, turn_id):
            return (
                ToolInvocationRecord(
                    invocation_id=f"invocation:{key}",
                    plan_run_id=None,
                    session_id=session_id,
                    turn_id=turn_id,
                    runtime_host_id="host-new",
                    task_run_id=None,
                    task_id="runtime_chat",
                    call_index=1,
                    tool_id=call.name,
                    retry_class="idempotent_write",
                    idempotency_key=key,
                    request_fingerprint=fingerprint,
                    arguments_json='{"message_id":"message-1"}',
                    state="prepared",
                    created_at=datetime.now(UTC),
                    idempotency_expires_at=expires_at,
                ),
            )

    class Outcome(FakeOutcomeLifecycle):
        def __init__(self):
            self.claimed: list[str] = []
            self.released: list[str] = []

        async def pending(self, session_id):
            del session_id
            return (SimpleNamespace(turn_id="request-1", tool_call_id=call.call_id),)

        async def claim_resolution(self, session_id, tool_call_id):
            del session_id
            self.claimed.append(tool_call_id)
            return True

        async def release_resolution_claim(self, session_id, tool_call_id):
            del session_id
            self.released.append(tool_call_id)

    outcome = Outcome()
    context = RequestContext(
        snapshot=request_context().snapshot,
        budget=request_context().budget,
        task_lifecycle=Journal(),
    )
    runtime = RuntimeChatAgentRuntime(
        loop,
        state,
        tools=(ModelToolDefinition("send_message", "Send", {"type": "object"}),),
        tool_side_effects={"send_message": ToolSideEffect.POSSIBLE},
        tool_retry_classes={"send_message": ToolRetryClass.IDEMPOTENT_WRITE},
        idempotency_windows={"send_message": 3600},
    )
    runtime.configure_tool_outcomes(outcome)

    preflight_reason = asyncio.run(
        runtime.recovery_tool_reason(
            "43df14e0-b250-4981-a414-3db49e1ecc3b",
            "request-1",
            task_lifecycle=Journal(),
        )
    )
    assert preflight_reason is None
    assert outcome.claimed == []

    asyncio.run(runtime.resume_turn("request-1", context))

    assert outcome.claimed == [call.call_id]
    assert outcome.released == [call.call_id]
    assert loop.request.claimed_recovery_outcome_ids == frozenset({call.call_id})
    assert loop.request.tool_call_index_start == 1


@pytest.mark.parametrize(
    ("expiry_offset", "provider_support"),
    [(timedelta(seconds=-1), True), (timedelta(hours=1), False)],
)
def test_resume_requires_reconciliation_for_expired_or_unsupported_writes(
    expiry_offset, provider_support
) -> None:
    call = ModelToolCall("provider-call-write", "send_message", {"message_id": "message-1"})
    key, fingerprint = stable_tool_invocation_identity(
        plan_run_id="request-1",
        task_id="runtime_chat",
        call_index=1,
        tool_id=call.name,
        arguments=call.arguments,
    )
    state = FakeState()
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="send it", turn_id="request-1"),
        ModelMessage(role=ModelRole.ASSISTANT, content="", turn_id="request-1", tool_calls=(call,)),
    )
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(RuntimeExecutionStatus.COMPLETED, "unused", (), steps=1)
    )
    if provider_support:
        loop.tools = SimpleNamespace(execute_idempotent=lambda: None)
    expires_at = datetime.now(UTC) + expiry_offset

    class Journal:
        async def list_runtime_tool_invocations(self, session_id, turn_id):
            return (
                ToolInvocationRecord(
                    invocation_id=f"invocation:{key}",
                    plan_run_id=None,
                    session_id=session_id,
                    turn_id=turn_id,
                    runtime_host_id="host-new",
                    task_run_id=None,
                    task_id="runtime_chat",
                    call_index=1,
                    tool_id=call.name,
                    retry_class="idempotent_write",
                    idempotency_key=key,
                    request_fingerprint=fingerprint,
                    arguments_json='{"message_id":"message-1"}',
                    state="prepared",
                    created_at=datetime.now(UTC),
                    idempotency_expires_at=expires_at,
                ),
            )

    outcome = FakeOutcomeLifecycle()

    async def pending(session_id):
        del session_id
        return (SimpleNamespace(turn_id="request-1", tool_call_id=call.call_id),)

    outcome.pending = pending
    context = RequestContext(
        snapshot=request_context().snapshot,
        budget=request_context().budget,
        task_lifecycle=Journal(),
    )
    runtime = RuntimeChatAgentRuntime(
        loop,
        state,
        tools=(ModelToolDefinition("send_message", "Send", {"type": "object"}),),
        tool_side_effects={"send_message": ToolSideEffect.POSSIBLE},
        tool_retry_classes={"send_message": ToolRetryClass.IDEMPOTENT_WRITE},
        idempotency_windows={"send_message": 3600},
    )
    runtime.configure_tool_outcomes(outcome)

    with pytest.raises(ValueError, match="reconciliation"):
        asyncio.run(runtime.resume_turn("request-1", context))

    assert loop.request is None


def test_runtime_chat_does_not_persist_a_max_steps_turn() -> None:
    state = FakeState()
    loop = FakeExecutionLoop(
        RuntimeExecutionResult(
            RuntimeExecutionStatus.MAX_STEPS_REACHED,
            None,
            (),
            steps=2,
        )
    )
    runtime = RuntimeChatAgentRuntime(loop, state)

    with pytest.raises(RuntimeError, match="maximum execution steps"):
        asyncio.run(runtime.respond("执行任务", request_context()))

    context = request_context()
    assert state.appended == [
        (
            ModelMessage(
                role=ModelRole.USER,
                content="执行任务",
                turn_id=context.snapshot.request_id,
            ),
        )
    ]


def test_runtime_chat_propagates_execution_errors_without_persisting_turn() -> None:
    state = FakeState()
    loop = FakeExecutionLoop(error=RuntimeError("provider unavailable"))
    runtime = RuntimeChatAgentRuntime(loop, state)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(runtime.respond("你好", request_context()))

    context = request_context()
    assert state.appended == [
        (
            ModelMessage(
                role=ModelRole.USER,
                content="你好",
                turn_id=context.snapshot.request_id,
            ),
        )
    ]


def test_runtime_chat_propagates_cancellation_without_persisting_turn() -> None:
    state = FakeState()
    loop = FakeExecutionLoop(error=asyncio.CancelledError())
    runtime = RuntimeChatAgentRuntime(loop, state)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.respond("你好", request_context()))

    context = request_context()
    assert state.appended == [
        (
            ModelMessage(
                role=ModelRole.USER,
                content="你好",
                turn_id=context.snapshot.request_id,
            ),
        )
    ]


def test_runtime_chat_delegates_selected_markdown_skills_to_legacy_runtime() -> None:
    runtime = RuntimeChatAgentRuntime(
        FakeExecutionLoop(), FakeState(), skill_runtime=FakeSkillRuntime()
    )

    response = asyncio.run(
        runtime.respond_with_skill("检查代码", request_context(), skill_id="business/review")
    )

    assert response.content == "skill:business/review"


def test_runtime_chat_maps_persisted_messages_to_existing_contract() -> None:
    state = FakeState()
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="前一个问题"),
        ModelMessage(role=ModelRole.ASSISTANT, content="前一个回答"),
    )
    runtime = RuntimeChatAgentRuntime(FakeExecutionLoop(), state)

    messages = asyncio.run(runtime.read_messages("43df14e0-b250-4981-a414-3db49e1ecc3b"))

    assert [message.role for message in messages] == [MessageRole.USER, MessageRole.ASSISTANT]
    assert [message.content for message in messages] == ["前一个问题", "前一个回答"]


def test_runtime_chat_does_not_repeat_a_completed_tool_after_model_failure() -> None:
    state = FakeState()
    first_call = ModelToolCall(
        call_id="write-call-1",
        name="write_file",
        arguments={"path": "report.txt"},
    )
    retried_call = ModelToolCall(
        call_id="write-call-2",
        name="write_file",
        arguments={"path": "report.txt"},
    )

    class FailingThenRetryingModel:
        def __init__(self) -> None:
            self.responses = [
                ModelResponse(text="", tool_calls=(first_call,)),
                RuntimeError("model disconnected after tool result"),
                ModelResponse(text="", tool_calls=(retried_call,)),
                ModelResponse(text="The report is already written."),
            ]
            self.requests: list[ModelRequest] = []

        async def complete(self, request_context, request):
            del request_context
            self.requests.append(request)
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response

    class CountingToolPort:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, request_context, tool_call):
            self.calls.append((request_context, tool_call))
            return "written successfully"

    model = FailingThenRetryingModel()
    tools = CountingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)
    runtime = RuntimeChatAgentRuntime(
        loop,
        state,
        tools=(ModelToolDefinition("write_file", "Write a file", {"type": "object"}),),
    )
    runtime.configure_tool_outcomes(FakeOutcomeLifecycle())

    with pytest.raises(RuntimeError, match="after tool result"):
        asyncio.run(runtime.respond("Write report.txt", request_context()))

    assert [message.role for message in state.messages] == [
        ModelRole.USER,
        ModelRole.ASSISTANT,
        ModelRole.TOOL,
    ]
    assert state.messages[-1].tool_call_id == "write-call-1"

    response = asyncio.run(runtime.respond("Write report.txt", request_context()))

    assert response.content == "The report is already written."
    assert len(tools.calls) == 1
    assert model.requests[-1].messages[-1] == ModelMessage(
        role=ModelRole.TOOL,
        content="written successfully",
        tool_call_id="write-call-2",
    )


def test_runtime_chat_blocks_a_tool_with_uncertain_outcome_from_interrupted_turn() -> None:
    state = FakeState()
    prior_call = ModelToolCall(
        call_id="uncertain-call-1",
        name="write_file",
        arguments={"path": "report.txt"},
    )
    retried_call = ModelToolCall(
        call_id="uncertain-call-2",
        name="write_file",
        arguments={"path": "report.txt"},
    )
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="Write report.txt"),
        ModelMessage(role=ModelRole.ASSISTANT, content="", tool_calls=(prior_call,)),
    )

    class RetryingModel:
        def __init__(self) -> None:
            self.responses = [
                ModelResponse(text="", tool_calls=(retried_call,)),
                ModelResponse(text="Please verify the report before I continue."),
            ]
            self.requests: list[ModelRequest] = []

        async def complete(self, request_context, request):
            del request_context
            self.requests.append(request)
            return self.responses.pop(0)

    class CountingToolPort:
        def __init__(self) -> None:
            self.calls = []

        async def execute(self, request_context, tool_call):
            self.calls.append((request_context, tool_call))
            return "unexpected execution"

    model = RetryingModel()
    tools = CountingToolPort()
    runtime = RuntimeChatAgentRuntime(
        DefaultRuntimeExecutionLoop(model, tools),
        state,
        tools=(ModelToolDefinition("write_file", "Write a file", {"type": "object"}),),
    )

    response = asyncio.run(runtime.respond("Write report.txt", request_context()))

    assert response.route is Route.CLARIFY
    assert "结果不确定" in response.content
    assert "核实" in response.content
    assert tools.calls == []
    assert model.requests == []


def test_runtime_chat_blocks_recovery_after_side_effect_checkpoint_failure() -> None:
    class FailingToolResultState(FakeState):
        def __init__(self) -> None:
            super().__init__()
            self.fail_tool_result_once = True

        async def append_messages(self, session_id, messages):
            if self.fail_tool_result_once and any(
                message.role is ModelRole.TOOL for message in messages
            ):
                self.fail_tool_result_once = False
                raise RuntimeError("injected checkpoint write failure after tool side effect")
            await super().append_messages(session_id, messages)

    call = ModelToolCall(
        call_id="uncertain-write-1",
        name="write_file",
        arguments={"path": "report.txt", "content": "done"},
    )

    class ToolProposingModel:
        async def complete(self, request_context, request):
            del request_context, request
            return ModelResponse(text="", tool_calls=(call,))

    class NeverCalledModel:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request_context, request):
            del request_context, request
            self.calls += 1
            raise AssertionError("model must not run while a tool outcome is uncertain")

    class SideEffectToolPort:
        def __init__(self) -> None:
            self.side_effects = 0

        async def execute(self, request_context, tool_call):
            del request_context, tool_call
            self.side_effects += 1
            return "write completed"

    state = FailingToolResultState()
    tools = SideEffectToolPort()
    definition = ModelToolDefinition(
        "write_file", "Write a file", {"type": "object", "properties": {}}
    )
    first_runtime = RuntimeChatAgentRuntime(
        DefaultRuntimeExecutionLoop(ToolProposingModel(), tools),
        state,
        tools=(definition,),
    )
    first_runtime.configure_tool_outcomes(FakeOutcomeLifecycle())

    with pytest.raises(RuntimeError, match="checkpoint write failure"):
        asyncio.run(first_runtime.respond("Write report.txt", request_context()))

    assert tools.side_effects == 1
    assert state.messages[-1].role is ModelRole.ASSISTANT
    assert state.messages[-1].tool_calls == (call,)

    retry_model = NeverCalledModel()
    restarted_runtime = RuntimeChatAgentRuntime(
        DefaultRuntimeExecutionLoop(retry_model, tools),
        state,
        tools=(definition,),
    )
    restarted_runtime.configure_tool_outcomes(FakeOutcomeLifecycle())
    response = asyncio.run(restarted_runtime.respond("Write report.txt", request_context()))

    assert response.route is Route.CLARIFY
    assert "结果不确定" in response.content
    assert "核实" in response.content
    assert retry_model.calls == 0
    assert tools.side_effects == 1


def test_runtime_chat_retries_interrupted_read_only_tool_call() -> None:
    state = FakeState()
    previous_call = ModelToolCall(
        call_id="read-call-1", name="read_text_file", arguments={"path": "about.txt"}
    )
    retry_call = ModelToolCall(
        call_id="read-call-2", name="read_text_file", arguments={"path": "about.txt"}
    )
    state.messages = (
        ModelMessage(role=ModelRole.USER, content="Read about.txt"),
        ModelMessage(role=ModelRole.ASSISTANT, content="", tool_calls=(previous_call,)),
    )

    class ReadToolPort:
        def __init__(self) -> None:
            self.calls = 0

        async def execute(self, request_context, tool_call):
            del request_context, tool_call
            self.calls += 1
            return "HostPilot"

    tool_port = ReadToolPort()

    class ReadThenFinalModel:
        def __init__(self) -> None:
            self.responses = [
                ModelResponse(text="", tool_calls=(retry_call,)),
                ModelResponse(text="HostPilot"),
            ]

        async def complete(self, request_context, request):
            del request_context, request
            return self.responses.pop(0)

    runtime = RuntimeChatAgentRuntime(
        DefaultRuntimeExecutionLoop(ReadThenFinalModel(), tool_port),
        state,
        tools=(ModelToolDefinition("read_text_file", "Read a file", {"type": "object"}),),
        tool_side_effects={"read_text_file": ToolSideEffect.NONE},
    )

    response = asyncio.run(runtime.respond("Read about.txt", request_context()))

    assert response.content == "HostPilot"
    assert tool_port.calls == 1
