from pathlib import Path

import yaml

from hostpilot.skills.yaml_registry import YamlSkillRegistry


def manifest(
    skill_id: str = "business/meeting-notes",
    *,
    priority: int = 50,
    enabled: bool = True,
    dependencies: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "meta": {
            "skill_id": skill_id,
            "name": "Meeting notes",
            "version": "1.0.0",
            "priority": priority,
            "enabled": enabled,
        },
        "intent_summary": "Turn rough meeting notes into minutes and action items.",
        "tags": ["meeting notes", "minutes"],
        "examples": ["summarize these meeting notes", "make meeting minutes"],
        "input_schema": {
            "type": "object",
            "properties": {"notes": {"type": "string"}},
            "required": ["notes"],
            "additionalProperties": False,
        },
        "output_schema": {
            "type": "object",
            "properties": {"minutes": {"type": "string"}},
            "required": ["minutes"],
            "additionalProperties": False,
        },
        "side_effect_level": "read_only",
        "execution": {
            "kind": "steps",
            "steps": [{"step_id": "draft", "action": "llm.complete", "params": {}}],
        },
        "dependencies": dependencies or {"skills": [], "tools": []},
    }


def write_manifest(root: Path, value: dict[str, object], folder: str = "meeting-notes") -> Path:
    package = root / folder
    package.mkdir(parents=True)
    (package / "skill.yaml").write_text(
        yaml.safe_dump(value, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    return package


def test_discover_validates_manifest_and_keeps_markdown_display_only(tmp_path: Path) -> None:
    package_path = write_manifest(tmp_path, manifest())
    (package_path / "SKILL.md").write_text("# Human-readable help\n", encoding="utf-8")
    (package_path / "assets").mkdir()
    (package_path / "assets" / "guide.txt").write_text("reference", encoding="utf-8")

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert [package.skill_id for package in catalog.skills] == ["business/meeting-notes"]
    assert catalog.skills[0].documentation == "# Human-readable help\n"
    assert catalog.skills[0].resources["assets/guide.txt"] == "reference"
    assert len(catalog.skills[0].content_hash) == 64
    assert catalog.issues == ()


def test_discover_combines_user_and_additional_package_roots(tmp_path: Path) -> None:
    user_root = tmp_path / "user-skills"
    packaged_root = tmp_path / "packaged-skills"
    user_root.mkdir()
    packaged_root.mkdir()
    write_manifest(user_root, manifest("business/user-skill"), folder="user-skill")
    write_manifest(packaged_root, manifest("business/packaged-skill"), folder="packaged-skill")

    catalog = YamlSkillRegistry(user_root, additional_roots=(packaged_root,)).discover()

    assert {package.skill_id for package in catalog.skills} == {
        "business/user-skill",
        "business/packaged-skill",
    }
    assert catalog.issues == ()


def test_unknown_fields_and_invalid_json_schema_are_rejected(tmp_path: Path) -> None:
    invalid = manifest()
    invalid["unexpected"] = True
    write_manifest(tmp_path, invalid)
    bad_schema = manifest("business/invalid-schema")
    bad_schema["input_schema"] = {"type": "not-a-json-schema-type"}
    write_manifest(tmp_path, bad_schema, folder="invalid-schema")

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert len(catalog.issues) == 2
    assert any("Extra inputs are not permitted" in issue.message for issue in catalog.issues)
    assert any(
        "input_schema is not a valid JSON Schema" in issue.message for issue in catalog.issues
    )


def test_json_schema_rejects_external_references(tmp_path: Path) -> None:
    invalid = manifest("business/external-schema")
    invalid["input_schema"] = {
        "type": "object",
        "properties": {"notes": {"$ref": "https://schemas.example/string.json"}},
    }
    write_manifest(tmp_path, invalid)
    valid = manifest("business/local-schema")
    valid["input_schema"] = {
        "type": "object",
        "$defs": {"note": {"type": "string"}},
        "properties": {"notes": {"$ref": "#/$defs/note"}},
    }
    write_manifest(tmp_path, valid, folder="local-schema")

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert [package.skill_id for package in catalog.skills] == ["business/local-schema"]
    assert len(catalog.issues) == 1
    assert "only use local JSON Schema references" in catalog.issues[0].message


def test_json_schema_rejects_excessive_depth_and_node_count(tmp_path: Path) -> None:
    deep_schema: dict[str, object] = {"type": "string"}
    for _ in range(40):
        deep_schema = {"type": "object", "properties": {"next": deep_schema}}
    deep_package = manifest("business/deep-schema")
    deep_package["input_schema"] = deep_schema
    write_manifest(tmp_path, deep_package, folder="deep-schema")

    large_package = manifest("business/large-schema")
    large_package["input_schema"] = {
        "type": "object",
        "properties": {f"field-{index}": {"type": "string"} for index in range(1100)},
    }
    write_manifest(tmp_path, large_package, folder="large-schema")

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert len(catalog.issues) == 2
    assert any("nesting limit" in issue.message for issue in catalog.issues)
    assert any("node limit" in issue.message for issue in catalog.issues)


def test_duplicate_yaml_keys_and_duplicate_skill_ids_are_rejected(tmp_path: Path) -> None:
    duplicate_key = tmp_path / "bad-yaml"
    duplicate_key.mkdir()
    (duplicate_key / "skill.yaml").write_text("schema_version: 1\nschema_version: 1\n")
    write_manifest(tmp_path, manifest(), folder="first")
    write_manifest(tmp_path, manifest(), folder="second")

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert len(catalog.issues) == 3
    assert any("duplicate key" in issue.message for issue in catalog.issues)
    assert sum("Duplicate skill_id" in issue.message for issue in catalog.issues) == 2


def test_package_path_and_reserved_builtin_id_are_rejected(tmp_path: Path) -> None:
    write_manifest(tmp_path, manifest("builtin/direct_answer"))
    outside = write_manifest(tmp_path / "outside", manifest("business/external"))
    root = tmp_path / "linked-root"
    root.symlink_to(outside.parent, target_is_directory=True)

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert any("reserved builtin IDs" in issue.message for issue in catalog.issues)
    assert YamlSkillRegistry(root).discover().issues[0].message == (
        "Skill root cannot be a symbolic link."
    )


def test_script_resources_are_allowed_but_symlink_and_binary_are_rejected(tmp_path: Path) -> None:
    symlink_package = write_manifest(tmp_path, manifest(), folder="symlink")
    target = tmp_path / "outside.txt"
    target.write_text("outside", encoding="utf-8")
    (symlink_package / "linked.txt").symlink_to(target)
    script_package = write_manifest(tmp_path, manifest("business/script"), folder="script")
    (script_package / "run.py").write_text("print('no')", encoding="utf-8")
    binary_package = write_manifest(tmp_path, manifest("business/binary"), folder="binary")
    (binary_package / "icon.png").write_bytes(b"\x89PNG")

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert [skill.skill_id for skill in catalog.skills] == ["business/script"]
    assert catalog.skills[0].resources["run.py"] == "print('no')"
    assert len(catalog.issues) == 2
    assert any("Symbolic links" in issue.message for issue in catalog.issues)
    assert any("Unsupported Skill resource" in issue.message for issue in catalog.issues)


def test_dependency_preflight_rejects_missing_skill_or_tool(tmp_path: Path) -> None:
    write_manifest(
        tmp_path,
        manifest(
            "business/needs-tools",
            dependencies={"skills": ["business/missing"], "tools": ["search_docs"]},
        ),
    )

    catalog = YamlSkillRegistry(tmp_path, registered_tool_ids=[]).discover()

    assert catalog.skills == ()
    assert "unknown Skill dependencies: business/missing" in catalog.issues[0].message
    assert "unknown tool dependencies: search_docs" in catalog.issues[0].message


def test_manifest_rejects_embedded_secret_values_and_duplicate_steps(tmp_path: Path) -> None:
    credential = manifest("business/credential")
    credential["execution"] = {
        "kind": "steps",
        "steps": [{"step_id": "call", "action": "tool.call", "params": {"api_key": "x"}}],
    }
    write_manifest(tmp_path, credential, folder="credential")
    duplicate_steps = manifest("business/repeated-steps")
    duplicate_steps["execution"] = {
        "kind": "steps",
        "steps": [
            {"step_id": "draft", "action": "llm.complete"},
            {"step_id": "draft", "action": "tool.call"},
        ],
    }
    write_manifest(tmp_path, duplicate_steps, folder="repeated-steps")

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert len(catalog.issues) == 2
    assert any(
        "cannot contain credentials or endpoints" in issue.message for issue in catalog.issues
    )
    assert any("Step IDs must be unique" in issue.message for issue in catalog.issues)


def test_manifest_rejects_unknown_or_cyclic_step_dependencies(tmp_path: Path) -> None:
    unknown = manifest("business/unknown-dependency")
    unknown["execution"] = {
        "kind": "steps",
        "steps": [{"step_id": "draft", "action": "text.draft", "depends_on": ["missing"]}],
    }
    cycle = manifest("business/cyclic-dependency")
    cycle["execution"] = {
        "kind": "steps",
        "steps": [
            {"step_id": "draft", "action": "text.draft", "depends_on": ["review"]},
            {"step_id": "review", "action": "text.review", "depends_on": ["draft"]},
        ],
    }
    duplicate = manifest("business/duplicate-dependency")
    duplicate["execution"] = {
        "kind": "steps",
        "steps": [
            {"step_id": "draft", "action": "text.draft", "depends_on": []},
            {
                "step_id": "review",
                "action": "text.review",
                "depends_on": ["draft", "draft"],
            },
        ],
    }
    write_manifest(tmp_path, unknown, folder="unknown")
    write_manifest(tmp_path, cycle, folder="cycle")
    write_manifest(tmp_path, duplicate, folder="duplicate")

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert len(catalog.issues) == 3
    assert any("unknown dependencies" in issue.message for issue in catalog.issues)
    assert any("cannot contain a cycle" in issue.message for issue in catalog.issues)
    assert any("dependencies must be unique" in issue.message for issue in catalog.issues)


def test_manifest_rejects_input_mapping_to_non_dependency(tmp_path: Path) -> None:
    invalid = manifest("business/unsafe-reference")
    invalid["execution"] = {
        "kind": "steps",
        "steps": [
            {"step_id": "left", "action": "text.left", "depends_on": []},
            {"step_id": "right", "action": "text.right", "depends_on": []},
            {
                "step_id": "combine",
                "action": "text.combine",
                "depends_on": ["left"],
                "input_mapping": {"value": {"$ref": "steps.right.value"}},
            },
        ],
    }
    write_manifest(tmp_path, invalid)

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert "must depend on referenced step right" in catalog.issues[0].message


def test_manifest_requires_a_supported_side_effect_level(tmp_path: Path) -> None:
    invalid = manifest("business/invalid-impact")
    invalid["side_effect_level"] = "unknown"
    write_manifest(tmp_path, invalid)

    catalog = YamlSkillRegistry(tmp_path).discover()

    assert catalog.skills == ()
    assert "side_effect_level" in catalog.issues[0].message


def test_invalid_yaml_root_and_oversized_files_are_rejected(tmp_path: Path) -> None:
    not_mapping = tmp_path / "not-mapping"
    not_mapping.mkdir()
    (not_mapping / "skill.yaml").write_text("- item\n", encoding="utf-8")
    write_manifest(tmp_path, manifest("business/oversized"), folder="oversized")

    catalog = YamlSkillRegistry(tmp_path, max_manifest_bytes=32).discover()

    assert catalog.skills == ()
    assert len(catalog.issues) == 2
    assert any("must contain a YAML mapping" in issue.message for issue in catalog.issues)
    assert any("size limit" in issue.message for issue in catalog.issues)


def test_missing_root_returns_empty_catalog(tmp_path: Path) -> None:
    catalog = YamlSkillRegistry(tmp_path / "missing").discover()

    assert catalog.skills == ()
    assert catalog.issues == ()


def test_registered_tool_actions_require_declared_tool_dependencies(tmp_path: Path) -> None:
    undeclared = manifest("business/undeclared-tool")
    undeclared["execution"] = {
        "kind": "steps",
        "steps": [{"step_id": "search", "action": "search"}],
    }
    write_manifest(tmp_path, undeclared, folder="undeclared")
    declared = manifest("business/declared-tool", dependencies={"skills": [], "tools": ["search"]})
    declared["execution"] = {
        "kind": "steps",
        "steps": [{"step_id": "search", "action": "search"}],
    }
    write_manifest(tmp_path, declared, folder="declared")

    catalog = YamlSkillRegistry(tmp_path, registered_tool_ids=("search",)).discover()

    assert [package.skill_id for package in catalog.skills] == ["business/declared-tool"]
    assert len(catalog.issues) == 1
    assert "tool actions missing from dependencies.tools: search" in catalog.issues[0].message
