"""Local Markdown storage for per-conversation summaries and shared memory."""

from __future__ import annotations

import os
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

from hostpilot.contracts import MemoryRawArchive

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
        max_archives: int = 500,
    ) -> None:
        if max_file_bytes < 1 or max_archives < 1:
            raise ValueError("Memory file and archive limits must be positive.")
        self.root = Path(root).expanduser()
        self.max_file_bytes = max_file_bytes
        self.max_archives = max_archives
        self._write_lock = RLock()

    @property
    def sessions_directory(self) -> Path:
        """Directory containing per-conversation summaries."""
        return self.root / "sessions"

    @property
    def shared_memory_path(self) -> Path:
        """Path to cross-conversation long-term memory."""
        return self.root / "long_term.md"

    @property
    def archives_directory(self) -> Path:
        """Directory containing original content for medium-tier memories."""
        return self.root / "archives"

    def read_session_summary(self, session_id: str) -> str | None:
        """Read one conversation summary without exposing other sessions."""
        return self._read(self._session_path(session_id))

    def write_session_summary(self, session_id: str, content: str) -> None:
        """Atomically replace a single conversation's summary."""
        self._atomic_write(self._session_path(session_id), content)

    def delete_session_summary(self, session_id: str) -> bool:
        """Delete one summary without touching shared long-term memory."""
        path = self._session_path(session_id)
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

    def archive_raw_memory(self, archive: MemoryRawArchive) -> None:
        """Atomically persist original memory content with non-sensitive metadata."""
        path = self._archive_path(archive.archive_id)
        encoded = archive.model_dump_json()
        with self._write_lock:
            self._ensure_root()
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            self._ensure_safe_parent(path)
            if path.is_symlink():
                raise ValueError("Memory archives cannot be symbolic links.")
            if not path.exists() and len(tuple(path.parent.glob("*.json"))) >= self.max_archives:
                raise ValueError("Memory archive limit has been reached.")
            self._atomic_write(path, encoded)

    def read_raw_memory_archive(self, archive_id: UUID) -> MemoryRawArchive | None:
        """Read one validated original memory archive by its immutable ID."""
        content = self._read(self._archive_path(archive_id))
        if content is None:
            return None
        return MemoryRawArchive.model_validate_json(content)

    def list_raw_memory_archives(
        self, session_id: str, *, limit: int = 100
    ) -> tuple[MemoryRawArchive, ...]:
        """List shared and current-session archives in newest-first order."""
        if not 1 <= limit <= 500:
            raise ValueError("Memory archive list limit must be between 1 and 500.")
        self._ensure_root()
        directory = self.archives_directory
        self._ensure_safe_parent(directory / "placeholder.json")
        if not directory.exists():
            return ()
        archives: list[MemoryRawArchive] = []
        for path in directory.glob("*.json"):
            content = self._read(path)
            if content is None:
                continue
            archive = MemoryRawArchive.model_validate_json(content)
            if archive.scope.value == "long_term" or archive.session_id == session_id:
                archives.append(archive)
        archives.sort(key=lambda item: (item.created_at, str(item.archive_id)), reverse=True)
        return tuple(archives[:limit])

    def delete_raw_memory_archive(self, archive_id: UUID) -> bool:
        """Delete one archive, used when its paired summary write fails."""
        path = self._archive_path(archive_id)
        with self._write_lock:
            self._ensure_root()
            self._ensure_safe_parent(path)
            if path.is_symlink():
                raise ValueError("Memory archives cannot be symbolic links.")
            if not path.exists():
                return False
            path.unlink()
            return True

    def _archive_path(self, archive_id: UUID) -> Path:
        return self.archives_directory / f"{archive_id}.json"

    def _session_path(self, session_id: str) -> Path:
        try:
            normalized_id = str(UUID(session_id))
        except (ValueError, AttributeError) as error:
            raise ValueError("session_id must be a UUID.") from error
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
