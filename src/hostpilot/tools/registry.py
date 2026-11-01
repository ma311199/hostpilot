"""Declarative tool registration and lookup."""

from __future__ import annotations

from collections.abc import Awaitable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from hostpilot.contracts import ToolDescriptor


class ArgumentValidator(Protocol):
    """Validate and normalize untrusted tool arguments."""

    def __call__(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]: ...


class ToolHandler(Protocol):
    """Run one registered tool with validated arguments."""

    def __call__(self, arguments: Mapping[str, Any]) -> Awaitable[Any]: ...


class ToolResultVerifier(Protocol):
    """Raise when a tool result does not satisfy its declared task contract."""

    def __call__(self, result: Any, arguments: Mapping[str, Any]) -> None: ...


class ConfirmationSummaryBuilder(Protocol):
    """Create a user-readable summary without exposing raw tool arguments."""

    def __call__(self, arguments: Mapping[str, Any]) -> str: ...


@dataclass(frozen=True)
class ToolRegistration:
    """Implementation and validation hooks associated with a tool descriptor."""

    descriptor: ToolDescriptor
    validate_arguments: ArgumentValidator
    handler: ToolHandler
    verify_result: ToolResultVerifier
    confirmation_summary: ConfirmationSummaryBuilder | None = None


class ToolRegistrationError(ValueError):
    """Raised for duplicate or unknown tool registrations."""


class ToolRegistry:
    """Store tools by stable name and expose immutable descriptors to callers."""

    def __init__(self) -> None:
        self._registrations: dict[str, ToolRegistration] = {}

    def register(self, registration: ToolRegistration) -> None:
        """Register a tool once; duplicate names fail closed."""
        name = registration.descriptor.name
        if name in self._registrations:
            raise ToolRegistrationError(f"Tool '{name}' is already registered.")
        self._registrations[name] = registration

    def get(self, name: str) -> ToolRegistration:
        """Return a registered tool or raise a typed lookup error."""
        try:
            return self._registrations[name]
        except KeyError as error:
            raise ToolRegistrationError(f"Tool '{name}' is not registered.") from error

    def descriptors(self) -> tuple[ToolDescriptor, ...]:
        """Return descriptors without exposing handler implementations."""
        return tuple(item.descriptor for item in self._registrations.values())


__all__ = [
    "ArgumentValidator",
    "ConfirmationSummaryBuilder",
    "ToolHandler",
    "ToolRegistration",
    "ToolRegistrationError",
    "ToolRegistry",
    "ToolResultVerifier",
]
