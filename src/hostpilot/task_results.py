"""Framework-neutral data contracts for persisted Plan task results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class CompletedPlanTaskResult:
    """Result payload persisted only when its Plan TaskRun completes."""

    description: str
    input: Mapping[str, Any]
    depends_on: tuple[str, ...]
    output: Any
    task_fingerprint: str = ""
    execution_summary: PlanTaskExecutionSummary | None = None


@dataclass(frozen=True)
class PlanTaskToolUsage:
    """Allowlisted aggregate of calls to one tool during a Plan subtask."""

    tool_id: str
    call_count: int
    side_effect_level: str


@dataclass(frozen=True)
class PlanTaskExecutionSummary:
    """Safe execution provenance, excluding tool arguments and returned values."""

    tools: tuple[PlanTaskToolUsage, ...] = ()
    confirmation_decisions: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoredPlanTaskResult:
    """Successful task output available as historical planning context."""

    task_run_id: str
    task_id: str
    turn_id: str
    session_id: str
    task_fingerprint: str
    description: str
    input: Mapping[str, Any]
    depends_on: tuple[str, ...]
    output: Any
    finished_at: datetime
    execution_summary: PlanTaskExecutionSummary | None = None


@dataclass(frozen=True)
class FailedPlanTaskSummary:
    """Safe task metadata and a stable error code, without failed output."""

    task_fingerprint: str
    description: str
    input: Mapping[str, Any]
    error_code: str
    side_effect_count: int


@dataclass(frozen=True)
class StoredPlanTaskFailure:
    """Failure reference for Planner context; never considered a reusable result."""

    task_run_id: str
    task_id: str
    turn_id: str
    session_id: str
    task_fingerprint: str
    description: str
    input: Mapping[str, Any]
    error_code: str
    side_effect_count: int
    finished_at: datetime
