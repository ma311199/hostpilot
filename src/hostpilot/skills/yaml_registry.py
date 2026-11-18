"""Discover and validate declarative Skill packages using ``skill.yaml``."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import yaml
from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from hostpilot.skills.registry import (
    EXECUTABLE_EXTENSIONS,
    RESOURCE_EXTENSIONS,
    SkillValidationError,
)

MAX_MANIFEST_BYTES = 128 * 1024
MAX_RESOURCE_BYTES = 512 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_FILES = 100
MAX_SCHEMA_DEPTH = 32
MAX_SCHEMA_NODES = 4096
SKILL_ID_PATTERN = re.compile(r"^(?:builtin|business|community)/[a-z0-9]+(?:[-_.][a-z0-9]+)*$")
STEP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class _UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            duplicate = key in result
        except TypeError as error:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "mapping keys must be hashable",
                key_node.start_mark,
            ) from error
        if duplicate:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


_UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SkillMetadata(StrictModel):
    skill_id: str = Field(min_length=3, max_length=128, pattern=SKILL_ID_PATTERN.pattern)
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    priority: int = Field(default=50, ge=0, le=100)
    enabled: bool = True


class SideEffectLevel(StrEnum):
    """Declared impact category used to constrain automatic fallback behavior."""

    READ_ONLY = "read_only"
    IDEMPOTENT = "idempotent"
    REVERSIBLE = "reversible"
    IRREVERSIBLE = "irreversible"


class SkillDependencySet(StrictModel):
    skills: tuple[str, ...] = ()
    tools: tuple[str, ...] = ()

    @field_validator("skills")
    @classmethod
    def skill_dependencies_are_valid(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Skill dependencies must be unique.")
        if any(not SKILL_ID_PATTERN.fullmatch(value) for value in values):
            raise ValueError("Skill dependencies must use a qualified Skill ID.")
        return values

    @field_validator("tools")
    @classmethod
    def tool_dependencies_are_valid(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Tool dependencies must be unique.")
        if any(not value.strip() for value in values):
            raise ValueError("Tool dependencies cannot be empty.")
        return values


class SkillStep(StrictModel):
    step_id: str = Field(min_length=1, max_length=64, pattern=STEP_ID_PATTERN.pattern)
    action: str = Field(min_length=1, max_length=128)
    depends_on: tuple[str, ...] | None = None
    params: dict[str, Any] = Field(default_factory=dict)
    input_mapping: dict[str, Any] = Field(default_factory=dict)

    @field_validator("depends_on")
    @classmethod
    def step_dependencies_are_unique(cls, values: tuple[str, ...] | None) -> tuple[str, ...] | None:
        if values is not None and len(values) != len(set(values)):
            raise ValueError("Step dependencies must be unique.")
        return values


class SkillExecution(StrictModel):
    kind: Literal["builtin", "steps"]
    skill_type: str | None = Field(default=None, min_length=1, max_length=128)
    steps: tuple[SkillStep, ...] = ()

    @model_validator(mode="after")
    def validate_execution_kind(self) -> SkillExecution:
        if self.kind == "builtin":
            if self.skill_type is None or self.steps:
                raise ValueError("Builtin execution requires skill_type and cannot define steps.")
        elif self.skill_type is not None or not self.steps:
            raise ValueError("Step execution requires steps and cannot define skill_type.")
        step_ids = [step.step_id for step in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("Step IDs must be unique within a Skill.")
        dependencies: dict[str, tuple[str, ...]] = {}
        known_steps = set(step_ids)
        for index, step in enumerate(self.steps):
            step_dependencies = step.depends_on
            if step_dependencies is None:
                step_dependencies = (self.steps[index - 1].step_id,) if index else ()
            if step.step_id in step_dependencies:
                raise ValueError("A step cannot depend on itself.")
            missing = set(step_dependencies) - known_steps
            if missing:
                raise ValueError(
                    f"Step {step.step_id} references unknown dependencies: "
                    f"{', '.join(sorted(missing))}"
                )
            dependencies[step.step_id] = step_dependencies

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("Step dependencies cannot contain a cycle.")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in step_ids:
            visit(step_id)

        def ancestors(step_id: str) -> set[str]:
            result: set[str] = set()
            for dependency in dependencies[step_id]:
                result.add(dependency)
                result.update(ancestors(dependency))
            return result

        for step in self.steps:
            for reference in _input_references(step.input_mapping):
                parts = reference.split(".")
                if len(parts) < 2 or parts[0] not in {"input", "steps"}:
                    raise ValueError(f"Unsupported input_mapping reference: {reference}")
                if parts[0] == "steps":
                    referenced_step = parts[1]
                    if referenced_step not in known_steps:
                        raise ValueError(
                            f"Step {step.step_id} references unknown output: {referenced_step}"
                        )
                    if referenced_step not in ancestors(step.step_id):
                        raise ValueError(
                            f"Step {step.step_id} must depend on referenced step {referenced_step}."
                        )
        return self


class SkillManifest(StrictModel):
    schema_version: Literal[1]
    meta: SkillMetadata
    intent_summary: str = Field(min_length=1, max_length=2048)
    tags: tuple[str, ...] = Field(min_length=1, max_length=64)
    examples: tuple[str, ...] = Field(min_length=1, max_length=64)
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    side_effect_level: SideEffectLevel
    execution: SkillExecution
    dependencies: SkillDependencySet = Field(default_factory=SkillDependencySet)

    @field_validator("tags")
    @classmethod
    def tags_must_be_unique_and_nonempty(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value for value in values) or len({value.casefold() for value in values}) != len(
            values
        ):
            raise ValueError("Tags must be non-empty and unique, ignoring case.")
        return values

    @field_validator("examples")
    @classmethod
    def examples_must_be_unique_and_nonempty(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = [value.strip().casefold() for value in values]
        if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
            raise ValueError("Examples must be non-empty and unique, ignoring case.")
        return values

    @model_validator(mode="after")
    def validate_schemas(self) -> SkillManifest:
        if self.meta.skill_id in self.dependencies.skills:
            raise ValueError("A Skill cannot declare itself as a Skill dependency.")
        for name, schema in (
            ("input_schema", self.input_schema),
            ("output_schema", self.output_schema),
        ):
            _validate_bounded_json_schema(schema, name)
            try:
                Draft202012Validator.check_schema(schema)
            except SchemaError as error:
                raise ValueError(f"{name} is not a valid JSON Schema: {error.message}") from error
        forbidden_keys = {
            "api_key",
            "access_token",
            "password",
            "secret",
            "credentials",
            "base_url",
        }
        if any(
            _contains_forbidden_execution_key(step.params, forbidden_keys)
            for step in self.execution.steps
        ):
            raise ValueError("Skill execution parameters cannot contain credentials or endpoints.")
        return self


def _validate_bounded_json_schema(schema: Mapping[str, Any], name: str) -> None:
    """Keep uploaded schemas bounded and prevent runtime external resource lookup."""
    pending: list[tuple[Any, int]] = [(schema, 0)]
    visited_containers: set[int] = set()
    node_count = 0
    reference_keywords = {"$ref", "$dynamicRef", "$recursiveRef"}

    while pending:
        value, depth = pending.pop()
        node_count += 1
        if node_count > MAX_SCHEMA_NODES:
            raise ValueError(f"{name} exceeds the JSON Schema node limit.")
        if depth > MAX_SCHEMA_DEPTH:
            raise ValueError(f"{name} exceeds the JSON Schema nesting limit.")
        if isinstance(value, Mapping):
            identity = id(value)
            if identity in visited_containers:
                continue
            visited_containers.add(identity)
            for key, child in value.items():
                if key in reference_keywords and (
                    not isinstance(child, str) or not child.startswith("#")
                ):
                    raise ValueError(
                        f"{name} may only use local JSON Schema references beginning with '#'."
                    )
                pending.extend(((key, depth + 1), (child, depth + 1)))
        elif isinstance(value, Sequence) and not isinstance(value, str | bytes):
            identity = id(value)
            if identity in visited_containers:
                continue
            visited_containers.add(identity)
            pending.extend((child, depth + 1) for child in value)


@dataclass(frozen=True)
class YamlSkillPackage:
    """A validated Skill manifest and its display-only text resources."""

    manifest: SkillManifest
    source_directory: Path
    content_hash: str
    documentation: str | None
    resources: Mapping[str, str]

    @property
    def skill_id(self) -> str:
        return self.manifest.meta.skill_id


@dataclass(frozen=True)
class YamlSkillCatalogIssue:
    path: Path
    message: str


@dataclass(frozen=True)
class YamlSkillCatalog:
    skills: tuple[YamlSkillPackage, ...]
    issues: tuple[YamlSkillCatalogIssue, ...]

    def get(self, skill_id: str) -> YamlSkillPackage | None:
        return next((skill for skill in self.skills if skill.skill_id == skill_id), None)


def _contains_forbidden_execution_key(value: Any, forbidden: set[str]) -> bool:
    if isinstance(value, Mapping):
        return any(
            str(key).casefold() in forbidden or _contains_forbidden_execution_key(item, forbidden)
            for key, item in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return any(_contains_forbidden_execution_key(item, forbidden) for item in value)
    return False


def _input_references(value: Any) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        if set(value) == {"$ref"}:
            reference = value["$ref"]
            if not isinstance(reference, str):
                raise ValueError("input_mapping $ref values must be strings.")
            return (reference,)
        return tuple(reference for item in value.values() for reference in _input_references(item))
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return tuple(reference for item in value for reference in _input_references(item))
    return ()


class YamlSkillRegistry:
    """Discover YAML Skill packages without changing the legacy Markdown registry."""

    def __init__(
        self,
        root: Path | str,
        *,
        additional_roots: Sequence[Path | str] = (),
        registered_tool_ids: Sequence[str] = (),
        registered_skill_ids: Sequence[str] = (),
        execution_modes: Mapping[str, str] | None = None,
        max_manifest_bytes: int = MAX_MANIFEST_BYTES,
        max_resource_bytes: int = MAX_RESOURCE_BYTES,
        max_package_bytes: int = MAX_PACKAGE_BYTES,
        max_package_files: int = MAX_PACKAGE_FILES,
    ) -> None:
        self.root = Path(root).expanduser()
        self.roots = tuple(
            dict.fromkeys((self.root, *(Path(item).expanduser() for item in additional_roots)))
        )
        self.registered_tool_ids = frozenset(registered_tool_ids)
        self.registered_skill_ids = frozenset(registered_skill_ids)
        self.execution_modes: dict[str, str] = (
            execution_modes if isinstance(execution_modes, dict) else dict(execution_modes or {})
        )
        self.max_manifest_bytes = max_manifest_bytes
        self.max_resource_bytes = max_resource_bytes
        self.max_package_bytes = max_package_bytes
        self.max_package_files = max_package_files

    def discover(self) -> YamlSkillCatalog:
        """Load valid packages, isolate errors, and preflight declared dependencies."""
        loaded: list[YamlSkillPackage] = []
        issues: list[YamlSkillCatalogIssue] = []
        for package_root in self.roots:
            if package_root.is_symlink():
                issues.append(
                    YamlSkillCatalogIssue(package_root, "Skill root cannot be a symbolic link.")
                )
                continue
            if not package_root.exists():
                continue
            if not package_root.is_dir():
                issues.append(
                    YamlSkillCatalogIssue(package_root, "Skill root must be a real directory.")
                )
                continue

            resolved_root = package_root.resolve()
            for directory in sorted(package_root.iterdir(), key=lambda item: item.name):
                if directory.name.startswith(".") or not directory.is_dir():
                    continue
                try:
                    loaded.append(self._load_package(directory, resolved_root))
                except (OSError, SkillValidationError, ValidationError) as error:
                    issues.append(YamlSkillCatalogIssue(directory, str(error)))

        counts: dict[str, int] = {}
        for package in loaded:
            counts[package.skill_id] = counts.get(package.skill_id, 0) + 1
        duplicate_ids = {skill_id for skill_id, count in counts.items() if count > 1}
        valid = [package for package in loaded if package.skill_id not in duplicate_ids]
        for package in loaded:
            if package.skill_id in duplicate_ids:
                issues.append(
                    YamlSkillCatalogIssue(
                        package.source_directory,
                        f"Duplicate skill_id: {package.skill_id}",
                    )
                )

        yaml_packages = [
            package
            for package in valid
            if self.execution_modes.get(package.skill_id, "yaml") in {"yaml", "both"}
        ]
        available_ids = {package.skill_id for package in yaml_packages} | self.registered_skill_ids
        preflighted: list[YamlSkillPackage] = []
        for package in yaml_packages:
            missing_skills = set(package.manifest.dependencies.skills) - available_ids
            missing_tools = set(package.manifest.dependencies.tools) - self.registered_tool_ids
            undeclared_tool_actions = {
                step.action
                for step in package.manifest.execution.steps
                if step.action in self.registered_tool_ids
                and step.action not in package.manifest.dependencies.tools
            }
            if missing_skills or missing_tools or undeclared_tool_actions:
                details = []
                if missing_skills:
                    details.append(
                        f"unknown Skill dependencies: {', '.join(sorted(missing_skills))}"
                    )
                if missing_tools:
                    details.append(f"unknown tool dependencies: {', '.join(sorted(missing_tools))}")
                if undeclared_tool_actions:
                    details.append(
                        "tool actions missing from dependencies.tools: "
                        + ", ".join(sorted(undeclared_tool_actions))
                    )
                issues.append(
                    YamlSkillCatalogIssue(package.source_directory, "; ".join(details) + ".")
                )
                continue
            preflighted.append(package)

        return YamlSkillCatalog(tuple(preflighted), tuple(issues))

    def load(self, skill_id: str) -> YamlSkillPackage:
        package = YamlSkillCatalog.get(self.discover(), skill_id)
        if package is None:
            raise KeyError(f"Skill not found or invalid: {skill_id}")
        return package

    def _load_package(self, directory: Path, root: Path) -> YamlSkillPackage:
        if directory.is_symlink():
            raise SkillValidationError("Skill package directory cannot be a symbolic link.")
        resolved = directory.resolve(strict=True)
        if not resolved.is_relative_to(root) or not resolved.is_dir():
            raise SkillValidationError("Skill package path escapes the configured root.")

        manifest_path = directory / "skill.yaml"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            raise SkillValidationError("Skill package must contain a regular skill.yaml file.")
        manifest_bytes = manifest_path.read_bytes()
        if len(manifest_bytes) > self.max_manifest_bytes:
            raise SkillValidationError("skill.yaml exceeds the configured size limit.")
        try:
            raw = yaml.load(manifest_bytes.decode("utf-8"), Loader=_UniqueKeyLoader)
        except UnicodeDecodeError as error:
            raise SkillValidationError("skill.yaml must use UTF-8 encoding.") from error
        except yaml.YAMLError as error:
            raise SkillValidationError(f"skill.yaml contains invalid YAML: {error}") from error
        if not isinstance(raw, Mapping):
            raise SkillValidationError("skill.yaml must contain a YAML mapping.")
        try:
            manifest = SkillManifest.model_validate(raw)
        except ValidationError as error:
            raise SkillValidationError(f"Invalid skill.yaml contract: {error}") from error

        if manifest.meta.skill_id.startswith("builtin/"):
            raise SkillValidationError("User Skill roots cannot register reserved builtin IDs.")
        if manifest.execution.kind == "builtin":
            raise SkillValidationError("User Skill packages cannot select builtin executors.")

        resources: dict[str, str] = {}
        package_bytes = len(manifest_bytes)
        file_count = 1
        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise SkillValidationError("Symbolic links are not allowed in Skill packages.")
            if not path.is_file() or path == manifest_path:
                continue
            if path.name.startswith("."):
                continue
            file_count += 1
            if file_count > self.max_package_files:
                raise SkillValidationError("Skill package contains too many files.")
            suffix = path.suffix.lower()
            if suffix in EXECUTABLE_EXTENSIONS:
                raise SkillValidationError(
                    f"Executable resource files are not supported: {path.name}"
                )
            if path.name == "SKILL.md" and path.parent == directory:
                pass
            elif suffix not in RESOURCE_EXTENSIONS:
                raise SkillValidationError(f"Unsupported Skill resource file: {path.name}")

            if path.stat().st_size > self.max_resource_bytes:
                raise SkillValidationError(f"Resource exceeds the size limit: {path.name}")
            content_bytes = path.read_bytes()
            package_bytes += len(content_bytes)
            if package_bytes > self.max_package_bytes:
                raise SkillValidationError("Skill package exceeds the total size limit.")
            try:
                content = content_bytes.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SkillValidationError(
                    f"Resource must use UTF-8 encoding: {path.name}"
                ) from error
            resources[path.relative_to(directory).as_posix()] = content

        digest = hashlib.sha256(manifest_bytes)
        for relative_path, content in sorted(resources.items()):
            digest.update(relative_path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content.encode("utf-8"))
        return YamlSkillPackage(
            manifest=manifest,
            source_directory=resolved,
            content_hash=digest.hexdigest(),
            documentation=resources.get("SKILL.md"),
            resources=MappingProxyType(resources),
        )


__all__ = [
    "SkillDependencySet",
    "SkillExecution",
    "SkillManifest",
    "SkillMetadata",
    "SideEffectLevel",
    "SkillStep",
    "SkillValidationError",
    "YamlSkillCatalog",
    "YamlSkillCatalogIssue",
    "YamlSkillPackage",
    "YamlSkillRegistry",
]
