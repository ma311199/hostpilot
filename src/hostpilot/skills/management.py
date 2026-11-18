"""Safe management operations for validated user-owned Skill packages."""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from hostpilot.skills.registry import DISABLED_MARKER, SkillRegistry
from hostpilot.skills.yaml_registry import SkillManifest, YamlSkillRegistry


class SkillManagementError(ValueError):
    """Raised when a Skill package cannot be safely changed."""


@dataclass(frozen=True)
class ManagedSkill:
    """Safe catalog metadata returned after a successful package operation."""

    skill_id: str
    name: str
    description: str
    execution_format: str
    enabled: bool


def set_skill_enabled(
    root: Path,
    skill_id: str,
    enabled: bool,
    *,
    registered_tool_ids: Sequence[str] = (),
) -> ManagedSkill:
    """Enable or disable one validated user package without changing built-ins."""
    if skill_id.startswith("legacy/"):
        name = skill_id.removeprefix("legacy/")
        if not name or "/" in name:
            raise KeyError(skill_id)
        package = SkillRegistry(root).load(name)
        directory = _ensure_user_package(root, package.source_directory)
        marker = directory / DISABLED_MARKER
        if marker.is_symlink() or (marker.exists() and not marker.is_file()):
            raise SkillManagementError("Skill 状态文件不可用。")
        try:
            if enabled:
                marker.unlink(missing_ok=True)
            elif not marker.exists():
                marker.write_text("disabled\n", encoding="utf-8")
        except OSError as error:
            raise SkillManagementError("无法更新 Skill 启用状态。") from error
        return ManagedSkill(
            skill_id=skill_id,
            name=package.name,
            description=package.description,
            execution_format="skill_md",
            enabled=enabled,
        )

    yaml_package = YamlSkillRegistry(root, registered_tool_ids=registered_tool_ids).load(skill_id)
    directory = _ensure_user_package(root, yaml_package.source_directory)
    manifest_path = directory / "skill.yaml"
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise SkillManagementError("Skill manifest 不可用。")
    try:
        original_manifest = manifest_path.read_text(encoding="utf-8")
        raw_manifest = yaml.safe_load(original_manifest)
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise SkillManagementError("无法读取 Skill manifest。") from error
    if not isinstance(raw_manifest, dict) or not isinstance(raw_manifest.get("meta"), dict):
        raise SkillManagementError("Skill manifest 格式无效。")
    raw_manifest["meta"]["enabled"] = enabled
    try:
        validated_manifest = SkillManifest.model_validate(raw_manifest)
    except ValidationError as error:
        raise SkillManagementError("Skill manifest 校验失败。") from error
    serialized = yaml.safe_dump(
        validated_manifest.model_dump(mode="json", exclude_none=True),
        allow_unicode=True,
        sort_keys=False,
    )
    _replace_manifest_atomically(manifest_path, directory, serialized)
    markdown_package = next(
        (
            package
            for package in SkillRegistry(root).discover().skills
            if package.source_directory.resolve() == directory
        ),
        None,
    )
    if markdown_package is not None:
        marker = directory / DISABLED_MARKER
        if marker.is_symlink() or (marker.exists() and not marker.is_file()):
            _replace_manifest_atomically(manifest_path, directory, original_manifest)
            raise SkillManagementError("Skill 状态文件不可用。")
        try:
            if enabled:
                marker.unlink(missing_ok=True)
            else:
                marker.write_text("disabled\n", encoding="utf-8")
        except OSError as error:
            _replace_manifest_atomically(manifest_path, directory, original_manifest)
            raise SkillManagementError("无法更新 Skill 启用状态。") from error
    return ManagedSkill(
        skill_id=yaml_package.skill_id,
        name=yaml_package.manifest.meta.name,
        description=yaml_package.manifest.intent_summary,
        execution_format="yaml",
        enabled=enabled,
    )


def delete_skill(
    root: Path,
    skill_id: str,
    *,
    registered_tool_ids: Sequence[str] = (),
) -> None:
    """Delete one validated user package; bundled packages are outside this registry."""
    if skill_id.startswith("legacy/"):
        name = skill_id.removeprefix("legacy/")
        if not name or "/" in name:
            raise KeyError(skill_id)
        package = SkillRegistry(root).load(name)
        directory = _ensure_user_package(root, package.source_directory)
    else:
        yaml_package = YamlSkillRegistry(root, registered_tool_ids=registered_tool_ids).load(
            skill_id
        )
        directory = _ensure_user_package(root, yaml_package.source_directory)
    try:
        shutil.rmtree(directory)
    except OSError as error:
        raise SkillManagementError("无法删除 Skill 包。") from error


def _ensure_user_package(root: Path, package_directory: Path) -> Path:
    if root.is_symlink() or not root.exists() or not root.is_dir():
        raise SkillManagementError("Skill 用户目录不可用。")
    resolved_root = root.resolve()
    if package_directory.is_symlink():
        raise SkillManagementError("不允许修改符号链接 Skill 包。")
    resolved_package = package_directory.resolve()
    if resolved_package.parent != resolved_root or resolved_package == resolved_root:
        raise SkillManagementError("只能修改用户目录中的单个 Skill 包。")
    return resolved_package


def _replace_manifest_atomically(path: Path, directory: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=directory,
            prefix=".skill-manifest-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    except OSError as error:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise SkillManagementError("无法更新 Skill 启用状态。") from error


__all__ = ["ManagedSkill", "SkillManagementError", "delete_skill", "set_skill_enabled"]
