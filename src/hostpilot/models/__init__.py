"""Provider-neutral model request contracts and adapters."""

from hostpilot.models.contracts import (
    ModelMessage,
    ModelProviderError,
    ModelProviderErrorCategory,
    ModelRequest,
    ModelResponse,
    ModelRole,
    ModelToolCall,
    ModelToolDefinition,
)
from hostpilot.models.factory import (
    MODEL_PROVIDER_PLUGIN_API_VERSION,
    ModelProviderRegistry,
    ModelRuntimeComponents,
    create_default_model_provider_registry,
    create_model_runtime_components,
)
from hostpilot.models.provider import ModelProvider

__all__ = [
    "ModelMessage",
    "ModelProvider",
    "ModelProviderError",
    "ModelProviderErrorCategory",
    "ModelRequest",
    "ModelResponse",
    "ModelRole",
    "ModelToolCall",
    "ModelToolDefinition",
    "ModelRuntimeComponents",
    "ModelProviderRegistry",
    "MODEL_PROVIDER_PLUGIN_API_VERSION",
    "create_default_model_provider_registry",
    "create_model_runtime_components",
]
