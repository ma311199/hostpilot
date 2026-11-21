import sys
from types import ModuleType

import pytest

from hostpilot.config import Settings
from hostpilot.models.factory import (
    MODEL_PROVIDER_PLUGIN_API_VERSION,
    ModelRuntimeComponents,
    create_default_model_provider_registry,
    create_model_runtime_components,
)
from hostpilot.models.langchain_adapter import ProviderChatModel


def test_factory_builds_openai_compatible_provider_from_settings(monkeypatch) -> None:
    model_arguments: dict[str, object] = {}
    http_clients: list[object] = []

    def model_factory(**kwargs):
        model_arguments.update(kwargs)
        return object()

    def http_client_factory():
        client = object()
        http_clients.append(client)
        return client

    fake_provider = ModuleType("langchain_openai")
    fake_provider.ChatOpenAI = model_factory
    monkeypatch.setitem(sys.modules, "langchain_openai", fake_provider)

    import openai

    monkeypatch.setattr(openai, "DefaultAsyncHttpx2Client", http_client_factory)
    settings = Settings(
        model_name="test-model",
        model_api_key="test-secret",
        model_base_url="https://model.example/v1",
        model_streaming=False,
    )

    model_components = create_model_runtime_components(settings)

    assert model_arguments["model"] == "test-model"
    assert model_arguments["api_key"].get_secret_value() == "test-secret"
    assert model_arguments["base_url"] == "https://model.example/v1"
    assert model_arguments["use_responses_api"] is False
    assert model_arguments["streaming"] is False
    assert len(http_clients) == 1
    assert model_arguments["http_async_client"] is http_clients[0]
    assert isinstance(model_components.agent_chat_model, ProviderChatModel)
    assert model_components.agent_chat_model.provider is model_components.provider
    assert model_components.provider.model_id == "test-model"


def test_factory_requires_model_name_and_api_key() -> None:
    with pytest.raises(ValueError, match="Model name and API key"):
        create_model_runtime_components(Settings())


def test_model_provider_registry_selects_custom_factory_and_checks_contract() -> None:
    class Provider:
        async def complete(self, request):
            raise NotImplementedError

    custom_runtime = ModelRuntimeComponents(Provider(), object())
    registry = create_default_model_provider_registry()
    registry.register("project.custom.v1", lambda settings: custom_runtime)
    settings = Settings(
        model_name="project-model",
        model_api_key="project-secret",
        model_provider_plugin="project.custom.v1",
    )

    assert MODEL_PROVIDER_PLUGIN_API_VERSION == "1.0"
    assert registry.implementations() == ("builtin.openai-compatible", "project.custom.v1")
    assert create_model_runtime_components(settings, registry=registry) is custom_runtime
    with pytest.raises(ValueError, match="Unknown model runtime"):
        create_model_runtime_components(settings)
    with pytest.raises(ValueError, match="Unsupported model runtime plugin API"):
        registry.register("old.provider", lambda _: custom_runtime, api_version="0.9")
    with pytest.raises(TypeError, match="must return ModelRuntimeComponents"):
        registry.register("invalid.provider", lambda _: object())
        registry.create("invalid.provider", settings)
