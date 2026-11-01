"""Validate and load user-managed Agent Skills from a local directory."""

from __future__ import annotations

import hashlib
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import yaml

FRONTMATTER_PATTERN = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SKILL_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
MAX_SKILL_FILE_BYTES = 256 * 1024
MAX_RESOURCE_BYTES = 512 * 1024
MAX_PACKAGE_BYTES = 2 * 1024 * 1024
MAX_PACKAGE_FILES = 100
RESOURCE_EXTENSIONS = frozenset({".csv", ".json", ".md", ".toml", ".txt", ".yaml", ".yml"})
EXECUTABLE_EXTENSIONS = frozenset(
    {".bat", ".cmd", ".dll", ".exe", ".js", ".py", ".ps1", ".sh", ".so"}
)
VIRTUAL_SKILLS_ROOT = "/skills/user"


class SkillValidationError(ValueError):
    """Raised when a Skill package is invalid or unsafe to load."""


@dataclass(frozen=True)
class SkillSummary:
    """Metadata shown to the user and Agent before a Skill is loaded."""

    name: str
    description: str
    content_hash: str


@dataclass(frozen=True)
class SkillPackage:
    """Validated Skill instructions and text resources."""

    summary: SkillSummary
    source_directory: Path
    source_content: str
    metadata: Mapping[str, object]
    instructions: str
    resources: Mapping[str, str]

    @property
    def name(self) -> str:
        return self.summary.name

    @property
    def description(self) -> str:
        return self.summary.description


@dataclass(frozen=True)
class SkillCatalogIssue:
    """A package-level error that does not prevent other Skills from loading."""

    path: Path
    message: str


@dataclass(frozen=True)
class SkillCatalog:
    """Validated Skills and isolated package errors from one directory scan."""

    skills: tuple[SkillPackage, ...]
    issues: tuple[SkillCatalogIssue, ...]

    def summaries(self) -> tuple[SkillSummary, ...]:
        """Return the safe metadata for each valid Skill."""
        return tuple(skill.summary for skill in self.skills)


class SkillRegistry:
    """Discover, validate, and edit Skills stored under one user-owned root."""

    def __init__(
        self,
        root: Path | str | None = None,
        *,
        max_skill_file_bytes: int = MAX_SKILL_FILE_BYTES,
        max_resource_bytes: int = MAX_RESOURCE_BYTES,
        max_package_bytes: int = MAX_PACKAGE_BYTES,
        max_package_files: int = MAX_PACKAGE_FILES,
    ) -> None:
        if root is None:
            root = os.environ.get("HOSTPILOT_SKILLS_DIR")
        self.root = Path(root).expanduser() if root else Path.home() / ".hostpilot" / "skills"
        self.max_skill_file_bytes = max_skill_file_bytes
        self.max_resource_bytes = max_resource_bytes
        self.max_package_bytes = max_package_bytes
        self.max_package_files = max_package_files

    def discover(self) -> SkillCatalog:
        """Load valid packages and return errors for invalid packages."""
        if not self.root.exists():
            return SkillCatalog(skills=(), issues=())
        if self.root.is_symlink() or not self.root.is_dir():
            return SkillCatalog(
                skills=(),
                issues=(SkillCatalogIssue(self.root, "Skill root must be a real directory."),),
            )

        root = self.root.resolve()
        packages: list[SkillPackage] = []
        issues: list[SkillCatalogIssue] = []
        names: set[str] = set()

        try:
            children = sorted(self.root.iterdir(), key=lambda path: path.name)
        except OSError as error:
            return SkillCatalog(
                skills=(),
                issues=(SkillCatalogIssue(self.root, f"Cannot list Skill root: {error}"),),
            )

        for child in children:
            if child.name.startswith(".") or not child.is_dir():
                continue
            try:
                package = self._load_package(child, root)
                if package.name in names:
                    raise SkillValidationError(f"Duplicate Skill name: {package.name}")
                names.add(package.name)
                packages.append(package)
            except (OSError, SkillValidationError) as error:
                issues.append(SkillCatalogIssue(child, str(error)))

        return SkillCatalog(skills=tuple(packages), issues=tuple(issues))

    def load(self, name: str) -> SkillPackage:
        """Load one current Skill by its validated name."""
        for package in self.discover().skills:
            if package.name == name:
                return package
        raise KeyError(f"Skill not found: {name}")

    def contains(self, name: str) -> bool:
        """Return whether a valid Skill with this name currently exists."""
        return any(package.name == name for package in self.discover().skills)

    def save(
        self,
        name: str,
        description: str,
        instructions: str,
        *,
        overwrite: bool = False,
        metadata: Mapping[str, object] | None = None,
    ) -> SkillPackage:
        """Create or update a Skill after validating its metadata and content."""
        self._validate_metadata(name, description, name)
        if not instructions.strip():
            raise SkillValidationError("Skill instructions cannot be empty.")
        content = self._render_skill(name, description, instructions, metadata)
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_skill_file_bytes:
            raise SkillValidationError("SKILL.md exceeds the configured size limit.")

        self._ensure_root()
        root = self.root.resolve()
        package_directory = self.root / name
        if package_directory.is_symlink():
            raise SkillValidationError("Skill package directory cannot be a symbolic link.")
        if package_directory.exists() and not package_directory.is_dir():
            raise SkillValidationError("Skill package path must be a directory.")
        if not package_directory.exists() and not overwrite:
            pass
        elif package_directory.exists() and not overwrite:
            raise FileExistsError(f"Skill already exists: {name}")

        package_directory.mkdir(parents=False, exist_ok=True)
        resolved_directory = package_directory.resolve()
        if not resolved_directory.is_relative_to(root):
            raise SkillValidationError("Skill package path escapes the configured root.")

        temporary_path = package_directory / f".SKILL.md.{uuid.uuid4().hex}.tmp"
        try:
            with temporary_path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            if package_directory.is_symlink() or not package_directory.resolve().is_relative_to(
                root
            ):
                raise SkillValidationError("Skill package directory changed during save.")
            os.replace(temporary_path, package_directory / "SKILL.md")
        finally:
            temporary_path.unlink(missing_ok=True)

        return self._load_package(package_directory, root)

    def _ensure_root(self) -> None:
        if self.root.is_symlink():
            raise SkillValidationError("Skill root cannot be a symbolic link.")
        self.root.mkdir(parents=True, exist_ok=True)
        if not self.root.is_dir():
            raise SkillValidationError("Skill root must be a directory.")

    def _load_package(self, directory: Path, root: Path) -> SkillPackage:
        if directory.is_symlink():
            raise SkillValidationError("Skill package directory cannot be a symbolic link.")
        resolved_directory = directory.resolve(strict=True)
        if not resolved_directory.is_relative_to(root):
            raise SkillValidationError("Skill package path escapes the configured root.")
        if not resolved_directory.is_dir():
            raise SkillValidationError("Skill package path must be a directory.")

        skill_file = directory / "SKILL.md"
        if skill_file.is_symlink() or not skill_file.is_file():
            raise SkillValidationError("Skill package must contain a regular SKILL.md file.")
        if skill_file.stat().st_size > self.max_skill_file_bytes:
            raise SkillValidationError("SKILL.md exceeds the configured size limit.")
        skill_bytes = skill_file.read_bytes()
        if len(skill_bytes) > self.max_skill_file_bytes:
            raise SkillValidationError("SKILL.md exceeds the configured size limit.")
        try:
            skill_content = skill_bytes.decode("utf-8")
        except UnicodeDecodeError as error:
            raise SkillValidationError("SKILL.md must use UTF-8 encoding.") from error

        name, description, instructions, metadata = self._parse_skill(skill_content)
        self._validate_metadata(name, description, directory.name)
        resources: dict[str, str] = {}
        package_bytes = len(skill_bytes)
        file_count = 1

        for path in sorted(directory.rglob("*")):
            if path.is_symlink():
                raise SkillValidationError("Symbolic links are not allowed in Skill packages.")
            if not path.is_file() or path == skill_file:
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
            if suffix not in RESOURCE_EXTENSIONS:
                continue

            if path.stat().st_size > self.max_resource_bytes:
                raise SkillValidationError(f"Resource exceeds the size limit: {path.name}")
            resource_bytes = path.read_bytes()
            if len(resource_bytes) > self.max_resource_bytes:
                raise SkillValidationError(f"Resource exceeds the size limit: {path.name}")
            package_bytes += len(resource_bytes)
            if package_bytes > self.max_package_bytes:
                raise SkillValidationError("Skill package exceeds the total size limit.")
            try:
                resource = resource_bytes.decode("utf-8")
            except UnicodeDecodeError as error:
                message = f"Resource must use UTF-8 encoding: {path.name}"
                raise SkillValidationError(message) from error
            relative = path.relative_to(directory).as_posix()
            resources[relative] = resource

        digest = hashlib.sha256()
        digest.update(skill_bytes)
        for relative, content in sorted(resources.items()):
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(content.encode("utf-8"))

        summary = SkillSummary(name=name, description=description, content_hash=digest.hexdigest())
        return SkillPackage(
            summary=summary,
            source_directory=resolved_directory,
            source_content=skill_content,
            metadata=MappingProxyType(metadata),
            instructions=instructions,
            resources=MappingProxyType(resources),
        )

    @staticmethod
    def _parse_skill(content: str) -> tuple[str, str, str, dict[str, object]]:
        match = FRONTMATTER_PATTERN.match(content)
        if match is None:
            raise SkillValidationError("SKILL.md must start with YAML frontmatter.")
        try:
            metadata = yaml.safe_load(match.group(1))
        except yaml.YAMLError as error:
            raise SkillValidationError("SKILL.md contains invalid YAML frontmatter.") from error
        if not isinstance(metadata, dict):
            raise SkillValidationError("SKILL.md frontmatter must be a YAML mapping.")

        name = metadata.get("name")
        description = metadata.get("description")
        if not isinstance(name, str) or not isinstance(description, str):
            raise SkillValidationError("Skill name and description must be strings.")
        instructions = content[match.end() :].strip()
        if not instructions:
            raise SkillValidationError("Skill instructions cannot be empty.")
        return name.strip(), description.strip(), instructions, metadata

    @staticmethod
    def _validate_metadata(name: str, description: str, directory_name: str) -> None:
        if not name or len(name) > MAX_SKILL_NAME_LENGTH or not SKILL_NAME_PATTERN.fullmatch(name):
            raise SkillValidationError(
                "Skill name must be lowercase letters, numbers, and hyphens (max 64 characters)."
            )
        if name != directory_name:
            raise SkillValidationError("Skill name must match its package directory.")
        if not description or len(description) > MAX_DESCRIPTION_LENGTH:
            raise SkillValidationError("Skill description must contain 1 to 1024 characters.")

    @staticmethod
    def _render_skill(
        name: str,
        description: str,
        instructions: str,
        metadata: Mapping[str, object] | None = None,
    ) -> str:
        frontmatter_data = dict(metadata or {})
        frontmatter_data.update(name=name, description=description)
        frontmatter = yaml.safe_dump(
            frontmatter_data,
            allow_unicode=True,
            sort_keys=False,
        ).rstrip()
        return f"---\n{frontmatter}\n---\n\n{instructions.strip()}\n"
