"""Contract tests for Runtime-owned execution loop boundaries."""

from __future__ import annotations

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot, ModelTokenUsage
from hostpilot.models import ModelMessage, ModelRole, ModelToolDefinition
from hostpilot.runtime import RequestContext
from hostpilot.runtime.execution import (
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeExecutionStatus,
)


def request_context() -> RequestContext:
    return RequestContext(
        ContextSnapshot(request_id="execution-test", session_id="session-1"),
        RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=500,
                    tool_calls=3,
                    retrieval_calls=2,
                    timeout_seconds=30,
                )
            )
        ),
    )


def test_execution_request_preserves_request_context_and_normalizes_sequences() -> None:
    context = request_context()
    messages = [ModelMessage(role=ModelRole.USER, content="Hello")]

    request = RuntimeExecutionRequest(
        request_context=context,
        messages=messages,
        tools=[],
        allowed_tool_ids=set(),
    )

    assert request.request_context is context
    assert request.messages == tuple(messages)
    assert isinstance(request.messages, tuple)
    assert isinstance(request.allowed_tool_ids, frozenset)


def test_execution_request_rejects_missing_request_context() -> None:
    with pytest.raises(TypeError, match="RequestContext"):
        RuntimeExecutionRequest(
            request_context=None,  # type: ignore[arg-type]
            messages=(ModelMessage(role=ModelRole.USER, content="Hello"),),
        )


@pytest.mark.parametrize("max_steps", [0, -1, True, 1.5])
def test_execution_request_rejects_invalid_step_limit(max_steps: object) -> None:
    with pytest.raises(ValueError, match="max_steps"):
        RuntimeExecutionRequest(
            request_context=request_context(),
            messages=(ModelMessage(role=ModelRole.USER, content="Hello"),),
            max_steps=max_steps,  # type: ignore[arg-type]
        )


def test_execution_request_rejects_duplicate_tools_and_unknown_allowlist_ids() -> None:
    tool = ModelToolDefinition(
        name="search_files",
        description="Search workspace files",
        parameters={"type": "object"},
    )
    message = ModelMessage(role=ModelRole.USER, content="Find a file")

    with pytest.raises(ValueError, match="unique"):
        RuntimeExecutionRequest(
            request_context=request_context(),
            messages=(message,),
            tools=(tool, tool),
        )

    with pytest.raises(ValueError, match="allowlist"):
        RuntimeExecutionRequest(
            request_context=request_context(),
            messages=(message,),
            tools=(tool,),
            allowed_tool_ids=frozenset({"delete_file"}),
        )


def test_execution_result_validates_terminal_step_count_and_keeps_usage() -> None:
    usage = ModelTokenUsage(input_tokens=12, output_tokens=8, total_tokens=20)

    result = RuntimeExecutionResult(
        status=RuntimeExecutionStatus.COMPLETED,
        response_text="Done",
        messages=(ModelMessage(role=ModelRole.ASSISTANT, content="Done"),),
        token_usage=usage,
        steps=2,
    )

    assert result.token_usage is usage
    assert result.steps == 2
    with pytest.raises(ValueError, match="steps"):
        RuntimeExecutionResult(
            status=RuntimeExecutionStatus.COMPLETED,
            response_text="Done",
            messages=(),
            steps=-1,
        )


def test_runtime_execution_protocols_are_exported_from_runtime_package() -> None:
    from hostpilot.runtime import (
        RuntimeExecutionLoop,
        RuntimeModelPort,
        RuntimeSkillPort,
        RuntimeToolPort,
    )

    assert all((RuntimeExecutionLoop, RuntimeModelPort, RuntimeSkillPort, RuntimeToolPort))
