"""Request-scoped data passed through Runtime execution adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from hostpilot.budget import RequestBudget
from hostpilot.contracts import ContextSnapshot

if TYPE_CHECKING:
    from hostpilot.runtime.lifecycle import TaskLifecycleManager
    from hostpilot.tools.executor import AsyncConfirmationProvider


@dataclass(frozen=True)
class RequestContext:
    """Carry one request's snapshot, mutable budget, and optional approval capability."""

    snapshot: ContextSnapshot
    budget: RequestBudget
    confirmation_provider: AsyncConfirmationProvider | None = None
    task_lifecycle: TaskLifecycleManager | None = None
