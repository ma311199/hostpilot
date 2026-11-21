import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path

from hostpilot.contracts import ContextSnapshot
from hostpilot.orchestration.chat import ChatService
from hostpilot.orchestration.runtime import ChatAgentResponse
from hostpilot.orchestration.state_services_host_plugin import StateServicesHostPlugin
from hostpilot.persistence import SQLiteConversationStore
from hostpilot.runtime import RuntimeContext, RuntimeHost


class FakeCheckpointer:
    async def adelete_thread(self, thread_id: str) -> None:
        del thread_id


class FakeCheckpointerContext(AbstractAsyncContextManager[FakeCheckpointer]):
    async def __aenter__(self) -> FakeCheckpointer:
        return FakeCheckpointer()

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback


class NoopMemoryStore:
    def delete_session_summary(self, session_id: str) -> bool:
        del session_id
        return False


class BlockingContextBuilder:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def build(self, session_id: str, *, query=None, state_reader=None):
        del query, state_reader
        self.started.set()
        await self.release.wait()
        return ContextSnapshot(request_id="request-1", session_id=session_id)


class BlockingChatRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.runs = 0

    async def read_messages(self, session_id: str):
        del session_id
        return ()

    async def respond(self, message, request_context) -> ChatAgentResponse:
        del message, request_context
        self.runs += 1
        self.started.set()
        await self.release.wait()
        return ChatAgentResponse("completed")

    async def record_turn(self, session_id, user_message, assistant_message) -> None:
        del session_id, user_message, assistant_message


def _state_host(database_path: Path) -> tuple[RuntimeHost, StateServicesHostPlugin]:
    host = RuntimeHost(RuntimeContext())
    plugin = StateServicesHostPlugin(
        SQLiteConversationStore,
        database_path,
        FakeCheckpointerContext(),
    )
    host.register_plugin(plugin)
    return host, plugin


def test_sqlite_workers_wait_for_chat_lease_before_session_cleanup(tmp_path) -> None:
    async def scenario() -> None:
        database_path = tmp_path / "hostpilot.sqlite3"
        conversation_store = SQLiteConversationStore(database_path)
        await conversation_store.initialize()
        conversation = await conversation_store.create("Shared Session")

        first_host, _ = _state_host(database_path)
        second_host, _ = _state_host(database_path)
        await first_host.start()
        await second_host.start()

        context_builder = BlockingContextBuilder()
        runtime = BlockingChatRuntime()
        chat = ChatService(
            conversation_store,
            context_builder,
            runtime,
            session_lifecycle=first_host.context.sessions,
        )
        turn = asyncio.create_task(chat.send(conversation.session_id, "hello"))
        await context_builder.started.wait()
        context_builder.release.set()
        await runtime.started.wait()

        cleanup_done = asyncio.Event()
        cleanup_calls = 0

        async def cleanup() -> None:
            nonlocal cleanup_calls
            cleanup_calls += 1
            await conversation_store.delete(conversation.session_id)
            cleanup_done.set()

        deletion = asyncio.create_task(
            second_host.context.sessions.close_session(conversation.session_id, cleanup)
        )
        await asyncio.sleep(0.05)
        assert not cleanup_done.is_set()

        runtime.release.set()
        await turn
        assert await deletion
        assert cleanup_calls == 1
        assert cleanup_done.is_set()
        assert await conversation_store.get(conversation.session_id) is None

        await second_host.close()
        await first_host.close()

    asyncio.run(scenario())
