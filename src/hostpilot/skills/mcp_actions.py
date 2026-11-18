"""Expose registered MCP tools as policy-governed YAML Skill actions."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

from hostpilot.budget import RequestBudget
from hostpilot.contracts import ToolDescriptor
from hostpilot.skills.runtime import (
    SkillSideEffect,
    StepActionCall,
    StepActionHandler,
    StepActionRegistration,
    StepActionRegistry,
    StepActionResult,
)
from hostpilot.skills.yaml_registry import SideEffectLevel
from hostpilot.tools.executor import AsyncConfirmationProvider


class GovernedMCPProvider(Protocol):
    def descriptors(self) -> tuple[ToolDescriptor, ...]: ...

    async def call_tool(
        self,
        name: str,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str: ...

    async def prepare_existing_file_write(
        self,
        arguments: Mapping[str, Any],
        *,
        budget: RequestBudget,
        confirmation_provider: AsyncConfirmationProvider | None = None,
    ) -> str: ...


def build_mcp_step_actions(provider: GovernedMCPProvider) -> StepActionRegistry:
    """Register every existing tool through the provider's Tool Executor chain."""
    actions = StepActionRegistry()
    for descriptor in provider.descriptors():
        if descriptor.name == "write_existing_text_file" and hasattr(
            provider, "prepare_existing_file_write"
        ):
            risk = SideEffectLevel.REVERSIBLE
            prepare = _prepare_existing_write(provider)
        else:
            risk = _side_effect_level(descriptor)
            prepare = None
        actions.register(
            StepActionRegistration(
                action=descriptor.name,
                side_effect_level=risk,
                handler=_handler(provider, descriptor.name, risk),
                prepare_compensation=prepare,
                tool_id=descriptor.name,
            )
        )
    return actions


def _side_effect_level(descriptor: ToolDescriptor) -> SideEffectLevel:
    if descriptor.tool_type.value == "retrieval" or descriptor.name == "read_text_file":
        return SideEffectLevel.READ_ONLY
    # ToolDescriptor has no finer mutation contract. Unknown actions fail safe.
    return SideEffectLevel.IRREVERSIBLE


def _handler(
    provider: GovernedMCPProvider,
    tool_name: str,
    side_effect_level: SideEffectLevel,
) -> StepActionHandler:
    async def execute(call: StepActionCall) -> StepActionResult:
        overlap = call.params.keys() & call.inputs.keys()
        if overlap:
            raise ValueError("Skill action params and mapped inputs cannot overlap.")
        arguments = {**call.params, **call.inputs}
        output = await provider.call_tool(
            tool_name,
            arguments,
            budget=call.budget,
            confirmation_provider=call.confirmation_provider,
        )
        side_effect = None
        if side_effect_level is not SideEffectLevel.READ_ONLY:
            side_effect = SkillSideEffect(
                action=tool_name,
                level=side_effect_level,
                idempotency_key=call.idempotency_key,
                compensation_handle=(
                    call.compensation_handle
                    if side_effect_level is SideEffectLevel.REVERSIBLE
                    else None
                ),
            )
        return StepActionResult(output=output, side_effect=side_effect, tool_id=tool_name)

    return execute


def _prepare_existing_write(
    provider: GovernedMCPProvider,
) -> Callable[[StepActionCall], Awaitable[str]]:
    async def prepare(call: StepActionCall) -> str:
        overlap = call.params.keys() & call.inputs.keys()
        if overlap:
            raise ValueError("Skill action params and mapped inputs cannot overlap.")
        return await provider.prepare_existing_file_write(
            {**call.params, **call.inputs},
            budget=call.budget,
            confirmation_provider=call.confirmation_provider,
        )

    return prepare


__all__ = ["GovernedMCPProvider", "build_mcp_step_actions"]
