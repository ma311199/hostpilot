"""Storage contracts consumed by application and orchestration modules."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from hostpilot.contracts import ConversationSummary, MemoryRawArchive, SandboxMode
from hostpilot.observability import AuditEvent, TraceEvent
from hostpilot.persistence.cancellations import CancellationAuditEntry
from hostpilot.persistence.harness_configuration import HarnessConfiguration
from hostpilot.persistence.mcp_configuration import MCPServerConfiguration
from hostpilot.persistence.model_configuration import ModelConfiguration
from hostpilot.persistence.routing_model_configuration import RoutingModelConfiguration
from hostpilot.persistence.skill_execution import SkillExecutionMode
from hostpilot.persistence.traces import StoredTraceEvent, TraceSummary
from hostpilot.skills.compensation import CancellationSnapshot
from hostpilot.task_results import (
    CompletedPlanTaskResult,
    FailedPlanTaskSummary,
    PlanRunSnapshot,
    PlanRunState,
    StoredPlanTaskFailure,
    StoredPlanTaskResult,
    ToolInvocationRecord,
    ToolInvocationState,
)


class ConversationStore(Protocol):
    """Metadata store required by conversation APIs and chat orchestration."""

    async def initialize(self) -> None: ...

    async def create(self, title: str | None = None) -> ConversationSummary: ...

    async def get(self, session_id: str) -> ConversationSummary | None: ...

    async def list(self) -> tuple[ConversationSummary, ...]: ...

    async def touch(self, session_id: str, *, title: str | None = None) -> None: ...

    async def delete(self, session_id: str) -> bool: ...

    async def upgrade_sandbox_mode(
        self, session_id: str, target: SandboxMode
    ) -> ConversationSummary: ...


class ConversationCheckpointer(Protocol):
    """Checkpoint operations directly required by the conversation API."""

    async def adelete_thread(self, thread_id: str) -> None: ...


class TurnStore(Protocol):
    """Persist Turn lifecycle metadata and Runtime Host lease ownership."""

    async def initialize(self) -> None: ...

    async def register_host(
        self, host_id: str, *, now: datetime, lease_expires_at: datetime
    ) -> None: ...

    async def heartbeat_host(
        self, host_id: str, *, now: datetime, lease_expires_at: datetime
    ) -> bool: ...

    async def release_host(self, host_id: str, *, now: datetime) -> None: ...

    async def create_turn(
        self,
        *,
        turn_id: str,
        session_id: str,
        runtime_host_id: str,
        started_at: datetime,
    ) -> None: ...

    async def finish_turn(
        self,
        turn_id: str,
        *,
        runtime_host_id: str,
        state: Literal["completed", "failed", "cancelled", "interrupted"],
        finished_at: datetime,
    ) -> None: ...

    async def list_interrupted_turns(self, session_id: str) -> tuple[StoredTurn, ...]: ...

    async def claim_interrupted_turn(
        self,
        turn_id: str,
        session_id: str,
        runtime_host_id: str,
        *,
        claimed_at: datetime,
    ) -> bool: ...


@dataclass(frozen=True)
class StoredTurn:
    """Non-sensitive persisted lifecycle metadata for a single Turn."""

    turn_id: str
    session_id: str
    runtime_host_id: str
    state: str
    started_at: datetime
    finished_at: datetime | None


TurnStoreFactory = Callable[[Path], TurnStore]


class SessionLifecycleStore(Protocol):
    """Coordinate durable Session state and operation leases across Runtime Hosts."""

    async def initialize(self) -> None: ...

    async def register_session(self, session_id: str) -> None: ...

    async def acquire_lease(
        self, session_id: str, lease_id: str, runtime_host_id: str, now: datetime
    ) -> None: ...

    async def release_lease(self, lease_id: str, runtime_host_id: str) -> None: ...

    async def begin_close(self, session_id: str, runtime_host_id: str) -> bool | None: ...

    async def release_close_claim(self, session_id: str, runtime_host_id: str) -> None: ...

    async def active_lease_count(self, session_id: str, now: datetime) -> int: ...

    async def finish_delete(self, session_id: str) -> None: ...


SessionLifecycleStoreFactory = Callable[[Path], SessionLifecycleStore]


@dataclass(frozen=True)
class PendingToolOutcome:
    """Safe metadata for a tool call whose result is not yet durable."""

    session_id: str
    turn_id: str
    task_run_id: str | None
    tool_call_id: str
    tool_name: str
    dispatched_host_id: str
    created_at: datetime
    resolving_host_id: str | None = None


class ToolOutcomeStore(Protocol):
    """Persist unresolved side-effecting tool calls until their result is durable."""

    async def initialize(self) -> None: ...

    async def record_pending(
        self,
        *,
        session_id: str,
        turn_id: str,
        task_run_id: str | None,
        tool_call_id: str,
        tool_name: str,
        runtime_host_id: str,
        now: datetime,
    ) -> None: ...

    async def list_pending(self, session_id: str) -> tuple[PendingToolOutcome, ...]: ...

    async def claim_resolution(
        self, session_id: str, tool_call_id: str, *, runtime_host_id: str, now: datetime
    ) -> bool: ...

    async def release_resolution_claim(
        self, session_id: str, tool_call_id: str, *, runtime_host_id: str
    ) -> None: ...

    async def clear_pending(
        self, session_id: str, tool_call_id: str, *, runtime_host_id: str
    ) -> None: ...


ToolOutcomeStoreFactory = Callable[[Path], ToolOutcomeStore]


class TaskStore(Protocol):
    """Persist Runtime TaskRun lifecycle metadata and completed Plan outputs."""

    async def initialize(self) -> None: ...

    async def create_plan_run(
        self,
        *,
        plan_run_id: str,
        turn_id: str,
        session_id: str,
        runtime_host_id: str,
        goal: str,
        snapshot: Mapping[str, object],
        started_at: datetime,
    ) -> None: ...

    async def update_plan_run(
        self,
        plan_run_id: str,
        *,
        runtime_host_id: str,
        state: PlanRunState,
        snapshot: Mapping[str, object],
        updated_at: datetime,
    ) -> None: ...

    async def get_plan_run(self, plan_run_id: str) -> PlanRunSnapshot | None: ...

    async def list_recoverable_plan_runs(self, session_id: str) -> tuple[PlanRunSnapshot, ...]: ...

    async def claim_plan_run(
        self, plan_run_id: str, *, runtime_host_id: str, now: datetime
    ) -> bool: ...

    async def release_plan_run_claim(self, plan_run_id: str, *, runtime_host_id: str) -> None: ...

    async def record_tool_invocation(
        self,
        *,
        invocation_id: str,
        plan_run_id: str,
        task_run_id: str,
        task_id: str,
        call_index: int,
        tool_id: str,
        retry_class: str,
        idempotency_key: str | None,
        request_fingerprint: str,
        arguments_json: str,
        created_at: datetime,
        runtime_host_id: str,
        idempotency_window_seconds: int | None = None,
    ) -> None: ...

    async def record_runtime_tool_invocation(
        self,
        *,
        invocation_id: str,
        session_id: str,
        turn_id: str,
        runtime_host_id: str,
        task_run_id: str | None,
        task_id: str,
        call_index: int,
        tool_id: str,
        retry_class: str,
        idempotency_key: str | None,
        request_fingerprint: str,
        arguments_json: str,
        idempotency_window_seconds: int | None,
        created_at: datetime,
    ) -> None: ...

    async def list_runtime_tool_invocations(
        self, session_id: str, turn_id: str
    ) -> tuple[ToolInvocationRecord, ...]: ...

    async def claim_runtime_tool_invocations(
        self, session_id: str, turn_id: str, *, runtime_host_id: str, now: datetime
    ) -> bool: ...

    async def finish_tool_invocation(
        self,
        invocation_id: str,
        *,
        plan_run_id: str | None,
        runtime_host_id: str,
        state: ToolInvocationState,
        finished_at: datetime,
    ) -> None: ...

    async def list_tool_invocations(
        self, plan_run_id: str | None = None, *, session_id: str | None = None
    ) -> tuple[ToolInvocationRecord, ...]: ...

    async def resolve_tool_invocation(
        self,
        invocation_id: str,
        *,
        plan_run_id: str,
        runtime_host_id: str,
        state: Literal["user_reported", "not_executed"],
        reported_result: str | None,
        resolved_at: datetime,
    ) -> None: ...

    async def create_task_run(
        self,
        *,
        task_run_id: str,
        task_id: str,
        turn_id: str,
        session_id: str,
        runtime_host_id: str,
        started_at: datetime,
    ) -> None: ...

    async def finish_task_run(
        self,
        task_run_id: str,
        *,
        runtime_host_id: str,
        state: Literal["completed", "failed", "cancelled"],
        finished_at: datetime,
        completed_result: CompletedPlanTaskResult | None = None,
        failure_summary: FailedPlanTaskSummary | None = None,
        plan_run_id: str | None = None,
        plan_snapshot: Mapping[str, object] | None = None,
    ) -> None: ...

    async def find_completed_task_result(
        self, *, session_id: str, exclude_turn_id: str, task_fingerprint: str
    ) -> StoredPlanTaskResult | None: ...

    async def list_recent_task_failures(
        self, *, session_id: str, exclude_turn_id: str, limit: int
    ) -> tuple[StoredPlanTaskFailure, ...]: ...

    async def list_completed_task_results(
        self,
        *,
        session_id: str,
        exclude_turn_id: str,
        limit: int,
        max_chars: int = 65_536,
    ) -> tuple[StoredPlanTaskResult, ...]: ...

    async def interrupt_inactive_host_tasks(self, *, now: datetime) -> int: ...


TaskStoreFactory = Callable[[Path], TaskStore]


class ModelConfigurationStore(Protocol):
    """Persist non-secret model settings without taking ownership of credentials."""

    async def initialize(self) -> None: ...

    async def get(self) -> ModelConfiguration | None: ...

    async def save(self, *, model_name: str, base_url: str | None, streaming: bool) -> None: ...


class RoutingModelConfigurationStore(Protocol):
    """Persist optional model settings and local embedding/reranker credentials."""

    async def initialize(self) -> None: ...

    async def get(self) -> RoutingModelConfiguration | None: ...

    async def save(
        self,
        *,
        judge_base_url: str | None,
        embedding_base_url: str | None,
        reranker_base_url: str | None,
        embedding_model: str | None,
        judge_model: str | None,
        reranker_model: str | None = None,
        embedding_api_key: str | None = None,
        reranker_api_key: str | None = None,
    ) -> None: ...


class MCPConfigurationStore(Protocol):
    """Persist non-secret MCP connection settings; credentials stay in process memory."""

    async def initialize(self) -> None: ...

    async def list(self) -> tuple[MCPServerConfiguration, ...]: ...

    async def save(self, configuration: MCPServerConfiguration) -> None: ...

    async def delete(self, name: str) -> bool: ...


class HarnessConfigurationStore(Protocol):
    """Persist validated non-secret request and runtime limits."""

    async def initialize(self) -> None: ...

    async def get(self) -> HarnessConfiguration | None: ...

    async def save(self, configuration: HarnessConfiguration) -> None: ...


class WorkspaceConfigurationStore(Protocol):
    """Persist the selected local workspace path."""

    async def initialize(self) -> None: ...

    async def get(self) -> str | None: ...

    async def save(self, workspace_path: str) -> None: ...


class SkillExecutionStore(Protocol):
    """Persist per-Skill execution mode preferences."""

    async def initialize(self) -> None: ...

    async def list(self) -> dict[str, SkillExecutionMode]: ...

    async def set(self, skill_id: str, execution_mode: SkillExecutionMode) -> None: ...

    async def delete(self, skill_id: str) -> None: ...


class AuditStore(Protocol):
    """Persist and query sanitized security audit events."""

    async def initialize(self) -> None: ...

    def record(self, event: AuditEvent) -> None: ...

    async def list_events(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        trace_id: UUID | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[AuditEvent, ...]: ...


class TraceStore(Protocol):
    """Persist, summarize, and query sanitized trace events."""

    async def initialize(self) -> None: ...

    def record(self, event: TraceEvent) -> None: ...

    async def list_recent(self, *, limit: int = 100) -> tuple[TraceSummary, ...]: ...

    async def get_trace(self, trace_id: UUID) -> tuple[StoredTraceEvent, ...]: ...


class CancellationStore(Protocol):
    """Persist safe cancellation snapshots and expose recent diagnostic entries."""

    async def initialize(self) -> None: ...

    async def record(self, snapshot: CancellationSnapshot) -> str: ...

    async def list_recent(self, *, limit: int = 100) -> tuple[CancellationAuditEntry, ...]: ...


class MemoryStore(Protocol):
    """Read and replace scoped memory through the memory governance layer."""

    @property
    def max_file_bytes(self) -> int: ...

    def read_session_summary(self, session_id: str) -> str | None: ...

    def write_session_summary(self, session_id: str, content: str) -> None: ...

    def delete_session_summary(self, session_id: str) -> bool: ...

    def read_long_term_memory(self) -> str | None: ...

    def write_long_term_memory(self, content: str) -> None: ...

    def archive_raw_memory(self, archive: MemoryRawArchive) -> None: ...

    def read_raw_memory_archive(self, archive_id: UUID) -> MemoryRawArchive | None: ...

    def list_raw_memory_archives(
        self, session_id: str, *, limit: int = 100
    ) -> tuple[MemoryRawArchive, ...]: ...

    def delete_raw_memory_archive(self, archive_id: UUID) -> bool: ...


CheckpointerFactory = Callable[[Path], AbstractAsyncContextManager[ConversationCheckpointer]]
ConversationStoreFactory = Callable[[Path], ConversationStore]
ModelConfigurationStoreFactory = Callable[[Path], ModelConfigurationStore]
RoutingModelConfigurationStoreFactory = Callable[[Path], RoutingModelConfigurationStore]
MCPConfigurationStoreFactory = Callable[[Path], MCPConfigurationStore]
HarnessConfigurationStoreFactory = Callable[[Path], HarnessConfigurationStore]
WorkspaceConfigurationStoreFactory = Callable[[Path], WorkspaceConfigurationStore]
SkillExecutionStoreFactory = Callable[[Path], SkillExecutionStore]
AuditStoreFactory = Callable[[Path], AuditStore]
TraceStoreFactory = Callable[[Path], TraceStore]
CancellationStoreFactory = Callable[[Path], CancellationStore]
MemoryStoreFactory = Callable[[Path], MemoryStore]


__all__ = [
    "CheckpointerFactory",
    "AuditStore",
    "AuditStoreFactory",
    "CancellationStore",
    "CancellationStoreFactory",
    "MemoryStore",
    "MemoryStoreFactory",
    "ConversationCheckpointer",
    "ConversationStore",
    "ConversationStoreFactory",
    "TurnStore",
    "TurnStoreFactory",
    "TaskStore",
    "TaskStoreFactory",
    "MCPConfigurationStore",
    "MCPConfigurationStoreFactory",
    "HarnessConfigurationStore",
    "HarnessConfigurationStoreFactory",
    "ModelConfigurationStore",
    "ModelConfigurationStoreFactory",
    "SkillExecutionStore",
    "SkillExecutionStoreFactory",
    "TraceStore",
    "TraceStoreFactory",
    "WorkspaceConfigurationStore",
    "WorkspaceConfigurationStoreFactory",
]
