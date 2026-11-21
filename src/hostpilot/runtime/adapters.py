"""Adapters from existing HostPilot services to Runtime execution Ports."""

from __future__ import annotations

from hostpilot.models import ModelProvider, ModelRequest, ModelResponse, ModelToolCall
from hostpilot.models.budget import complete_with_budget
from hostpilot.runtime.context import RequestContext
from hostpilot.tools.facade import ToolFacade


class ModelProviderRuntimeAdapter:
    """Adapt a configured ModelProvider while retaining shared budget enforcement."""

    def __init__(self, provider: ModelProvider) -> None:
        self.provider = provider

    async def complete(
        self,
        request_context: RequestContext,
        request: ModelRequest,
    ) -> ModelResponse:
        return await complete_with_budget(self.provider, request, request_context.budget)


class ToolFacadeRuntimeAdapter:
    """Adapt the stable governed ToolFacade to the Runtime Tool Port."""

    def __init__(self, tool_facade: ToolFacade) -> None:
        self.tool_facade = tool_facade

    async def execute(
        self,
        request_context: RequestContext,
        tool_call: ModelToolCall,
    ) -> str:
        return await self.tool_facade.call_tool(
            tool_call.name,
            tool_call.arguments,
            budget=request_context.budget,
            confirmation_provider=request_context.confirmation_provider,
        )


__all__ = ["ModelProviderRuntimeAdapter", "ToolFacadeRuntimeAdapter"]
