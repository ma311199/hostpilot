import asyncio
from uuid import uuid4

from hostpilot.contracts import ContextSnapshot, Route
from hostpilot.observability import TraceEvent
from hostpilot.orchestration.chat import ChatService
from hostpilot.orchestration.skill_runtime import SkillSelection
from hostpilot.routing import CommandRouter


class FakeConversationStore:
    async def touch(self, conversation_id: str) -> None:
        self.touched = conversation_id


class FakeContextBuilder:
    async def build(self, conversation_id: str, *, query: str | None = None) -> ContextSnapshot:
        self.query = query
        return ContextSnapshot(request_id="request-1", conversation_id=conversation_id)


class FakeRuntime:
    def __init__(self) -> None:
        self.recorded: tuple[str, str, str] | None = None

    async def record_turn(
        self,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        self.recorded = (conversation_id, user_message, assistant_message)

    async def respond(self, message: str, context: ContextSnapshot, *, budget=None):
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


def test_chat_service_records_safe_completion_trace_with_request_id() -> None:
    async def scenario() -> None:
        trace_id = uuid4()

        class UUIDContextBuilder(FakeContextBuilder):
            async def build(self, conversation_id: str, *, query: str | None = None):
                return ContextSnapshot(
                    request_id=str(trace_id),
                    conversation_id=conversation_id,
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
            async def select(self, message, context, *, budget):
                self.message = message
                self.context = context
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
        assert runtime.selector.context.session_summary is None
        assert runtime.selector.context.long_term_memory is None

    asyncio.run(scenario())
