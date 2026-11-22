"""Framework-neutral data contracts for persisted Plan task results."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

PlanRunState = Literal[
    "running",
    "interrupted",
    "awaiting_reconciliation",
    "completed",
    "failed",
    "cancelled",
]
ToolInvocationState = Literal[
    "prepared",
    "completed",
    "awaiting_reconciliation",
    "user_reported",
    "not_executed",
]


@dataclass(frozen=True)
class PlanRunSnapshot:
    """Versioned, Session-scoped snapshot needed to resume one validated Plan."""

    plan_run_id: str
    turn_id: str
    session_id: str
    runtime_host_id: str
    goal: str
    state: PlanRunState
    snapshot: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime
    resume_claim_host_id: str | None = None


@dataclass(frozen=True)
class ToolInvocationRecord:
    """Private durable record for one logical tool invocation and its outcome."""

    invocation_id: str
    plan_run_id: str | None
    session_id: str
    turn_id: str
    runtime_host_id: str
    task_run_id: str | None
    task_id: str
    call_index: int
    tool_id: str
    retry_class: str
    idempotency_key: str | None
    request_fingerprint: str
    arguments_json: str
    state: ToolInvocationState
    created_at: datetime
    idempotency_expires_at: datetime | None = None
    finished_at: datetime | None = None
    reported_result: str | None = None


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
