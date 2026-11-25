from importlib import import_module
from inspect import Parameter, signature


def test_chat_agent_runtime_exports_use_role_specific_names() -> None:
    runtime = import_module("hostpilot.orchestration.runtime")
    registry = import_module("hostpilot.orchestration.runtime_plugins")
    host_plugin = import_module("hostpilot.orchestration.runtime_host_plugin")

    assert runtime.ChatAgentRuntime is not None
    assert runtime.ChatAgentResponse is not None
    assert runtime.LangGraphChatAgentRuntime is not None
    assert runtime.ChatAgentRuntimeUnavailable is not None
    assert registry.ChatAgentRuntimePluginContext is not None
    assert registry.ChatAgentRuntimeRegistry is not None
    assert host_plugin.ChatAgentRuntimeHostPlugin is not None
    assert not hasattr(runtime, "AgentRuntime")
    assert not hasattr(runtime, "AgentResponse")
    assert not hasattr(registry, "ChatRuntimeRegistry")
    assert not hasattr(host_plugin, "AgentRuntimeHostPlugin")


def test_skill_model_and_harness_types_use_role_specific_names() -> None:
    skills = import_module("hostpilot.skills")
    models = import_module("hostpilot.models.factory")
    routing_models = import_module("hostpilot.routing.models")
    harness = import_module("hostpilot.persistence.harness_configuration")

    assert skills.SkillEngine is not None
    assert models.ModelRuntimeComponents is not None
    assert models.ModelProviderRegistry is not None
    assert routing_models.RoutingModelComponents is not None
    assert harness.HarnessConfiguration is not None
    assert not hasattr(skills, "SkillRuntime")
    assert not hasattr(models, "ModelRuntime")
    assert not hasattr(models, "ModelRuntimeRegistry")
    assert not hasattr(routing_models, "RoutingModelRuntime")
    assert not hasattr(harness, "HarnessRuntimeConfiguration")


def test_chat_agent_runtime_factory_parameters_use_domain_specific_names() -> None:
    app = import_module("hostpilot.api.app")
    runtime = import_module("hostpilot.orchestration.runtime")

    app_parameters = signature(app.create_app).parameters
    runtime_parameters = signature(runtime.create_configured_chat_agent_runtime).parameters

    assert "chat_agent_runtime_factory" in app_parameters
    assert "chat_agent_runtime_registry" in app_parameters
    assert "runtime_factory" not in app_parameters
    assert "chat_agent_runtime_registry" in runtime_parameters
    assert "chat_runtime_registry" not in runtime_parameters
    assert hasattr(runtime, "create_configured_chat_agent_runtime")
    assert not hasattr(runtime, "create_configured_runtime")


def test_chat_agent_runtime_factory_preserves_full_signature_and_package_export() -> None:
    runtime = import_module("hostpilot.orchestration.runtime")
    package = import_module("hostpilot.orchestration")
    factory = runtime.create_configured_chat_agent_runtime
    parameters = signature(factory).parameters

    assert package.create_configured_chat_agent_runtime is factory
    assert tuple(parameters) == (
        "settings",
        "checkpointer",
        "tools",
        "tool_provider",
        "memory_controller",
        "compensation_registry",
        "cancellation_sink",
        "skill_execution_modes",
        "trace_sink",
        "plan_component_registry",
        "chat_agent_runtime_registry",
        "model_provider_registry",
    )
    assert {name: parameter.kind for name, parameter in parameters.items()} == {
        "settings": Parameter.POSITIONAL_OR_KEYWORD,
        "checkpointer": Parameter.KEYWORD_ONLY,
        "tools": Parameter.KEYWORD_ONLY,
        "tool_provider": Parameter.KEYWORD_ONLY,
        "memory_controller": Parameter.KEYWORD_ONLY,
        "compensation_registry": Parameter.KEYWORD_ONLY,
        "cancellation_sink": Parameter.KEYWORD_ONLY,
        "skill_execution_modes": Parameter.KEYWORD_ONLY,
        "trace_sink": Parameter.KEYWORD_ONLY,
        "plan_component_registry": Parameter.KEYWORD_ONLY,
        "chat_agent_runtime_registry": Parameter.KEYWORD_ONLY,
        "model_provider_registry": Parameter.KEYWORD_ONLY,
    }
    assert {name: parameter.default for name, parameter in parameters.items()} == {
        "settings": Parameter.empty,
        "checkpointer": Parameter.empty,
        "tools": (),
        "tool_provider": None,
        "memory_controller": None,
        "compensation_registry": None,
        "cancellation_sink": None,
        "skill_execution_modes": None,
        "trace_sink": None,
        "plan_component_registry": None,
        "chat_agent_runtime_registry": None,
        "model_provider_registry": None,
    }
    assert {name: parameter.annotation for name, parameter in parameters.items()} == {
        "settings": "Settings",
        "checkpointer": "Any",
        "tools": "Sequence[Any]",
        "tool_provider": "Any | None",
        "memory_controller": "Any | None",
        "compensation_registry": "CompensationRegistry | None",
        "cancellation_sink": "CancellationSink | None",
        "skill_execution_modes": "Mapping[str, str] | None",
        "trace_sink": "TraceSink | None",
        "plan_component_registry": "PlanComponentRegistry | None",
        "chat_agent_runtime_registry": "ChatAgentRuntimeRegistry | None",
        "model_provider_registry": "ModelProviderRegistry | None",
    }
