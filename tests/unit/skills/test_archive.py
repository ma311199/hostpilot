import stat
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from hostpilot.skills.archive import SkillArchiveImportError, import_skill_archive
from hostpilot.skills.registry import SkillRegistry
from hostpilot.skills.yaml_registry import YamlSkillRegistry


def build_zip(files: dict[str, str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def test_imports_one_wrapped_markdown_skill_and_keeps_text_resources(tmp_path: Path) -> None:
    archive = build_zip(
        {
            "meeting-notes/SKILL.md": (
                "---\nname: meeting-notes\ndescription: Turn notes into minutes.\n---\n\n"
                "Summarize decisions.\n"
            ),
            "meeting-notes/reference.md": "Use concise headings.\n",
            "meeting-notes/helpers.py": "raise RuntimeError('resource was executed')\n",
            "meeting-notes/check.sh": "exit 42\n",
            "meeting-notes/metadata.json": '{"format": "example"}\n',
        }
    )
    root = tmp_path / "skills"

    result = import_skill_archive(archive, root, bundled_root=tmp_path / "bundled")

    assert result.skill_id == "legacy/meeting-notes"
    assert result.execution_format == "skill_md"
    assert (root / "meeting-notes" / "reference.md").read_text() == "Use concise headings.\n"
    package = SkillRegistry(root).load("meeting-notes")
    assert package.resources["helpers.py"] == "raise RuntimeError('resource was executed')\n"
    assert package.resources["check.sh"] == "exit 42\n"
    assert package.resources["metadata.json"] == '{"format": "example"}\n'
    assert result.directory == root / "meeting-notes"


@pytest.mark.parametrize(
    "files",
    [
        {"../escape.txt": "no"},
        {"skills/../../escape.txt": "no"},
        {"skill/runner.exe": "no"},
    ],
)
def test_rejects_unsafe_or_unsupported_archive_paths_without_installing(
    tmp_path: Path, files: dict[str, str]
) -> None:
    root = tmp_path / "skills"

    with pytest.raises(SkillArchiveImportError):
        import_skill_archive(build_zip(files), root, bundled_root=tmp_path / "bundled")

    assert not root.exists()
    assert not (tmp_path / "escape.txt").exists()


def test_rejects_symlink_archive_members(tmp_path: Path) -> None:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        info = zipfile.ZipInfo("skill/SKILL.md")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "../../outside")
    root = tmp_path / "skills"

    with pytest.raises(SkillArchiveImportError, match="不支持的文件类型"):
        import_skill_archive(buffer.getvalue(), root, bundled_root=tmp_path / "bundled")

    assert not root.exists()


def test_rejects_duplicate_skill_without_overwriting_existing_files(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    existing = root / "meeting-notes"
    existing.mkdir(parents=True)
    original = "---\nname: meeting-notes\ndescription: Existing notes.\n---\n\nKeep this.\n"
    (existing / "SKILL.md").write_text(original, encoding="utf-8")
    archive = build_zip(
        {"SKILL.md": "---\nname: meeting-notes\ndescription: New notes.\n---\n\nReplace?\n"}
    )

    with pytest.raises(SkillArchiveImportError, match="同名 Skill 已存在"):
        import_skill_archive(archive, root, bundled_root=tmp_path / "bundled")

    assert (existing / "SKILL.md").read_text(encoding="utf-8") == original


def test_imports_yaml_skill_from_single_top_level_directory(tmp_path: Path) -> None:
    manifest = """schema_version: 1
meta:
  skill_id: community/archive-example
  name: Archive example
  version: 1.0.0
intent_summary: An imported test Skill.
tags: [example]
examples: [Run the archive example.]
input_schema:
  type: object
output_schema:
  type: object
side_effect_level: read_only
execution:
  kind: steps
  steps:
    - step_id: answer
      action: llm.complete
"""
    archive = build_zip(
        {
            "archive-example/skill.yaml": manifest,
            "archive-example/SKILL.md": "# Archive example\nDetails.\n",
            "archive-example/runner.py": "raise RuntimeError('resource was executed')\n",
            "archive-example/runner.sh": "exit 42\n",
            "archive-example/data.json": '{"safe": true}\n',
        }
    )
    root = tmp_path / "skills"

    result = import_skill_archive(archive, root, bundled_root=tmp_path / "bundled")

    assert result.skill_id == "community/archive-example"
    assert result.execution_format == "yaml"
    assert result.directory.is_relative_to(root)
    catalog = YamlSkillRegistry(root).discover()
    assert [skill.skill_id for skill in catalog.skills] == ["community/archive-example"]
    assert catalog.issues == ()
    resources = catalog.skills[0].resources
    assert resources["runner.py"] == "raise RuntimeError('resource was executed')\n"
    assert resources["runner.sh"] == "exit 42\n"
    assert resources["data.json"] == '{"safe": true}\n'
