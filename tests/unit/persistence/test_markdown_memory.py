import stat
from pathlib import Path
from uuid import uuid4

import pytest

from hostpilot.contracts import MemoryRawArchive, MemoryScope
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


def test_raw_memory_archive_round_trips_and_filters_other_sessions(tmp_path: Path) -> None:
    memory = MarkdownMemoryStore(tmp_path / "memory")
    first = MemoryRawArchive(
        archive_id=uuid4(),
        proposal_id="proposal-first",
        scope=MemoryScope.SESSION_SUMMARY,
        conversation_id="b1e1a2f4-a333-4f47-93fb-88936298d70c",
        importance_score=55,
        content="Original private detail",
    )
    second = MemoryRawArchive(
        archive_id=uuid4(),
        proposal_id="proposal-second",
        scope=MemoryScope.LONG_TERM,
        importance_score=55,
        content="Original shared detail",
    )
    memory.archive_raw_memory(first)
    memory.archive_raw_memory(second)

    visible = memory.list_raw_memory_archives(first.conversation_id, limit=10)

    assert {item.archive_id for item in visible} == {first.archive_id, second.archive_id}
    assert memory.read_raw_memory_archive(first.archive_id) == first
    assert b"Original private detail" in (
        memory.archives_directory / f"{first.archive_id}.json"
    ).read_bytes()


def test_raw_memory_archive_rejects_other_session_and_enforces_limit(tmp_path: Path) -> None:
    memory = MarkdownMemoryStore(tmp_path / "memory", max_archives=1)
    archive = MemoryRawArchive(
        archive_id=uuid4(),
        proposal_id="proposal-first",
        scope=MemoryScope.SESSION_SUMMARY,
        conversation_id="b1e1a2f4-a333-4f47-93fb-88936298d70c",
        importance_score=55,
        content="Private original detail",
    )
    memory.archive_raw_memory(archive)

    assert memory.list_raw_memory_archives("e300d895-4582-48dd-87b9-8a9e7e9c54cd") == ()
    with pytest.raises(ValueError, match="limit has been reached"):
        memory.archive_raw_memory(
            MemoryRawArchive(
                archive_id=uuid4(),
                proposal_id="proposal-second",
                scope=MemoryScope.LONG_TERM,
                importance_score=55,
                content="Shared original detail",
            )
        )
