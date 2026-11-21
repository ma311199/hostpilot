"""Bounded filesystem operations executed inside Linux bubblewrap namespaces."""

from __future__ import annotations

import base64
import json
from functools import cached_property
from typing import Any, Literal

from hostpilot.contracts import SandboxMode, SandboxPolicy, SandboxResourceLimits
from hostpilot.sandbox.linux import LinuxPythonSandbox
from hostpilot.tools.filesystem import WorkspaceFileError, WorkspaceFiles

MountMode = Literal["--ro-bind", "--bind"]

_FILE_OPERATION_SOURCE = r"""
import base64
import fnmatch
import json
import os
import posixpath
import sys
from pathlib import Path

request = json.loads(__REQUEST_JSON__)
operation = request["operation"]
root = Path(request["root"]).resolve()
relative = request.get("path", "")
candidate = Path(relative)
if candidate.is_absolute() or any(part in {".", ".."} for part in candidate.parts):
    raise ValueError("Unsafe sandbox path.")
target = root
for part in candidate.parts:
    target = target / part
    if target.is_symlink():
        raise ValueError("Symbolic links are not allowed.")
target = target.resolve(strict=False)
if not target.is_relative_to(root):
    raise ValueError("Path escaped its authorized mount.")

if operation == "read_text_file":
    if not target.is_file():
        raise ValueError("Path is not a regular file.")
    content = target.read_bytes()
    if len(content) > request["max_file_bytes"]:
        raise ValueError("File exceeds the configured size limit.")
    content.decode("utf-8")
    result = {"content_b64": base64.b64encode(content).decode("ascii")}
elif operation == "read_text_range":
    if not target.is_file():
        raise ValueError("Path is not a regular file.")
    with target.open("rb") as file:
        file.seek(request["offset"])
        content = file.read(request["length"])
    content.decode("utf-8")
    result = {"content_b64": base64.b64encode(content).decode("ascii")}
elif operation == "list_directory":
    if not target.is_dir():
        raise ValueError("Path is not a directory.")
    entries = []
    scanned = 0
    truncated = False
    with os.scandir(target) as iterator:
        for entry in iterator:
            scanned += 1
            if scanned > 5000:
                truncated = True
                break
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                entries.append({"name": entry.name, "type": "directory", "size": None})
            elif entry.is_file(follow_symlinks=False):
                entries.append({"name": entry.name, "type": "file", "size": entry.stat().st_size})
    entries.sort(key=lambda entry: entry["name"].casefold())
    result = {"entries": entries[:request["limit"]],
              "truncated": truncated or len(entries) > request["limit"]}
elif operation == "search_files":
    if not target.is_dir():
        raise ValueError("Path is not a directory.")
    pending = [(target, 0)]
    matches = []
    scanned = 0
    truncated = False
    while pending:
        directory, depth = pending.pop()
        with os.scandir(directory) as iterator:
            for entry in iterator:
                scanned += 1
                if scanned > 5000:
                    truncated = True
                    pending.clear()
                    break
                if entry.is_symlink():
                    continue
                child = Path(entry.path)
                if entry.is_file(follow_symlinks=False) and fnmatch.fnmatchcase(
                    entry.name.casefold(), request["pattern"].casefold()
                ):
                    relative_match = child.relative_to(target).as_posix()
                    matches.append(posixpath.join(request["display_prefix"], relative_match))
                    if len(matches) > request["max_results"]:
                        truncated = True
                        pending.clear()
                        break
                elif entry.is_dir(follow_symlinks=False) and depth < request["max_depth"]:
                    pending.append((child, depth + 1))
    matches.sort(key=str.casefold)
    result = {"matches": matches[:request["max_results"]],
              "scanned_entries": min(scanned, 5000), "truncated": truncated}
elif operation in {"write_text_file", "write_existing_text_file", "restore_text"}:
    target.parent.mkdir(parents=False, exist_ok=True) if request.get("create_parent") else None
    if operation == "write_existing_text_file" and not target.is_file():
        raise ValueError("Target file does not exist.")
    content = base64.b64decode(request["content_b64"], validate=True)
    if len(content) > request["max_file_bytes"]:
        raise ValueError("Content exceeds the configured size limit.")
    content.decode("utf-8")
    if operation == "restore_text":
        if not target.is_file():
            raise ValueError("Target file no longer exists.")
        current = target.read_text(encoding="utf-8")
        if current == request["original"]:
            result = {"restored": False}
        elif current != request["expected_current"]:
            raise ValueError("Target file changed after the compensated write.")
        else:
            target.write_bytes(content)
            result = {"restored": True}
    else:
        target.write_bytes(content)
        result = {"bytes_written": len(content)}
else:
    raise ValueError("Unsupported sandbox file operation.")

print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
"""


class LinuxWorkspaceSandbox:
    """Execute fixed workspace operations with only per-request path mounts."""

    def __init__(self) -> None:
        self._runner = LinuxPythonSandbox(
            policy=SandboxPolicy(
                allowed=True,
                capabilities=frozenset({"filesystem.operation"}),
                network_access=False,
                filesystem_access=False,
                resource_limits=SandboxResourceLimits(
                    cpu_seconds=2,
                    memory_bytes=268_435_456,
                    max_processes=1,
                ),
            ),
            max_source_bytes=524_288,
            max_output_bytes=262_144,
            capability="filesystem.operation",
        )

    @cached_property
    def host_supported(self) -> bool:
        """Return whether the Linux namespace and its Python runtime pass a probe."""
        return self._runner.host_supported

    @cached_property
    def unavailable_reason(self) -> str | None:
        """Return the install or host-configuration hint for this filesystem sandbox."""
        return self._runner.unavailable_reason

    async def execute(
        self,
        workspace: WorkspaceFiles,
        operation: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Run one previously validated operation inside its least-privilege mounts."""
        if not self.host_supported:
            raise WorkspaceFileError("Linux filesystem sandbox is unavailable.")
        payload, mounts = self._request(workspace, operation, arguments)
        request_json = json.dumps(payload, ensure_ascii=True, separators=(",", ":"))
        source = _FILE_OPERATION_SOURCE.replace("__REQUEST_JSON__", repr(request_json))
        result = await self._runner.run(source, extra_mounts=mounts)
        if result.status != "completed":
            detail = result.stderr.strip()[:512]
            raise WorkspaceFileError(f"Sandboxed file operation failed ({result.status}): {detail}")
        try:
            decoded = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise WorkspaceFileError(
                "Sandbox returned an invalid file operation result."
            ) from error
        if operation in {"read_text_file", "read_text_range"}:
            try:
                return base64.b64decode(decoded["content_b64"], validate=True).decode("utf-8")
            except (KeyError, ValueError, UnicodeDecodeError) as error:
                raise WorkspaceFileError("Sandbox returned invalid UTF-8 file data.") from error
        if operation in {"write_text_file", "write_existing_text_file"}:
            return decoded["bytes_written"]
        if operation == "restore_text":
            return decoded["restored"]
        return decoded

    def _request(
        self,
        workspace: WorkspaceFiles,
        operation: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], tuple[tuple[MountMode, str, str], ...]]:
        mode = workspace.active_sandbox_mode
        file_operations = {"read_text_file", "read_text_range"}
        directory_operations = {"list_directory", "search_files"}
        write_operations = {"write_text_file", "write_existing_text_file", "restore_text"}
        if operation not in file_operations | directory_operations | write_operations:
            raise WorkspaceFileError("Unsupported sandbox file operation.")

        if mode is SandboxMode.DANGER_FULL_ACCESS:
            request, mount = self._danger_request(workspace, operation, arguments)
            return request, (mount,)

        root = workspace.root
        writable = mode is SandboxMode.WORKSPACE_WRITE and not workspace.read_only
        mount_mode: MountMode = "--bind" if writable else "--ro-bind"
        request = {
            "operation": operation,
            "root": "/workspace",
            "path": arguments.get("path", ""),
            "display_prefix": "",
            "max_file_bytes": workspace.max_file_bytes,
            **{key: value for key, value in arguments.items() if key != "path"},
        }
        if operation == "write_text_file":
            request["content_b64"] = base64.b64encode(
                str(arguments["content"]).encode("utf-8")
            ).decode("ascii")
            request["path"] = arguments["path"]
        elif operation == "write_existing_text_file":
            request["content_b64"] = base64.b64encode(
                str(arguments["content"]).encode("utf-8")
            ).decode("ascii")
            request["path"] = arguments["path"]
        elif operation == "restore_text":
            request["content_b64"] = base64.b64encode(
                str(arguments["original"]).encode("utf-8")
            ).decode("ascii")
            request["path"] = arguments["path"]
        return request, ((mount_mode, str(root), "/workspace"),)

    def _danger_request(
        self,
        workspace: WorkspaceFiles,
        operation: str,
        arguments: dict[str, Any],
    ) -> tuple[dict[str, Any], tuple[MountMode, str, str]]:
        request: dict[str, Any] = {
            "operation": operation,
            "max_file_bytes": workspace.max_file_bytes,
            "display_prefix": "",
        }
        if operation in {"list_directory", "search_files"}:
            directory = workspace.validate_directory_path(str(arguments.get("path", "")))
            request.update({"root": "/grant", "path": ""})
            request["display_prefix"] = str(directory)
            mount: tuple[MountMode, str, str] = ("--ro-bind", str(directory), "/grant")
        elif operation in {"read_text_file", "read_text_range"}:
            target = workspace.validate_read_path(str(arguments["path"]))
            request.update({"root": "/grant", "path": ""})
            mount = ("--ro-bind", str(target), "/grant")
        else:
            target = workspace.validate_write_path(str(arguments["path"]))
            request.update({"root": "/grant-parent", "path": target.name})
            mount = ("--bind", str(target.parent), "/grant-parent")

        request.update({key: value for key, value in arguments.items() if key != "path"})
        if operation in {"write_text_file", "write_existing_text_file"}:
            request["content_b64"] = base64.b64encode(
                str(arguments["content"]).encode("utf-8")
            ).decode("ascii")
        elif operation == "restore_text":
            request["content_b64"] = base64.b64encode(
                str(arguments["original"]).encode("utf-8")
            ).decode("ascii")
        return request, mount


__all__ = ["LinuxWorkspaceSandbox"]
