"""Application service for loading context and executing chat turns."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from time import perf_counter
from typing import Any, Protocol, cast
from uuid import UUID, uuid4

from hostpilot.budget import RequestBudget
from hostpilot.context import ContextBuilder, ConversationNotFoundError
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ChatTurnResult,
    ConversationMessage,
    ConversationSummary,
    ModelTokenUsage,
    Route,
    SandboxMode,
)
from hostpilot.observability import (
    CostEvent,
    CostSink,
    CostUnit,
    StructuredCostSink,
    TraceContext,
    TraceEvent,
    TraceSink,
    trace_context_scope,
)
from hostpilot.orchestration.runtime import ChatAgentResponse, ChatAgentRuntime
from hostpilot.output import OutputProcessor
from hostpilot.persistence import ConversationStore
from hostpilot.policy.confirmation import ConfirmationDecision
from hostpilot.routing import DirectRouter, Router
from hostpilot.runtime import (
    RequestContext,
    SessionLifecycleManager,
    TaskLifecycleManager,
    TurnLifecycleManager,
    TurnRecoveryCancelled,
)
from hostpilot.runtime.context import TaskCacheApprovalProvider
from hostpilot.sandbox import sandbox_mode_scope as bind_sandbox_mode
from hostpilot.skills.runtime import SkillExecutionResult
from hostpilot.tools.executor import AsyncConfirmationProvider, ToolConfirmationDeniedError
from hostpilot.tools.facade import ToolFacade


class GovernedToolProvider(Protocol):
    """Run router-selected tools through the registered policy-first executor."""

    async def call_tool(
        self,
        tool_id: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
        task_cache_approval_provider: TaskCacheApprovalProvider | None = None,
    ) -> str: ...


class RequestTooLargeError(ValueError):
    """Raised when user input exceeds the configured request size limit."""


@dataclass(frozen=True)
class RouteSelectionResult:
    """Safe selector output for evaluations that must not execute Skills or tools."""

    request_id: str
    route: Route
    skill_id: str
    reason_code: str
    token_usage: ModelTokenUsage | None = None


@dataclass(frozen=True)
class RecoverableChatTurn:
    """Safe summary of one interrupted Chat Turn and its recovery availability."""

    turn_id: str
    started_at: datetime
    recoverable: bool
    reason_code: str | None = None


class ChatService:
    """Coordinate conversation lookup, bounded context, runtime, and activity time."""

    def __init__(
        self,
        conversations: ConversationStore,
        context_builder: ContextBuilder,
        runtime: ChatAgentRuntime,
        *,
        budget_limits: BudgetLimits | None = None,
        max_request_chars: int = 32_000,
        router: Router | None = None,
        tool_provider: GovernedToolProvider | ToolFacade | None = None,
        output_processor: OutputProcessor | None = None,
        cost_sink: CostSink | None = None,
        trace_sink: TraceSink | None = None,
        turn_lifecycle: TurnLifecycleManager | None = None,
        task_lifecycle: TaskLifecycleManager | None = None,
        session_lifecycle: SessionLifecycleManager | None = None,
        tool_outcome_lifecycle: Any | None = None,
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
        self.trace_sink = trace_sink
        self.turn_lifecycle = turn_lifecycle
        self.task_lifecycle = task_lifecycle
        self.session_lifecycle = session_lifecycle
        self.tool_outcome_lifecycle = tool_outcome_lifecycle

    async def send(
        self,
        session_id: str,
        message: str,
        *,
        confirmation_provider: AsyncConfirmationProvider | None = None,
        task_cache_approval_provider: TaskCacheApprovalProvider | None = None,
    ) -> ChatTurnResult:
        """Run one chat turn and update the session activity timestamp."""
        if len(message) > self.max_request_chars:
            raise RequestTooLargeError("Message exceeds the configured request size limit.")
        async with _lease_session(self.session_lifecycle, session_id):
            conversation = await self.conversations.get(session_id)
            if conversation is None:
                raise ConversationNotFoundError(session_id)
            pending = await self._pending_tool_outcomes(session_id)
            if pending:
                return _blocked_tool_outcome_result(session_id)
            async with self._sandbox_mode_scope(conversation.sandbox_mode):
                return await self._send_leased(
                    session_id,
                    message,
                    sandbox_mode=conversation.sandbox_mode,
                    confirmation_provider=confirmation_provider,
                    task_cache_approval_provider=task_cache_approval_provider,
                )

    async def resume_plan_run(
        self,
        session_id: str,
        plan_run_id: str,
        *,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> SkillExecutionResult:
        """Resume one interrupted Plan only after an explicit user API action."""
        if self.task_lifecycle is None:
            raise RuntimeError("Plan recovery persistence is not configured.")
        async with _lease_session(self.session_lifecycle, session_id):
            conversation = await self.conversations.get(session_id)
            if conversation is None:
                raise ConversationNotFoundError(session_id)
            if await self._pending_tool_outcomes(session_id):
                raise RuntimeError("Resolve pending chat tool outcomes before resuming a Plan.")
            plan_run = await self.task_lifecycle.get_plan_run(plan_run_id)
            if (
                plan_run is None
                or plan_run.session_id != session_id
                or plan_run.state not in {"interrupted", "awaiting_reconciliation"}
            ):
                raise ValueError(
                    "PlanRun is missing, belongs to another Session, or is not recoverable."
                )
            async with _lease_runtime(self.runtime) as runtime:
                resume = getattr(runtime, "resume_plan_run", None)
                if not callable(resume):
                    raise RuntimeError("The active Runtime cannot resume PlanRuns.")
                budget = RequestBudget.start(BudgetContext(limits=self.budget_limits))
                async with asyncio.timeout(self.budget_limits.timeout_seconds):
                    context = await self.context_builder.build(
                        session_id,
                        query=plan_run.goal,
                        state_reader=runtime,
                    )
                    context = context.model_copy(update={"budget": budget.snapshot()})
                    request_context = RequestContext(
                        snapshot=context,
                        budget=budget,
                        sandbox_mode=conversation.sandbox_mode,
                        confirmation_provider=confirmation_provider,
                        task_lifecycle=self.task_lifecycle,
                        plan_run_id=plan_run_id,
                    )
                    if not await self.task_lifecycle.claim_plan_run(plan_run_id):
                        raise ValueError("PlanRun is already claimed by another Runtime Host.")
                    await self.task_lifecycle.update_plan_run(
                        plan_run_id,
                        state="running",
                        snapshot=plan_run.snapshot,
                        updated_at=datetime.now(UTC),
                    )
                    async with self._sandbox_mode_scope(conversation.sandbox_mode):
                        if self.turn_lifecycle is None:
                            result = await resume(plan_run_id, request_context)
                        else:
                            async with self.turn_lifecycle.track(
                                request_context.snapshot.request_id,
                                session_id,
                            ):
                                result = await resume(plan_run_id, request_context)
                    if not isinstance(result, SkillExecutionResult):
                        raise TypeError("Runtime returned an invalid Plan recovery result.")
                    return result

    async def list_recoverable_turns(self, session_id: str) -> tuple[RecoverableChatTurn, ...]:
        """List interrupted Turns with safe checkpoint/runtime recovery reasons."""
        if self.turn_lifecycle is None:
            return ()
        async with _lease_session(self.session_lifecycle, session_id):
            conversation = await self.conversations.get(session_id)
            if conversation is None:
                raise ConversationNotFoundError(session_id)
            turns = await self.turn_lifecycle.list_recoverable(session_id)
            async with _lease_runtime(self.runtime) as runtime:
                reason_reader = getattr(runtime, "recoverability_reason", None)
                recoverable: list[RecoverableChatTurn] = []
                for turn in turns:
                    reason: str | None = None
                    if not callable(getattr(runtime, "resume_turn", None)):
                        reason = "runtime_recovery_unsupported"
                    elif not callable(reason_reader):
                        reason = "checkpoint_boundary_unavailable"
                    else:
                        reason = await reason_reader(session_id, turn.turn_id)
                    tool_reason_reader = getattr(runtime, "recovery_tool_reason", None)
                    if reason is None and callable(tool_reason_reader):
                        reason = await tool_reason_reader(
                            session_id,
                            turn.turn_id,
                            task_lifecycle=self.task_lifecycle,
                        )
                    recoverable.append(
                        RecoverableChatTurn(
                            turn_id=turn.turn_id,
                            started_at=turn.started_at,
                            recoverable=reason is None,
                            reason_code=reason,
                        )
                    )
                return tuple(recoverable)

    async def resume_turn(
        self,
        session_id: str,
        turn_id: str,
        *,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> ChatTurnResult:
        """Explicitly claim and resume an interrupted Runtime-native Chat Turn."""
        if self.turn_lifecycle is None:
            raise RuntimeError("Chat Turn recovery persistence is not configured.")
        async with _lease_session(self.session_lifecycle, session_id):
            conversation = await self.conversations.get(session_id)
            if conversation is None:
                raise ConversationNotFoundError(session_id)
            turns = await self.turn_lifecycle.list_recoverable(session_id)
            turn = next((item for item in turns if item.turn_id == turn_id), None)
            if turn is None:
                raise ValueError(
                    "Chat Turn is missing, belongs to another Session, or is not interrupted."
                )
            async with _lease_runtime(self.runtime) as runtime:
                resume = getattr(runtime, "resume_turn", None)
                recover_user_message = getattr(runtime, "recoverable_user_message", None)
                reason_reader = getattr(runtime, "recoverability_reason", None)
                if not callable(resume) or not callable(recover_user_message):
                    raise ValueError("The active Runtime does not support Chat Turn recovery.")
                if not callable(reason_reader):
                    raise ValueError("The active Runtime cannot validate the checkpoint boundary.")
                reason = await reason_reader(session_id, turn_id)
                if reason is not None:
                    raise ValueError(f"Chat Turn checkpoint is not recoverable: {reason}.")
                tool_reason_reader = getattr(runtime, "recovery_tool_reason", None)
                if callable(tool_reason_reader):
                    tool_reason = await tool_reason_reader(
                        session_id,
                        turn_id,
                        task_lifecycle=self.task_lifecycle,
                    )
                    if tool_reason is not None:
                        raise ValueError(f"Chat Turn tool recovery is not safe: {tool_reason}.")
                original_message = await recover_user_message(session_id, turn_id)
                budget = RequestBudget.start(BudgetContext(limits=self.budget_limits))
                async with asyncio.timeout(self.budget_limits.timeout_seconds):
                    context = await self.context_builder.build(
                        session_id,
                        query=original_message,
                        state_reader=runtime,
                    )
                    context = context.model_copy(
                        update={"request_id": turn_id, "budget": budget.snapshot()}
                    )
                    request_context = RequestContext(
                        snapshot=context,
                        budget=budget,
                        sandbox_mode=conversation.sandbox_mode,
                        confirmation_provider=confirmation_provider,
                        task_lifecycle=self.task_lifecycle,
                    )
                    if not await self.turn_lifecycle.claim_recovery(turn_id, session_id):
                        raise ValueError("Chat Turn is already claimed by another Runtime Host.")
                    if (
                        self.task_lifecycle is not None
                        and not await self.task_lifecycle.claim_runtime_invocations(
                            session_id, turn_id
                        )
                    ):
                        raise ValueError("Chat Turn tool invocation journal could not be claimed.")
                    started_at = perf_counter()
                    try:
                        async with self.turn_lifecycle.track_recovery(turn_id, session_id):
                            try:
                                async with self._sandbox_mode_scope(conversation.sandbox_mode):
                                    response = await resume(turn_id, request_context)
                            except ToolConfirmationDeniedError as error:
                                if error.decision in {
                                    ConfirmationDecision.REJECTED,
                                    ConfirmationDecision.CANCELLED,
                                }:
                                    raise TurnRecoveryCancelled(error) from error
                                raise
                    except TurnRecoveryCancelled as error:
                        if isinstance(error.cause, BaseException):
                            raise error.cause from error
                        raise
                    if not isinstance(response, ChatAgentResponse):
                        raise TypeError("Runtime returned an invalid Chat Turn recovery response.")
                    assistant_message = self.output_processor.process(response.content)
                    await self.conversations.touch(session_id)
                    try:
                        request_trace = TraceContext(trace_id=UUID(turn_id))
                    except ValueError:
                        request_trace = TraceContext()
                    if response.token_usage is not None:
                        self.cost_sink.record(
                            CostEvent(
                                trace_id=request_trace.trace_id,
                                component="model",
                                unit=CostUnit.TOKEN,
                                quantity=Decimal(response.token_usage.total_tokens),
                            )
                        )
                        if not response.budget_usage_accounted:
                            budget.consume_tokens(response.token_usage.total_tokens)
                    self._record_completed_trace(
                        route=(response.route or Route.DIRECT).value,
                        total_tokens=(
                            response.token_usage.total_tokens
                            if response.token_usage is not None
                            else None
                        ),
                        duration_ms=int((perf_counter() - started_at) * 1000),
                        trace_context=request_trace,
                    )
                    return ChatTurnResult(
                        session_id=session_id,
                        request_id=turn_id,
                        assistant_message=assistant_message,
                        route=response.route or Route.DIRECT,
                        clarification_prompt=response.clarification_prompt,
                        token_usage=response.token_usage,
                    )

    async def _send_leased(
        self,
        session_id: str,
        message: str,
        *,
        sandbox_mode: SandboxMode,
        confirmation_provider: AsyncConfirmationProvider | None,
        task_cache_approval_provider: TaskCacheApprovalProvider | None,
    ) -> ChatTurnResult:
        """Run chat after the caller has acquired its Session operation lease."""
        async with _lease_runtime(self.runtime) as runtime:
            started_at = perf_counter()
            budget = RequestBudget.start(BudgetContext(limits=self.budget_limits))
            async with asyncio.timeout(self.budget_limits.timeout_seconds):
                context = await self.context_builder.build(
                    session_id,
                    query=message,
                    state_reader=runtime,
                )
                context = context.model_copy(update={"budget": budget.snapshot()})
                request_context = RequestContext(
                    snapshot=context,
                    budget=budget,
                    sandbox_mode=sandbox_mode,
                    confirmation_provider=confirmation_provider,
                    task_cache_approval_provider=task_cache_approval_provider,
                    task_lifecycle=self.task_lifecycle,
                )
                if self.turn_lifecycle is not None:
                    async with self.turn_lifecycle.track(
                        request_context.snapshot.request_id,
                        session_id,
                    ):
                        return await self._execute_turn(
                            runtime,
                            request_context,
                            session_id,
                            message,
                            started_at,
                        )
                return await self._execute_turn(
                    runtime,
                    request_context,
                    session_id,
                    message,
                    started_at,
                )

    @asynccontextmanager
    async def _sandbox_mode_scope(self, mode: SandboxMode) -> AsyncIterator[None]:
        """Bind a Session mode through ToolFacade or the local request context."""
        scope = getattr(self.tool_provider, "sandbox_mode_scope", None)
        if callable(scope):
            async with scope(mode):
                yield
            return
        with bind_sandbox_mode(mode):
            yield

    async def _execute_turn(
        self,
        runtime: ChatAgentRuntime,
        request_context: RequestContext,
        session_id: str,
        message: str,
        started_at: float,
    ) -> ChatTurnResult:
        context = request_context.snapshot
        budget = request_context.budget
        confirmation_provider = request_context.confirmation_provider
        try:
            request_trace = TraceContext(trace_id=UUID(context.request_id))
        except ValueError:
            request_trace = TraceContext()
        decision = self.router.decide(message, context)
        response_route = decision.route
        clarification_prompt = decision.clarification_prompt
        token_usage = None
        if decision.route is Route.DIRECT:
            with trace_context_scope(request_trace):
                agent_response = await runtime.respond(message, request_context)
            assistant_message = agent_response.content
            token_usage = agent_response.token_usage
            response_route = agent_response.route or response_route
            clarification_prompt = agent_response.clarification_prompt or clarification_prompt
            if token_usage is not None:
                self.cost_sink.record(
                    CostEvent(
                        trace_id=UUID(context.request_id),
                        component="model",
                        unit=CostUnit.TOKEN,
                        quantity=Decimal(token_usage.total_tokens),
                    )
                )
                if not agent_response.budget_usage_accounted:
                    budget.consume_tokens(token_usage.total_tokens)
        elif decision.route is Route.CLARIFY:
            assistant_message = decision.clarification_prompt or "Please clarify your request."
            await _record_chat_turn(
                runtime, session_id, message, assistant_message, context.request_id
            )
        elif decision.route is Route.TOOL_CALL:
            if self.tool_provider is None or decision.tool_name is None:
                raise UnsupportedRouteError(decision.route.value)
            with trace_context_scope(request_trace):
                assistant_message = await self.tool_provider.call_tool(
                    decision.tool_name,
                    decision.tool_arguments,
                    budget=budget,
                    confirmation_provider=confirmation_provider,
                )
            await _record_chat_turn(
                runtime, session_id, message, assistant_message, context.request_id
            )
        else:
            raise UnsupportedRouteError(decision.route.value)
        assistant_message = self.output_processor.process(assistant_message)
        await self.conversations.touch(session_id)
        self._record_completed_trace(
            route=response_route.value,
            total_tokens=token_usage.total_tokens if token_usage is not None else None,
            duration_ms=int((perf_counter() - started_at) * 1000),
            trace_context=request_trace,
        )
        return ChatTurnResult(
            session_id=session_id,
            request_id=context.request_id,
            assistant_message=assistant_message,
            route=response_route,
            clarification_prompt=clarification_prompt,
            token_usage=token_usage,
        )

    async def select_route_only(
        self,
        session_id: str,
        message: str,
        *,
        evaluation_messages: tuple[ConversationMessage, ...] | None = None,
        evaluation_session_summary: str | None = None,
        evaluation_long_term_memory: str | None = None,
    ) -> RouteSelectionResult:
        """Run top-level routing without invoking a Skill executor or any tool."""
        if len(message) > self.max_request_chars:
            raise RequestTooLargeError("Message exceeds the configured request size limit.")
        async with _lease_session(self.session_lifecycle, session_id):
            if await self._pending_tool_outcomes(session_id):
                return RouteSelectionResult(
                    request_id=str(uuid4()),
                    route=Route.CLARIFY,
                    skill_id="builtin/clarify",
                    reason_code="unresolved_tool_outcome",
                )
            return await self._select_route_only_leased(
                session_id,
                message,
                evaluation_messages=evaluation_messages,
                evaluation_session_summary=evaluation_session_summary,
                evaluation_long_term_memory=evaluation_long_term_memory,
            )

    async def _pending_tool_outcomes(self, session_id: str) -> tuple[Any, ...]:
        """Read the durable Session gate before any routing or model work."""
        if self.tool_outcome_lifecycle is None:
            return ()
        return cast(tuple[Any, ...], await self.tool_outcome_lifecycle.pending(session_id))

    async def _select_route_only_leased(
        self,
        session_id: str,
        message: str,
        *,
        evaluation_messages: tuple[ConversationMessage, ...] | None,
        evaluation_session_summary: str | None,
        evaluation_long_term_memory: str | None,
    ) -> RouteSelectionResult:
        """Run route evaluation after acquiring the Session operation lease."""
        async with _lease_runtime(self.runtime) as runtime:
            budget = RequestBudget.start(BudgetContext(limits=self.budget_limits))
            async with asyncio.timeout(self.budget_limits.timeout_seconds):
                context = await self.context_builder.build(
                    session_id,
                    query=message,
                    state_reader=runtime,
                )
                context_update: dict[str, Any] = {
                    "budget": budget.snapshot(),
                    "session_summary": None,
                    "long_term_memory": None,
                }
                if evaluation_messages is not None:
                    context_update.update(
                        {
                            "messages": evaluation_messages,
                            "session_summary": evaluation_session_summary,
                            "long_term_memory": evaluation_long_term_memory,
                        }
                    )
                context = context.model_copy(update=context_update)
                try:
                    request_trace = TraceContext(trace_id=UUID(context.request_id))
                except ValueError:
                    request_trace = TraceContext()
                decision = self.router.decide(message, context)
                if decision.route is Route.CLARIFY:
                    return RouteSelectionResult(
                        request_id=context.request_id,
                        route=Route.CLARIFY,
                        skill_id="builtin/clarify",
                        reason_code="deterministic_router_clarification",
                    )
                if decision.route is not Route.DIRECT:
                    raise UnsupportedRouteError(
                        "route-only evaluation supports selector routes, not direct tool commands"
                    )
                selector = getattr(runtime, "selector", None)
                if selector is None:
                    raise UnsupportedRouteError("runtime does not expose a Skill selector")
                with trace_context_scope(request_trace):
                    selection = await selector.select(message, RequestContext(context, budget))
            return RouteSelectionResult(
                request_id=context.request_id,
                route=_selected_route(selection.skill_id),
                skill_id=selection.skill_id,
                reason_code=selection.reason_code,
                token_usage=selection.token_usage,
            )

    def _record_completed_trace(
        self,
        *,
        route: str,
        total_tokens: int | None,
        duration_ms: int,
        trace_context: TraceContext,
    ) -> None:
        """Record routing and usage metadata without conversation content."""
        if self.trace_sink is None:
            return
        try:
            attributes: dict[str, str | int | float | bool] = {
                "status": "completed",
                "route": route,
                "duration_ms": duration_ms,
            }
            if total_tokens is not None:
                attributes["total_tokens"] = total_tokens
            self.trace_sink.record(
                TraceEvent(
                    context=trace_context,
                    name="chat.turn",
                    attributes=attributes,
                )
            )
        except Exception:
            # Trace storage must never change a completed chat response.
            return

    async def history(
        self, session_id: str
    ) -> tuple[ConversationSummary, tuple[ConversationMessage, ...]]:
        """Return one session's messages and metadata."""
        async with _lease_session(self.session_lifecycle, session_id):
            conversation = await self.conversations.get(session_id)
            if conversation is None:
                raise ConversationNotFoundError(session_id)
            async with _lease_runtime(self.runtime) as runtime:
                messages = await runtime.read_messages(session_id)
            return conversation, messages


class UnsupportedRouteError(RuntimeError):
    """Raised when a route has not yet been connected to an execution path."""


def _blocked_tool_outcome_result(session_id: str) -> ChatTurnResult:
    """Return a local recovery prompt without starting a Turn or invoking a model."""
    return ChatTurnResult(
        session_id=session_id,
        request_id=str(uuid4()),
        assistant_message=(
            "上一次可能产生副作用的工具调用结果尚未核实。为避免重复执行，当前会话暂时不能继续；"
            "请先核实该调用是否生效，并提交实际结果或确认未生效。"
        ),
        route=Route.CLARIFY,
        clarification_prompt="请先核实未决工具调用的结果。",
    )


@asynccontextmanager
async def _lease_runtime(runtime: ChatAgentRuntime) -> AsyncIterator[ChatAgentRuntime]:
    """Use a provider lease for a complete operation when the runtime has one."""
    lease = getattr(runtime, "lease", None)
    if callable(lease):
        async with lease() as leased_runtime:
            yield cast(ChatAgentRuntime, leased_runtime)
        return
    yield runtime


async def _record_chat_turn(
    runtime: ChatAgentRuntime,
    session_id: str,
    user_message: str,
    assistant_message: str,
    turn_id: str,
) -> None:
    """Persist route-only turns with a Turn association when the Runtime supports it."""
    record_associated = getattr(runtime, "record_associated_turn", None)
    if callable(record_associated):
        await record_associated(
            session_id,
            user_message,
            assistant_message,
            turn_id=turn_id,
        )
        return
    await runtime.record_turn(session_id, user_message, assistant_message)


@asynccontextmanager
async def _lease_session(
    lifecycle: SessionLifecycleManager | None, session_id: str
) -> AsyncIterator[None]:
    """Use a Session operation lease when the Runtime provider is configured."""
    if lifecycle is None:
        yield
        return
    async with lifecycle.lease(session_id):
        yield


def _selected_route(skill_id: str) -> Route:
    if skill_id == "builtin/clarify":
        return Route.CLARIFY
    if skill_id == "builtin/orchestrator_plan":
        return Route.PLAN_EXECUTE
    if skill_id == "builtin/direct_answer":
        return Route.DIRECT
    return Route.SKILL_EXECUTE


__all__ = [
    "ChatService",
    "GovernedToolProvider",
    "RequestTooLargeError",
    "RouteSelectionResult",
    "UnsupportedRouteError",
]
