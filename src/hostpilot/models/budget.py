"""Budget-aware model calls shared by Agent, Skill, routing, and Plan adapters."""

from __future__ import annotations

from dataclasses import replace

from hostpilot.budget import RequestBudget
from hostpilot.models.contracts import ModelRequest, ModelResponse
from hostpilot.models.provider import ModelProvider


async def complete_with_budget(
    provider: ModelProvider,
    request: ModelRequest,
    budget: RequestBudget,
) -> ModelResponse:
    """Serialize one request, cap its output allowance, and account provider usage."""
    async with budget.model_call_lock:
        budget.ensure_available()
        snapshot = budget.snapshot()
        remaining_tokens = budget.remaining_tokens
        max_output_tokens = request.max_output_tokens
        if max_output_tokens is None or max_output_tokens > remaining_tokens:
            max_output_tokens = remaining_tokens
        remaining_seconds = snapshot.limits.timeout_seconds - snapshot.usage.elapsed_seconds
        timeout_seconds = (
            remaining_seconds
            if request.timeout_seconds is None
            else min(request.timeout_seconds, remaining_seconds)
        )
        bounded_request = replace(
            request,
            max_output_tokens=max_output_tokens,
            timeout_seconds=timeout_seconds,
        )
        response = await provider.complete(bounded_request)
        accounted = response.token_usage is not None
        if response.token_usage is not None:
            budget.consume_tokens(response.token_usage.total_tokens)
        return replace(response, budget_usage_accounted=accounted)


__all__ = ["complete_with_budget"]
