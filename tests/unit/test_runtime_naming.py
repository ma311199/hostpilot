from importlib import import_module
from inspect import signature


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
