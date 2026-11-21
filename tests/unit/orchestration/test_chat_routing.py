import asyncio
from uuid import uuid4

from hostpilot.contracts import ContextSnapshot, Route
from hostpilot.observability import TraceEvent
from hostpilot.orchestration.chat import ChatService
from hostpilot.orchestration.skill_runtime import SkillSelection
from hostpilot.routing import CommandRouter
from hostpilot.runtime import RequestContext, TurnLifecycleManager


class FakeConversationStore:
    async def touch(self, session_id: str) -> None:
        self.touched = session_id


class FakeContextBuilder:
    async def build(self, session_id: str, *, query: str | None = None) -> ContextSnapshot:
        self.query = query
        return ContextSnapshot(request_id="request-1", session_id=session_id)


class FakeRuntime:
    def __init__(self) -> None:
        self.recorded: tuple[str, str, str] | None = None

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


def test_chat_service_carries_request_context_and_unpacks_legacy_tool_arguments() -> None:
    async def scenario() -> None:
        class CapturingChatService(ChatService):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                self.request_contexts: list[RequestContext] = []

            async def _execute_turn(
                self,
                request_context: RequestContext,
                session_id: str,
                message: str,
                started_at: float,
            ):
                self.request_contexts.append(request_context)
                return await super()._execute_turn(
                    request_context,
                    session_id,
                    message,
                    started_at,
                )

        class CapturingContextBuilder(FakeContextBuilder):
            def __init__(self) -> None:
                self.snapshots: list[ContextSnapshot] = []

            async def build(self, session_id: str, *, query: str | None = None):
                snapshot = await super().build(session_id, query=query)
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
            async def build(self, session_id: str, *, query: str | None = None):
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
