"""Workflow runners for declarative YAML Skill steps."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Annotated, Any, Protocol, TypedDict, cast

from hostpilot.skills.yaml_registry import SkillStep


def _merge_mappings(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    return {**left, **right}


class WorkflowState(TypedDict):
    """Per-run state with reducers for results written by parallel branches."""

    step_outputs: Annotated[dict[str, Any], _merge_mappings]
    step_states: Annotated[dict[str, Any], _merge_mappings]
    side_effects: Annotated[dict[str, Any], _merge_mappings]
    invoked_tool_ids: Annotated[dict[str, Any], _merge_mappings]


WorkflowStep = Callable[[SkillStep, WorkflowState], Awaitable[Mapping[str, Any]]]


class WorkflowRunner(Protocol):
    """Compile and invoke the steps declared by one Skill package."""

    async def run(
        self,
        steps: Sequence[SkillStep],
        initial_state: WorkflowState,
        execute_step: WorkflowStep,
    ) -> WorkflowState: ...


class SequentialWorkflowRunner:
    """Small fallback runner used when the optional LangGraph package is absent."""

    async def run(
        self,
        steps: Sequence[SkillStep],
        initial_state: WorkflowState,
        execute_step: WorkflowStep,
    ) -> WorkflowState:
        state = initial_state
        dependencies = step_dependencies(steps)
        completed: set[str] = set()
        pending = {step.step_id: step for step in steps}
        while pending:
            ready = [
                step
                for step in steps
                if step.step_id in pending and set(dependencies[step.step_id]) <= completed
            ]
            if not ready:
                raise ValueError("Workflow step dependencies contain a cycle.")
            for step in ready:
                failed_dependency = next(
                    (
                        dependency
                        for dependency in dependencies[step.step_id]
                        if _state_status(state["step_states"].get(dependency)) != "completed"
                    ),
                    None,
                )
                if failed_dependency is not None:
                    update: Mapping[str, Any] = {
                        "step_states": {
                            step.step_id: {
                                "status": "skipped",
                                "error_code": f"dependency_failed:{failed_dependency}",
                            }
                        }
                    }
                else:
                    update = await execute_step(step, state)
                state = cast(WorkflowState, {**state, **dict(update)})
                completed.add(step.step_id)
                pending.pop(step.step_id)
        return state


class LangGraphWorkflowRunner:
    """Compile YAML step dependencies into a LangGraph DAG and run it."""

    async def run(
        self,
        steps: Sequence[SkillStep],
        initial_state: WorkflowState,
        execute_step: WorkflowStep,
    ) -> WorkflowState:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError as error:
            raise RuntimeError(
                "Install the agents dependency group to execute YAML Skills with LangGraph."
            ) from error

        graph = StateGraph(WorkflowState)
        dependencies = step_dependencies(steps)
        for step in steps:

            async def node(state: WorkflowState, current_step: SkillStep = step) -> dict[str, Any]:
                failed_dependency = next(
                    (
                        dependency
                        for dependency in dependencies[current_step.step_id]
                        if _state_status(state["step_states"].get(dependency)) != "completed"
                    ),
                    None,
                )
                if failed_dependency is not None:
                    return {
                        "step_states": {
                            current_step.step_id: {
                                "status": "skipped",
                                "error_code": f"dependency_failed:{failed_dependency}",
                            }
                        }
                    }
                return dict(await execute_step(current_step, state))

            graph.add_node(step.step_id, node)

        dependents: dict[str, set[str]] = {step.step_id: set() for step in steps}
        roots = []
        for step in steps:
            required_steps = dependencies[step.step_id]
            if not required_steps:
                roots.append(step.step_id)
            else:
                graph.add_edge(list(required_steps), step.step_id)
            for dependency in required_steps:
                dependents[dependency].add(step.step_id)
        for root in roots:
            graph.add_edge(START, root)
        for step_id, downstream in dependents.items():
            if not downstream:
                graph.add_edge(step_id, END)
        compiled = graph.compile()
        return cast(WorkflowState, await compiled.ainvoke(initial_state))


def step_dependencies(steps: Sequence[SkillStep]) -> dict[str, tuple[str, ...]]:
    dependencies: dict[str, tuple[str, ...]] = {}
    for index, step in enumerate(steps):
        declared = step.depends_on
        if declared is None:
            declared = (steps[index - 1].step_id,) if index else ()
        dependencies[step.step_id] = declared
    return dependencies


def _state_status(value: Any) -> str | None:
    if isinstance(value, Mapping):
        status = value.get("status")
    else:
        status = getattr(value, "status", None)
    return status if isinstance(status, str) else None


__all__ = [
    "LangGraphWorkflowRunner",
    "SequentialWorkflowRunner",
    "WorkflowRunner",
    "step_dependencies",
]
