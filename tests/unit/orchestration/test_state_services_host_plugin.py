import asyncio

from hostpilot.orchestration.state_services_host_plugin import StateServicesHostPlugin
from hostpilot.runtime import RuntimeContext, RuntimeHost


class FakeCheckpointerContext:
    def __init__(self, checkpointer: object) -> None:
        self.checkpointer = checkpointer
        self.entered = False
        self.exited = False

    async def __aenter__(self):
        self.entered = True
        return self.checkpointer

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        del exc_type, exc, traceback
        self.exited = True


class FakeConversationStore:
    def __init__(self) -> None:
        self.initialized = False
        self.closed = False

    async def initialize(self) -> None:
        self.initialized = True

    async def aclose(self) -> None:
        self.closed = True


def test_state_plugin_registers_and_unregisters_injected_store_ports(tmp_path) -> None:
    async def scenario() -> tuple[
        FakeConversationStore,
        object,
        RuntimeContext,
        FakeCheckpointerContext,
    ]:
        context = RuntimeContext()
        host = RuntimeHost(context)
        conversations = FakeConversationStore()
        checkpointer = object()
        checkpointer_context = FakeCheckpointerContext(checkpointer)
        plugin = StateServicesHostPlugin(
            lambda _database_path: conversations,
            tmp_path / "hostpilot.sqlite3",
            checkpointer_context,
        )
        host.register_plugin(plugin)
        await host.start()

        assert context.services.resolve(plugin.conversation_store_service_id) is conversations
        assert context.services.resolve(plugin.checkpointer_service_id) is checkpointer
        await host.close()
        return conversations, checkpointer, context, checkpointer_context

    conversations, _, context, checkpointer_context = asyncio.run(scenario())
    assert context.services.snapshot() == {}
    assert conversations.initialized
    assert conversations.closed
    assert checkpointer_context.entered
    assert checkpointer_context.exited
