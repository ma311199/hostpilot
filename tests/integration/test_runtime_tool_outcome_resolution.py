from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    ToolSideEffect,
)
from hostpilot.models import (
    ModelMessage,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelToolDefinition,
)
from hostpilot.orchestration.runtime_chat import RuntimeChatAgentRuntime
from hostpilot.persistence import SQLiteToolOutcomeStore, SQLiteTurnStore
from hostpilot.runtime import (
    DefaultRuntimeExecutionLoop,
    RequestContext,
    ToolOutcomeLifecycleManager,
)


async def _manager(database_path: Path, host_id: str) -> ToolOutcomeLifecycleManager:
    now = datetime.now(UTC)
    host_store = SQLiteTurnStore(database_path)
    await host_store.initialize()
    await host_store.register_host(
        host_id,
        now=now,
        lease_expires_at=now + timedelta(minutes=1),
    )
    outcome_store = SQLiteToolOutcomeStore(database_path)
    await outcome_store.initialize()
    manager = ToolOutcomeLifecycleManager(clock=lambda: now)
    manager.configure_persistence(outcome_store, host_id)
    return manager


def _request_context() -> RequestContext:
    return RequestContext(
        snapshot=ContextSnapshot(request_id="turn-1", session_id="session-1"),
        budget=RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=100,
                    tool_calls=4,
                    retrieval_calls=2,
                    timeout_seconds=30,
                )
            )
        ),
    )


class ToolProposalModel:
    def __init__(self, tool_call: ModelToolCall) -> None:
        self.tool_call = tool_call

    async def complete(self, request_context, request) -> ModelResponse:
        del request_context, request
        return ModelResponse(text="", tool_calls=(self.tool_call,))


class CountingWriteTool:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, request_context, tool_call) -> str:
        del request_context, tool_call
        self.calls += 1
        return "external write completed"


class FaultingCheckpoint:
    def __init__(self, failure: Literal["before", "after"]) -> None:
        self.messages: tuple[ModelMessage, ...] = ()
        self.failure = failure
        self.fail_tool_message = True

    async def load_messages(self, session_id: str) -> tuple[ModelMessage, ...]:
        assert session_id == "session-1"
        return self.messages

    async def append_messages(
        self,
        session_id: str,
        messages: tuple[ModelMessage, ...],
    ) -> None:
        assert session_id == "session-1"
        is_tool_result = any(message.role is ModelRole.TOOL for message in messages)
        if is_tool_result and self.fail_tool_message and self.failure == "before":
            self.fail_tool_message = False
            raise OSError("checkpoint unavailable before durable append")
        self.messages += messages
        if is_tool_result and self.fail_tool_message and self.failure == "after":
            self.fail_tool_message = False
            raise OSError("checkpoint acknowledgement lost after durable append")


async def _start_write_and_expect_checkpoint_failure(
    manager: ToolOutcomeLifecycleManager,
    checkpoint: FaultingCheckpoint,
    tool: CountingWriteTool,
) -> None:
    call = ModelToolCall("write-call", "write_file", {"path": "report.txt"})
    runtime = RuntimeChatAgentRuntime(
        DefaultRuntimeExecutionLoop(ToolProposalModel(call), tool),
        checkpoint,
        tools=(ModelToolDefinition("write_file", "Write a file", {"type": "object"}),),
        tool_side_effects={"write_file": ToolSideEffect.POSSIBLE},
    )
    runtime.configure_tool_outcomes(manager)
    try:
        await runtime.respond("Write report.txt", _request_context())
    except OSError as error:
        assert "checkpoint" in str(error)
    else:
        raise AssertionError("Injected checkpoint failure must escape the Runtime.")


def test_side_effect_result_loss_survives_host_restart_until_manual_resolution(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        first_manager = await _manager(database_path, "host-before-restart")
        checkpoint = FaultingCheckpoint("before")
        tool = CountingWriteTool()
        await _start_write_and_expect_checkpoint_failure(first_manager, checkpoint, tool)

        pending = await first_manager.pending("session-1")
        assert [item.tool_call_id for item in pending] == ["write-call"]
        assert tool.calls == 1

        host_store = SQLiteTurnStore(database_path)
        await host_store.release_host("host-before-restart", now=datetime.now(UTC))
        second_manager = await _manager(database_path, "host-after-restart")
        restarted_runtime = RuntimeChatAgentRuntime(
            DefaultRuntimeExecutionLoop(
                ToolProposalModel(
                    ModelToolCall("different-call", "write_file", {"path": "report.txt"})
                ),
                tool,
            ),
            checkpoint,
            tools=(ModelToolDefinition("write_file", "Write a file", {"type": "object"}),),
            tool_side_effects={"write_file": ToolSideEffect.POSSIBLE},
        )
        restarted_runtime.configure_tool_outcomes(second_manager)

        await restarted_runtime.resolve_tool_outcome(
            "session-1",
            "write-call",
            result_text="Human verified: report.txt contains the expected content.",
        )

        assert await second_manager.pending("session-1") == ()
        assert tool.calls == 1
        assert checkpoint.messages[-1] == ModelMessage(
            role=ModelRole.TOOL,
            content="Human verified: report.txt contains the expected content.",
            tool_call_id="write-call",
        )

    asyncio.run(scenario())


def test_checkpoint_persisted_before_ack_loss_is_reconciled_without_overwrite(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "runtime.sqlite3"
        first_manager = await _manager(database_path, "host-before-restart")
        checkpoint = FaultingCheckpoint("after")
        tool = CountingWriteTool()
        await _start_write_and_expect_checkpoint_failure(first_manager, checkpoint, tool)

        assert checkpoint.messages[-1] == ModelMessage(
            role=ModelRole.TOOL,
            content="external write completed",
            tool_call_id="write-call",
            turn_id="turn-1",
        )
        host_store = SQLiteTurnStore(database_path)
        await host_store.release_host("host-before-restart", now=datetime.now(UTC))
        second_manager = await _manager(database_path, "host-after-restart")
        runtime = RuntimeChatAgentRuntime(
            DefaultRuntimeExecutionLoop(
                ToolProposalModel(ModelToolCall("other-call", "write_file", {})), tool
            ),
            checkpoint,
            tools=(ModelToolDefinition("write_file", "Write a file", {"type": "object"}),),
            tool_side_effects={"write_file": ToolSideEffect.POSSIBLE},
        )
        runtime.configure_tool_outcomes(second_manager)

        await runtime.resolve_tool_outcome(
            "session-1", "write-call", result_text="external write completed"
        )

        assert await second_manager.pending("session-1") == ()
        assert tool.calls == 1
        tool_messages = [
            message for message in checkpoint.messages if message.role is ModelRole.TOOL
        ]
        assert len(tool_messages) == 1

        try:
            await runtime.resolve_tool_outcome(
                "session-1", "write-call", result_text="conflicting result"
            )
        except ValueError:
            pass
        else:
            raise AssertionError("A conflicting resolution must be rejected.")

    asyncio.run(scenario())


def test_read_only_checkpoint_failure_does_not_create_pending_record(tmp_path) -> None:
    async def scenario() -> None:
        manager = await _manager(tmp_path / "runtime.sqlite3", "host-read-only")
        checkpoint = FaultingCheckpoint("before")
        tool = CountingWriteTool()
        call = ModelToolCall("read-call", "read_text_file", {"path": "about.txt"})
        runtime = RuntimeChatAgentRuntime(
            DefaultRuntimeExecutionLoop(ToolProposalModel(call), tool),
            checkpoint,
            tools=(ModelToolDefinition("read_text_file", "Read a file", {"type": "object"}),),
            tool_side_effects={"read_text_file": ToolSideEffect.NONE},
        )
        runtime.configure_tool_outcomes(manager)

        try:
            await runtime.respond("Read about.txt", _request_context())
        except OSError as error:
            assert "checkpoint" in str(error)
        else:
            raise AssertionError("Injected checkpoint failure must escape the Runtime.")

        assert tool.calls == 1
        assert await manager.pending("session-1") == ()

    asyncio.run(scenario())
