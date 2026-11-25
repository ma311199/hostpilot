import asyncio
import importlib.util
from types import SimpleNamespace

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.config import RemoteMCPServerSettings, Settings
from hostpilot.contracts import BudgetContext, BudgetLimits, ContextSnapshot, ModelTokenUsage
from hostpilot.models import ModelMessage, ModelRole, ModelToolDefinition
from hostpilot.orchestration.runtime import (
    ChatAgentResponse,
    create_default_chat_agent_runtime_registry,
)
from hostpilot.orchestration.runtime_host_plugin import ChatAgentRuntimeHostPlugin
from hostpilot.orchestration.runtime_plugins import (
    CHAT_AGENT_RUNTIME_PLUGIN_API_VERSION,
    ChatAgentRuntimePluginContext,
    ChatAgentRuntimeRegistry,
)
from hostpilot.runtime import (
    RequestContext,
    RuntimeContext,
    RuntimeExecutionResult,
    RuntimeExecutionStatus,
)


class FakeChatRuntime:
    async def read_messages(self, session_id):
        return ()

    async def respond(self, message, request_context):
        raise NotImplementedError

    async def record_turn(self, session_id, user_message, assistant_message):
        return None


def test_chat_runtime_assembly_plugin_builds_candidate_from_runtime_services(monkeypatch) -> None:
    module_name = "hostpilot.orchestration.chat_runtime_assembly_host_plugin"
    assert importlib.util.find_spec(module_name) is not None

    from hostpilot.orchestration.chat_runtime_assembly_host_plugin import (
        ChatRuntimeAssemblyHostPlugin,
        ChatRuntimeAssemblyRequest,
    )

    settings = object()
    tool_provider = object()
    memory_controller = object()
    checkpointer = object()
    trace_sink = object()
    cancellation_sink = object()
    plan_component_registry = object()
    chat_agent_runtime_registry = object()
    model_provider_registry = object()
    expected_runtime = FakeChatRuntime()
    composer_calls = []

    def composer(settings_arg, **kwargs):
        composer_calls.append((settings_arg, kwargs))
        return expected_runtime

    monkeypatch.setattr(
        "hostpilot.orchestration.chat_runtime_composition.build_configured_chat_agent_runtime",
        composer,
    )

    def reject_legacy_factory(*_args, **_kwargs):
        raise AssertionError("The Runtime assembly Plugin must use the canonical composer.")

    monkeypatch.setattr(
        "hostpilot.orchestration.runtime.create_configured_chat_agent_runtime",
        reject_legacy_factory,
    )
    plugin = ChatRuntimeAssemblyHostPlugin(
        plan_component_registry=plan_component_registry,
        chat_agent_runtime_registry=chat_agent_runtime_registry,
        model_provider_registry=model_provider_registry,
    )
    context = RuntimeContext()
    context.services.register(
        "hostpilot.conversation-checkpointer", checkpointer, owner="test.state"
    )
    context.services.register("hostpilot.trace-store", trace_sink, owner="test.app")
    context.services.register("hostpilot.cancellation-store", cancellation_sink, owner="test.app")

    async def setup() -> None:
        await plugin.setup(context)

    asyncio.run(setup())
    request = ChatRuntimeAssemblyRequest(
        settings=settings,
        tools=("framework-tool",),
        tool_provider=tool_provider,
        memory_controller=memory_controller,
        skill_execution_modes={"business/review": "yaml"},
    )

    assert plugin.build_candidate(request) is expected_runtime
    assert context.services.resolve(plugin.service_id) is plugin
    assert composer_calls == [
        (
            request.settings,
            {
                "checkpointer": checkpointer,
                "tools": ("framework-tool",),
                "tool_provider": tool_provider,
                "memory_controller": memory_controller,
                "cancellation_sink": cancellation_sink,
                "skill_execution_modes": {"business/review": "yaml"},
                "trace_sink": trace_sink,
                "plan_component_registry": plan_component_registry,
                "chat_agent_runtime_registry": chat_agent_runtime_registry,
                "model_provider_registry": model_provider_registry,
            },
        )
    ]
    assert request.tools == ("framework-tool",)
    with pytest.raises(TypeError):
        request.skill_execution_modes["business/review"] = "markdown"


def test_chat_runtime_assembly_plugin_setup_requires_runtime_services() -> None:
    module_name = "hostpilot.orchestration.chat_runtime_assembly_host_plugin"
    assert importlib.util.find_spec(module_name) is not None

    from hostpilot.orchestration.chat_runtime_assembly_host_plugin import (
        ChatRuntimeAssemblyHostPlugin,
    )

    plugin = ChatRuntimeAssemblyHostPlugin()
    context = RuntimeContext()

    with pytest.raises(KeyError, match="hostpilot.conversation-checkpointer"):
        asyncio.run(plugin.setup(context))


def test_chat_runtime_assembly_request_snapshots_nested_settings() -> None:
    from pydantic import SecretStr

    from hostpilot.orchestration.chat_runtime_assembly_host_plugin import (
        ChatRuntimeAssemblyRequest,
    )

    original_settings = Settings(
        mcp_servers=(
            RemoteMCPServerSettings(
                name="example",
                url="https://example.test/mcp",
                headers={"X-Token": SecretStr("original")},
            ),
        )
    )
    request = ChatRuntimeAssemblyRequest(settings=original_settings)

    original_settings.mcp_servers[0].headers["X-Token"] = SecretStr("mutated")

    assert request.settings is not original_settings
    assert request.settings.mcp_servers[0].headers["X-Token"].get_secret_value() == "original"


def test_chat_agent_runtime_registry_checks_version_contract_and_duplicate_ids() -> None:
    registry = ChatAgentRuntimeRegistry()
    registry.register("test.runtime", lambda _: FakeChatRuntime())
    context = ChatAgentRuntimePluginContext(None, None, None, ())

    assert CHAT_AGENT_RUNTIME_PLUGIN_API_VERSION == "2.0"
    assert registry.implementations() == ("test.runtime",)
    assert isinstance(registry.create("test.runtime", context), FakeChatRuntime)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("test.runtime", lambda _: FakeChatRuntime())
    with pytest.raises(ValueError, match="Unsupported chat runtime plugin API"):
        registry.register("test.old", lambda _: FakeChatRuntime(), api_version="1.0")
    with pytest.raises(ValueError, match="Unknown chat runtime"):
        registry.create("missing.runtime", context)
    with pytest.raises(TypeError, match="read_messages"):
        registry.register("test.invalid", lambda _: object())
        registry.create("test.invalid", context)


def test_runtime_chat_is_registered_as_an_opt_in_plugin() -> None:
    registry = create_default_chat_agent_runtime_registry()

    assert registry.implementations() == ("builtin.deepagents", "builtin.runtime-chat")


def test_runtime_chat_plugin_uses_injected_loop_and_langgraph_checkpoint(monkeypatch) -> None:
    class StateGraph:
        def __init__(self) -> None:
            self.messages = []

        async def aget_state(self, config):
            del config
            return SimpleNamespace(values={"messages": self.messages})

        async def aupdate_state(self, config, values):
            del config
            self.messages.extend(values["messages"])

    graph = StateGraph()

    class SkillRuntime:
        def __init__(self) -> None:
            self.graph = graph

        async def respond_with_skill(self, message, request_context, *, skill_id):
            return ChatAgentResponse(f"{message}:{skill_id}")

        async def aclose(self):
            return None

    class ExecutionLoop:
        async def run(self, request):
            assert request.messages[-1] == ModelMessage(role=ModelRole.USER, content="hello")
            assistant_message = ModelMessage(role=ModelRole.ASSISTANT, content="Runtime reply")
            if request.message_sink is not None:
                await request.message_sink((assistant_message,))
            return RuntimeExecutionResult(
                RuntimeExecutionStatus.COMPLETED,
                "Runtime reply",
                (assistant_message,),
                ModelTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                steps=1,
            )

    monkeypatch.setattr(
        "hostpilot.orchestration.runtime.create_deepagents_runtime",
        lambda *args, **kwargs: SkillRuntime(),
    )
    loop = ExecutionLoop()
    tool = ModelToolDefinition("search", "Search", {"type": "object"})
    registry = create_default_chat_agent_runtime_registry()
    runtime = registry.create(
        "builtin.runtime-chat",
        ChatAgentRuntimePluginContext(
            model=None,
            checkpointer=None,
            skills=None,
            tools=(),
            runtime_execution_loop=loop,
            runtime_tool_definitions=(tool,),
            runtime_allowed_tool_ids=frozenset({"search"}),
        ),
    )
    request_context = RequestContext(
        snapshot=ContextSnapshot(
            request_id="request-1",
            session_id="43df14e0-b250-4981-a414-3db49e1ecc3b",
        ),
        budget=RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=100,
                    tool_calls=2,
                    retrieval_calls=1,
                    timeout_seconds=10,
                )
            )
        ),
    )

    response = asyncio.run(runtime.respond("hello", request_context))

    assert response.content == "Runtime reply"
    assert graph.messages[0].content == "hello"
    assert graph.messages[1].content == "Runtime reply"


def test_chat_agent_runtime_host_plugin_forwards_the_request_context_object() -> None:
    class CapturingRuntime:
        def __init__(self) -> None:
            self.request_context = None

        async def read_messages(self, session_id):
            return ()

        async def respond(self, message, request_context):
            self.request_context = request_context
            return ChatAgentResponse(message)

        async def record_turn(self, session_id, user_message, assistant_message):
            return None

    runtime = CapturingRuntime()
    plugin = ChatAgentRuntimeHostPlugin(runtime)
    request_context = RequestContext(
        snapshot=ContextSnapshot(request_id="request-1"),
        budget=RequestBudget.start(
            BudgetContext(
                limits=BudgetLimits(
                    total_tokens=100,
                    tool_calls=2,
                    retrieval_calls=1,
                    timeout_seconds=10,
                )
            )
        ),
    )

    response = asyncio.run(plugin.respond("hello", request_context))

    assert response.content == "hello"
    assert runtime.request_context is request_context


def test_chat_agent_runtime_host_plugin_uses_runtime_agent_manager_and_closes_on_stop() -> None:
    class CloseableRuntime(FakeChatRuntime):
        def __init__(self) -> None:
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    async def scenario() -> None:
        runtime = CloseableRuntime()
        plugin = ChatAgentRuntimeHostPlugin(runtime)
        context = RuntimeContext()
        await plugin.setup(context)
        assert context.services.resolve(plugin.service_id) is plugin
        assert context.agents.current is runtime

        lease_entered = asyncio.Event()
        release_lease = asyncio.Event()

        async def hold_lease() -> None:
            async with plugin.lease() as leased_runtime:
                assert leased_runtime is runtime
                lease_entered.set()
                await release_lease.wait()

        active_turn = asyncio.create_task(hold_lease())
        await lease_entered.wait()
        stopping = asyncio.create_task(plugin.stop(context))
        await asyncio.sleep(0)
        assert not stopping.done()
        with pytest.raises(RuntimeError, match="shutting down"):
            async with plugin.lease():
                pytest.fail("the plugin must reject leases once stopping starts")
        assert runtime.close_calls == 0

        release_lease.set()
        await active_turn
        await stopping
        assert runtime.close_calls == 1

    asyncio.run(scenario())


def test_chat_agent_runtime_host_plugin_does_not_discover_nested_sandbox_providers() -> None:
    class ToolProvider:
        def __init__(self) -> None:
            self.sandbox_mode = None

        def set_sandbox_mode(self, mode) -> None:
            self.sandbox_mode = mode

    class AgentWithProvider(FakeChatRuntime):
        def __init__(self) -> None:
            self.provider = ToolProvider()

    runtime = AgentWithProvider()
    plugin = ChatAgentRuntimeHostPlugin(runtime)

    assert not hasattr(plugin, "set_sandbox_mode")
    assert runtime.provider.sandbox_mode is None


def test_chat_agent_runtime_host_plugin_publishes_replacement_while_old_turn_drains() -> None:
    class CloseableRuntime(FakeChatRuntime):
        def __init__(self, name: str) -> None:
            self.name = name
            self.close_calls = 0

        async def aclose(self) -> None:
            self.close_calls += 1

    async def scenario() -> tuple[int, int]:
        old_runtime = CloseableRuntime("old")
        new_runtime = CloseableRuntime("new")
        plugin = ChatAgentRuntimeHostPlugin(old_runtime)
        await plugin.setup(RuntimeContext())
        lease_entered = asyncio.Event()
        release_lease = asyncio.Event()

        async def hold_old_turn() -> None:
            async with plugin.lease() as runtime:
                assert runtime is old_runtime
                lease_entered.set()
                await release_lease.wait()

        old_turn = asyncio.create_task(hold_old_turn())
        await lease_entered.wait()
        replacement = asyncio.create_task(plugin.replace(new_runtime))

        async def wait_for_new_generation() -> None:
            while True:
                async with plugin.lease() as runtime:
                    if runtime is new_runtime:
                        return
                await asyncio.sleep(0)

        await asyncio.wait_for(wait_for_new_generation(), timeout=1)

        async with plugin.lease() as runtime:
            assert runtime is new_runtime
        assert old_runtime.close_calls == 0

        release_lease.set()
        await old_turn
        await replacement
        assert old_runtime.close_calls == 1
        await plugin.stop(RuntimeContext())
        return old_runtime.close_calls, new_runtime.close_calls

    assert asyncio.run(scenario()) == (1, 1)
