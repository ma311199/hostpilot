import asyncio

from hostpilot.contracts import ContextSnapshot, Route
from hostpilot.orchestration.chat import ChatService
from hostpilot.routing import CommandRouter


class FakeConversationStore:
    async def touch(self, conversation_id: str) -> None:
        self.touched = conversation_id


class FakeContextBuilder:
    async def build(self, conversation_id: str) -> ContextSnapshot:
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
