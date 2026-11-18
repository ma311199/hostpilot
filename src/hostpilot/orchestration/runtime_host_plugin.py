"""Adapter that gives the configured chat Runtime a RuntimeHost lifecycle."""

from __future__ import annotations

from typing import Any

from hostpilot.budget import RequestBudget
from hostpilot.contracts import ContextSnapshot, ConversationMessage
from hostpilot.orchestration.runtime import AgentResponse, AgentRuntime
from hostpilot.runtime import RuntimeContext, RuntimePluginSpec
from hostpilot.tools.executor import AsyncConfirmationProvider


class AgentRuntimeHostPlugin:
    """Own the active AgentRuntime and expose it as a HostPilot service."""

    spec = RuntimePluginSpec(
        "builtin.chat-execution",
        dependencies=(
            "builtin.mcp-provider",
            "builtin.state-services",
            "builtin.tool-governance",
        ),
    )
    service_id = "hostpilot.agent-runtime"

    def __init__(self, runtime: AgentRuntime | None = None) -> None:
        self._runtime = runtime

    @property
    def selector(self) -> Any:
        """Expose the optional selector used by route-only evaluation."""
        return getattr(self._require_runtime(), "selector", None)

    async def setup(self, context: RuntimeContext) -> None:
        context.services.register(self.service_id, self, owner=self.spec.plugin_id)

    async def start(self, context: RuntimeContext) -> None:
        del context

    async def stop(self, context: RuntimeContext) -> None:
        del context
        await self.replace(None)

    async def replace(self, runtime: AgentRuntime | None) -> None:
        previous = self._runtime
        self._runtime = runtime
        if previous is None or previous is runtime:
            return
        close = getattr(previous, "aclose", None)
        if callable(close):
            await close()

    async def read_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]:
        return await self._require_runtime().read_messages(conversation_id)

    async def respond(
        self,
        message: str,
        context: ContextSnapshot,
        *,
        budget: RequestBudget | None = None,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> AgentResponse:
        runtime = self._require_runtime()
        if confirmation_provider is None:
            return await runtime.respond(message, context, budget=budget)
        return await runtime.respond(
            message,
            context,
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

    async def record_turn(
        self,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        await self._require_runtime().record_turn(
            conversation_id,
            user_message,
            assistant_message,
        )

    def _require_runtime(self) -> AgentRuntime:
        if self._runtime is None:
            raise RuntimeError("The configured chat Runtime is not available.")
        return self._runtime
