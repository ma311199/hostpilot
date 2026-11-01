"""Application service for loading context and executing chat turns."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from hostpilot.budget import RequestBudget
from hostpilot.context import ContextBuilder, ConversationNotFoundError
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ChatTurnResult,
    ConversationMessage,
    ConversationSummary,
    Route,
)
from hostpilot.observability import CostEvent, CostSink, CostUnit, StructuredCostSink
from hostpilot.orchestration.runtime import AgentRuntime
from hostpilot.output import OutputProcessor
from hostpilot.persistence import SQLiteConversationStore
from hostpilot.routing import DirectRouter, Router
from hostpilot.tools.executor import AsyncConfirmationProvider


class GovernedToolProvider(Protocol):
    """Run router-selected tools through the registered policy-first executor."""

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str: ...


class RequestTooLargeError(ValueError):
    """Raised when user input exceeds the configured request size limit."""


class ChatService:
    """Coordinate conversation lookup, bounded context, runtime, and activity time."""

    def __init__(
        self,
        conversations: SQLiteConversationStore,
        context_builder: ContextBuilder,
        runtime: AgentRuntime,
        *,
        budget_limits: BudgetLimits | None = None,
        max_request_chars: int = 32_000,
        router: Router | None = None,
        tool_provider: GovernedToolProvider | None = None,
        output_processor: OutputProcessor | None = None,
        cost_sink: CostSink | None = None,
    ) -> None:
        self.conversations = conversations
        self.context_builder = context_builder
        self.runtime = runtime
        self.budget_limits = budget_limits or BudgetLimits(
            total_tokens=20_000,
            tool_calls=5,
            retrieval_calls=2,
            timeout_seconds=60,
        )
        self.max_request_chars = max_request_chars
        self.router = router or DirectRouter()
        self.tool_provider = tool_provider
        self.output_processor = output_processor or OutputProcessor()
        self.cost_sink = cost_sink or StructuredCostSink()

    async def send(
        self,
        conversation_id: str,
        message: str,
        *,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> ChatTurnResult:
        """Run one chat turn and update the session activity timestamp."""
        if len(message) > self.max_request_chars:
            raise RequestTooLargeError("Message exceeds the configured request size limit.")
        budget = RequestBudget.start(BudgetContext(limits=self.budget_limits))
        async with asyncio.timeout(self.budget_limits.timeout_seconds):
            context = await self.context_builder.build(conversation_id)
            context = context.model_copy(update={"budget": budget.snapshot()})
            decision = self.router.decide(message, context)
            token_usage = None
            if decision.route is Route.DIRECT:
                if confirmation_provider is None:
                    agent_response = await self.runtime.respond(message, context, budget=budget)
                else:
                    agent_response = await self.runtime.respond(
                        message,
                        context,
                        budget=budget,
                        confirmation_provider=confirmation_provider,
                    )
                assistant_message = agent_response.content
                token_usage = agent_response.token_usage
                if token_usage is not None:
                    self.cost_sink.record(
                        CostEvent(
                            trace_id=UUID(context.request_id),
                            component="model",
                            unit=CostUnit.TOKEN,
                            quantity=Decimal(token_usage.total_tokens),
                        )
                    )
                    budget.consume_tokens(token_usage.total_tokens)
            elif decision.route is Route.CLARIFY:
                assistant_message = decision.clarification_prompt or "Please clarify your request."
                await self.runtime.record_turn(
                    conversation_id,
                    message,
                    assistant_message,
                )
            elif decision.route is Route.TOOL_CALL:
                if self.tool_provider is None or decision.tool_name is None:
                    raise UnsupportedRouteError(decision.route.value)
                assistant_message = await self.tool_provider.call_tool(
                    decision.tool_name,
                    decision.tool_arguments,
                    budget=budget,
                    confirmation_provider=confirmation_provider,
                )
                await self.runtime.record_turn(
                    conversation_id,
                    message,
                    assistant_message,
                )
            else:
                raise UnsupportedRouteError(decision.route.value)
            assistant_message = self.output_processor.process(assistant_message)
            await self.conversations.touch(conversation_id)
        return ChatTurnResult(
            conversation_id=conversation_id,
            request_id=context.request_id,
            assistant_message=assistant_message,
            route=decision.route,
            clarification_prompt=decision.clarification_prompt,
            token_usage=token_usage,
        )

    async def history(
        self, conversation_id: str
    ) -> tuple[ConversationSummary, tuple[ConversationMessage, ...]]:
        """Return one session's messages and metadata."""
        conversation = await self.conversations.get(conversation_id)
        if conversation is None:
            raise ConversationNotFoundError(conversation_id)
        messages = await self.runtime.read_messages(conversation_id)
        return conversation, messages


class UnsupportedRouteError(RuntimeError):
    """Raised when a route has not yet been connected to an execution path."""


__all__ = ["ChatService", "GovernedToolProvider", "RequestTooLargeError", "UnsupportedRouteError"]
