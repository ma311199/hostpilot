"""Local Markdown storage for per-conversation summaries and shared memory."""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

MAX_MEMORY_FILE_BYTES = 256 * 1024


class MarkdownMemoryStore:
    """Read and atomically replace scoped Markdown memory files.

    Callers must pass policy-approved, sanitized memory text. Markdown content
    is untrusted context and must never grant capabilities or tool permissions.
    """

    def __init__(
        self,
        root: Path | str,
        *,
        max_file_bytes: int = MAX_MEMORY_FILE_BYTES,
    ) -> None:
        self.root = Path(root).expanduser()
        self.max_file_bytes = max_file_bytes
        self._write_lock = RLock()

    @property
    def sessions_directory(self) -> Path:
        """Directory containing per-conversation summaries."""
        return self.root / "sessions"

    @property
    def shared_memory_path(self) -> Path:
        """Path to cross-conversation long-term memory."""
        return self.root / "long_term.md"

    def read_session_summary(self, conversation_id: str) -> str | None:
        """Read one conversation summary without exposing other sessions."""
        return self._read(self._session_path(conversation_id))

    def write_session_summary(self, conversation_id: str, content: str) -> None:
        """Atomically replace a single conversation's summary."""
        self._atomic_write(self._session_path(conversation_id), content)

    def delete_session_summary(self, conversation_id: str) -> bool:
        """Delete one summary without touching shared long-term memory."""
        path = self._session_path(conversation_id)
        with self._write_lock:
            self._ensure_root()
            self._ensure_safe_parent(path)
            if path.is_symlink():
                raise ValueError("Session memory cannot be a symbolic link.")
            if not path.exists():
                return False
            path.unlink()
            return True

    def read_long_term_memory(self) -> str | None:
        """Read shared Markdown memory for the local user."""
        return self._read(self.shared_memory_path)

    def write_long_term_memory(self, content: str) -> None:
        """Atomically replace shared Markdown memory.

        The caller owns proposal review and policy checks before invoking this method.
        """
        self._atomic_write(self.shared_memory_path, content)

    def _session_path(self, conversation_id: str) -> Path:
        try:
            normalized_id = str(UUID(conversation_id))
        except (ValueError, AttributeError) as error:
            raise ValueError("conversation_id must be a UUID.") from error
        return self.sessions_directory / f"{normalized_id}.md"

    def _read(self, path: Path) -> str | None:
        self._ensure_root()
        self._ensure_safe_parent(path)
        if path.is_symlink():
            raise ValueError("Memory files cannot be symbolic links.")
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > self.max_file_bytes:
            raise ValueError("Memory file is not a supported regular file.")
        return path.read_text(encoding="utf-8")

    def _atomic_write(self, path: Path, content: str) -> None:
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise ValueError("Memory content exceeds the configured size limit.")
        with self._write_lock:
            self._ensure_root()
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._ensure_safe_parent(path)
            os.chmod(path.parent, 0o700)
            if path.is_symlink():
                raise ValueError("Memory paths cannot be symbolic links.")
            temporary_path = path.parent / f".{path.name}.{uuid4().hex}.tmp"
            try:
                with temporary_path.open("x", encoding="utf-8", newline="\n") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary_path, 0o600)
                os.replace(temporary_path, path)
            finally:
                temporary_path.unlink(missing_ok=True)

    def _ensure_root(self) -> None:
        if self.root.is_symlink():
            raise ValueError("Memory root cannot be a symbolic link.")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise ValueError("Memory root must be a directory.")
        os.chmod(self.root, 0o700)

    def _ensure_safe_parent(self, path: Path) -> None:
        if path.parent.is_symlink():
            raise ValueError("Memory directories cannot be symbolic links.")
        if not path.parent.resolve().is_relative_to(self.root.resolve()):
            raise ValueError("Memory path escapes the configured root.")


__all__ = ["MAX_MEMORY_FILE_BYTES", "MarkdownMemoryStore"]
