"""Compose configured Model, Skill, Plan, and Chat Runtime components."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from hostpilot.config import Settings
from hostpilot.contracts import ToolDescriptor
from hostpilot.models import ModelToolCall, ModelToolDefinition
from hostpilot.models.factory import ModelProviderRegistry
from hostpilot.observability import TraceSink
from hostpilot.orchestration.runtime import create_default_chat_agent_runtime_registry
from hostpilot.orchestration.runtime_plugins import (
    ChatAgentRuntimePluginContext,
    ChatAgentRuntimeRegistry,
)
from hostpilot.runtime import DefaultRuntimeExecutionLoop, RequestContext
from hostpilot.runtime.adapters import ModelProviderRuntimeAdapter, ToolFacadeRuntimeAdapter
from hostpilot.skills import SkillRegistry
from hostpilot.skills.plan_plugins import PlanComponentRegistry, PlanComponentSelection

if TYPE_CHECKING:
    from hostpilot.orchestration.runtime import ChatAgentRuntime
    from hostpilot.skills.compensation import CancellationSink, CompensationRegistry


def build_configured_chat_agent_runtime(
    settings: Settings,
    *,
    checkpointer: Any,
    tools: Sequence[Any] = (),
    tool_provider: Any | None = None,
    memory_controller: Any | None = None,
    compensation_registry: CompensationRegistry | None = None,
    cancellation_sink: CancellationSink | None = None,
    skill_execution_modes: Mapping[str, str] | None = None,
    trace_sink: TraceSink | None = None,
    plan_component_registry: PlanComponentRegistry | None = None,
    chat_agent_runtime_registry: ChatAgentRuntimeRegistry | None = None,
    model_provider_registry: ModelProviderRegistry | None = None,
) -> ChatAgentRuntime:
    """Build one unpublished Chat Runtime candidate from explicit dependencies."""
    from hostpilot.models.factory import create_model_runtime_components
    from hostpilot.orchestration.skill_runtime import SkillEngineAgentAdapter
    from hostpilot.routing.skill_selector import ModelProviderSkillSelector
    from hostpilot.skills.compensation import CompensationRegistry
    from hostpilot.skills.deepagents_adapter import DeepAgentsSkillAdapter
    from hostpilot.skills.llm_actions import register_llm_completion_action
    from hostpilot.skills.mcp_actions import build_mcp_step_actions
    from hostpilot.skills.model_plan import (
        LLMSkillAssessor,
        ModelPlanFallback,
        build_model_plan_executor,
    )
    from hostpilot.skills.model_provider_adapter import ProviderSingleTurnLLM
    from hostpilot.skills.runtime import (
        BuiltinExecutorRegistry,
        SkillEngine,
        register_core_builtin_skills,
    )
    from hostpilot.skills.yaml_registry import YamlSkillRegistry
    from hostpilot.tools.facade import ToolFacade

    model_components = create_model_runtime_components(settings, registry=model_provider_registry)
    model_provider = model_components.provider
    if tool_provider is not None and not callable(getattr(tool_provider, "descriptors", None)):
        tool_provider = ToolFacade(tool_provider)
    runtime_tool_definitions = _runtime_tool_definitions(tool_provider)
    runtime_execution_loop = DefaultRuntimeExecutionLoop(
        ModelProviderRuntimeAdapter(model_provider),
        ToolFacadeRuntimeAdapter(tool_provider)
        if tool_provider is not None
        else _UnavailableRuntimeToolPort(),
    )
    compensation_handlers = compensation_registry or CompensationRegistry()
    additional_roots = (Path(__file__).resolve().parents[1] / "skill_packages",)
    legacy_registry = SkillRegistry(settings.skills_dir, additional_roots=additional_roots)
    registered_tool_ids = (
        tuple(descriptor.name for descriptor in _tool_descriptors(tool_provider))
        if tool_provider is not None
        else ()
    )
    yaml_registry_options: dict[str, Any] = {
        "additional_roots": additional_roots,
        "registered_tool_ids": registered_tool_ids,
    }
    unfiltered_yaml_registry = YamlSkillRegistry(legacy_registry.root, **yaml_registry_options)
    yaml_by_directory = {
        package.source_directory.resolve(): package
        for package in unfiltered_yaml_registry.discover().skills
    }
    markdown_by_directory = {
        package.source_directory.resolve(): package for package in legacy_registry.discover().skills
    }
    skill_ids_by_name: dict[str, str] = {}
    execution_modes: dict[str, str] = {}
    saved_execution_modes = skill_execution_modes or {}
    for package in yaml_by_directory.values():
        markdown_package = markdown_by_directory.get(package.source_directory.resolve())
        available_modes = {"yaml", "markdown", "both"} if markdown_package is not None else {"yaml"}
        preferred = saved_execution_modes.get(package.skill_id, "yaml")
        execution_modes[package.skill_id] = preferred if preferred in available_modes else "yaml"
    for markdown_package in markdown_by_directory.values():
        yaml_package = yaml_by_directory.get(markdown_package.source_directory.resolve())
        skill_id = (
            yaml_package.skill_id if yaml_package is not None else f"legacy/{markdown_package.name}"
        )
        skill_ids_by_name[markdown_package.name] = skill_id
        if yaml_package is None:
            execution_modes[skill_id] = "markdown"
    deepagents_skills = DeepAgentsSkillAdapter(
        legacy_registry,
        execution_modes=execution_modes,
        skill_ids_by_name=skill_ids_by_name,
    )
    chat_agent_runtime_registry = (
        chat_agent_runtime_registry or create_default_chat_agent_runtime_registry()
    )
    chat_agent_runtime = chat_agent_runtime_registry.create(
        settings.chat_runtime_plugin,
        ChatAgentRuntimePluginContext(
            model=model_components.agent_chat_model,
            checkpointer=checkpointer,
            skills=deepagents_skills,
            tools=tools,
            runtime_execution_loop=runtime_execution_loop,
            runtime_tool_definitions=runtime_tool_definitions,
            runtime_tool_side_effects={
                descriptor.name: descriptor.side_effect
                for descriptor in _tool_descriptors(tool_provider)
            }
            if tool_provider is not None
            else {},
            runtime_tool_retry_classes={
                descriptor.name: descriptor.effective_retry_class
                for descriptor in _tool_descriptors(tool_provider)
            }
            if tool_provider is not None
            else {},
            runtime_idempotency_windows={
                descriptor.name: descriptor.idempotency_window_seconds
                for descriptor in _tool_descriptors(tool_provider)
                if descriptor.idempotency_window_seconds is not None
            }
            if tool_provider is not None
            else {},
            runtime_idempotency_business_fields={
                descriptor.name: descriptor.idempotency_business_fields
                for descriptor in _tool_descriptors(tool_provider)
                if descriptor.idempotency_business_fields
            }
            if tool_provider is not None
            else {},
            runtime_allowed_tool_ids=frozenset(
                definition.name for definition in runtime_tool_definitions
            ),
        ),
    )
    skill_registry = YamlSkillRegistry(
        legacy_registry.root,
        **yaml_registry_options,
        execution_modes=execution_modes,
    )
    tool_descriptors = (
        {descriptor.name: descriptor for descriptor in _tool_descriptors(tool_provider)}
        if tool_provider is not None
        else {}
    )
    step_actions = build_mcp_step_actions(tool_provider) if tool_provider is not None else None
    if tool_provider is not None and hasattr(tool_provider, "compensate_existing_file_write"):

        async def compensate_existing_file_write(call: Any) -> None:
            if call.compensation_handle is None:
                raise ValueError("Existing-file compensation handle is unavailable.")
            await tool_provider.compensate_existing_file_write(
                call.compensation_handle,
                budget=call.budget,
                confirmation_provider=call.confirmation_provider,
            )

        compensation_handlers.register(
            "write_existing_text_file",
            compensate_existing_file_write,
            safe_for_uncertain_effect=True,
        )
    if step_actions is None:
        from hostpilot.skills.runtime import StepActionRegistry

        step_actions = StepActionRegistry()
    register_llm_completion_action(step_actions, model_provider)

    builtins = BuiltinExecutorRegistry()
    assessors = LLMSkillAssessor(model_provider)
    skill_runtime = SkillEngine(
        skill_registry,
        step_actions,
        builtins,
        assessors,
        assessors,
        compensation_registry=compensation_handlers,
        cancellation_sink=cancellation_sink,
    )
    plan_executor = build_model_plan_executor(
        model_provider,
        skills=skill_registry,
        skill_runtime=skill_runtime,
        tool_provider=tool_provider,
        tool_descriptors=tool_descriptors,
        memory_controller=memory_controller,
        compensation_registry=compensation_handlers,
        cancellation_sink=cancellation_sink,
        trace_sink=trace_sink,
        component_registry=plan_component_registry,
        component_selection=PlanComponentSelection(
            planner=settings.plan_planner_plugin,
            validator=settings.plan_validator_plugin,
            task_executor=settings.plan_task_executor_plugin,
            verifier=settings.plan_verifier_plugin,
            replanner=settings.plan_replanner_plugin,
        ),
        plan_max_tasks=settings.plan_max_tasks,
        plan_tool_agent_max_turns=settings.plan_tool_agent_max_turns,
        plan_tool_candidate_limit=settings.plan_tool_candidate_limit,
    )
    skill_runtime.plan_fallback = ModelPlanFallback(plan_executor)
    register_core_builtin_skills(
        builtins,
        llm=ProviderSingleTurnLLM(model_provider),
        plan_executor=plan_executor,
    )
    selector = ModelProviderSkillSelector(
        skill_registry,
        model_provider,
        fallback_provider=model_provider,
        markdown_registry=legacy_registry,
        markdown_skill_ids_by_name=skill_ids_by_name,
        execution_modes=execution_modes,
        minimum_business_confidence=settings.routing_minimum_business_confidence,
        minimum_business_margin=settings.routing_minimum_business_margin,
        minimum_plan_candidate_confidence=settings.routing_minimum_plan_candidate_confidence,
        max_plan_candidates=settings.routing_max_plan_candidates,
        max_business_candidates=settings.routing_max_business_candidates,
        trace_sink=trace_sink,
    )
    return SkillEngineAgentAdapter(
        chat_agent_runtime,
        skill_runtime,
        selector,
        trace_sink=trace_sink,
    )


def _tool_descriptors(provider: Any) -> tuple[Any, ...]:
    """Read descriptors from the facade, with compatibility for older adapters."""
    describe = getattr(provider, "descriptors", None)
    if callable(describe):
        return tuple(describe())
    registry = getattr(provider, "registry", None)
    if registry is None:
        raise TypeError("Tool provider must expose a descriptor catalog.")
    return tuple(registry.descriptors())


def _runtime_tool_definitions(provider: Any) -> tuple[ModelToolDefinition, ...]:
    """Expose the configured governed tool catalog as normalized model definitions."""
    if provider is None:
        return ()
    definitions: list[ModelToolDefinition] = []
    for descriptor in _tool_descriptors(provider):
        if not isinstance(descriptor, ToolDescriptor):
            raise TypeError("Runtime Chat requires validated ToolDescriptor values.")
        definitions.append(
            ModelToolDefinition(
                name=descriptor.name,
                description=descriptor.description,
                parameters=descriptor.input_schema,
            )
        )
    return tuple(definitions)


class _UnavailableRuntimeToolPort:
    """Reject impossible tool dispatch when no governed ToolFacade is configured."""

    async def execute(
        self,
        request_context: RequestContext,
        tool_call: ModelToolCall,
    ) -> str:
        del request_context
        raise RuntimeError(f"No ToolFacade is configured for tool {tool_call.name!r}.")


__all__ = ["build_configured_chat_agent_runtime"]
