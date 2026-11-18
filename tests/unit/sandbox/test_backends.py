"""Contracts and default factory behavior for Runtime-owned Sandbox backends."""

from __future__ import annotations

from hostpilot.sandbox import (
    LinuxSandboxBackendFactory,
    SandboxBackendSet,
    WorkspaceSandboxBackend,
)
from hostpilot.sandbox.filesystem import LinuxWorkspaceSandbox
from hostpilot.sandbox.linux import (
    LinuxJavaScriptSandbox,
    LinuxPythonSandbox,
    LinuxShellSandbox,
)


def test_linux_factory_builds_all_backends_without_running_host_probes(monkeypatch) -> None:
    def fail_if_probed(*args, **kwargs):
        raise AssertionError("factory construction must not probe host support")

    monkeypatch.setattr("hostpilot.sandbox.linux.subprocess.run", fail_if_probed)

    backends = LinuxSandboxBackendFactory().create()

    assert isinstance(backends, SandboxBackendSet)
    assert isinstance(backends.filesystem, LinuxWorkspaceSandbox)
    assert isinstance(backends.python, LinuxPythonSandbox)
    assert isinstance(backends.javascript, LinuxJavaScriptSandbox)
    assert isinstance(backends.shell, LinuxShellSandbox)
    assert all(
        "host_supported" not in backend.__dict__
        for backend in (backends.filesystem, backends.python, backends.javascript, backends.shell)
    )


def test_filesystem_backend_protocol_accepts_existing_workspace_sandbox() -> None:
    backend: WorkspaceSandboxBackend = LinuxWorkspaceSandbox()

    assert callable(backend.execute)
