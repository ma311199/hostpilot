"""Storage contracts consumed by application and orchestration modules."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from hostpilot.contracts import ConversationSummary, MemoryRawArchive, SandboxMode
from hostpilot.observability import AuditEvent, TraceEvent
from hostpilot.persistence.cancellations import CancellationAuditEntry
from hostpilot.persistence.harness_configuration import HarnessRuntimeConfiguration
from hostpilot.persistence.mcp_configuration import MCPServerConfiguration
from hostpilot.persistence.model_configuration import ModelConfiguration
from hostpilot.persistence.routing_model_configuration import RoutingModelConfiguration
from hostpilot.persistence.skill_execution import SkillExecutionMode
from hostpilot.persistence.traces import StoredTraceEvent, TraceSummary
from hostpilot.skills.compensation import CancellationSnapshot


class ConversationStore(Protocol):
    """Metadata store required by conversation APIs and chat orchestration."""

    async def initialize(self) -> None: ...

    async def create(self, title: str | None = None) -> ConversationSummary: ...

    async def get(self, conversation_id: str) -> ConversationSummary | None: ...

    async def list(self) -> tuple[ConversationSummary, ...]: ...

    async def touch(self, conversation_id: str, *, title: str | None = None) -> None: ...

    async def delete(self, conversation_id: str) -> bool: ...

    async def upgrade_sandbox_mode(
        self, conversation_id: str, target: SandboxMode
    ) -> ConversationSummary: ...


class ConversationCheckpointer(Protocol):
    """Checkpoint operations directly required by the conversation API."""

    async def adelete_thread(self, thread_id: str) -> None: ...


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

    async def get(self) -> HarnessRuntimeConfiguration | None: ...

    async def save(self, configuration: HarnessRuntimeConfiguration) -> None: ...


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

    def read_session_summary(self, conversation_id: str) -> str | None: ...

    def write_session_summary(self, conversation_id: str, content: str) -> None: ...

    def delete_session_summary(self, conversation_id: str) -> bool: ...

    def read_long_term_memory(self) -> str | None: ...

    def write_long_term_memory(self, content: str) -> None: ...

    def archive_raw_memory(self, archive: MemoryRawArchive) -> None: ...

    def read_raw_memory_archive(self, archive_id: UUID) -> MemoryRawArchive | None: ...

    def list_raw_memory_archives(
        self, conversation_id: str, *, limit: int = 100
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
