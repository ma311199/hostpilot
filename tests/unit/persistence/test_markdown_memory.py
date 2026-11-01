import stat
from pathlib import Path

import pytest

from hostpilot.persistence import MarkdownMemoryStore


def test_session_summaries_are_isolated_from_shared_memory(tmp_path: Path) -> None:
    memory = MarkdownMemoryStore(tmp_path / "memory")
    first_id = "6c442c1e-6e95-4908-88ab-0b3a3c195443"
    second_id = "9fcb6ed8-264e-4729-9b0f-f99f7b853e1f"
    memory.write_session_summary(first_id, "# Session one\n")
    memory.write_session_summary(second_id, "# Session two\n")
    memory.write_long_term_memory("# Preferences\n")

    assert memory.read_session_summary(first_id) == "# Session one\n"
    assert memory.read_session_summary(second_id) == "# Session two\n"
    assert memory.read_long_term_memory() == "# Preferences\n"
    assert stat.S_IMODE((tmp_path / "memory").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "memory" / "long_term.md").stat().st_mode) == 0o600
    assert memory.delete_session_summary(first_id)
    assert memory.read_session_summary(first_id) is None
    assert memory.read_session_summary(second_id) == "# Session two\n"
    assert memory.read_long_term_memory() == "# Preferences\n"


def test_memory_rejects_path_traversal_and_oversized_content(tmp_path: Path) -> None:
    memory = MarkdownMemoryStore(tmp_path / "memory", max_file_bytes=10)

    with pytest.raises(ValueError, match="must be a UUID"):
        memory.read_session_summary("../../secret")
    with pytest.raises(ValueError, match="size limit"):
        memory.write_long_term_memory("x" * 11)


def test_memory_rejects_symlinked_session_directory(tmp_path: Path) -> None:
    root = tmp_path / "memory"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "sessions").symlink_to(outside, target_is_directory=True)
    memory = MarkdownMemoryStore(root)

    with pytest.raises(ValueError, match="symbolic links"):
        memory.write_session_summary(
            "6c442c1e-6e95-4908-88ab-0b3a3c195443",
            "summary",
        )
