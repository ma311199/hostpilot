"""Build the configured provider and framework-facing chat adapter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from hostpilot.config import Settings
from hostpilot.models.provider import ModelProvider

MODEL_RUNTIME_PLUGIN_API_VERSION = "1.0"


@dataclass(frozen=True)
class ModelRuntime:
    """Provider-neutral client plus the chat-model adapter used by Agent frameworks."""

    provider: ModelProvider
    agent_chat_model: Any


ModelRuntimeFactory = Callable[[Settings], ModelRuntime]


class ModelRuntimeRegistry:
    """Create provider and framework adapter pairs from trusted factories."""

    def __init__(self) -> None:
        self._factories: dict[str, tuple[str, ModelRuntimeFactory]] = {}

    def register(
        self,
        implementation_id: str,
        factory: ModelRuntimeFactory,
        *,
        api_version: str = MODEL_RUNTIME_PLUGIN_API_VERSION,
    ) -> None:
        if not implementation_id or implementation_id.strip() != implementation_id:
            raise ValueError("Model runtime implementation IDs must be non-empty and trimmed.")
        if api_version != MODEL_RUNTIME_PLUGIN_API_VERSION:
            raise ValueError(
                f"Unsupported model runtime plugin API version {api_version!r}; "
                f"expected {MODEL_RUNTIME_PLUGIN_API_VERSION!r}."
            )
        if implementation_id in self._factories:
            raise ValueError(f"Model runtime already registered: {implementation_id}.")
        self._factories[implementation_id] = (api_version, factory)

    def create(self, implementation_id: str, settings: Settings) -> ModelRuntime:
        registered = self._factories.get(implementation_id)
        if registered is None:
            raise ValueError(f"Unknown model runtime implementation: {implementation_id}.")
        _api_version, factory = registered
        runtime = factory(settings)
        if not isinstance(runtime, ModelRuntime):
            raise TypeError("Model runtime plugin factory must return ModelRuntime.")
        if not callable(getattr(runtime.provider, "complete", None)):
            raise TypeError("Model runtime provider must implement complete().")
        if runtime.agent_chat_model is None:
            raise TypeError("Model runtime must supply an agent_chat_model adapter.")
        return runtime

    def implementations(self) -> tuple[str, ...]:
        """Return registered implementation IDs in stable sorted order."""
        return tuple(sorted(self._factories))


def create_default_model_runtime_registry() -> ModelRuntimeRegistry:
    """Register the current OpenAI-compatible model runtime implementation."""
    registry = ModelRuntimeRegistry()
    registry.register("builtin.openai-compatible", _create_openai_compatible_runtime)
    return registry


def create_model_runtime(
    settings: Settings, *, registry: ModelRuntimeRegistry | None = None
) -> ModelRuntime:
    """Create the configured provider and its framework-facing model adapter."""
    runtime_registry = registry or create_default_model_runtime_registry()
    return runtime_registry.create(settings.model_provider_plugin, settings)


def _create_openai_compatible_runtime(settings: Settings) -> ModelRuntime:
    """Create the built-in OpenAI-compatible provider and LangChain adapter."""
    if not settings.model_name or not settings.model_api_key:
        raise ValueError("Model name and API key must be configured before creating the runtime.")

    try:
        from langchain_openai import ChatOpenAI
        from openai import DefaultAsyncHttpx2Client
    except ImportError as error:
        raise RuntimeError(
            "Install the agents dependency group to use the configured model runtime."
        ) from error

    from hostpilot.models.langchain_adapter import ProviderChatModel
    from hostpilot.models.langchain_openai import OpenAICompatibleChatProvider

    agent_chat_model = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        # Some OpenAI-compatible providers expose Chat Completions but not /responses.
        use_responses_api=False,
        streaming=settings.model_streaming,
        http_async_client=DefaultAsyncHttpx2Client(),
    )
    provider = OpenAICompatibleChatProvider(agent_chat_model, model_id=settings.model_name)
    framework_model = ProviderChatModel(provider=provider, model_id=settings.model_name)
    return ModelRuntime(provider=provider, agent_chat_model=framework_model)


__all__ = [
    "MODEL_RUNTIME_PLUGIN_API_VERSION",
    "ModelRuntime",
    "ModelRuntimeRegistry",
    "create_default_model_runtime_registry",
    "create_model_runtime",
]
