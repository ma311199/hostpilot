"""Register persistence ports with the application RuntimeHost."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hostpilot.persistence import (
    ConversationCheckpointer,
    ConversationStore,
    ConversationStoreFactory,
    SessionLifecycleStore,
    SessionLifecycleStoreFactory,
    SQLiteSessionLifecycleStore,
    SQLiteTaskStore,
    SQLiteTurnStore,
    TaskStore,
    TaskStoreFactory,
    TurnStore,
    TurnStoreFactory,
)
from hostpilot.runtime import RuntimeContext, RuntimePluginSpec


class StateServicesHostPlugin:
    """Expose injected session and checkpoint ports through stable service IDs."""

    spec = RuntimePluginSpec("builtin.state-services")
    conversation_store_service_id = "hostpilot.conversation-store"
    checkpointer_service_id = "hostpilot.conversation-checkpointer"
    turn_store_service_id = "hostpilot.turn-store"
    task_store_service_id = "hostpilot.task-store"
    session_store_service_id = "hostpilot.session-lifecycle-store"
    heartbeat_interval_seconds = 5.0
    lease_duration_seconds = 30.0

    def __init__(
        self,
        conversation_store_factory: ConversationStoreFactory,
        database_path: Path,
        checkpointer_context: AbstractAsyncContextManager[ConversationCheckpointer],
        *,
        turn_store_factory: TurnStoreFactory = SQLiteTurnStore,
        task_store_factory: TaskStoreFactory = SQLiteTaskStore,
        session_store_factory: SessionLifecycleStoreFactory = SQLiteSessionLifecycleStore,
        heartbeat_interval_seconds: float = heartbeat_interval_seconds,
        lease_duration_seconds: float = lease_duration_seconds,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        if heartbeat_interval_seconds <= 0 or lease_duration_seconds <= heartbeat_interval_seconds:
            raise ValueError("Host lease must exceed a positive heartbeat interval.")
        self._conversation_store_factory = conversation_store_factory
        self._turn_store_factory = turn_store_factory
        self._task_store_factory = task_store_factory
        self._session_store_factory = session_store_factory
        self._database_path = database_path
        self._conversations: ConversationStore | None = None
        self._turn_store: TurnStore | None = None
        self._task_store: TaskStore | None = None
        self._session_store: SessionLifecycleStore | None = None
        self._checkpointer_context = checkpointer_context
        self._checkpointer: ConversationCheckpointer | None = None
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._lease_duration_seconds = lease_duration_seconds
        self._clock = clock
        self._runtime_host_id: str | None = None

    async def setup(self, context: RuntimeContext) -> None:
        conversations = self._conversation_store_factory(self._database_path)
        turn_store = self._turn_store_factory(self._database_path)
        task_store = self._task_store_factory(self._database_path)
        session_store = self._session_store_factory(self._database_path)
        self._conversations = conversations
        self._turn_store = turn_store
        self._task_store = task_store
        self._session_store = session_store
        try:
            context.services.register(
                self.conversation_store_service_id,
                conversations,
                owner=self.spec.plugin_id,
            )
            context.services.register(
                self.turn_store_service_id,
                turn_store,
                owner=self.spec.plugin_id,
            )
            context.services.register(
                self.task_store_service_id,
                task_store,
                owner=self.spec.plugin_id,
            )
            context.services.register(
                self.session_store_service_id,
                session_store,
                owner=self.spec.plugin_id,
            )
        except BaseException:
            self._conversations = None
            self._turn_store = None
            self._task_store = None
            self._session_store = None
            await self._close_conversation_store(conversations)
            raise

    async def start(self, context: RuntimeContext) -> None:
        conversations = self._conversations
        turn_store = self._turn_store
        task_store = self._task_store
        session_store = self._session_store
        if conversations is None:
            raise RuntimeError("The ConversationStore was not constructed during plugin setup.")
        if turn_store is None:
            raise RuntimeError("The TurnStore was not constructed during plugin setup.")
        if task_store is None:
            raise RuntimeError("The TaskStore was not constructed during plugin setup.")
        if session_store is None:
            raise RuntimeError("The SessionLifecycleStore was not constructed during plugin setup.")
        checkpointer: ConversationCheckpointer | None = None
        host_registered = False
        try:
            await conversations.initialize()
            await turn_store.initialize()
            await task_store.initialize()
            await session_store.initialize()
            now = self._clock()
            await turn_store.register_host(
                context.runtime_host_id,
                now=now,
                lease_expires_at=now + timedelta(seconds=self._lease_duration_seconds),
            )
            host_registered = True
            await task_store.interrupt_inactive_host_tasks(now=now)
            for conversation in await conversations.list():
                await session_store.register_session(conversation.session_id)
            self._runtime_host_id = context.runtime_host_id
            context.sessions.configure_persistence(session_store, context.runtime_host_id)
            context.turns.configure_persistence(turn_store, context.runtime_host_id)
            context.tasks.configure_persistence(task_store, context.runtime_host_id)
            checkpointer = await self._checkpointer_context.__aenter__()
            context.services.register(
                self.checkpointer_service_id,
                checkpointer,
                owner=self.spec.plugin_id,
            )
        except BaseException as error:
            try:
                if checkpointer is not None:
                    await self._checkpointer_context.__aexit__(
                        type(error),
                        error,
                        error.__traceback__,
                    )
            finally:
                try:
                    if host_registered:
                        await turn_store.release_host(
                            context.runtime_host_id,
                            now=self._clock(),
                        )
                finally:
                    await self._close_conversation_store(conversations)
            raise
        self._checkpointer = checkpointer

    async def heartbeat(self, context: RuntimeContext) -> None:
        """Renew this worker's lease until RuntimeHost stops the plugin task."""
        del context
        if self._turn_store is None or self._runtime_host_id is None:
            raise RuntimeError("The TurnStore Host lease is not active.")
        while True:
            await asyncio.sleep(self._heartbeat_interval_seconds)
            await self.heartbeat_once()

    async def heartbeat_once(self) -> None:
        """Renew the current Host lease once; return an error if ownership was lost."""
        turn_store = self._turn_store
        host_id = self._runtime_host_id
        if turn_store is None or host_id is None:
            raise RuntimeError("The TurnStore Host lease is not active.")
        now = self._clock()
        renewed = await turn_store.heartbeat_host(
            host_id,
            now=now,
            lease_expires_at=now + timedelta(seconds=self._lease_duration_seconds),
        )
        if not renewed:
            raise RuntimeError("Runtime Host lease expired before heartbeat renewal.")

    async def stop(self, context: RuntimeContext) -> None:
        del context
        try:
            if self._checkpointer is not None:
                self._checkpointer = None
                await self._checkpointer_context.__aexit__(None, None, None)
        finally:
            turn_store = self._turn_store
            host_id = self._runtime_host_id
            self._runtime_host_id = None
            try:
                if turn_store is not None and host_id is not None:
                    await turn_store.release_host(host_id, now=self._clock())
            finally:
                self._turn_store = None
                self._task_store = None
                self._session_store = None
                conversations = self._conversations
                self._conversations = None
                if conversations is not None:
                    await self._close_conversation_store(conversations)

    @staticmethod
    async def _close_conversation_store(conversations: ConversationStore) -> None:
        close = getattr(conversations, "aclose", None)
        if callable(close):
            result: Any = close()
            if hasattr(result, "__await__"):
                await result
