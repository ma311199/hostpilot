"""Versioned registration contracts for replaceable plan components."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PLAN_PLUGIN_API_VERSION = "1.0"


class PlanComponentRole(StrEnum):
    """Replaceable implementation roles around the stable plan orchestrator."""

    PLANNER = "planner"
    VALIDATOR = "validator"
    TASK_EXECUTOR = "task_executor"
    VERIFIER = "verifier"
    REPLANNER = "replanner"


@dataclass(frozen=True)
class PlanComponentSelection:
    """Configured implementation IDs for each replaceable plan role."""

    planner: str = "builtin.llm"
    validator: str = "builtin.runtime"
    task_executor: str = "builtin.governed"
    verifier: str = "builtin.llm"
    replanner: str = "builtin.llm"

    def for_role(self, role: PlanComponentRole) -> str:
        return {
            PlanComponentRole.PLANNER: self.planner,
            PlanComponentRole.VALIDATOR: self.validator,
            PlanComponentRole.TASK_EXECUTOR: self.task_executor,
            PlanComponentRole.VERIFIER: self.verifier,
            PlanComponentRole.REPLANNER: self.replanner,
        }[role]


@dataclass(frozen=True)
class PlanComponentContext:
    """Dependencies supplied by HostPilot when constructing one component."""

    provider: Any
    skills: Any
    skill_runtime: Any
    tool_provider: Any
    tool_descriptors: Mapping[str, Any]
    memory_controller: Any = None
    compensation_registry: Any = None
    cancellation_sink: Any = None
    max_replans: int = 2
    plan_max_tasks: int = 10
    plan_tool_agent_max_turns: int = 5
    plan_tool_candidate_limit: int = 5
    skill_authorizations: Mapping[str, tuple[str, ...]] | None = None
    tool_authorizations: Mapping[str, tuple[str, ...]] | None = None


ComponentFactory = Callable[[PlanComponentContext], Any]


@dataclass(frozen=True)
class _Registration:
    role: PlanComponentRole
    implementation_id: str
    api_version: str
    factory: ComponentFactory


class PlanComponentRegistry:
    """Resolve trusted, explicitly registered implementations by role and ID."""

    def __init__(self) -> None:
        self._registrations: dict[tuple[PlanComponentRole, str], _Registration] = {}

    def register(
        self,
        role: PlanComponentRole,
        implementation_id: str,
        factory: ComponentFactory,
        *,
        api_version: str = PLAN_PLUGIN_API_VERSION,
    ) -> None:
        if not implementation_id or implementation_id.strip() != implementation_id:
            raise ValueError("Plan component implementation IDs must be non-empty and trimmed.")
        if api_version != PLAN_PLUGIN_API_VERSION:
            raise ValueError(
                f"Unsupported plan plugin API version {api_version!r}; "
                f"expected {PLAN_PLUGIN_API_VERSION!r}."
            )
        key = (role, implementation_id)
        if key in self._registrations:
            raise ValueError(
                f"Plan component already registered: {role.value}/{implementation_id}."
            )
        self._registrations[key] = _Registration(role, implementation_id, api_version, factory)

    def create(
        self,
        role: PlanComponentRole,
        implementation_id: str,
        context: PlanComponentContext,
    ) -> Any:
        registration = self._registrations.get((role, implementation_id))
        if registration is None:
            raise ValueError(
                f"Unknown plan component implementation: {role.value}/{implementation_id}."
            )
        component = registration.factory(context)
        required_method = {
            PlanComponentRole.PLANNER: "create_plan",
            PlanComponentRole.VALIDATOR: "validate",
            PlanComponentRole.TASK_EXECUTOR: "execute_task",
            PlanComponentRole.VERIFIER: "verify",
            PlanComponentRole.REPLANNER: "replan",
        }[role]
        if not callable(getattr(component, required_method, None)):
            raise TypeError(
                f"Plan component {role.value}/{implementation_id} must implement "
                f"{required_method}()."
            )
        return component

    def implementations(self, role: PlanComponentRole) -> tuple[str, ...]:
        """Return registered implementation IDs in stable sorted order."""
        return tuple(
            sorted(
                implementation_id
                for registered_role, implementation_id in self._registrations
                if registered_role is role
            )
        )


__all__ = [
    "PLAN_PLUGIN_API_VERSION",
    "PlanComponentContext",
    "PlanComponentRegistry",
    "PlanComponentRole",
    "PlanComponentSelection",
]
