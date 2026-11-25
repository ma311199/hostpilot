"""Adapter that gives the configured chat Runtime a RuntimeHost lifecycle."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from typing import Any, cast

from hostpilot.contracts import ConversationMessage
from hostpilot.orchestration.runtime import ChatAgentResponse, ChatAgentRuntime
from hostpilot.runtime import (
    AgentLifecycleManager,
    RequestContext,
    RuntimeContext,
    RuntimePluginSpec,
)


class ChatAgentRuntimeHostPlugin:
    """Own the active ChatAgentRuntime and expose it as a HostPilot service."""

    spec = RuntimePluginSpec(
        "builtin.chat-execution",
        dependencies=(
            "builtin.mcp-provider",
            "builtin.state-services",
            "builtin.tool-governance",
        ),
    )
    service_id = "hostpilot.agent-runtime"

    def __init__(self, runtime: ChatAgentRuntime | None = None) -> None:
        self._agents = AgentLifecycleManager(runtime)
        self._tool_outcomes: Any | None = None

    @property
    def selector(self) -> Any:
        """Expose the optional selector used by route-only evaluation."""
        runtime = self._agents.current
        return getattr(runtime, "selector", None)

    def lease(self) -> AbstractAsyncContextManager[ChatAgentRuntime]:
        """Lease one Agent generation for the complete caller operation."""
        return cast(AbstractAsyncContextManager[ChatAgentRuntime], self._agents.lease())

    async def setup(self, context: RuntimeContext) -> None:
        initial_runtime = self._agents.current
        if initial_runtime is not None:
            await context.agents.replace(initial_runtime)
        self._agents = context.agents
        context.services.register(self.service_id, self, owner=self.spec.plugin_id)

    async def start(self, context: RuntimeContext) -> None:
        self._tool_outcomes = context.tool_outcomes
        runtime = self._agents.current
        configure = getattr(runtime, "configure_tool_outcomes", None)
        if callable(configure):
            configure(self._tool_outcomes)

    async def stop(self, context: RuntimeContext) -> None:
        del context
        await self._agents.aclose()

    async def replace(self, runtime: ChatAgentRuntime | None) -> None:
        configure = getattr(runtime, "configure_tool_outcomes", None)
        if callable(configure) and self._tool_outcomes is not None:
            configure(self._tool_outcomes)
        await self._agents.replace(runtime)

    async def read_messages(self, session_id: str) -> tuple[ConversationMessage, ...]:
        async with self._agents.lease() as runtime:
            return await cast(ChatAgentRuntime, runtime).read_messages(session_id)

    async def respond(
        self,
        message: str,
        request_context: RequestContext,
    ) -> ChatAgentResponse:
        async with self._agents.lease() as runtime:
            return await cast(ChatAgentRuntime, runtime).respond(message, request_context)

    async def resume_plan_run(self, plan_run_id: str, request_context: RequestContext) -> Any:
        """Forward explicit Plan recovery to the active Runtime generation."""
        async with self._agents.lease() as runtime:
            resume = getattr(runtime, "resume_plan_run", None)
            if not callable(resume):
                raise RuntimeError("The active Runtime cannot resume PlanRuns.")
            return await resume(plan_run_id, request_context)

    async def record_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        async with self._agents.lease() as runtime:
            await cast(ChatAgentRuntime, runtime).record_turn(
                session_id,
                user_message,
                assistant_message,
            )

    async def record_associated_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
        *,
        turn_id: str,
    ) -> None:
        """Forward durable Turn association when the active Chat Runtime supports it."""
        async with self._agents.lease() as runtime:
            record = getattr(runtime, "record_associated_turn", None)
            if not callable(record):
                await cast(ChatAgentRuntime, runtime).record_turn(
                    session_id, user_message, assistant_message
                )
                return
            await record(session_id, user_message, assistant_message, turn_id=turn_id)

    async def resolve_tool_outcome(
        self,
        session_id: str,
        tool_call_id: str,
        *,
        result_text: str,
    ) -> None:
        """Apply a verified result through the currently leased chat runtime."""
        async with self._agents.lease() as runtime:
            resolve = getattr(runtime, "resolve_tool_outcome", None)
            if not callable(resolve):
                raise RuntimeError("The active Chat Runtime cannot resolve tool outcomes.")
            await resolve(session_id, tool_call_id, result_text=result_text)
