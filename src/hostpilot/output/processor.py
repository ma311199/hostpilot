"""Final response validation and normalization for the L4 output boundary."""

from __future__ import annotations


class OutputValidationError(ValueError):
    """Raised when a response is empty or exceeds its configured size bound."""


class OutputProcessor:
    """Normalize plain-text responses and enforce an output size ceiling."""

    def __init__(self, *, max_chars: int = 100_000) -> None:
        if max_chars < 1:
            raise ValueError("Maximum response size must be positive.")
        self.max_chars = max_chars

    def process(self, response: str) -> str:
        """Strip boundary whitespace and reject empty or oversized responses."""
        normalized = response.strip()
        if not normalized:
            raise OutputValidationError("Agent returned an empty response.")
        if len(normalized) > self.max_chars:
            raise OutputValidationError("Agent response exceeds the configured size limit.")
        return normalized


__all__ = ["OutputProcessor", "OutputValidationError"]
