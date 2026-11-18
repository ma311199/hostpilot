"""Verify file operations receive only their authorized Bubblewrap mounts."""

from __future__ import annotations

import asyncio

import pytest

from hostpilot.contracts import SandboxMode
from hostpilot.sandbox.filesystem import LinuxWorkspaceSandbox
from hostpilot.sandbox.linux import LinuxPythonSandbox
from hostpilot.tools.filesystem import WorkspaceFileError, WorkspaceFiles


def test_workspace_modes_mount_only_workspace_with_expected_access(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    workspace = WorkspaceFiles(workspace_root)
    sandbox = LinuxWorkspaceSandbox()

    workspace.set_sandbox_mode(SandboxMode.READ_ONLY)
    _, readonly_mounts = sandbox._request(workspace, "list_directory", {"path": ""})
    workspace.set_sandbox_mode(SandboxMode.WORKSPACE_WRITE)
    _, writable_mounts = sandbox._request(workspace, "list_directory", {"path": ""})

    assert readonly_mounts == (("--ro-bind", str(workspace_root.resolve()), "/workspace"),)
    assert writable_mounts == (("--bind", str(workspace_root.resolve()), "/workspace"),)


def test_danger_mode_mounts_only_requested_file_or_directory(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    file_path = tmp_path / "private.txt"
    file_path.write_text("secret", encoding="utf-8")
    workspace = WorkspaceFiles(workspace_root)
    workspace.set_sandbox_mode(SandboxMode.DANGER_FULL_ACCESS)
    sandbox = LinuxWorkspaceSandbox()

    _, read_mounts = sandbox._request(
        workspace, "read_text_file", {"path": str(file_path)}
    )
    _, write_mounts = sandbox._request(
        workspace, "write_text_file", {"path": str(file_path), "content": "updated"}
    )
    _, list_mounts = sandbox._request(
        workspace, "list_directory", {"path": str(tmp_path)}
    )

    assert read_mounts == (("--ro-bind", str(file_path), "/grant"),)
    assert write_mounts == (("--bind", str(tmp_path), "/grant-parent"),)
    assert list_mounts == (("--ro-bind", str(tmp_path.resolve()), "/grant"),)


def test_sandbox_rejects_unavailable_runtime_without_host_fallback(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    sandbox = LinuxWorkspaceSandbox()
    sandbox._runner.host_supported = False

    with pytest.raises(WorkspaceFileError, match="sandbox is unavailable"):
        asyncio.run(
            sandbox.execute(
                WorkspaceFiles(workspace_root),
                "list_directory",
                {"path": ""},
            )
        )


@pytest.mark.skipif(
    not LinuxPythonSandbox().host_supported,
    reason="Linux bubblewrap and prlimit are not installed on this host.",
)
def test_file_sandbox_rejects_symlink_escape_and_keeps_outside_file_hidden(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("host secret", encoding="utf-8")
    (workspace_root / "escape.txt").symlink_to(outside_file)
    sandbox = LinuxWorkspaceSandbox()
    workspace = WorkspaceFiles(workspace_root)

    with pytest.raises(WorkspaceFileError):
        workspace.validate_read_path("escape.txt")

    with pytest.raises(WorkspaceFileError, match="Sandboxed file operation failed"):
        asyncio.run(
            sandbox.execute(workspace, "read_text_file", {"path": "escape.txt"})
        )


@pytest.mark.skipif(
    not LinuxPythonSandbox().host_supported,
    reason="Linux bubblewrap and prlimit are not installed on this host.",
)
def test_danger_file_sandbox_mounts_only_requested_file(tmp_path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    authorized_file = tmp_path / "authorized.txt"
    authorized_file.write_text("authorized", encoding="utf-8")
    workspace = WorkspaceFiles(workspace_root)
    workspace.set_sandbox_mode(SandboxMode.DANGER_FULL_ACCESS)
    sandbox = LinuxWorkspaceSandbox()

    assert asyncio.run(
        sandbox.execute(workspace, "read_text_file", {"path": str(authorized_file)})
    ) == "authorized"
    _, mounts = sandbox._request(
        workspace, "read_text_file", {"path": str(authorized_file)}
    )
    assert mounts == (("--ro-bind", str(authorized_file), "/grant"),)
