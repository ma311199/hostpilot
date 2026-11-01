from pathlib import Path

import pytest

from hostpilot.skills.registry import SkillRegistry, SkillValidationError


def write_skill(root: Path, name: str = "meeting-notes") -> Path:
    package = root / name
    package.mkdir(parents=True)
    (package / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        "description: Turn rough notes into clear minutes.\n"
        "---\n\n"
        "# Instructions\n\n"
        "Summarize decisions and action items.\n",
        encoding="utf-8",
    )
    return package


def test_discover_loads_valid_skill_and_hashes_resources(tmp_path: Path) -> None:
    package = write_skill(tmp_path)
    (package / "reference.md").write_text("Use concise headings.\n", encoding="utf-8")
    registry = SkillRegistry(tmp_path)

    catalog = registry.discover()

    assert len(catalog.skills) == 1
    skill = catalog.skills[0]
    assert skill.name == "meeting-notes"
    assert skill.description == "Turn rough notes into clear minutes."
    assert skill.resources["reference.md"] == "Use concise headings.\n"
    assert len(skill.summary.content_hash) == 64
    assert catalog.issues == ()


def test_discover_reports_invalid_package_without_hiding_valid_skills(tmp_path: Path) -> None:
    write_skill(tmp_path)
    invalid = tmp_path / "broken"
    invalid.mkdir()
    (invalid / "SKILL.md").write_text("no frontmatter", encoding="utf-8")
    registry = SkillRegistry(tmp_path)

    catalog = registry.discover()

    assert [skill.name for skill in catalog.skills] == ["meeting-notes"]
    assert len(catalog.issues) == 1
    assert "YAML frontmatter" in catalog.issues[0].message


def test_skill_name_must_match_package_directory(tmp_path: Path) -> None:
    package = write_skill(tmp_path, "wrong-folder")
    (package / "SKILL.md").write_text(
        "---\nname: another-name\ndescription: A description.\n---\n\nInstructions.",
        encoding="utf-8",
    )

    catalog = SkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert "match its package directory" in catalog.issues[0].message


def test_package_symlink_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    outside = write_skill(tmp_path / "outside")
    (root / "linked").symlink_to(outside, target_is_directory=True)

    catalog = SkillRegistry(root).discover()

    assert catalog.skills == ()
    assert "symbolic link" in catalog.issues[0].message


def test_resource_symlink_is_rejected(tmp_path: Path) -> None:
    package = write_skill(tmp_path)
    external = tmp_path / "private.txt"
    external.write_text("private", encoding="utf-8")
    (package / "reference.txt").symlink_to(external)

    catalog = SkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert "Symbolic links" in catalog.issues[0].message


def test_executable_resources_are_rejected(tmp_path: Path) -> None:
    package = write_skill(tmp_path)
    (package / "helper.py").write_text("print('not run')", encoding="utf-8")

    catalog = SkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert "not supported" in catalog.issues[0].message


def test_save_creates_and_updates_local_skill(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")

    created = registry.save("new-skill", "A new Skill.", "Do useful work.")

    assert created.instructions == "Do useful work."
    with pytest.raises(FileExistsError):
        registry.save("new-skill", "A new Skill.", "Replacement.")

    updated = registry.save(
        "new-skill",
        "An updated Skill.",
        "Do different work.",
        overwrite=True,
    )
    assert updated.description == "An updated Skill."
    assert updated.instructions == "Do different work."
    assert updated.summary.content_hash != created.summary.content_hash


def test_save_rejects_invalid_names_and_empty_instructions(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")

    with pytest.raises(SkillValidationError):
        registry.save("../outside", "Description.", "Instructions.")
    with pytest.raises(SkillValidationError):
        registry.save("valid-name", "Description.", "  ")


def test_missing_root_is_an_empty_catalog(tmp_path: Path) -> None:
    catalog = SkillRegistry(tmp_path / "missing").discover()

    assert catalog.skills == ()
    assert catalog.issues == ()


def test_save_preserves_agent_skill_metadata(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    metadata = {"metadata": {"include_tools": "search"}, "license": "MIT"}

    created = registry.save(
        "search-notes",
        "Save research notes.",
        "Record useful sources.",
        metadata=metadata,
    )
    updated = registry.save(
        "search-notes",
        "Save concise research notes.",
        "Record claims and citations.",
        overwrite=True,
        metadata=created.metadata,
    )

    assert updated.metadata["metadata"] == {"include_tools": "search"}
    assert updated.metadata["license"] == "MIT"


def test_oversized_skill_is_rejected(tmp_path: Path) -> None:
    package = write_skill(tmp_path)
    (package / "SKILL.md").write_text("x" * 100, encoding="utf-8")

    catalog = SkillRegistry(tmp_path, max_skill_file_bytes=32).discover()

    assert catalog.skills == ()
    assert "size limit" in catalog.issues[0].message


def test_oversized_resource_is_rejected(tmp_path: Path) -> None:
    package = write_skill(tmp_path)
    (package / "reference.md").write_text("12345", encoding="utf-8")

    catalog = SkillRegistry(tmp_path, max_resource_bytes=4).discover()

    assert catalog.skills == ()
    assert "size limit" in catalog.issues[0].message
