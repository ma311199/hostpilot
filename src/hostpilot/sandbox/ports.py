"""Replaceable Sandbox backend contracts owned by the Runtime host."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from hostpilot.sandbox.linux import SandboxRunResult

if TYPE_CHECKING:
    from hostpilot.tools.filesystem import WorkspaceFiles


class WorkspaceSandboxBackend(Protocol):
    """Execute a validated filesystem operation within the supplied workspace."""

    @property
    def host_supported(self) -> bool: ...

    @property
    def unavailable_reason(self) -> str | None: ...

    async def execute(
        self,
        workspace: WorkspaceFiles,
        operation: str,
        arguments: dict[str, Any],
    ) -> Any: ...


class CodeSandboxBackend(Protocol):
    """Run source code inside an isolated execution environment."""

    @property
    def max_source_bytes(self) -> int: ...

    @property
    def host_supported(self) -> bool: ...

    @property
    def unavailable_reason(self) -> str | None: ...

    async def run(self, source: str) -> SandboxRunResult: ...


@dataclass(frozen=True, slots=True)
class SandboxBackendSet:
    """The filesystem and language backends shared by one Runtime host."""

    filesystem: WorkspaceSandboxBackend
    python: CodeSandboxBackend
    javascript: CodeSandboxBackend
    shell: CodeSandboxBackend


class SandboxBackendFactory(Protocol):
    """Construct one set of process-scoped Sandbox backend instances."""

    def create(self) -> SandboxBackendSet: ...


class LinuxSandboxBackendFactory:
    """Build the existing Linux bubblewrap backends without probing the host."""

    def create(self) -> SandboxBackendSet:
        """Return a new backend set; availability remains lazily probed per backend."""
        from hostpilot.sandbox.filesystem import LinuxWorkspaceSandbox
        from hostpilot.sandbox.linux import (
            LinuxJavaScriptSandbox,
            LinuxPythonSandbox,
            LinuxShellSandbox,
        )

        return SandboxBackendSet(
            filesystem=LinuxWorkspaceSandbox(),
            python=LinuxPythonSandbox(),
            javascript=LinuxJavaScriptSandbox(),
            shell=LinuxShellSandbox(),
        )
