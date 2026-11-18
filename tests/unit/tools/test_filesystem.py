from pathlib import Path

import pytest

from hostpilot.contracts import SandboxMode
from hostpilot.sandbox import sandbox_mode_scope
from hostpilot.tools.filesystem import WorkspaceFileError, WorkspaceFiles, WorkspaceReadOnlyError


def test_workspace_files_read_and_write_bounded_utf8_text(tmp_path: Path) -> None:
    workspace = WorkspaceFiles(tmp_path, max_file_bytes=16)
    (tmp_path / "readme.txt").write_text("hello", encoding="utf-8")

    assert workspace.read_text("readme.txt") == "hello"
    assert workspace.write_text("notes.txt", "你好") == len("你好".encode())
    assert (tmp_path / "notes.txt").read_text(encoding="utf-8") == "你好"

    with pytest.raises(WorkspaceFileError, match="size limit"):
        workspace.write_text("large.txt", "x" * 17)


def test_workspace_files_read_utf8_ranges_from_large_files(tmp_path: Path) -> None:
    content = "A" * 70_000 + "你好"
    (tmp_path / "large.txt").write_text(content, encoding="utf-8")
    workspace = WorkspaceFiles(tmp_path)

    assert workspace.read_text_range("large.txt", offset=65_536, length=100) == "A" * 100
    with pytest.raises(WorkspaceFileError, match="splits a UTF-8 character"):
        workspace.read_text_range("large.txt", offset=70_000, length=1)
    with pytest.raises(WorkspaceFileError, match="allowed bounds"):
        workspace.read_text_range("large.txt", offset=0, length=65_537)


def test_workspace_files_list_directory_is_bounded_and_skips_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "b.txt").write_text("b", encoding="utf-8")
    (root / "a.txt").write_text("aa", encoding="utf-8")
    (root / "nested").mkdir()
    (root / "secret-link").symlink_to(tmp_path / "outside", target_is_directory=True)
    workspace = WorkspaceFiles(root)

    result = workspace.list_directory(limit=2)

    assert result == {
        "entries": [
            {"name": "a.txt", "type": "file", "size": 2},
            {"name": "b.txt", "type": "file", "size": 1},
        ],
        "truncated": True,
    }


def test_workspace_files_search_is_bounded_and_respects_session_roots(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    (root / "one.txt").write_text("one", encoding="utf-8")
    nested = root / "nested"
    nested.mkdir()
    (nested / "two.txt").write_text("two", encoding="utf-8")
    (nested / "three.py").write_text("three", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    workspace = WorkspaceFiles(root)

    result = workspace.search_files("*.txt")
    assert result["matches"] == ["nested/two.txt", "one.txt"]
    assert result["truncated"] is False
    with pytest.raises(WorkspaceFileError, match="relative path"):
        workspace.search_files("*.txt", str(outside))

    with sandbox_mode_scope(SandboxMode.DANGER_FULL_ACCESS):
        unrestricted = workspace.search_files("outside.txt", str(tmp_path), max_depth=0)
    assert str(outside) in unrestricted["matches"]


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


def test_sandbox_modes_apply_session_scoped_filesystem_boundaries(tmp_path: Path) -> None:
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    workspace = WorkspaceFiles(workspace_root)

    with sandbox_mode_scope(SandboxMode.READ_ONLY):
        with pytest.raises(WorkspaceReadOnlyError, match="只读沙箱"):
            workspace.write_text("new.txt", "denied")

    with sandbox_mode_scope(SandboxMode.WORKSPACE_WRITE):
        workspace.write_text("inside.txt", "allowed")
        with pytest.raises(WorkspaceFileError, match="relative path"):
            workspace.read_text(str(outside))

    with sandbox_mode_scope(SandboxMode.DANGER_FULL_ACCESS):
        assert workspace.read_text(str(outside)) == "outside"
        workspace.write_text(str(outside), "updated")

    assert outside.read_text(encoding="utf-8") == "updated"
