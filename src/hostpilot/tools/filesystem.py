"""Bounded text-file operations confined to the local HostPilot workspace."""

from __future__ import annotations

from pathlib import Path


class WorkspaceFileError(ValueError):
    """Raised when a workspace file operation is unsafe or cannot be completed."""


class WorkspaceFiles:
    """Read and write UTF-8 text files below a fixed workspace directory."""

    def __init__(self, root: Path, *, max_file_bytes: int = 65_536) -> None:
        if max_file_bytes < 1:
            raise ValueError("Maximum file size must be positive.")
        self.root = root.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("Workspace root must be a directory.")
        self.max_file_bytes = max_file_bytes

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

    def validate_write_path(self, relative_path: str) -> Path:
        """Validate a future write target before presenting it for confirmation."""
        path = self._resolve(relative_path)
        if not path.parent.is_dir():
            raise WorkspaceFileError("Parent directory must already exist in the workspace.")
        return path

    def validate_read_path(self, relative_path: str) -> Path:
        """Validate a read path without requiring the target to exist yet."""
        return self._resolve(relative_path)

    def _resolve(self, relative_path: str) -> Path:
        """Reject absolute paths, traversal, symlinks, and workspace escapes."""
        candidate_path = Path(relative_path.strip())
        if (
            not relative_path.strip()
            or candidate_path.is_absolute()
            or any(part in {".", ".."} for part in candidate_path.parts)
        ):
            raise WorkspaceFileError("Use a workspace-relative path without traversal segments.")

        candidate = self.root / candidate_path
        current = self.root
        for part in candidate_path.parts:
            current = current / part
            if current.is_symlink():
                raise WorkspaceFileError("Symbolic links are not allowed in workspace paths.")
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.root):
            raise WorkspaceFileError("Workspace path escapes the configured root.")
        return resolved


__all__ = ["WorkspaceFileError", "WorkspaceFiles"]
