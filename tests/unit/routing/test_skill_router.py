from pathlib import Path

import yaml

from hostpilot.routing.skill_router import MetadataSkillRouter, SkillSignatureIndex
from hostpilot.skills.yaml_registry import YamlSkillRegistry


def add_skill(
    root: Path,
    folder: str,
    skill_id: str,
    *,
    priority: int,
    enabled: bool = True,
) -> None:
    package = root / folder
    package.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "meta": {
            "skill_id": skill_id,
            "name": folder,
            "version": "1.0.0",
            "priority": priority,
            "enabled": enabled,
        },
        "intent_summary": "Prepare concise meeting minutes.",
        "tags": ["meeting", "minutes"],
        "examples": ["summarize these meeting notes"],
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
        "side_effect_level": "read_only",
        "execution": {"kind": "steps", "steps": [{"step_id": "draft", "action": "llm"}]},
    }
    (package / "skill.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def test_router_prefers_example_then_priority_and_returns_skill_id(tmp_path: Path) -> None:
    add_skill(tmp_path, "minutes-low", "business/minutes-low", priority=10)
    add_skill(tmp_path, "minutes-high", "business/minutes-high", priority=90)
    catalog = YamlSkillRegistry(tmp_path).discover()

    match = MetadataSkillRouter().route(
        "Please summarize these meeting notes for me",
        SkillSignatureIndex.from_catalog(catalog),
    )

    assert match is not None
    assert match.skill_id == "business/minutes-high"
    assert match.score == 0.9
    assert match.reason_code == "example_phrase"


def test_router_matches_structured_tags_and_ignores_disabled_skills(tmp_path: Path) -> None:
    add_skill(tmp_path, "disabled", "business/disabled", priority=100, enabled=False)
    add_skill(tmp_path, "minutes", "business/minutes", priority=20)
    catalog = YamlSkillRegistry(tmp_path).discover()

    match = MetadataSkillRouter().route(
        "Help me prepare meeting minutes", SkillSignatureIndex.from_catalog(catalog)
    )

    assert match is not None
    assert match.skill_id == "business/minutes"
    assert match.reason_code == "tag_match"


def test_router_returns_no_match_for_weak_or_empty_queries(tmp_path: Path) -> None:
    add_skill(tmp_path, "minutes", "business/minutes", priority=20)
    catalog = YamlSkillRegistry(tmp_path).discover()
    router = MetadataSkillRouter()

    index = SkillSignatureIndex.from_catalog(catalog)
    assert router.route("", index) is None
    assert router.route("Can you help me?", index) is None


def test_router_uses_skill_id_to_break_equal_priority_ties(tmp_path: Path) -> None:
    add_skill(tmp_path, "minutes-z", "business/z-minutes", priority=20)
    add_skill(tmp_path, "minutes-a", "business/a-minutes", priority=20)
    catalog = YamlSkillRegistry(tmp_path).discover()

    match = MetadataSkillRouter().route(
        "meeting minutes", SkillSignatureIndex.from_catalog(catalog)
    )

    assert match is not None
    assert match.skill_id == "business/a-minutes"


def test_router_uses_intent_summary_when_examples_and_tags_do_not_match(tmp_path: Path) -> None:
    add_skill(tmp_path, "minutes", "business/minutes", priority=20)
    package = tmp_path / "minutes" / "skill.yaml"
    manifest = yaml.safe_load(package.read_text(encoding="utf-8"))
    manifest["tags"] = ["documentation"]
    manifest["examples"] = ["write a project brief"]
    package.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    catalog = YamlSkillRegistry(tmp_path).discover()

    match = MetadataSkillRouter().route(
        "minutes meeting concise prepare", SkillSignatureIndex.from_catalog(catalog)
    )

    assert match is not None
    assert match.skill_id == "business/minutes"
    assert match.reason_code == "intent_token_overlap"
