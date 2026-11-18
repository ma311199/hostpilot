"""Bounded text-file operations confined to the local HostPilot workspace."""

from __future__ import annotations

import fnmatch
import os
from pathlib import Path

from hostpilot.contracts import SandboxMode
from hostpilot.sandbox import current_sandbox_mode


class WorkspaceFileError(ValueError):
    """Raised when a workspace file operation is unsafe or cannot be completed."""


class WorkspaceReadOnlyError(WorkspaceFileError):
    """Raised when a write is attempted against a read-only workspace."""


class WorkspaceFiles:
    """Read and write UTF-8 text files below a fixed workspace directory."""

    def __init__(
        self,
        root: Path,
        *,
        max_file_bytes: int = 65_536,
        read_only: bool = False,
    ) -> None:
        if max_file_bytes < 1:
            raise ValueError("Maximum file size must be positive.")
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace root must be a directory.")
        self.max_file_bytes = max_file_bytes
        self.read_only = read_only
        self._sandbox_mode: SandboxMode | None = None

    def set_sandbox_mode(self, mode: SandboxMode | None) -> None:
        """Set the serialized chat request mode for MCP server-side tool handlers."""
        self._sandbox_mode = mode

    @property
    def active_sandbox_mode(self) -> SandboxMode:
        """Return the explicit session mode, defaulting to the least privilege."""
        return self._sandbox_mode or current_sandbox_mode() or SandboxMode.READ_ONLY

    def set_workspace(self, root: Path, *, read_only: bool) -> None:
        """Switch the root used by already registered tools after validation."""
        resolved = root.resolve(strict=True)
        if not resolved.is_dir():
            raise ValueError("Workspace root must be a directory.")
        self.root = resolved
        self.read_only = read_only

    def read_text(self, relative_path: str) -> str:
        """Read a bounded UTF-8 file after validating its path and file type."""
        path = self._resolve(relative_path)
        if not path.is_file():
            raise WorkspaceFileError("Workspace path is not a regular file.")
        try:
            content = path.read_bytes()
        except OSError as error:
            raise WorkspaceFileError("Workspace file could not be read.") from error
        if len(content) > self.max_file_bytes:
            raise WorkspaceFileError("Workspace file exceeds the configured size limit.")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkspaceFileError("Workspace file is not valid UTF-8 text.") from error

    def read_text_range(self, relative_path: str, *, offset: int, length: int) -> str:
        """Read a bounded UTF-8 byte range without loading the whole file."""
        if offset < 0 or length < 1 or length > self.max_file_bytes:
            raise WorkspaceFileError("Read range is outside the allowed bounds.")
        path = self._resolve(relative_path)
        if not path.is_file():
            raise WorkspaceFileError("Workspace path is not a regular file.")
        try:
            with path.open("rb") as source:
                source.seek(offset)
                content = source.read(length)
        except OSError as error:
            raise WorkspaceFileError("Workspace file could not be read.") from error
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WorkspaceFileError(
                "The requested byte range splits a UTF-8 character; adjust offset and length."
            ) from error

    def list_directory(self, relative_path: str = "", *, limit: int = 100) -> dict[str, object]:
        """List immediate non-symlink entries with a bounded result count."""
        if not 1 <= limit <= 100:
            raise WorkspaceFileError("Directory listing limit must be between 1 and 100.")
        directory = self._resolve_directory(relative_path)
        if not directory.is_dir():
            raise WorkspaceFileError("Workspace path is not a directory.")
        entries: list[os.DirEntry[str]] = []
        scan_truncated = False
        try:
            with os.scandir(directory) as iterator:
                for entry in iterator:
                    if len(entries) >= 5000:
                        scan_truncated = True
                        break
                    entries.append(entry)
        except OSError as error:
            raise WorkspaceFileError("Workspace directory could not be read.") from error
        entries.sort(key=lambda entry: entry.name.casefold())
        visible: list[dict[str, str | int | None]] = []
        for entry in entries:
            if entry.is_symlink():
                continue
            try:
                if entry.is_dir(follow_symlinks=False):
                    visible.append({"name": entry.name, "type": "directory", "size": None})
                elif entry.is_file(follow_symlinks=False):
                    visible.append(
                        {"name": entry.name, "type": "file", "size": entry.stat().st_size}
                    )
            except OSError as error:
                raise WorkspaceFileError("Workspace entry could not be inspected.") from error
        return {
            "entries": visible[:limit],
            "truncated": scan_truncated or len(visible) > limit,
        }

    def search_files(
        self,
        pattern: str,
        relative_path: str = "",
        *,
        max_results: int = 50,
        max_depth: int = 6,
    ) -> dict[str, object]:
        """Search file names without following symlinks or scanning unbounded trees."""
        if not pattern.strip() or len(pattern) > 256 or "/" in pattern or "\\" in pattern:
            raise WorkspaceFileError("Search pattern must be a bounded file-name glob.")
        if not 1 <= max_results <= 100 or not 0 <= max_depth <= 8:
            raise WorkspaceFileError("Search result or depth limit is outside allowed bounds.")
        root = self._resolve_directory(relative_path)
        if not root.is_dir():
            raise WorkspaceFileError("Workspace path is not a directory.")

        pending: list[tuple[Path, int]] = [(root, 0)]
        matches: list[str] = []
        scanned = 0
        truncated = False
        mode = self._sandbox_mode or current_sandbox_mode()
        while pending:
            directory, depth = pending.pop()
            try:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > 5000:
                            truncated = True
                            pending.clear()
                            break
                        if entry.is_symlink():
                            continue
                        try:
                            candidate = Path(entry.path)
                            if entry.is_file(follow_symlinks=False) and fnmatch.fnmatchcase(
                                entry.name.casefold(), pattern.casefold()
                            ):
                                if mode is SandboxMode.DANGER_FULL_ACCESS:
                                    matches.append(str(candidate))
                                else:
                                    matches.append(candidate.relative_to(self.root).as_posix())
                                if len(matches) > max_results:
                                    truncated = True
                                    pending.clear()
                                    break
                            elif entry.is_dir(follow_symlinks=False) and depth < max_depth:
                                pending.append((candidate, depth + 1))
                        except OSError as error:
                            raise WorkspaceFileError(
                                "Workspace entry could not be searched."
                            ) from error
            except OSError as error:
                raise WorkspaceFileError("Workspace directory could not be searched.") from error
        matches.sort(key=str.casefold)
        return {
            "matches": matches[:max_results],
            "scanned_entries": min(scanned, 5000),
            "truncated": truncated,
        }

    def write_text(self, relative_path: str, content: str) -> int:
        """Write a bounded UTF-8 file below the workspace and return its byte count."""
        path = self.validate_write_path(relative_path)
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise WorkspaceFileError("Content exceeds the configured file size limit.")
        try:
            path.write_bytes(encoded)
        except OSError as error:
            raise WorkspaceFileError("Workspace file could not be written.") from error
        return len(encoded)

    def restore_text(self, relative_path: str, *, expected_current: str, original: str) -> None:
        """Restore a file only while it still contains the value written by this action."""
        path = self._resolve(relative_path)
        if not path.is_file():
            raise WorkspaceFileError("Workspace file no longer exists.")
        current = self.read_text(relative_path)
        if current == original:
            return
        if current != expected_current:
            raise WorkspaceFileError("Workspace file changed after the compensated write.")
        self.write_text(relative_path, original)

    def validate_write_path(self, relative_path: str) -> Path:
        """Validate a future write target before presenting it for confirmation."""
        mode = self._sandbox_mode or current_sandbox_mode()
        if mode is SandboxMode.READ_ONLY:
            raise WorkspaceReadOnlyError("当前会话处于只读沙箱，不能写入文件。")
        if mode is not SandboxMode.DANGER_FULL_ACCESS and self.read_only:
            raise WorkspaceReadOnlyError("当前工作区为只读模式，不能写入文件。")
        path = self._resolve(relative_path)
        if not path.parent.is_dir():
            raise WorkspaceFileError("Parent directory must already exist in the workspace.")
        return path

    def validate_read_path(self, relative_path: str) -> Path:
        """Validate a read path without requiring the target to exist yet."""
        return self._resolve(relative_path)

    def validate_directory_path(self, relative_path: str = "") -> Path:
        """Validate an optional directory path without requiring it to exist."""
        return self._resolve_directory(relative_path)

    def _resolve_directory(self, relative_path: str) -> Path:
        """Resolve an optional directory path, using the workspace root when empty."""
        if not relative_path.strip() or relative_path.strip() == ".":
            return self.root
        return self._resolve(relative_path)

    def _resolve(self, relative_path: str) -> Path:
        """Reject absolute paths, traversal, symlinks, and workspace escapes."""
        candidate_path = Path(relative_path.strip())
        mode = self._sandbox_mode or current_sandbox_mode()
        danger_mode = mode is SandboxMode.DANGER_FULL_ACCESS
        if (
            not relative_path.strip()
            or (not danger_mode and candidate_path.is_absolute())
            or (not danger_mode and any(part in {".", ".."} for part in candidate_path.parts))
        ):
            raise WorkspaceFileError("Use a workspace-relative path without traversal segments.")

        root = Path(self.root.anchor) if danger_mode else self.root
        candidate = (
            candidate_path
            if danger_mode and candidate_path.is_absolute()
            else root / candidate_path
        )
        if not danger_mode:
            current = self.root
            for part in candidate_path.parts:
                current = current / part
                if current.is_symlink():
                    raise WorkspaceFileError("Symbolic links are not allowed in workspace paths.")
        resolved = candidate.resolve(strict=False)
        if not danger_mode and not resolved.is_relative_to(self.root):
            raise WorkspaceFileError("Workspace path escapes the configured root.")
        return resolved


__all__ = ["WorkspaceFileError", "WorkspaceFiles", "WorkspaceReadOnlyError"]
