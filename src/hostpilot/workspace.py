"""Local workspace folder selection and permission checks."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path


class WorkspaceAccessError(ValueError):
    """Raised when a selected folder cannot be used as a workspace."""


class NativeFolderPickerUnavailable(RuntimeError):
    """Raised when the local operating system folder dialog cannot be opened."""


@dataclass(frozen=True)
class WorkspaceAccess:
    """Canonical workspace path and the access mode available to HostPilot."""

    path: Path
    read_only: bool


def inspect_workspace(path: Path | str) -> WorkspaceAccess:
    """Check a folder by opening it and attempting a removable write probe."""
    candidate = Path(path).expanduser()
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise WorkspaceAccessError("所选目录不存在或无法访问。") from error
    if not resolved.is_dir():
        raise WorkspaceAccessError("请选择一个文件夹作为工作区。")

    try:
        with os.scandir(resolved) as entries:
            next(entries, None)
    except OSError as error:
        raise WorkspaceAccessError("HostPilot 没有读取此目录的权限。") from error

    descriptor: int | None = None
    probe_path: Path | None = None
    try:
        descriptor, probe_name = tempfile.mkstemp(prefix=".hostpilot-access-check-", dir=resolved)
        probe_path = Path(probe_name)
    except PermissionError:
        return WorkspaceAccess(path=resolved, read_only=True)
    except OSError as error:
        if error.errno == errno.EROFS:
            return WorkspaceAccess(path=resolved, read_only=True)
        raise WorkspaceAccessError("无法检查此目录的写入权限。") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
            if probe_path is not None:
                try:
                    probe_path.unlink()
                except OSError as error:
                    raise WorkspaceAccessError("无法清理工作区权限检查文件。") from error

    return WorkspaceAccess(path=resolved, read_only=False)


def choose_folder_native(initial_directory: Path | str | None = None) -> Path | None:
    """Open the operating system folder dialog from the local API process."""
    picker_program = (
        "import sys, tkinter as tk\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk()\n"
        "root.withdraw()\n"
        "root.attributes('-topmost', True)\n"
        "selected = filedialog.askdirectory("
        "parent=root, title='选择 HostPilot 工作区', "
        "initialdir=sys.argv[1] or None, mustexist=True)\n"
        "root.destroy()\n"
        "print(selected) if selected else None\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", picker_program, str(initial_directory or "")],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError as error:
        raise NativeFolderPickerUnavailable("无法启动本机文件夹选择器。") from error
    if result.returncode != 0:
        raise NativeFolderPickerUnavailable(
            "无法打开本机文件夹选择器。请在具有桌面会话的本机环境运行 HostPilot。"
        )
    selected = result.stdout.strip()
    return Path(selected) if selected else None


__all__ = [
    "NativeFolderPickerUnavailable",
    "WorkspaceAccess",
    "WorkspaceAccessError",
    "choose_folder_native",
    "inspect_workspace",
]
