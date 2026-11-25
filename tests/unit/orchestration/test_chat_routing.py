import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

from hostpilot.contracts import ContextSnapshot, Route, SandboxMode
from hostpilot.observability import TraceEvent
from hostpilot.orchestration.chat import ChatService
from hostpilot.orchestration.runtime import ChatAgentResponse
from hostpilot.orchestration.skill_runtime import SkillSelection
from hostpilot.routing import CommandRouter
from hostpilot.runtime import AgentLifecycleManager, RequestContext, TurnLifecycleManager
from hostpilot.sandbox import current_sandbox_mode
from hostpilot.skills.runtime import SkillExecutionResult, SkillExecutionStatus
from hostpilot.tools.facade import ToolFacade


class FakeConversationStore:
    def __init__(self, sandbox_mode: SandboxMode = SandboxMode.READ_ONLY) -> None:
        self.sandbox_mode = sandbox_mode

    async def get(self, session_id: str):
        return SimpleNamespace(session_id=session_id, sandbox_mode=self.sandbox_mode)

    async def touch(self, session_id: str) -> None:
        self.touched = session_id


class SandboxModeProvider:
    def __init__(self) -> None:
        self.sandbox_mode = None
        self.history = []

    def set_sandbox_mode(self, mode) -> None:
        self.sandbox_mode = mode
        self.history.append(mode)


class FakeContextBuilder:
    async def build(
        self,
        session_id: str,
        *,
        query: str | None = None,
        state_reader=None,
    ) -> ContextSnapshot:
        self.query = query
        self.state_reader = state_reader
        return ContextSnapshot(request_id="request-1", session_id=session_id)


class FakeRuntime:
    def __init__(self) -> None:
        self.recorded: tuple[str, str, str] | None = None

    async def read_messages(self, session_id: str):
        del session_id
        return ()

    async def record_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        self.recorded = (session_id, user_message, assistant_message)

    async def respond(self, message: str, request_context: RequestContext):
        raise AssertionError("Explicit file commands should use the selected tool path.")


class FakeToolProvider:
    async def call_tool(self, name, arguments, *, budget, confirmation_provider=None) -> str:
        assert name == "read_text_file"
        assert arguments == {"path": "README.md"}
        return "HostPilot workspace"


def test_chat_service_executes_router_selected_tool_and_records_the_turn() -> None:
    async def scenario() -> None:
        runtime = FakeRuntime()
        conversations = FakeConversationStore()
        service = ChatService(
            conversations,
            FakeContextBuilder(),
            runtime,
            router=CommandRouter(),
            tool_provider=FakeToolProvider(),
        )

        result = await service.send("conversation-1", "/read README.md")

        assert result.route is Route.TOOL_CALL
        assert result.assistant_message == "HostPilot workspace"
        assert runtime.recorded == (
            "conversation-1",
            "/read README.md",
            "HostPilot workspace",
        )
        assert conversations.touched == "conversation-1"

    asyncio.run(scenario())


def test_chat_service_does_not_execute_when_turn_start_persistence_fails() -> None:
    async def scenario() -> None:
        class FailingTurnStore:
            async def create_turn(self, **kwargs) -> None:
                del kwargs
                raise OSError("turn store unavailable")

        class ExecutionRecorder(ChatService):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.executed = False

            async def _execute_turn(self, *args, **kwargs):
                del args, kwargs
                self.executed = True
                raise AssertionError("turn execution must not start")

        service = ExecutionRecorder(
            FakeConversationStore(),
            FakeContextBuilder(),
            FakeRuntime(),
            turn_lifecycle=TurnLifecycleManager(
                store=FailingTurnStore(),
                runtime_host_id="host-1",
            ),
        )

        try:
            await service.send("session-1", "hello")
        except OSError as error:
            assert str(error) == "turn store unavailable"
        else:
            raise AssertionError("Turn start persistence failure must reach the caller")
        assert not service.executed

    asyncio.run(scenario())


def test_chat_service_blocks_pending_outcome_before_context_or_route_selection() -> None:
    async def scenario() -> None:
        class PendingOutcomes:
            async def pending(self, session_id: str):
                assert session_id == "session-1"
                return (SimpleNamespace(tool_name="write_file", tool_call_id="call-1"),)

        class CountingContextBuilder(FakeContextBuilder):
            def __init__(self) -> None:
                self.calls = 0

            async def build(self, *args, **kwargs):
                self.calls += 1
                return await super().build(*args, **kwargs)

        builder = CountingContextBuilder()
        service = ChatService(
            FakeConversationStore(),
            builder,
            FakeRuntime(),
            tool_outcome_lifecycle=PendingOutcomes(),
        )

        result = await service.send("session-1", "hello there")

        assert result.route is Route.CLARIFY
        assert "核实" in result.assistant_message
        assert builder.calls == 0

        selection = await service.select_route_only("session-1", "run a skill")
        assert selection.route is Route.CLARIFY
        assert selection.reason_code == "unresolved_tool_outcome"
        assert builder.calls == 0

    asyncio.run(scenario())


def test_chat_service_carries_request_context_and_unpacks_legacy_tool_arguments() -> None:
    async def scenario() -> None:
        class CapturingChatService(ChatService):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.request_contexts: list[RequestContext] = []

            async def _execute_turn(
                self,
                runtime,
                request_context: RequestContext,
                session_id: str,
                message: str,
                started_at: float,
            ):
                self.request_contexts.append(request_context)
                return await super()._execute_turn(
                    runtime,
                    request_context,
                    session_id,
                    message,
                    started_at,
                )

        class CapturingContextBuilder(FakeContextBuilder):
            def __init__(self) -> None:
                self.snapshots: list[ContextSnapshot] = []

            async def build(self, session_id: str, *, query: str | None = None, state_reader=None):
                snapshot = await super().build(session_id, query=query, state_reader=state_reader)
                self.snapshots.append(snapshot)
                return snapshot

        class ApprovalProvider:
            pass

        class CapturingToolProvider:
            def __init__(self) -> None:
                self.calls: list[tuple[object, object | None]] = []

            async def call_tool(
                self, name, arguments, *, budget, confirmation_provider=None
            ) -> str:
                self.calls.append((budget, confirmation_provider))
                return "HostPilot workspace"

        builder = CapturingContextBuilder()
        tools = CapturingToolProvider()
        service = CapturingChatService(
            FakeConversationStore(),
            builder,
            FakeRuntime(),
            router=CommandRouter(),
            tool_provider=tools,
        )
        approval = ApprovalProvider()

        await service.send("conversation-1", "/read README.md")
        await service.send(
            "conversation-1",
            "/read README.md",
            confirmation_provider=approval,
        )

        assert len(service.request_contexts) == 2
        assert len(builder.snapshots) == 2
        assert len(tools.calls) == 2
        first_context, second_context = service.request_contexts
        assert first_context.snapshot.request_id == builder.snapshots[0].request_id
        assert second_context.snapshot.request_id == builder.snapshots[1].request_id
        assert first_context.snapshot.session_id == "conversation-1"
        assert second_context.snapshot.session_id == "conversation-1"
        assert first_context.snapshot.budget is not None
        assert second_context.snapshot.budget is not None
        assert first_context.snapshot.budget.usage.total_tokens == 0
        assert second_context.snapshot.budget.usage.tool_calls == 0
        assert first_context.confirmation_provider is None
        assert second_context.confirmation_provider is approval
        assert first_context.budget is not second_context.budget
        assert tools.calls[0] == (first_context.budget, None)
        assert tools.calls[1] == (second_context.budget, approval)

    asyncio.run(scenario())


def test_chat_service_records_safe_completion_trace_with_request_id() -> None:
    async def scenario() -> None:
        trace_id = uuid4()

        class UUIDContextBuilder(FakeContextBuilder):
            async def build(self, session_id: str, *, query: str | None = None, state_reader=None):
                del state_reader
                return ContextSnapshot(
                    request_id=str(trace_id),
                    session_id=session_id,
                )

        class TraceRecorder:
            def __init__(self) -> None:
                self.events: list[TraceEvent] = []

            def record(self, event: TraceEvent) -> None:
                self.events.append(event)

        class TraceToolProvider:
            async def call_tool(
                self, name, arguments, *, budget, confirmation_provider=None
            ) -> str:
                return "done"

        trace = TraceRecorder()
        service = ChatService(
            FakeConversationStore(),
            UUIDContextBuilder(),
            FakeRuntime(),
            router=CommandRouter(),
            tool_provider=TraceToolProvider(),
            trace_sink=trace,
        )

        await service.send("conversation-1", "/read private-note.txt")

        assert len(trace.events) == 1
        event = trace.events[0]
        assert event.context.trace_id == trace_id
        assert event.name == "chat.turn"
        assert event.attributes["route"] == "tool_call"
        assert event.attributes["status"] == "completed"
        assert "private-note.txt" not in str(event.attributes)

    asyncio.run(scenario())


def test_chat_turn_keeps_one_agent_generation_while_configuration_replaces_it() -> None:
    async def scenario() -> None:
        old_responding = asyncio.Event()
        release_old_respond = asyncio.Event()

        class Agent:
            def __init__(self, name: str, *, block: bool = False) -> None:
                self.name = name
                self.block = block
                self.closed = False
                self.observed: list[str] = []

            async def read_messages(self, session_id: str):
                del session_id
                assert not self.closed
                self.observed.append("context")
                return ()

            async def respond(self, message: str, request_context: RequestContext):
                del message, request_context
                assert not self.closed
                self.observed.append("respond")
                if self.block:
                    old_responding.set()
                    await release_old_respond.wait()
                await self.record_turn("session-1", "user", "assistant")
                return SimpleNamespace(
                    content=self.name,
                    token_usage=None,
                    route=None,
                    clarification_prompt=None,
                    budget_usage_accounted=False,
                )

            async def record_turn(self, session_id, user_message, assistant_message):
                del session_id, user_message, assistant_message
                assert not self.closed
                self.observed.append("record")

            async def aclose(self):
                self.closed = True

        class ConversationStore(FakeConversationStore):
            async def get(self, session_id: str):
                return SimpleNamespace(
                    session_id=session_id,
                    sandbox_mode=SandboxMode.READ_ONLY,
                )

        class ContextBuilder:
            async def build(self, session_id, *, query=None, state_reader=None):
                del query
                await state_reader.read_messages(session_id)
                return ContextSnapshot(request_id="request-1", session_id=session_id)

        old_agent = Agent("old", block=True)
        new_agent = Agent("new")
        agents = AgentLifecycleManager(old_agent)
        service = ChatService(ConversationStore(), ContextBuilder(), agents)

        old_turn = asyncio.create_task(service.send("session-1", "first"))
        await old_responding.wait()
        replacement = asyncio.create_task(agents.replace(new_agent))

        async def wait_for_new_generation() -> None:
            while agents.current is not new_agent:
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_new_generation(), timeout=1)
        new_result = await service.send("session-1", "second")
        assert new_result.assistant_message == "new"
        assert old_agent.observed == ["context", "respond"]
        assert not old_agent.closed
        assert new_agent.observed == ["context", "respond", "record"]

        release_old_respond.set()
        old_result = await old_turn
        await replacement
        assert old_result.assistant_message == "old"
        assert old_agent.observed == ["context", "respond", "record"]
        assert old_agent.closed
        await agents.aclose()
        assert new_agent.closed

    asyncio.run(scenario())


def test_route_only_evaluation_selects_skill_without_executing_runtime_or_tools() -> None:
    async def scenario() -> None:
        class Selector:
            async def select(self, message, request_context):
                self.message = message
                self.request_context = request_context
                return SkillSelection(
                    "legacy/office-automation",
                    {"text": "payload must not be returned"},
                    "matched_skill",
                )

        runtime = FakeRuntime()
        runtime.selector = Selector()
        conversations = FakeConversationStore()
        service = ChatService(
            conversations,
            FakeContextBuilder(),
            runtime,
            router=CommandRouter(),
            tool_provider=FakeToolProvider(),
        )

        result = await service.select_route_only("conversation-1", "请只用文本整理这段虚构周报")

        assert result.route is Route.SKILL_EXECUTE
        assert result.skill_id == "legacy/office-automation"
        assert result.reason_code == "matched_skill"
        assert runtime.recorded is None
        assert not hasattr(conversations, "touched")
        assert runtime.selector.request_context.snapshot.session_summary is None
        assert runtime.selector.request_context.snapshot.long_term_memory is None

    asyncio.run(scenario())


def test_chat_service_holds_session_lease_across_context_and_model_work() -> None:
    async def scenario() -> None:
        context_started = asyncio.Event()
        continue_context = asyncio.Event()
        model_started = asyncio.Event()
        continue_model = asyncio.Event()
        lease_events: list[str] = []

        class SessionLifecycle:
            @asynccontextmanager
            async def lease(self, session_id: str):
                lease_events.append(f"acquire:{session_id}")
                try:
                    yield
                finally:
                    lease_events.append(f"release:{session_id}")

        class BlockingContextBuilder(FakeContextBuilder):
            async def build(self, session_id, *, query=None, state_reader=None):
                context_started.set()
                await continue_context.wait()
                return await super().build(session_id, query=query, state_reader=state_reader)

        class BlockingRuntime(FakeRuntime):
            async def respond(self, message, request_context):
                del message, request_context
                model_started.set()
                await continue_model.wait()
                return ChatAgentResponse("completed")

        service = ChatService(
            FakeConversationStore(),
            BlockingContextBuilder(),
            BlockingRuntime(),
            session_lifecycle=SessionLifecycle(),
        )
        sending = asyncio.create_task(service.send("session-1", "hello"))
        await context_started.wait()
        assert lease_events == ["acquire:session-1"]

        continue_context.set()
        await model_started.wait()
        assert lease_events == ["acquire:session-1"]

        continue_model.set()
        result = await sending
        assert result.assistant_message == "completed"
        assert lease_events == ["acquire:session-1", "release:session-1"]

    asyncio.run(scenario())


def test_chat_history_and_route_evaluation_hold_session_leases() -> None:
    async def scenario() -> None:
        lease_events: list[str] = []

        class SessionLifecycle:
            @asynccontextmanager
            async def lease(self, session_id: str):
                lease_events.append(f"acquire:{session_id}")
                try:
                    yield
                finally:
                    lease_events.append(f"release:{session_id}")

        class ConversationStore(FakeConversationStore):
            async def get(self, session_id: str):
                return SimpleNamespace(session_id=session_id)

        class Selector:
            async def select(self, message, request_context):
                del message, request_context
                return SkillSelection("builtin/clarify", {}, "needs_clarification")

        runtime = FakeRuntime()
        runtime.selector = Selector()
        service = ChatService(
            ConversationStore(),
            FakeContextBuilder(),
            runtime,
            session_lifecycle=SessionLifecycle(),
        )

        conversation, messages = await service.history("session-1")
        assert conversation.session_id == "session-1"
        assert messages == ()
        selected = await service.select_route_only("session-1", "help")
        assert selected.skill_id == "builtin/clarify"
        assert lease_events == [
            "acquire:session-1",
            "release:session-1",
            "acquire:session-1",
            "release:session-1",
        ]

    asyncio.run(scenario())


def test_send_uses_persisted_sandbox_mode_in_request_context() -> None:
    async def scenario() -> None:
        provider = SandboxModeProvider()

        class Runtime(FakeRuntime):
            async def respond(self, message: str, request_context: RequestContext):
                del message
                assert provider.sandbox_mode is SandboxMode.WORKSPACE_WRITE
                assert current_sandbox_mode() is SandboxMode.WORKSPACE_WRITE
                self.request_context = request_context
                return ChatAgentResponse("done", route=Route.DIRECT)

        runtime = Runtime()
        service = ChatService(
            FakeConversationStore(SandboxMode.WORKSPACE_WRITE),
            FakeContextBuilder(),
            runtime,
            tool_provider=ToolFacade(provider),
        )

        await service.send("session-1", "hello")

        assert runtime.request_context.sandbox_mode is SandboxMode.WORKSPACE_WRITE
        assert provider.history == [SandboxMode.WORKSPACE_WRITE, None]
        assert current_sandbox_mode() is None

    asyncio.run(scenario())


def test_resume_plan_run_uses_persisted_sandbox_mode() -> None:
    async def scenario() -> None:
        provider = SandboxModeProvider()
        run = SimpleNamespace(
            session_id="session-1",
            state="interrupted",
            goal="resume the work",
            snapshot=None,
        )

        class TaskLifecycle:
            async def get_plan_run(self, plan_run_id):
                assert plan_run_id == "plan-run-1"
                return run

            async def claim_plan_run(self, plan_run_id):
                assert plan_run_id == "plan-run-1"
                return True

            async def update_plan_run(self, *args, **kwargs):
                del args, kwargs

        class Runtime(FakeRuntime):
            async def resume_plan_run(self, plan_run_id, request_context):
                assert plan_run_id == "plan-run-1"
                assert provider.sandbox_mode is SandboxMode.DANGER_FULL_ACCESS
                assert current_sandbox_mode() is SandboxMode.DANGER_FULL_ACCESS
                self.request_context = request_context
                return SkillExecutionResult(SkillExecutionStatus.COMPLETED)

        runtime = Runtime()
        service = ChatService(
            FakeConversationStore(SandboxMode.DANGER_FULL_ACCESS),
            FakeContextBuilder(),
            runtime,
            tool_provider=ToolFacade(provider),
            task_lifecycle=TaskLifecycle(),
        )

        result = await service.resume_plan_run("session-1", "plan-run-1")

        assert result.status is SkillExecutionStatus.COMPLETED
        assert runtime.request_context.sandbox_mode is SandboxMode.DANGER_FULL_ACCESS
        assert provider.history == [SandboxMode.DANGER_FULL_ACCESS, None]
        assert current_sandbox_mode() is None

    asyncio.run(scenario())


def test_resume_turn_uses_persisted_sandbox_mode() -> None:
    async def scenario() -> None:
        provider = SandboxModeProvider()

        class TurnLifecycle:
            async def list_recoverable(self, session_id):
                assert session_id == "session-1"
                return (SimpleNamespace(turn_id="turn-1"),)

            async def claim_recovery(self, turn_id, session_id):
                assert (turn_id, session_id) == ("turn-1", "session-1")
                return True

            @asynccontextmanager
            async def track_recovery(self, turn_id, session_id):
                assert (turn_id, session_id) == ("turn-1", "session-1")
                yield

        class Runtime(FakeRuntime):
            async def recoverability_reason(self, session_id, turn_id):
                return None

            async def recovery_tool_reason(self, session_id, turn_id, *, task_lifecycle):
                del session_id, turn_id, task_lifecycle
                return None

            async def recoverable_user_message(self, session_id, turn_id):
                return "continue"

            async def resume_turn(self, turn_id, request_context):
                assert turn_id == "turn-1"
                assert provider.sandbox_mode is SandboxMode.WORKSPACE_WRITE
                assert current_sandbox_mode() is SandboxMode.WORKSPACE_WRITE
                self.request_context = request_context
                return ChatAgentResponse("continued", route=Route.DIRECT)

        runtime = Runtime()
        service = ChatService(
            FakeConversationStore(SandboxMode.WORKSPACE_WRITE),
            FakeContextBuilder(),
            runtime,
            tool_provider=ToolFacade(provider),
            turn_lifecycle=TurnLifecycle(),
        )

        result = await service.resume_turn("session-1", "turn-1")

        assert result.assistant_message == "continued"
        assert runtime.request_context.sandbox_mode is SandboxMode.WORKSPACE_WRITE
        assert provider.history == [SandboxMode.WORKSPACE_WRITE, None]
        assert current_sandbox_mode() is None

    asyncio.run(scenario())


def test_send_uses_read_only_contextvar_without_tool_facade() -> None:
    async def scenario() -> None:
        class Runtime(FakeRuntime):
            async def respond(self, message: str, request_context: RequestContext):
                del message
                assert current_sandbox_mode() is SandboxMode.READ_ONLY
                self.request_context = request_context
                return ChatAgentResponse("done", route=Route.DIRECT)

        runtime = Runtime()
        service = ChatService(FakeConversationStore(), FakeContextBuilder(), runtime)

        await service.send("session-1", "hello")

        assert runtime.request_context.sandbox_mode is SandboxMode.READ_ONLY
        assert current_sandbox_mode() is None

    asyncio.run(scenario())
