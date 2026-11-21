import asyncio
from types import SimpleNamespace

import pytest

from hostpilot.budget import RequestBudget
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
from hostpilot.runtime import RequestContext, RuntimeExecutionResult, RuntimeExecutionStatus


class FakeChatRuntime:
    async def read_messages(self, session_id):
        return ()

    async def respond(self, message, request_context):
        raise NotImplementedError

    async def record_turn(self, session_id, user_message, assistant_message):
        return None


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
