"""Bounded, validated import of local Skill ZIP archives."""

from __future__ import annotations

import hashlib
import shutil
import stat
import tempfile
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath

from hostpilot.skills.registry import (
    EXECUTABLE_EXTENSIONS,
    RESOURCE_EXTENSIONS,
    SkillRegistry,
    SkillValidationError,
)
from hostpilot.skills.registry import (
    MAX_PACKAGE_BYTES as LEGACY_MAX_PACKAGE_BYTES,
)
from hostpilot.skills.registry import (
    MAX_PACKAGE_FILES as LEGACY_MAX_PACKAGE_FILES,
)
from hostpilot.skills.yaml_registry import MAX_PACKAGE_BYTES as YAML_MAX_PACKAGE_BYTES
from hostpilot.skills.yaml_registry import MAX_PACKAGE_FILES as YAML_MAX_PACKAGE_FILES
from hostpilot.skills.yaml_registry import YamlSkillRegistry

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_PACKAGE_BYTES = min(LEGACY_MAX_PACKAGE_BYTES, YAML_MAX_PACKAGE_BYTES)
MAX_PACKAGE_FILES = min(LEGACY_MAX_PACKAGE_FILES, YAML_MAX_PACKAGE_FILES)


class SkillArchiveImportError(ValueError):
    """Raised when an archive is oversized, unsafe, invalid, or conflicts."""


@dataclass(frozen=True)
class ImportedSkill:
    """Safe metadata for one Skill installed from a validated archive."""

    skill_id: str
    name: str
    description: str
    execution_format: str
    enabled: bool
    directory: Path


def import_skill_archive(
    archive_bytes: bytes,
    root: Path,
    *,
    bundled_root: Path,
    registered_tool_ids: Sequence[str] = (),
) -> ImportedSkill:
    """Validate and atomically install exactly one Skill package from a ZIP."""
    if not archive_bytes:
        raise SkillArchiveImportError("ZIP 文件为空。")
    if len(archive_bytes) > MAX_ARCHIVE_BYTES:
        raise SkillArchiveImportError("ZIP 文件不能超过 4 MiB。")
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise SkillArchiveImportError("Skill 安装目录不可用。")

    try:
        with tempfile.TemporaryDirectory(prefix="hostpilot-skill-zip-") as temporary:
            staging_root = Path(temporary) / "packages"
            staging_root.mkdir()
            unpacked_directory = Path(temporary) / "unpacked"
            unpacked_directory.mkdir()
            _unpack_validated_archive(archive_bytes, unpacked_directory)
            markdown_file = unpacked_directory / "SKILL.md"
            yaml_manifest = unpacked_directory / "skill.yaml"
            if markdown_file.is_file() and not yaml_manifest.is_file():
                name, description, _, _ = SkillRegistry._parse_skill(
                    markdown_file.read_text(encoding="utf-8")
                )
                SkillRegistry._validate_metadata(name, description, name)
                package_directory = staging_root / name
            else:
                package_directory = staging_root / "package"
            unpacked_directory.replace(package_directory)

            legacy_catalog = SkillRegistry(staging_root).discover()
            yaml_catalog = YamlSkillRegistry(
                staging_root,
                additional_roots=(bundled_root,),
                registered_tool_ids=registered_tool_ids,
            ).discover()
            candidate = package_directory.resolve()
            legacy_matches = [
                package
                for package in legacy_catalog.skills
                if package.source_directory.resolve() == candidate
            ]
            yaml_matches = [
                package
                for package in yaml_catalog.skills
                if package.source_directory.resolve() == candidate
            ]
            if len(legacy_matches) + len(yaml_matches) != 1:
                raise SkillArchiveImportError("ZIP 应只包含一个有效的 YAML 或 SKILL.md Skill 包。")

            if legacy_matches:
                legacy_package = legacy_matches[0]
                skill_id = f"legacy/{legacy_package.name}"
                if SkillRegistry(root).contains(legacy_package.name):
                    raise SkillArchiveImportError("同名 Skill 已存在。")
                install_name = legacy_package.name
                result = ImportedSkill(
                    skill_id=skill_id,
                    name=legacy_package.name,
                    description=legacy_package.description,
                    execution_format="skill_md",
                    enabled=True,
                    directory=root / install_name,
                )
            else:
                yaml_package = yaml_matches[0]
                existing = YamlSkillRegistry(
                    root,
                    additional_roots=(bundled_root,),
                    registered_tool_ids=registered_tool_ids,
                ).discover()
                if any(item.skill_id == yaml_package.skill_id for item in existing.skills) or any(
                    issue.message == f"Duplicate skill_id: {yaml_package.skill_id}"
                    for issue in existing.issues
                ):
                    raise SkillArchiveImportError("同一 Skill ID 已存在。")
                digest = hashlib.sha256(yaml_package.skill_id.encode("utf-8")).hexdigest()[:10]
                safe_name = yaml_package.skill_id.replace("/", "--").replace("_", "-")
                install_name = f"{safe_name}-{digest}"
                result = ImportedSkill(
                    skill_id=yaml_package.skill_id,
                    name=yaml_package.manifest.meta.name,
                    description=yaml_package.manifest.intent_summary,
                    execution_format="yaml",
                    enabled=yaml_package.manifest.meta.enabled,
                    directory=root / install_name,
                )

            root.mkdir(parents=True, exist_ok=True)
            if result.directory.exists() or result.directory.is_symlink():
                raise SkillArchiveImportError("Skill 安装目标已存在。")
            with tempfile.TemporaryDirectory(prefix=".skill-import-", dir=root) as install_stage:
                staged_package = Path(install_stage) / "package"
                shutil.copytree(package_directory, staged_package)
                staged_package.replace(result.directory)
            return result
    except SkillArchiveImportError:
        raise
    except SkillValidationError as error:
        raise SkillArchiveImportError("ZIP 应包含有效的 YAML 或 SKILL.md Skill 包。") from error
    except UnicodeError as error:
        raise SkillArchiveImportError("Skill 包内文本文件必须使用 UTF-8 编码。") from error
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise SkillArchiveImportError("无法读取或安装该 ZIP Skill 包。") from error


def _unpack_validated_archive(archive_bytes: bytes, destination: Path) -> None:
    try:
        archive = zipfile.ZipFile(BytesIO(archive_bytes))
    except (zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise SkillArchiveImportError("文件不是有效 ZIP 包。") from error

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_PACKAGE_FILES * 2:
            raise SkillArchiveImportError("ZIP 文件数量无效或超出限制。")
        for info in infos:
            _validated_archive_path(info)
        files = [info for info in infos if not info.is_dir()]
        if not files or len(files) > MAX_PACKAGE_FILES:
            raise SkillArchiveImportError("ZIP 文件数量无效或超出限制。")

        paths = [_validated_archive_path(info) for info in files]
        if len(set(paths)) != len(paths):
            raise SkillArchiveImportError("ZIP 包含重复文件路径。")

        unwrapped_paths = paths
        has_root_file = any(len(path.parts) == 1 for path in paths)
        top_levels = {path.parts[0] for path in paths}
        if (
            not has_root_file
            and len(top_levels) == 1
            and all(len(path.parts) > 1 for path in paths)
        ):
            unwrapped_paths = [PurePosixPath(*path.parts[1:]) for path in paths]
        elif len(top_levels) > 1 and not has_root_file:
            raise SkillArchiveImportError("ZIP 必须只包含一个 Skill 包目录。")

        total_size = 0
        for info, path in zip(files, unwrapped_paths, strict=True):
            extension = path.suffix.lower()
            if extension in EXECUTABLE_EXTENSIONS or extension not in RESOURCE_EXTENSIONS:
                raise SkillArchiveImportError(
                    f"ZIP 包含不支持的文件扩展名：{extension or '（无扩展名）'}。"
                )
            if info.flag_bits & 0x1:
                raise SkillArchiveImportError("不支持加密 ZIP 文件。")
            if info.file_size < 0 or info.file_size > MAX_PACKAGE_BYTES:
                raise SkillArchiveImportError("Skill 文件超出解压大小限制。")
            total_size += info.file_size
            if total_size > MAX_PACKAGE_BYTES:
                raise SkillArchiveImportError("Skill 包解压后不能超过 2 MiB。")

            output = destination.joinpath(*path.parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            remaining = MAX_PACKAGE_BYTES - (total_size - info.file_size)
            with archive.open(info) as source, output.open("xb") as target:
                content = source.read(remaining + 1)
                if len(content) > remaining:
                    raise SkillArchiveImportError("Skill 包解压后不能超过 2 MiB。")
                target.write(content)
                if source.read(1):
                    raise SkillArchiveImportError("Skill 包解压后不能超过 2 MiB。")


def _validated_archive_path(info: zipfile.ZipInfo) -> PurePosixPath:
    raw_path = info.filename
    if (
        not raw_path
        or "\x00" in raw_path
        or "\\" in raw_path
        or raw_path.startswith("/")
        or any(part in {"", ".", ".."} for part in raw_path.rstrip("/").split("/"))
        or any(part.startswith(".") or ":" in part for part in raw_path.split("/"))
    ):
        raise SkillArchiveImportError("ZIP 包含不安全的文件路径。")

    path = PurePosixPath(raw_path)
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    allowed_types = {0, stat.S_IFREG, stat.S_IFDIR}
    if (
        file_type not in allowed_types
        or (info.is_dir() and file_type == stat.S_IFREG)
        or (not info.is_dir() and file_type == stat.S_IFDIR)
    ):
        raise SkillArchiveImportError("ZIP 包含不支持的文件类型。")
    return path


__all__ = [
    "ImportedSkill",
    "MAX_ARCHIVE_BYTES",
    "SkillArchiveImportError",
    "import_skill_archive",
]
