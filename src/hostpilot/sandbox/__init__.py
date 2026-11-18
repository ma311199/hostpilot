"""Conversation-scoped sandbox mode context for governed tool execution."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

from hostpilot.contracts import SandboxMode
from hostpilot.sandbox.linux import (
    LinuxJavaScriptSandbox,
    LinuxPythonSandbox,
    LinuxShellSandbox,
    SandboxRunResult,
)
from hostpilot.sandbox.ports import (
    CodeSandboxBackend,
    LinuxSandboxBackendFactory,
    SandboxBackendFactory,
    SandboxBackendSet,
    WorkspaceSandboxBackend,
)

_active_mode: ContextVar[SandboxMode | None] = ContextVar("hostpilot_sandbox_mode", default=None)


@contextmanager
def sandbox_mode_scope(mode: SandboxMode) -> Iterator[None]:
    """Bind one persisted conversation mode to the current async execution context."""
    token: Token[SandboxMode | None] = _active_mode.set(mode)
    try:
        yield
    finally:
        _active_mode.reset(token)


def current_sandbox_mode() -> SandboxMode | None:
    """Return the mode governing the current conversation, if one is bound."""
    return _active_mode.get()


__all__ = [
    "LinuxJavaScriptSandbox",
    "LinuxPythonSandbox",
    "LinuxShellSandbox",
    "LinuxSandboxBackendFactory",
    "CodeSandboxBackend",
    "SandboxBackendFactory",
    "SandboxBackendSet",
    "SandboxRunResult",
    "WorkspaceSandboxBackend",
    "current_sandbox_mode",
    "sandbox_mode_scope",
]
