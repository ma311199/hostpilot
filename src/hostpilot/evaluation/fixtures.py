"""Builders for synthetic Skill packages used by local model evaluations."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def write_fixture_skills(
    root: Path,
    fixtures: list[dict[str, Any]],
    *,
    skip_skill_ids: frozenset[str] = frozenset(),
) -> set[str]:
    """Write read-only synthetic Skill packages beneath a temporary directory."""
    allowed_ids = {
        "builtin/direct_answer",
        "builtin/clarify",
        "builtin/orchestrator_plan",
    }
    for fixture in fixtures:
        skill_id = str(fixture["skill_id"])
        allowed_ids.add(skill_id)
        if skill_id in skip_skill_ids:
            continue
        skill_dir = root / skill_id.split("/", 1)[1]
        skill_dir.mkdir(parents=True)
        input_name = str(fixture["required_input"])
        input_property: dict[str, str] = {"type": "string"}
        input_title = fixture.get("input_title")
        input_description = fixture.get("input_description")
        if isinstance(input_title, str) and input_title.strip():
            input_property["title"] = input_title
        if isinstance(input_description, str) and input_description.strip():
            input_property["description"] = input_description
        uses_model_transform = fixture.get("execution") == "pure_text_transform"
        step = {
            "step_id": "evaluation_transform" if uses_model_transform else "evaluation_noop",
            "action": "llm.complete" if uses_model_transform else "evaluation.noop",
            "depends_on": [],
            "input_mapping": (
                {"source": {"$ref": f"input.{input_name}"}}
                if uses_model_transform
                else {input_name: {"$ref": f"input.{input_name}"}}
            ),
            "params": (
                {
                    "instruction": fixture.get(
                        "execution_instruction",
                        "Transform the supplied text while preserving its factual content.",
                    )
                }
                if uses_model_transform
                else {}
            ),
        }
        manifest = {
            "schema_version": 1,
            "meta": {
                "skill_id": skill_id,
                "name": skill_id.rsplit("/", 1)[-1].replace("-", " ").title(),
                "version": "1.0.0",
                "priority": 50,
                "enabled": bool(fixture.get("enabled", True)),
            },
            "intent_summary": fixture["intent_summary"],
            "tags": list(fixture["examples"]),
            "examples": list(fixture["examples"]),
            "input_schema": {
                "type": "object",
                "properties": {input_name: input_property},
                "required": [input_name],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"text" if uses_model_transform else "result": {"type": "string"}},
                "required": ["text" if uses_model_transform else "result"],
                "additionalProperties": False,
            },
            "side_effect_level": "read_only",
            "execution": {
                "kind": "steps",
                "steps": [step],
            },
            "dependencies": {"skills": [], "tools": []},
        }
        (skill_dir / "skill.yaml").write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    return allowed_ids


__all__ = ["write_fixture_skills"]
