from pathlib import Path

import pytest

from hostpilot.tools.filesystem import WorkspaceFileError, WorkspaceFiles


def test_workspace_files_read_and_write_bounded_utf8_text(tmp_path: Path) -> None:
    workspace = WorkspaceFiles(tmp_path, max_file_bytes=16)
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")

    assert workspace.read_text("readme.txt") == "hello"
    assert workspace.write_text("notes.txt", "你好") == len("你好".encode())
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "你好"

    with pytest.raises(WorkspaceFileError, match="size limit"):
        workspace.write_text("large.txt", "x" * 17)


@pytest.mark.parametrize("path", ("../outside.txt", "/tmp/outside.txt", "nested/../outside.txt"))
def test_workspace_files_reject_path_escape_and_traversal(tmp_path: Path, path: str) -> None:
    workspace = WorkspaceFiles(tmp_path)

    with pytest.raises(WorkspaceFileError, match="relative path"):
        workspace.read_text(path)


def test_workspace_files_reject_symbolic_links(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (tmp_path / "linked.txt").symlink_to(outside)
    workspace = WorkspaceFiles(tmp_path)

    with pytest.raises(WorkspaceFileError, match="Symbolic links"):
        workspace.read_text("linked.txt")
