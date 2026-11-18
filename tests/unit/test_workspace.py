from pathlib import Path

import pytest

from hostpilot.tools.filesystem import WorkspaceFiles, WorkspaceReadOnlyError
from hostpilot.workspace import WorkspaceAccessError, inspect_workspace


def test_inspect_workspace_resolves_directory_and_detects_writability(tmp_path: Path) -> None:
    selected = tmp_path / "workspace"
    selected.mkdir()

    access = inspect_workspace(selected)

    assert access.path == selected.resolve()
    assert access.read_only is False
    assert list(selected.iterdir()) == []


def test_inspect_workspace_fails_when_directory_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hostpilot import workspace

    selected = tmp_path / "workspace"
    selected.mkdir()
    original_scandir = workspace.os.scandir

    def denied_scandir(path):
        if Path(path) == selected:
            raise PermissionError("denied")
        return original_scandir(path)

    monkeypatch.setattr(workspace.os, "scandir", denied_scandir)

    with pytest.raises(WorkspaceAccessError, match="没有读取"):
        inspect_workspace(selected)


def test_inspect_workspace_marks_directory_read_only_when_write_probe_is_denied(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from hostpilot import workspace

    selected = tmp_path / "workspace"
    selected.mkdir()

    def denied_write_probe(*args, **kwargs):
        del args, kwargs
        raise PermissionError("denied")

    monkeypatch.setattr(workspace.tempfile, "mkstemp", denied_write_probe)

    access = inspect_workspace(selected)

    assert access.path == selected.resolve()
    assert access.read_only is True


def test_read_only_workspace_rejects_write_before_file_creation(tmp_path: Path) -> None:
    selected = tmp_path / "workspace"
    selected.mkdir()
    files = WorkspaceFiles(selected, read_only=True)

    with pytest.raises(WorkspaceReadOnlyError, match="只读模式"):
        files.write_text("blocked.txt", "no write")
    assert list(selected.iterdir()) == []
