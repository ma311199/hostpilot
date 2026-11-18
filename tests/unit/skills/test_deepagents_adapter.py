from pathlib import Path

import pytest

from hostpilot.skills.deepagents_adapter import DeepAgentsSkillAdapter
from hostpilot.skills.registry import SkillRegistry


def test_adapter_builds_virtual_skill_snapshot(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    skill_file = tmp_path / "skills" / "meeting-notes" / "SKILL.md"
    skill_file.write_text(
        (
            "---\nname: meeting-notes\ndescription: Write minutes.\n"
            "metadata:\n  include_tools: search\n---\n\nList decisions.\n"
        ),
        encoding="utf-8",
    )
    adapter = DeepAgentsSkillAdapter(registry)

    prepared = adapter.prepare_input({"messages": []})

    path = "/skills/user/meeting-notes/SKILL.md"
    assert path in prepared["files"]
    assert "name: meeting-notes" in prepared["files"][path]["content"]
    assert "List decisions." in prepared["files"][path]["content"]
    assert "include_tools: search" in prepared["files"][path]["content"]
    assert adapter.agent_options()["skills"] == ["/skills/user/"]


def test_adapter_rejects_caller_shadowing_skill_snapshot(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    adapter = DeepAgentsSkillAdapter(registry)

    with pytest.raises(ValueError, match="reserved Skill path"):
        adapter.prepare_input(
            {
                "messages": [],
                "files": {"/skills/user/meeting-notes/SKILL.md": {"content": "override"}},
            }
        )


def test_adapter_does_not_include_disabled_markdown_skills(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    marker = tmp_path / "skills" / "meeting-notes" / ".hostpilot-disabled"
    marker.write_text("disabled\n", encoding="utf-8")

    prepared = DeepAgentsSkillAdapter(registry).prepare_input({"messages": []})

    assert "/skills/user/meeting-notes/SKILL.md" not in prepared["files"]


def test_adapter_uses_yaml_runtime_by_default_for_dual_format_package(
    tmp_path: Path,
) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    (tmp_path / "skills" / "meeting-notes" / "skill.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    adapter = DeepAgentsSkillAdapter(registry)

    prepared = adapter.prepare_input({"messages": []})

    assert "/skills/user/meeting-notes/SKILL.md" not in prepared["files"]


def test_adapter_can_include_yaml_packages_when_both_formats_are_enabled(
    tmp_path: Path,
) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    (tmp_path / "skills" / "meeting-notes" / "skill.yaml").write_text(
        "schema_version: 1\n", encoding="utf-8"
    )
    adapter = DeepAgentsSkillAdapter(registry, include_yaml_packages=True)

    prepared = adapter.prepare_input({"messages": []})

    assert "/skills/user/meeting-notes/SKILL.md" in prepared["files"]


def test_adapter_can_prepare_only_the_selected_markdown_skill(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "skills")
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    registry.save("research", "Research a topic.", "Gather sources.")
    adapter = DeepAgentsSkillAdapter(registry)

    prepared = adapter.prepare_input({"messages": []}, selected_skill_id="legacy/research")

    assert "/skills/user/research/SKILL.md" in prepared["files"]
    assert "/skills/user/meeting-notes/SKILL.md" not in prepared["files"]


def test_adapter_rejects_unavailable_selected_skill(tmp_path: Path) -> None:
    adapter = DeepAgentsSkillAdapter(SkillRegistry(tmp_path / "skills"))

    with pytest.raises(KeyError, match="not available"):
        adapter.prepare_input({"messages": []}, selected_skill_id="legacy/missing")


def test_agent_options_uses_ephemeral_state_backend(tmp_path: Path) -> None:
    options = DeepAgentsSkillAdapter(SkillRegistry(tmp_path / "skills")).agent_options()

    assert options["backend"].__class__.__name__ == "StateBackend"


def test_agent_options_passes_persistent_checkpointer(tmp_path: Path) -> None:
    adapter = DeepAgentsSkillAdapter(SkillRegistry(tmp_path / "skills"))
    checkpointer = object()

    options = adapter.agent_options(checkpointer=checkpointer)

    assert options["checkpointer"] is checkpointer
