import asyncio
from datetime import UTC, datetime, timedelta

from hostpilot.orchestration.state_services_host_plugin import StateServicesHostPlugin
from hostpilot.persistence import SQLiteTaskStore, SQLiteTurnStore
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
        assert context.services.resolve(plugin.turn_store_service_id) is plugin._turn_store
        assert context.services.resolve(plugin.task_store_service_id) is plugin._task_store
        await host.close()
        return conversations, checkpointer, context, checkpointer_context

    conversations, _, context, checkpointer_context = asyncio.run(scenario())
    assert context.services.snapshot() == {}
    assert conversations.initialized
    assert conversations.closed
    assert checkpointer_context.entered
    assert checkpointer_context.exited


def test_state_plugin_heartbeat_keeps_live_host_turn_from_recovery(tmp_path) -> None:
    async def scenario() -> tuple[str, str]:
        database_path = tmp_path / "runtime.sqlite3"
        now = [datetime(2026, 11, 21, 10, 0, tzinfo=UTC)]
        checkpointer_context = FakeCheckpointerContext(object())
        first_context = RuntimeContext()
        first_host = RuntimeHost(first_context)
        first_plugin = StateServicesHostPlugin(
            lambda _: FakeConversationStore(),
            database_path,
            checkpointer_context,
            heartbeat_interval_seconds=2,
            lease_duration_seconds=10,
            clock=lambda: now[0],
        )
        first_host.register_plugin(first_plugin)
        await first_host.start()
        turn_store = first_context.services.resolve(first_plugin.turn_store_service_id)
        task_store = first_context.services.resolve(first_plugin.task_store_service_id)
        await turn_store.create_turn(
            turn_id="live-turn",
            session_id="session-1",
            runtime_host_id=first_context.runtime_host_id,
            started_at=now[0],
        )
        await task_store.create_task_run(
            task_run_id="live-task-run",
            task_id="read_source",
            turn_id="live-turn",
            session_id="session-1",
            runtime_host_id=first_context.runtime_host_id,
            started_at=now[0],
        )

        now[0] += timedelta(seconds=6)
        await first_plugin.heartbeat_once()
        now[0] += timedelta(seconds=5)

        second_host = RuntimeHost(RuntimeContext())
        second_plugin = StateServicesHostPlugin(
            lambda _: FakeConversationStore(),
            database_path,
            FakeCheckpointerContext(object()),
            heartbeat_interval_seconds=2,
            lease_duration_seconds=10,
            clock=lambda: now[0],
        )
        second_host.register_plugin(second_plugin)
        await second_host.start()

        record = (await turn_store.list_turns())[0]
        task_record = (await task_store.list_task_runs())[0]
        await second_host.close()
        await first_host.close()
        return record.state, task_record.state

    assert asyncio.run(scenario()) == ("running", "running")


def test_state_plugin_start_recovers_turns_from_expired_hosts(tmp_path) -> None:
    async def scenario() -> tuple[str, str]:
        database_path = tmp_path / "runtime.sqlite3"
        now = datetime(2026, 11, 21, 10, 0, tzinfo=UTC)
        stale_store = SQLiteTurnStore(database_path)
        await stale_store.initialize()
        await stale_store.register_host(
            "stale-host",
            now=now - timedelta(seconds=20),
            lease_expires_at=now - timedelta(seconds=1),
        )
        stale_tasks = SQLiteTaskStore(database_path)
        await stale_tasks.initialize()
        await stale_tasks.create_task_run(
            task_run_id="stale-task-run",
            task_id="read_source",
            turn_id="stale-turn",
            session_id="session-1",
            runtime_host_id="stale-host",
            started_at=now - timedelta(seconds=20),
        )
        await stale_store.create_turn(
            turn_id="stale-turn",
            session_id="session-1",
            runtime_host_id="stale-host",
            started_at=now - timedelta(seconds=20),
        )

        host = RuntimeHost(RuntimeContext())
        plugin = StateServicesHostPlugin(
            lambda _: FakeConversationStore(),
            database_path,
            FakeCheckpointerContext(object()),
            clock=lambda: now,
        )
        host.register_plugin(plugin)
        await host.start()
        turn_store = host.context.services.resolve(plugin.turn_store_service_id)
        task_store = host.context.services.resolve(plugin.task_store_service_id)
        record = (await turn_store.list_turns())[0]
        task_record = (await task_store.list_task_runs())[0]
        await host.close()
        return record.state, task_record.state

    assert asyncio.run(scenario()) == ("interrupted", "interrupted")


def test_host_shutdown_persists_cancellation_before_releasing_lease(tmp_path) -> None:
    async def scenario() -> tuple[str, str]:
        database_path = tmp_path / "runtime.sqlite3"
        context = RuntimeContext()
        host = RuntimeHost(context)
        plugin = StateServicesHostPlugin(
            lambda _: FakeConversationStore(),
            database_path,
            FakeCheckpointerContext(object()),
        )
        host.register_plugin(plugin)
        await host.start()
        started = asyncio.Event()

        async def run_turn() -> None:
            async with context.turns.track("active-turn", "session-1"):
                async with context.tasks.track("write_report", "active-turn", "session-1"):
                    started.set()
                    await asyncio.Event().wait()

        task = asyncio.create_task(run_turn())
        await started.wait()
        await host.close()
        assert task.cancelled()
        turn_store = SQLiteTurnStore(database_path)
        turn_record = (await turn_store.list_turns())[0]
        task_record = (await SQLiteTaskStore(database_path).list_task_runs())[0]
        return turn_record.state, task_record.state

    assert asyncio.run(scenario()) == ("cancelled", "cancelled")
