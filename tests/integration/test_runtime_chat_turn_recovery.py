from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from hostpilot.contracts import BudgetLimits, ContextSnapshot, Route, SandboxMode
from hostpilot.orchestration.chat import ChatService
from hostpilot.orchestration.runtime import ChatAgentResponse
from hostpilot.persistence import SQLiteTurnStore
from hostpilot.runtime import TurnLifecycleManager


class ConversationStore:
    async def get(self, session_id):
        return SimpleNamespace(session_id=session_id, sandbox_mode=SandboxMode.READ_ONLY)

    async def touch(self, session_id):
        self.touched = session_id


class ContextBuilder:
    async def build(self, session_id, *, query=None, state_reader=None):
        self.query = query
        self.state_reader = state_reader
        return ContextSnapshot(request_id="fresh-request", session_id=session_id)


class RecoverableRuntime:
    def __init__(self, *, reason=None, tool_reason=None):
        self.reason = reason
        self.tool_reason = tool_reason
        self.resume_calls = []

    async def recoverability_reason(self, session_id, turn_id):
        return self.reason

    async def recoverable_user_message(self, session_id, turn_id):
        return "continue the original work"

    async def recovery_tool_reason(self, session_id, turn_id, *, task_lifecycle):
        del session_id, turn_id, task_lifecycle
        return self.tool_reason

    async def resume_turn(self, turn_id, request_context):
        self.resume_calls.append((turn_id, request_context))
        return ChatAgentResponse("continued", route=Route.DIRECT)

    async def read_messages(self, session_id):
        return ()


async def interrupted_turn(tmp_path):
    database_path = tmp_path / "runtime.sqlite3"
    turn_store = SQLiteTurnStore(database_path)
    await turn_store.initialize()
    old_host_started = datetime.now(UTC) - timedelta(seconds=10)
    old_host_expiry = old_host_started + timedelta(seconds=5)
    await turn_store.register_host(
        "host-old", now=old_host_started, lease_expires_at=old_host_expiry
    )
    await turn_store.create_turn(
        turn_id="turn-original",
        session_id="session-1",
        runtime_host_id="host-old",
        started_at=old_host_started + timedelta(seconds=1),
    )
    current_host_started = old_host_expiry + timedelta(seconds=1)
    await turn_store.register_host(
        "host-current",
        now=current_host_started,
        lease_expires_at=current_host_started + timedelta(minutes=1),
    )
    manager = TurnLifecycleManager(store=turn_store, runtime_host_id="host-current")
    return turn_store, manager


def test_recoverable_turn_query_does_not_dispatch_and_explicit_resume_uses_fresh_context(
    tmp_path,
) -> None:
    async def scenario():
        turn_store, lifecycle = await interrupted_turn(tmp_path)
        runtime = RecoverableRuntime()
        context_builder = ContextBuilder()
        service = ChatService(
            ConversationStore(),
            context_builder,
            runtime,
            turn_lifecycle=lifecycle,
            budget_limits=BudgetLimits(
                total_tokens=900,
                tool_calls=7,
                retrieval_calls=2,
                timeout_seconds=40,
            ),
        )

        listed = await service.list_recoverable_turns("session-1")
        assert listed[0].recoverable is True
        assert runtime.resume_calls == []

        resumed = await service.resume_turn("session-1", "turn-original")
        stored_turn = (await turn_store.list_turns())[0]
        return runtime, context_builder, resumed, stored_turn

    runtime, context_builder, resumed, stored_turn = asyncio.run(scenario())

    assert resumed.request_id == "turn-original"
    assert resumed.assistant_message == "continued"
    assert context_builder.query == "continue the original work"
    assert runtime.resume_calls[0][0] == "turn-original"
    request_context = runtime.resume_calls[0][1]
    assert request_context.snapshot.request_id == "turn-original"
    assert request_context.snapshot.session_id == "session-1"
    assert request_context.budget.context.limits.total_tokens == 900
    assert stored_turn.state == "completed"


def test_recoverable_turn_query_reports_stale_checkpoint_without_resuming(tmp_path) -> None:
    async def scenario():
        _, lifecycle = await interrupted_turn(tmp_path)
        runtime = RecoverableRuntime(reason="checkpoint_superseded")
        service = ChatService(
            ConversationStore(),
            ContextBuilder(),
            runtime,
            turn_lifecycle=lifecycle,
        )
        listed = await service.list_recoverable_turns("session-1")
        return listed, runtime

    listed, runtime = asyncio.run(scenario())

    assert listed[0].recoverable is False
    assert listed[0].reason_code == "checkpoint_superseded"
    assert runtime.resume_calls == []


def test_reconciliation_required_turn_is_not_claimed_or_resumed(tmp_path) -> None:
    async def scenario():
        turn_store, lifecycle = await interrupted_turn(tmp_path)
        runtime = RecoverableRuntime(tool_reason="tool_invocation_reconciliation_required")
        service = ChatService(
            ConversationStore(),
            ContextBuilder(),
            runtime,
            turn_lifecycle=lifecycle,
        )
        listed = await service.list_recoverable_turns("session-1")
        try:
            await service.resume_turn("session-1", "turn-original")
        except ValueError:
            pass
        stored_turn = (await turn_store.list_turns())[0]
        return listed, runtime, stored_turn

    listed, runtime, stored_turn = asyncio.run(scenario())

    assert listed[0].recoverable is False
    assert listed[0].reason_code == "tool_invocation_reconciliation_required"
    assert runtime.resume_calls == []
    assert stored_turn.state == "interrupted"
