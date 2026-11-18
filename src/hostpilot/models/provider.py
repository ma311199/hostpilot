"""HostPilot model provider port."""

from __future__ import annotations

from typing import Protocol

from hostpilot.models.contracts import ModelRequest, ModelResponse


class ModelProvider(Protocol):
    """Generate normalized text and optional tool proposals for HostPilot."""

    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Complete one request and return provider-neutral output."""


__all__ = ["ModelProvider"]
