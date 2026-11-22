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


@dataclass(frozen=True)
class StoredPlanTaskResult:
    """Successful task output available as historical planning context."""

    task_run_id: str
    task_id: str
    turn_id: str
    session_id: str
    description: str
    input: Mapping[str, Any]
    depends_on: tuple[str, ...]
    output: Any
    finished_at: datetime
