from types import SimpleNamespace

import pytest

from hostpilot.skills import (
    PLAN_PLUGIN_API_VERSION,
    PlanComponentContext,
    PlanComponentRegistry,
    PlanComponentRole,
    PlanComponentSelection,
)
from hostpilot.skills.model_plan import build_model_plan_executor


class Component:
    async def create_plan(self, *args):
        raise NotImplementedError

    async def validate(self, *args):
        raise NotImplementedError

    async def execute_task(self, *args):
        raise NotImplementedError

    async def verify(self, *args):
        raise NotImplementedError

    async def replan(self, *args):
        raise NotImplementedError


def test_plan_component_registry_validates_version_duplicates_and_role_contract() -> None:
    registry = PlanComponentRegistry()
    registry.register(PlanComponentRole.PLANNER, "test.planner", lambda _: Component())

    assert PLAN_PLUGIN_API_VERSION == "1.0"
    assert registry.implementations(PlanComponentRole.PLANNER) == ("test.planner",)
    assert isinstance(
        registry.create(
            PlanComponentRole.PLANNER,
            "test.planner",
            PlanComponentContext(None, None, None, None, {}),
        ),
        Component,
    )
    with pytest.raises(ValueError, match="Unsupported plan plugin API version"):
        registry.register(
            PlanComponentRole.PLANNER,
            "test.old",
            lambda _: Component(),
            api_version="0.9",
        )
    with pytest.raises(ValueError, match="already registered"):
        registry.register(PlanComponentRole.PLANNER, "test.planner", lambda _: Component())
    with pytest.raises(TypeError, match="create_plan"):
        registry.register(PlanComponentRole.PLANNER, "test.invalid", lambda _: object())
        registry.create(
            PlanComponentRole.PLANNER,
            "test.invalid",
            PlanComponentContext(None, None, None, None, {}),
        )


def test_model_plan_executor_uses_configured_replaceable_components() -> None:
    class SkillRegistry:
        def discover(self):
            return SimpleNamespace(skills=())

    registry = PlanComponentRegistry()
    components: dict[PlanComponentRole, Component] = {}
    for role in PlanComponentRole:
        component = Component()
        components[role] = component
        registry.register(role, "custom.test", lambda _, value=component: value)

    executor = build_model_plan_executor(
        object(),
        skills=SkillRegistry(),
        skill_runtime=object(),
        tool_provider=None,
        tool_descriptors={},
        component_registry=registry,
        component_selection=PlanComponentSelection(
            planner="custom.test",
            validator="custom.test",
            task_executor="custom.test",
            verifier="custom.test",
            replanner="custom.test",
        ),
    )

    assert executor.planner is components[PlanComponentRole.PLANNER]
    assert executor.validator is components[PlanComponentRole.VALIDATOR]
    assert executor.task_executor is components[PlanComponentRole.TASK_EXECUTOR]
    assert executor.verifier is components[PlanComponentRole.VERIFIER]
    assert executor.replanner is components[PlanComponentRole.REPLANNER]
