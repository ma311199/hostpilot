from __future__ import annotations

import asyncio

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    MessageRole,
    ModelTokenUsage,
    Route,
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
)


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

    async def run(self, request):
        self.request = request
        if self.error is not None:
            raise self.error
        if request.message_sink is not None and self.result.messages:
            await request.message_sink(self.result.messages)
        return self.result


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
        (ModelMessage(role=ModelRole.USER, content="你好"),),
        (ModelMessage(role=ModelRole.ASSISTANT, content="你好。"),),
    ]


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

    assert state.appended == [(ModelMessage(role=ModelRole.USER, content="执行任务"),)]


def test_runtime_chat_propagates_execution_errors_without_persisting_turn() -> None:
    state = FakeState()
    loop = FakeExecutionLoop(error=RuntimeError("provider unavailable"))
    runtime = RuntimeChatAgentRuntime(loop, state)

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(runtime.respond("你好", request_context()))

    assert state.appended == [(ModelMessage(role=ModelRole.USER, content="你好"),)]


def test_runtime_chat_propagates_cancellation_without_persisting_turn() -> None:
    state = FakeState()
    loop = FakeExecutionLoop(error=asyncio.CancelledError())
    runtime = RuntimeChatAgentRuntime(loop, state)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.respond("你好", request_context()))

    assert state.appended == [(ModelMessage(role=ModelRole.USER, content="你好"),)]


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
    response = asyncio.run(restarted_runtime.respond("Write report.txt", request_context()))

    assert response.route is Route.CLARIFY
    assert "结果不确定" in response.content
    assert "核实" in response.content
    assert retry_model.calls == 0
    assert tools.side_effects == 1
