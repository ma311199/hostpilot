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
    MODEL_RUNTIME_PLUGIN_API_VERSION,
    ModelRuntime,
    ModelRuntimeRegistry,
    create_default_model_runtime_registry,
    create_model_runtime,
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
    "ModelRuntime",
    "ModelRuntimeRegistry",
    "MODEL_RUNTIME_PLUGIN_API_VERSION",
    "create_default_model_runtime_registry",
    "create_model_runtime",
]
