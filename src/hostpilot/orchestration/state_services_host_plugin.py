"""Register persistence ports with the application RuntimeHost."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any

from hostpilot.persistence import (
    ConversationCheckpointer,
    ConversationStore,
    ConversationStoreFactory,
)
from hostpilot.runtime import RuntimeContext, RuntimePluginSpec


class StateServicesHostPlugin:
    """Expose injected session and checkpoint ports through stable service IDs."""

    spec = RuntimePluginSpec("builtin.state-services")
    conversation_store_service_id = "hostpilot.conversation-store"
    checkpointer_service_id = "hostpilot.conversation-checkpointer"

    def __init__(
        self,
        conversation_store_factory: ConversationStoreFactory,
        database_path: Path,
        checkpointer_context: AbstractAsyncContextManager[ConversationCheckpointer],
    ) -> None:
        self._conversation_store_factory = conversation_store_factory
        self._database_path = database_path
        self._conversations: ConversationStore | None = None
        self._checkpointer_context = checkpointer_context
        self._checkpointer: ConversationCheckpointer | None = None

    async def setup(self, context: RuntimeContext) -> None:
        conversations = self._conversation_store_factory(self._database_path)
        self._conversations = conversations
        try:
            context.services.register(
                self.conversation_store_service_id,
                conversations,
                owner=self.spec.plugin_id,
            )
        except BaseException:
            self._conversations = None
            await self._close_conversation_store(conversations)
            raise

    async def start(self, context: RuntimeContext) -> None:
        conversations = self._conversations
        if conversations is None:
            raise RuntimeError("The ConversationStore was not constructed during plugin setup.")
        checkpointer: ConversationCheckpointer | None = None
        try:
            await conversations.initialize()
            checkpointer = await self._checkpointer_context.__aenter__()
            context.services.register(
                self.checkpointer_service_id,
                checkpointer,
                owner=self.spec.plugin_id,
            )
        except BaseException as error:
            if checkpointer is not None:
                await self._checkpointer_context.__aexit__(
                    type(error),
                    error,
                    error.__traceback__,
                )
            await self._close_conversation_store(conversations)
            raise
        self._checkpointer = checkpointer

    async def stop(self, context: RuntimeContext) -> None:
        del context
        try:
            if self._checkpointer is not None:
                self._checkpointer = None
                await self._checkpointer_context.__aexit__(None, None, None)
        finally:
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
