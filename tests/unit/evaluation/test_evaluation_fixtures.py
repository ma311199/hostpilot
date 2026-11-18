from pathlib import Path

from hostpilot.evaluation.fixtures import write_fixture_skills
from hostpilot.skills.yaml_registry import YamlSkillRegistry


def test_write_fixture_skills_creates_a_valid_read_only_package(tmp_path: Path) -> None:
    fixture = {
        "skill_id": "business/meeting-minutes",
        "required_input": "notes",
        "intent_summary": "Turn supplied meeting notes into minutes.",
        "examples": ["Summarize these meeting notes."],
        "execution": "pure_text_transform",
        "input_title": "会议记录或笔记",
        "input_description": "日期和会议名称可选；未提供时标记为未说明。",
        "execution_instruction": "Format the supplied notes without asking for optional details.",
    }

    allowed_ids = write_fixture_skills(tmp_path, [fixture])
    catalog = YamlSkillRegistry(tmp_path).discover()

    assert "business/meeting-minutes" in allowed_ids
    assert {skill.skill_id for skill in catalog.skills} == {"business/meeting-minutes"}
    assert catalog.issues == ()
    assert catalog.skills[0].manifest.side_effect_level.value == "read_only"
    assert catalog.skills[0].manifest.input_schema["properties"]["notes"]["title"] == (
        "会议记录或笔记"
    )
    assert catalog.skills[0].manifest.execution.steps[0].action == "llm.complete"
    assert catalog.skills[0].manifest.output_schema["required"] == ["text"]


def test_write_fixture_skills_can_skip_a_packaged_skill(tmp_path: Path) -> None:
    fixture = {
        "skill_id": "business/code-review-check",
        "required_input": "code_or_diff",
        "intent_summary": "Review supplied code.",
        "examples": ["Review this code."],
    }

    allowed_ids = write_fixture_skills(
        tmp_path,
        [fixture],
        skip_skill_ids=frozenset({"business/code-review-check"}),
    )

    assert "business/code-review-check" in allowed_ids
    assert YamlSkillRegistry(tmp_path).discover().skills == ()
