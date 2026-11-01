"""Safe snapshot integration with DeepAgents SkillsMiddleware."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpilot.skills.registry import VIRTUAL_SKILLS_ROOT, SkillRegistry


class DeepAgentsSkillAdapter:
    """Pass validated local Skill snapshots to DeepAgents' native Skill support."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry

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

    def prepare_input(self, agent_input: Mapping[str, Any]) -> dict[str, Any]:
        """Merge a fresh, validated Skill snapshot into graph state."""
        from deepagents.backends.utils import create_file_data

        prepared = dict(agent_input)
        current_files = prepared.get("files", {})
        if not isinstance(current_files, Mapping):
            raise TypeError("Agent input files must be a mapping.")
        files = dict(current_files)

        for package in self.registry.discover().skills:
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
