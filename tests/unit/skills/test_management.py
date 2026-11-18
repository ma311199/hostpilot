from pathlib import Path

import pytest

from hostpilot.skills.management import delete_skill, set_skill_enabled
from hostpilot.skills.registry import DISABLED_MARKER, SkillRegistry
from hostpilot.skills.yaml_registry import YamlSkillRegistry

YAML_MANIFEST = """schema_version: 1
meta:
  skill_id: community/manage-example
  name: Manage example
  version: 1.0.0
intent_summary: A Skill used to test package management.
tags: [example]
examples: [Manage this test Skill.]
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


def write_markdown_skill(root: Path, name: str = "meeting-notes") -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: Turn notes into minutes.\n---\n\nSummarize.\n",
        encoding="utf-8",
    )
    return package


def write_yaml_skill(root: Path) -> Path:
    package = root / "manage-example"
    package.mkdir(parents=True)
    (package / "skill.yaml").write_text(YAML_MANIFEST, encoding="utf-8")
    return package


def test_markdown_enable_state_uses_hidden_marker_and_is_read_by_registry(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    package = write_markdown_skill(root)

    disabled = set_skill_enabled(root, "legacy/meeting-notes", False)
    assert disabled.enabled is False
    assert (package / DISABLED_MARKER).is_file()
    assert SkillRegistry(root).load("meeting-notes").enabled is False

    enabled = set_skill_enabled(root, "legacy/meeting-notes", True)
    assert enabled.enabled is True
    assert not (package / DISABLED_MARKER).exists()
    assert SkillRegistry(root).load("meeting-notes").enabled is True


def test_yaml_enable_state_updates_manifest_and_registry(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_yaml_skill(root)

    result = set_skill_enabled(root, "community/manage-example", False)

    assert result.enabled is False
    assert YamlSkillRegistry(root).load("community/manage-example").manifest.meta.enabled is False
    enabled = set_skill_enabled(root, "community/manage-example", True)
    assert enabled.enabled is True
    assert YamlSkillRegistry(root).load("community/manage-example").manifest.meta.enabled is True


def test_delete_removes_only_validated_user_package(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    package = write_markdown_skill(root)

    delete_skill(root, "legacy/meeting-notes")

    assert not package.exists()
    with pytest.raises(KeyError):
        delete_skill(root, "legacy/meeting-notes")


def test_management_rejects_bundled_or_unvalidated_packages(tmp_path: Path) -> None:
    root = tmp_path / "skills"

    with pytest.raises(KeyError):
        set_skill_enabled(root, "business/code-review-check", False)
    with pytest.raises(KeyError):
        delete_skill(root, "business/code-review-check")


def test_management_refuses_package_directories_outside_user_root(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    outside = write_markdown_skill(tmp_path / "outside")
    (root).mkdir()
    (root / "meeting-notes").symlink_to(outside, target_is_directory=True)

    with pytest.raises(KeyError):
        set_skill_enabled(root, "legacy/meeting-notes", False)
    with pytest.raises(KeyError):
        delete_skill(root, "legacy/meeting-notes")
    assert (outside / "SKILL.md").exists()
