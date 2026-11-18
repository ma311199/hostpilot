import pytest

from hostpilot.orchestration.runtime_plugins import (
    CHAT_RUNTIME_PLUGIN_API_VERSION,
    ChatRuntimePluginContext,
    ChatRuntimeRegistry,
)


class FakeChatRuntime:
    async def read_messages(self, conversation_id):
        return ()

    async def respond(self, message, context, **kwargs):
        raise NotImplementedError

    async def record_turn(self, conversation_id, user_message, assistant_message):
        return None


def test_chat_runtime_registry_checks_version_contract_and_duplicate_ids() -> None:
    registry = ChatRuntimeRegistry()
    registry.register("test.runtime", lambda _: FakeChatRuntime())
    context = ChatRuntimePluginContext(None, None, None, ())

    assert CHAT_RUNTIME_PLUGIN_API_VERSION == "1.0"
    assert registry.implementations() == ("test.runtime",)
    assert isinstance(registry.create("test.runtime", context), FakeChatRuntime)

    with pytest.raises(ValueError, match="already registered"):
        registry.register("test.runtime", lambda _: FakeChatRuntime())
    with pytest.raises(ValueError, match="Unsupported chat runtime plugin API"):
        registry.register("test.old", lambda _: FakeChatRuntime(), api_version="0.9")
    with pytest.raises(ValueError, match="Unknown chat runtime"):
        registry.create("missing.runtime", context)
    with pytest.raises(TypeError, match="read_messages"):
        registry.register("test.invalid", lambda _: object())
        registry.create("test.invalid", context)
