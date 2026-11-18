"""Safe snapshot integration with DeepAgents SkillsMiddleware."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpilot.skills.registry import VIRTUAL_SKILLS_ROOT, SkillRegistry


class DeepAgentsSkillAdapter:
    """Pass validated local Skill snapshots to DeepAgents' native Skill support."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        include_yaml_packages: bool = False,
        execution_modes: Mapping[str, str] | None = None,
        skill_ids_by_name: Mapping[str, str] | None = None,
    ) -> None:
        self.registry = registry
        self.include_yaml_packages = include_yaml_packages
        self.execution_modes = execution_modes
        self.skill_ids_by_name = skill_ids_by_name or {}

    @property
    def source(self) -> str:
        """Return the virtual POSIX path used by the in-memory backend."""
        return f"{VIRTUAL_SKILLS_ROOT}/"

    def agent_options(self, *, checkpointer: Any | None = None) -> dict[str, Any]:
        """Return DeepAgents options and optionally its persistent graph checkpointer."""
        options: dict[str, Any] = {"backend": self._state_backend(), "skills": [self.source]}
        if checkpointer is not None:
            options["checkpointer"] = checkpointer
        return options

    def prepare_input(
        self,
        agent_input: Mapping[str, Any],
        *,
        selected_skill_id: str | None = None,
    ) -> dict[str, Any]:
        """Merge a fresh, validated Skill snapshot into graph state."""
        from deepagents.backends.utils import create_file_data

        prepared = dict(agent_input)
        current_files = prepared.get("files", {})
        if not isinstance(current_files, Mapping):
            raise TypeError("Agent input files must be a mapping.")
        files = dict(current_files)
        included_selected_skill = False

        for package in self.registry.discover().skills:
            if not package.enabled:
                continue
            yaml_manifest = package.source_directory / "skill.yaml"
            has_yaml_manifest = yaml_manifest.exists() or yaml_manifest.is_symlink()
            if self.execution_modes is None:
                if not self.include_yaml_packages and has_yaml_manifest:
                    continue
            else:
                skill_id = self.skill_ids_by_name.get(package.name, f"legacy/{package.name}")
                mode = self.execution_modes.get(
                    skill_id,
                    "yaml" if has_yaml_manifest else "deepagents",
                )
                if mode not in {"deepagents", "both"}:
                    continue
            skill_id = self.skill_ids_by_name.get(package.name, f"legacy/{package.name}")
            if selected_skill_id is not None and skill_id != selected_skill_id:
                continue
            included_selected_skill = included_selected_skill or skill_id == selected_skill_id
            package_prefix = f"{self.source}{package.name}/"
            skill_contents = {
                "SKILL.md": package.source_content,
                **package.resources,
            }
            for relative_path, content in skill_contents.items():
                path = f"{package_prefix}{relative_path}"
                if path in files:
                    raise ValueError(f"Agent input already contains reserved Skill path: {path}")
                files[path] = create_file_data(content)

        if selected_skill_id is not None and not included_selected_skill:
            raise KeyError(f"DeepAgents Skill is not available: {selected_skill_id}")

        prepared["files"] = files
        return prepared

    @staticmethod
    def _state_backend() -> Any:
        try:
            from deepagents.backends.state import StateBackend
        except ImportError as error:
            raise RuntimeError(
                "Install the agents dependency group to use DeepAgents Skill integration."
            ) from error
        return StateBackend()
