"""Behavior tests for the Runtime-owned model/tool loop."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import pytest

from hostpilot.budget import BudgetExceededError, RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    ModelTokenUsage,
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
from hostpilot.runtime import (
    RequestContext,
    RuntimeExecutionRequest,
    RuntimeExecutionStatus,
    RuntimeToolReplay,
)
from hostpilot.runtime.execution_loop import DefaultRuntimeExecutionLoop, RuntimeToolProposalError


def execution_context() -> RequestContext:
    return RequestContext(
        ContextSnapshot(request_id="loop-test", session_id="session-1"),
        RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=100,
                    tool_calls=5,
                    retrieval_calls=2,
                    timeout_seconds=30,
                )
            )
        ),
    )


def definition(name: str = "search_files") -> ModelToolDefinition:
    return ModelToolDefinition(name=name, description="Search", parameters={"type": "object"})


def request(
    context: RequestContext,
    *,
    tools: tuple[ModelToolDefinition, ...] = (),
    allowed_tool_ids: frozenset[str] = frozenset(),
    max_steps: int = 4,
    tool_side_effects=None,
    tool_outcomes=None,
    message_sink=None,
) -> RuntimeExecutionRequest:
    return RuntimeExecutionRequest(
        request_context=context,
        messages=(ModelMessage(role=ModelRole.USER, content="Find and summarize."),),
        tools=tools,
        allowed_tool_ids=allowed_tool_ids,
        max_steps=max_steps,
        tool_side_effects=(
            {tool.name: ToolSideEffect.NONE for tool in tools}
            if tool_side_effects is None
            else tool_side_effects
        ),
        tool_outcomes=tool_outcomes,
        message_sink=message_sink,
    )


class SequenceModelPort:
    def __init__(self, responses: Sequence[ModelResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[RequestContext, ModelRequest]] = []

    async def complete(
        self, request_context: RequestContext, model_request: ModelRequest
    ) -> ModelResponse:
        self.calls.append((request_context, model_request))
        return self.responses.pop(0)


class RecordingToolPort:
    def __init__(self, outputs: dict[str, str] | None = None) -> None:
        self.outputs = outputs or {}
        self.calls: list[tuple[RequestContext, ModelToolCall]] = []

    async def execute(self, request_context: RequestContext, tool_call: ModelToolCall) -> str:
        self.calls.append((request_context, tool_call))
        return self.outputs.get(tool_call.call_id, f"result:{tool_call.call_id}")


class RecordingOutcomeLifecycle:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def begin_invocation(self, **kwargs) -> None:
        self.events.append(f"registered:{kwargs['tool_call_id']}")

    async def result_checkpointed(self, session_id: str, tool_call_id: str) -> None:
        del session_id
        self.events.append(f"cleared:{tool_call_id}")


def test_loop_registers_side_effect_before_dispatch_and_clears_after_checkpoint() -> None:
    context = execution_context()
    call = ModelToolCall("write-1", "write_file", {"path": "report.txt"})
    model = SequenceModelPort(
        [ModelResponse(text="", tool_calls=(call,)), ModelResponse(text="Done.")]
    )
    events: list[str] = []

    class OrderedToolPort(RecordingToolPort):
        async def execute(self, request_context, tool_call):
            events.append(f"executed:{tool_call.call_id}")
            return await super().execute(request_context, tool_call)

    async def message_sink(messages):
        if messages[0].role is ModelRole.TOOL:
            events.append(f"checkpointed:{messages[0].tool_call_id}")

    loop = DefaultRuntimeExecutionLoop(model, OrderedToolPort())
    outcome_lifecycle = RecordingOutcomeLifecycle(events)

    asyncio.run(
        loop.run(
            request(
                context,
                tools=(definition("write_file"),),
                allowed_tool_ids=frozenset({"write_file"}),
                tool_side_effects={"write_file": ToolSideEffect.POSSIBLE},
                tool_outcomes=outcome_lifecycle,
                message_sink=message_sink,
            )
        )
    )

    assert events == [
        "registered:write-1",
        "executed:write-1",
        "checkpointed:write-1",
        "cleared:write-1",
    ]


def test_loop_does_not_dispatch_unknown_effect_tool_without_outcome_store() -> None:
    context = execution_context()
    call = ModelToolCall("unknown-1", "custom_tool", {})
    model = SequenceModelPort([ModelResponse(text="", tool_calls=(call,))])
    tools = RecordingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)

    with pytest.raises(RuntimeError, match="requires outcome persistence"):
        asyncio.run(
            loop.run(
                request(
                    context,
                    tools=(definition("custom_tool"),),
                    allowed_tool_ids=frozenset({"custom_tool"}),
                    tool_side_effects={},
                )
            )
        )

    assert tools.calls == []


def test_loop_does_not_dispatch_if_pending_record_cannot_be_written() -> None:
    context = execution_context()
    call = ModelToolCall("write-1", "write_file", {})
    model = SequenceModelPort([ModelResponse(text="", tool_calls=(call,))])
    tools = RecordingToolPort()

    class FailingOutcomeLifecycle(RecordingOutcomeLifecycle):
        async def begin_invocation(self, **kwargs) -> None:
            del kwargs
            raise OSError("outcome store unavailable")

    with pytest.raises(OSError, match="outcome store unavailable"):
        asyncio.run(
            DefaultRuntimeExecutionLoop(model, tools).run(
                request(
                    context,
                    tools=(definition("write_file"),),
                    allowed_tool_ids=frozenset({"write_file"}),
                    tool_side_effects={"write_file": ToolSideEffect.POSSIBLE},
                    tool_outcomes=FailingOutcomeLifecycle([]),
                )
            )
        )

    assert tools.calls == []


def test_loop_returns_model_final_text_and_accounts_unmanaged_usage() -> None:
    context = execution_context()
    usage = ModelTokenUsage(input_tokens=6, output_tokens=4, total_tokens=10)
    model = SequenceModelPort([ModelResponse(text="Found it.", token_usage=usage)])
    tools = RecordingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)

    result = asyncio.run(loop.run(request(context)))

    assert result.status is RuntimeExecutionStatus.COMPLETED
    assert result.response_text == "Found it."
    assert result.steps == 1
    assert result.messages == (ModelMessage(role=ModelRole.ASSISTANT, content="Found it."),)
    assert context.budget.snapshot().usage.total_tokens == 10
    assert model.calls[0][0] is context
    assert tools.calls == []


def test_loop_executes_allowed_tools_and_returns_correlated_results_to_model() -> None:
    context = execution_context()
    first_call = ModelToolCall(call_id="call-1", name="search_files", arguments={"pattern": "*.py"})
    second_call = ModelToolCall(
        call_id="call-2", name="read_text_file", arguments={"path": "README.md"}
    )
    model = SequenceModelPort(
        [
            ModelResponse(text="", tool_calls=(first_call, second_call)),
            ModelResponse(text="Two files found."),
        ]
    )
    tools = RecordingToolPort({"call-1": "README.py", "call-2": "HostPilot"})
    loop = DefaultRuntimeExecutionLoop(model, tools)
    definitions = (
        definition("search_files"),
        definition("read_text_file"),
        definition("delete_file"),
    )

    result = asyncio.run(
        loop.run(
            request(
                context,
                tools=definitions,
                allowed_tool_ids=frozenset({"search_files", "read_text_file"}),
            )
        )
    )

    assert result.status is RuntimeExecutionStatus.COMPLETED
    assert result.response_text == "Two files found."
    assert [call.call_id for _, call in tools.calls] == ["call-1", "call-2"]
    assert all(request_context is context for request_context, _ in tools.calls)
    assert [tool.name for tool in model.calls[0][1].tools] == [
        "search_files",
        "read_text_file",
    ]
    follow_up = model.calls[1][1].messages
    tool_messages = [message for message in follow_up if message.role is ModelRole.TOOL]
    assert [(message.tool_call_id, message.content) for message in tool_messages] == [
        ("call-1", "README.py"),
        ("call-2", "HostPilot"),
    ]
    assert result.steps == 2
    assert result.messages[-1] == ModelMessage(role=ModelRole.ASSISTANT, content="Two files found.")


def test_loop_rejects_an_unlisted_proposal_before_calling_tools() -> None:
    context = execution_context()
    model = SequenceModelPort(
        [
            ModelResponse(
                text="",
                tool_calls=(
                    ModelToolCall(
                        call_id="call-danger",
                        name="delete_file",
                        arguments={"path": "README.md"},
                    ),
                ),
            )
        ]
    )
    tools = RecordingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)

    with pytest.raises(RuntimeToolProposalError, match="allowlist"):
        asyncio.run(
            loop.run(
                request(
                    context,
                    tools=(definition(),),
                    allowed_tool_ids=frozenset({"search_files"}),
                )
            )
        )

    assert tools.calls == []


def test_loop_validates_whole_proposal_batch_before_any_tool_side_effect() -> None:
    context = execution_context()
    model = SequenceModelPort(
        [
            ModelResponse(
                text="",
                tool_calls=(
                    ModelToolCall(call_id="good", name="search_files", arguments={}),
                    ModelToolCall(call_id="bad", name="delete_file", arguments={}),
                ),
            )
        ]
    )
    tools = RecordingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)

    with pytest.raises(RuntimeToolProposalError):
        asyncio.run(
            loop.run(
                request(
                    context,
                    tools=(definition(),),
                    allowed_tool_ids=frozenset({"search_files"}),
                )
            )
        )

    assert tools.calls == []


@pytest.mark.parametrize(
    "calls",
    [
        (
            ModelToolCall(call_id="duplicate", name="search_files", arguments={}),
            ModelToolCall(call_id="duplicate", name="search_files", arguments={}),
        ),
        (ModelToolCall(call_id="", name="search_files", arguments={}),),
        (ModelToolCall(call_id="bad-args", name="search_files", arguments=[]),),  # type: ignore[arg-type]
    ],
)
def test_loop_rejects_malformed_proposals_before_tool_dispatch(
    calls: tuple[ModelToolCall, ...],
) -> None:
    context = execution_context()
    model = SequenceModelPort([ModelResponse(text="", tool_calls=calls)])
    tools = RecordingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)

    with pytest.raises(RuntimeToolProposalError):
        asyncio.run(
            loop.run(
                request(
                    context,
                    tools=(definition(),),
                    allowed_tool_ids=frozenset({"search_files"}),
                )
            )
        )

    assert tools.calls == []


def test_loop_stops_before_executing_tools_after_maximum_model_steps() -> None:
    context = execution_context()
    model = SequenceModelPort(
        [
            ModelResponse(
                text="",
                tool_calls=(ModelToolCall(call_id="call-1", name="search_files", arguments={}),),
            )
        ]
    )
    tools = RecordingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)

    result = asyncio.run(
        loop.run(
            request(
                context,
                tools=(definition(),),
                allowed_tool_ids=frozenset({"search_files"}),
                max_steps=1,
            )
        )
    )

    assert result.status is RuntimeExecutionStatus.MAX_STEPS_REACHED
    assert result.steps == 1
    assert result.messages == ()
    assert tools.calls == []


def test_loop_does_not_call_model_after_budget_is_exhausted() -> None:
    context = execution_context()
    context.budget.consume_tokens(100)
    model = SequenceModelPort([ModelResponse(text="unreachable")])
    loop = DefaultRuntimeExecutionLoop(model, RecordingToolPort())

    with pytest.raises(BudgetExceededError, match="total_tokens"):
        asyncio.run(loop.run(request(context)))

    assert model.calls == []


def test_loop_propagates_cancellation_from_tool_execution() -> None:
    context = execution_context()
    model = SequenceModelPort(
        [
            ModelResponse(
                text="",
                tool_calls=(ModelToolCall(call_id="call-1", name="search_files", arguments={}),),
            )
        ]
    )

    class CancellingToolPort(RecordingToolPort):
        async def execute(self, request_context: RequestContext, tool_call: ModelToolCall) -> str:
            del request_context, tool_call
            raise asyncio.CancelledError

    loop = DefaultRuntimeExecutionLoop(model, CancellingToolPort())

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            loop.run(
                request(
                    context,
                    tools=(definition(),),
                    allowed_tool_ids=frozenset({"search_files"}),
                )
            )
        )


def test_loop_checkpoints_tool_steps_and_reuses_an_interrupted_turn_result() -> None:
    context = execution_context()
    repeated_call = ModelToolCall(
        call_id="new-call-id",
        name="write_file",
        arguments={"path": "report.txt"},
    )
    model = SequenceModelPort(
        [
            ModelResponse(text="", tool_calls=(repeated_call,)),
            ModelResponse(text="The report is already written."),
        ]
    )
    tools = RecordingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)
    message_deltas: list[tuple[ModelMessage, ...]] = []

    async def persist(messages: tuple[ModelMessage, ...]) -> None:
        message_deltas.append(messages)

    execution_request = RuntimeExecutionRequest(
        request_context=context,
        messages=(ModelMessage(role=ModelRole.USER, content="Write report.txt"),),
        tools=(definition("write_file"),),
        allowed_tool_ids=frozenset({"write_file"}),
        tool_replays=(
            RuntimeToolReplay(
                tool_name="write_file",
                arguments_json='{"path":"report.txt"}',
                result="written successfully",
            ),
        ),
        message_sink=persist,
    )

    result = asyncio.run(loop.run(execution_request))

    assert result.status is RuntimeExecutionStatus.COMPLETED
    assert tools.calls == []
    assert model.calls[1][1].messages[-1] == ModelMessage(
        role=ModelRole.TOOL,
        content="written successfully",
        tool_call_id="new-call-id",
    )
    assert message_deltas == [
        (ModelMessage(role=ModelRole.ASSISTANT, content="", tool_calls=(repeated_call,)),),
        (
            ModelMessage(
                role=ModelRole.TOOL,
                content="written successfully",
                tool_call_id="new-call-id",
            ),
        ),
        (ModelMessage(role=ModelRole.ASSISTANT, content="The report is already written."),),
    ]


def test_loop_rejects_non_json_tool_arguments_before_checkpoint_or_execution() -> None:
    context = execution_context()
    call = ModelToolCall(
        call_id="call-with-invalid-arguments",
        name="write_file",
        arguments={"path": object()},
    )
    model = SequenceModelPort([ModelResponse(text="", tool_calls=(call,))])
    tools = RecordingToolPort()
    loop = DefaultRuntimeExecutionLoop(model, tools)
    message_deltas: list[tuple[ModelMessage, ...]] = []

    async def persist(messages: tuple[ModelMessage, ...]) -> None:
        message_deltas.append(messages)

    with pytest.raises(RuntimeToolProposalError, match="JSON serializable"):
        asyncio.run(
            loop.run(
                RuntimeExecutionRequest(
                    request_context=context,
                    messages=(ModelMessage(role=ModelRole.USER, content="Write report.txt"),),
                    tools=(definition("write_file"),),
                    allowed_tool_ids=frozenset({"write_file"}),
                    message_sink=persist,
                )
            )
        )

    assert tools.calls == []
    assert message_deltas == []
