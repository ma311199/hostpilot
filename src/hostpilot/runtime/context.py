"""Request-scoped data passed through Runtime execution adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from hostpilot.budget import RequestBudget
from hostpilot.contracts import ContextSnapshot, SandboxMode
from hostpilot.task_results import PlanTaskExecutionSummary

if TYPE_CHECKING:
    from hostpilot.runtime.lifecycle import TaskLifecycleManager
    from hostpilot.tools.executor import AsyncConfirmationProvider


@dataclass(frozen=True)
class RequestContext:
    """Carry one request's snapshot, mutable budget, and optional approval capability."""

    snapshot: ContextSnapshot
    budget: RequestBudget
    confirmation_provider: AsyncConfirmationProvider | None = None
    task_cache_approval_provider: TaskCacheApprovalProvider | None = None
    task_lifecycle: TaskLifecycleManager | None = None
    plan_run_id: str | None = None
    task_run_id: str | None = None
    tool_call_index_start: int = 1
    sandbox_mode: SandboxMode = SandboxMode.READ_ONLY


class TaskCacheDecision(StrEnum):
    """Explicit user choice for one exact successful Plan task match."""

    REUSE = "reuse"
    EXECUTE = "execute"


@dataclass(frozen=True)
class TaskCacheApprovalRequest:
    """Historical result shown to a user before deciding whether to reuse it."""

    request_id: str
    task_id: str
    description: str
    source_turn_id: str
    finished_at: datetime
    output: Any
    execution_summary: PlanTaskExecutionSummary | None = None


class TaskCacheApprovalProvider(Protocol):
    """Request a human decision before skipping a matching successful task."""

    async def decide(self, request: TaskCacheApprovalRequest) -> TaskCacheDecision | None: ...
