"""Adapt Skill selection and execution to the chat AgentRuntime contract."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Literal, Protocol
from uuid import UUID

from hostpilot.budget import RequestBudget
from hostpilot.contracts import ContextSnapshot, ConversationMessage, ModelTokenUsage, Route
from hostpilot.observability import (
    TraceContext,
    TraceEvent,
    TraceSink,
    child_trace_context,
    trace_context_scope,
)
from hostpilot.orchestration.runtime import AgentResponse, AgentRuntime
from hostpilot.skills.runtime import (
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
    SkillRuntime,
)
from hostpilot.tools.executor import AsyncConfirmationProvider


@dataclass(frozen=True)
class SkillSelection:
    """Validated target and schema-shaped input returned by the top-level selector."""

    skill_id: str
    payload: Mapping[str, Any]
    reason_code: str
    token_usage: ModelTokenUsage | None = None
    execution_mode: Literal["yaml", "deepagents"] | None = None
    candidate_skill_ids: tuple[str, ...] = ()


class SkillSelectionPort(Protocol):
    """Select one builtin or business Skill without granting tool permissions."""

    async def select(
        self, message: str, context: ContextSnapshot, *, budget: RequestBudget
    ) -> SkillSelection | None: ...


class SkillRuntimeAgentAdapter:
    """Route selected requests through SkillRuntime and persist them in chat history."""

    def __init__(
        self,
        chat_runtime: AgentRuntime,
        skill_runtime: SkillRuntime,
        selector: SkillSelectionPort,
        *,
        trace_sink: TraceSink | None = None,
    ) -> None:
        self.chat_runtime = chat_runtime
        self.skill_runtime = skill_runtime
        self.selector = selector
        self.trace_sink = trace_sink

    def set_execution_mode(self, skill_id: str, execution_mode: str) -> None:
        """Update the shared YAML and DeepAgents package filters in place."""
        preferences = self.skill_runtime.registry.execution_modes
        preferences[skill_id] = execution_mode

    def configure_harness(self, configuration: Mapping[str, Any]) -> None:
        """Apply live routing and Plan sub-agent controls without rebuilding the graph."""
        configure_selector = getattr(self.selector, "configure_harness", None)
        if callable(configure_selector):
            configure_selector(
                minimum_business_confidence=configuration["routing_minimum_business_confidence"],
                minimum_business_margin=configuration["routing_minimum_business_margin"],
                minimum_plan_candidate_confidence=configuration[
                    "routing_minimum_plan_candidate_confidence"
                ],
                max_plan_candidates=configuration["routing_max_plan_candidates"],
                max_business_candidates=configuration["routing_max_business_candidates"],
            )
        fallback = getattr(self.skill_runtime, "plan_fallback", None)
        plan_executor = getattr(fallback, "plan_executor", None)
        configure_plan_executor = getattr(plan_executor, "configure_harness", None)
        if callable(configure_plan_executor):
            configure_plan_executor(max_tasks=configuration["plan_max_tasks"])
        task_executor = getattr(plan_executor, "task_executor", None)
        configure_task_executor = getattr(task_executor, "configure_harness", None)
        if callable(configure_task_executor):
            configure_task_executor(
                max_turns=configuration["plan_tool_agent_max_turns"],
                candidate_limit=configuration["plan_tool_candidate_limit"],
            )

    async def read_messages(self, conversation_id: str) -> tuple[ConversationMessage, ...]:
        return await self.chat_runtime.read_messages(conversation_id)

    async def respond(
        self,
        message: str,
        context: ContextSnapshot,
        *,
        budget: RequestBudget | None = None,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> AgentResponse:
        selection = await self.selector.select(message, context, budget=budget) if budget else None
        if selection is None or budget is None:
            return await self.chat_runtime.respond(
                message,
                context,
                budget=budget,
                confirmation_provider=confirmation_provider,
            )

        if self.trace_sink is None:
            return await self._execute_selected(
                selection,
                message,
                context,
                budget,
                confirmation_provider,
            )
        started_at = perf_counter()
        try:
            span_context = child_trace_context(trace_id=UUID(context.request_id))
        except ValueError:
            span_context = child_trace_context()
        with trace_context_scope(span_context):
            try:
                response = await self._execute_selected(
                    selection,
                    message,
                    context,
                    budget,
                    confirmation_provider,
                )
            except asyncio.CancelledError:
                self._record_skill_span(
                    span_context,
                    selection,
                    status="cancelled",
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    reason_code="execution_cancelled",
                )
                raise
            except Exception as error:
                self._record_skill_span(
                    span_context,
                    selection,
                    status="failed",
                    duration_ms=int((perf_counter() - started_at) * 1000),
                    reason_code=type(error).__name__,
                )
                raise
            self._record_skill_span(
                span_context,
                selection,
                status="fallback" if response.route is Route.PLAN_EXECUTE else "completed",
                duration_ms=int((perf_counter() - started_at) * 1000),
                reason_code=(
                    str(selection.payload.get("evaluation_failure_reason"))
                    if response.route is Route.PLAN_EXECUTE
                    and selection.payload.get("evaluation_failure_reason")
                    else selection.reason_code
                    if response.route is Route.PLAN_EXECUTE
                    else None
                ),
            )
        return response

    async def _execute_selected(
        self,
        selection: SkillSelection,
        message: str,
        context: ContextSnapshot,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None,
    ) -> AgentResponse:
        if selection.execution_mode == "deepagents":
            respond_with_skill = getattr(self.chat_runtime, "respond_with_skill", None)
            if not callable(respond_with_skill):
                return await self._fallback_to_plan(
                    selection,
                    message,
                    context,
                    budget,
                    confirmation_provider,
                    reason_code="deepagents_executor_unavailable",
                )
            try:
                response = await respond_with_skill(
                    message,
                    context,
                    skill_id=selection.skill_id,
                    budget=budget,
                    confirmation_provider=confirmation_provider,
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                return await self._fallback_to_plan(
                    selection,
                    message,
                    context,
                    budget,
                    confirmation_provider,
                    reason_code=f"deepagents_execution_{type(error).__name__}",
                    outcome_uncertain=True,
                )
            if response.token_usage is not None and not response.budget_usage_accounted:
                budget.consume_tokens(response.token_usage.total_tokens)
            assess_result = getattr(self.selector, "assess_deepagents_result", None)
            if callable(assess_result):
                try:
                    accepted, reason_code, assessment_usage = await assess_result(
                        selection.skill_id,
                        message,
                        response.content,
                        context,
                        budget=budget,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    return await self._fallback_to_plan(
                        selection,
                        message,
                        context,
                        budget,
                        confirmation_provider,
                        reason_code=f"deepagents_result_assessment_{type(error).__name__}",
                        prior_usage=response.token_usage,
                        outcome_uncertain=True,
                    )
                if not accepted:
                    return await self._fallback_to_plan(
                        selection,
                        message,
                        context,
                        budget,
                        confirmation_provider,
                        reason_code=reason_code,
                        prior_usage=_sum_token_usage(response.token_usage, assessment_usage),
                        outcome_uncertain=True,
                    )
            else:
                assessment_usage = None
            return AgentResponse(
                content=response.content,
                token_usage=_sum_token_usage(
                    selection.token_usage,
                    _sum_token_usage(response.token_usage, assessment_usage),
                ),
                budget_usage_accounted=True,
                route=Route.SKILL_EXECUTE,
            )

        try:
            result = await self.skill_runtime.execute(
                SkillExecutionRequest(
                    skill_id=selection.skill_id,
                    payload=selection.payload,
                    context=context,
                    budget=budget,
                    idempotency_key=context.request_id,
                    confirmation_provider=confirmation_provider,
                    original_goal=message,
                    candidate_skill_ids=selection.candidate_skill_ids,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            return await self._fallback_to_plan(
                selection,
                message,
                context,
                budget,
                confirmation_provider,
                reason_code=f"skill_execution_{type(error).__name__}",
                outcome_uncertain=True,
            )
        fallback_already_attempted = isinstance(result.output, Mapping) and isinstance(
            result.output.get("evaluation_failure_reason"), str
        )
        if (
            result.status is SkillExecutionStatus.FAILED
            and selection.skill_id != "builtin/orchestrator_plan"
            and not fallback_already_attempted
        ):
            failure_reason: str | None = None
            if result.snapshot is not None and result.snapshot.failure_code:
                failure_reason = result.snapshot.failure_code
            elif isinstance(result.output, Mapping):
                output_reason = result.output.get("error_code")
                if isinstance(output_reason, str):
                    failure_reason = output_reason
            return await self._fallback_to_plan(
                selection,
                message,
                context,
                budget,
                confirmation_provider,
                reason_code=failure_reason or "skill_execution_failed",
                prior_usage=result.token_usage,
                outcome_uncertain=bool(result.snapshot and result.snapshot.side_effects),
            )
        response = _render_result(
            result,
            evaluation_failure_reason=(
                str(selection.payload["evaluation_failure_reason"])
                if selection.skill_id == "builtin/orchestrator_plan"
                and selection.payload.get("evaluation_failure_reason")
                else None
            ),
        )
        if context.conversation_id is None:
            raise ValueError("Skill-selected chat context must contain a conversation_id.")
        await self.chat_runtime.record_turn(context.conversation_id, message, response)
        return AgentResponse(
            content=response,
            token_usage=_sum_token_usage(selection.token_usage, result.token_usage),
            budget_usage_accounted=True,
            route=_response_route(selection.skill_id, result.status),
            clarification_prompt=result.clarification_prompt,
        )

    async def _fallback_to_plan(
        self,
        selection: SkillSelection,
        message: str,
        context: ContextSnapshot,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None,
        *,
        reason_code: str,
        prior_usage: ModelTokenUsage | None = None,
        outcome_uncertain: bool = False,
    ) -> AgentResponse:
        """Continue an unsuccessful Skill attempt through the built-in Plan executor."""
        plan_payload: dict[str, Any] = {
            "task_description": message,
            "evaluation_failure_reason": reason_code,
            "failed_skill_id": selection.skill_id,
            "previous_execution_outcome_uncertain": outcome_uncertain,
        }
        if selection.candidate_skill_ids:
            plan_payload["candidate_skill_ids"] = list(selection.candidate_skill_ids)
        result = await self.skill_runtime.execute(
            SkillExecutionRequest(
                skill_id="builtin/orchestrator_plan",
                payload=plan_payload,
                context=context,
                budget=budget,
                idempotency_key=context.request_id,
                confirmation_provider=confirmation_provider,
                original_goal=message,
                candidate_skill_ids=selection.candidate_skill_ids,
            )
        )
        rendered = _render_result(result, evaluation_failure_reason=reason_code)
        if context.conversation_id is not None:
            await self.chat_runtime.record_turn(context.conversation_id, message, rendered)
        return AgentResponse(
            content=rendered,
            token_usage=_sum_token_usage(
                _sum_token_usage(selection.token_usage, prior_usage), result.token_usage
            ),
            budget_usage_accounted=True,
            route=_response_route("builtin/orchestrator_plan", result.status),
            clarification_prompt=result.clarification_prompt,
        )

    def _record_skill_span(
        self,
        span_context: TraceContext,
        selection: SkillSelection,
        *,
        status: str,
        duration_ms: int,
        reason_code: str | None = None,
    ) -> None:
        if self.trace_sink is None:
            return
        attributes: dict[str, str | int | float | bool] = {
            "skill_id": selection.skill_id,
            "execution_mode": selection.execution_mode or "yaml",
            "status": status,
            "duration_ms": duration_ms,
        }
        if reason_code is not None:
            attributes["reason_code"] = reason_code
        try:
            self.trace_sink.record(
                TraceEvent(context=span_context, name="skill.execute", attributes=attributes)
            )
        except Exception:
            return

    async def record_turn(
        self,
        conversation_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        await self.chat_runtime.record_turn(conversation_id, user_message, assistant_message)

    async def aclose(self) -> None:
        close = getattr(self.chat_runtime, "aclose", None)
        if close is not None:
            await close()


def _render_result(
    result: SkillExecutionResult, *, evaluation_failure_reason: str | None = None
) -> str:
    if result.status is SkillExecutionStatus.CLARIFICATION and result.clarification_prompt:
        return result.clarification_prompt
    if result.status is SkillExecutionStatus.HUMAN_REVIEW:
        return "该 Skill 已执行但需要人工复核，请检查执行状态后再继续。"
    if result.status is SkillExecutionStatus.FAILED:
        error_code = result.output.get("error_code") if isinstance(result.output, Mapping) else None
        rendered_error = f"Skill 执行失败：{error_code or 'unknown_error'}"
        reason = (
            result.output.get("evaluation_failure_reason")
            if isinstance(result.output, Mapping)
            else None
        ) or evaluation_failure_reason
        if isinstance(reason, str):
            rendered_error += f"\n\n处理说明：Skill 评估未通过（{reason}），已转交 Plan 处理。"
        return rendered_error
    if isinstance(result.output, str):
        rendered = result.output
    elif isinstance(result.output, Mapping):
        rendered = next(
            (
                value
                for key in ("message", "answer", "summary", "text")
                if isinstance((value := result.output.get(key)), str)
            ),
            json.dumps(result.output, ensure_ascii=False, default=str),
        )
    else:
        rendered = json.dumps(result.output, ensure_ascii=False, default=str)
    reason = evaluation_failure_reason
    if reason is None and isinstance(result.output, Mapping):
        candidate_reason = result.output.get("evaluation_failure_reason")
        reason = candidate_reason if isinstance(candidate_reason, str) else None
    if reason:
        rendered = (
            f"{rendered}\n\n"
            f"处理说明：Skill 路由或执行评估未通过（{reason}），已交由 Plan 继续处理。"
        )
    return rendered


def _sum_token_usage(
    left: ModelTokenUsage | None, right: ModelTokenUsage | None
) -> ModelTokenUsage | None:
    if left is None:
        return right
    if right is None:
        return left
    return ModelTokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
    )


def _response_route(skill_id: str, status: SkillExecutionStatus) -> Route:
    if status is SkillExecutionStatus.CLARIFICATION or skill_id == "builtin/clarify":
        return Route.CLARIFY
    if skill_id == "builtin/orchestrator_plan":
        return Route.PLAN_EXECUTE
    if skill_id.startswith(("business/", "community/")):
        return Route.SKILL_EXECUTE
    return Route.DIRECT


__all__ = ["SkillRuntimeAgentAdapter", "SkillSelection", "SkillSelectionPort"]
