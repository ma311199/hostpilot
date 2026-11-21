"""Local FastAPI application for managing conversation sessions."""

from __future__ import annotations

import asyncio
import csv
import io
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, cast
from uuid import UUID

from fastapi import (
    APIRouter,
    FastAPI,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)

from hostpilot.api.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationHistoryResponse,
    RoutingEvaluationRequest,
    RoutingEvaluationResponse,
)
from hostpilot.budget import BudgetExceededError
from hostpilot.config import (
    RemoteMCPServerSettings,
    Settings,
    contains_credential_argument,
    load_settings,
)
from hostpilot.context import ContextBuilder, ConversationNotFoundError
from hostpilot.contracts import (
    BudgetLimits,
    ConversationMessage,
    ConversationSummary,
    ExecutionEnvironment,
    MemoryScope,
    MemoryWriteProposal,
    MessageRole,
    SandboxMode,
    ToolConnectionStatus,
    ToolConnectionTransport,
    ToolType,
    TrustLevel,
)
from hostpilot.memory import MemoryController, MemoryWriteDenied, MemoryWriteService
from hostpilot.memory.review import (
    DuplicatePendingProposal,
    MemoryProposalReview,
    PendingProposalNotFound,
)
from hostpilot.models.factory import ModelProviderRegistry
from hostpilot.observability import (
    AuditEvent,
    AuditOutcome,
    CompositeAuditSink,
    StructuredAuditSink,
)
from hostpilot.orchestration import (
    ChatAgentRuntime,
    ChatService,
    create_configured_chat_agent_runtime,
)
from hostpilot.orchestration.application_stores_host_plugin import (
    ApplicationStoresHostPlugin,
)
from hostpilot.orchestration.chat import RequestTooLargeError, UnsupportedRouteError
from hostpilot.orchestration.configuration_stores_host_plugin import (
    ConfigurationStoresHostPlugin,
)
from hostpilot.orchestration.mcp_provider_host_plugin import MCPProviderHostPlugin
from hostpilot.orchestration.routing_models_host_plugin import RoutingModelsHostPlugin
from hostpilot.orchestration.runtime_host_plugin import ChatAgentRuntimeHostPlugin
from hostpilot.orchestration.runtime_plugins import ChatAgentRuntimeRegistry
from hostpilot.orchestration.sandbox_backends_host_plugin import (
    SandboxBackendsHostPlugin,
)
from hostpilot.orchestration.state_services_host_plugin import StateServicesHostPlugin
from hostpilot.orchestration.tool_governance_host_plugin import ToolGovernanceHostPlugin
from hostpilot.output import OutputProcessor, OutputValidationError
from hostpilot.persistence import (
    AuditStore,
    AuditStoreFactory,
    CancellationStore,
    CancellationStoreFactory,
    CheckpointerFactory,
    ConversationStore,
    ConversationStoreFactory,
    HarnessConfiguration,
    HarnessConfigurationStore,
    HarnessConfigurationStoreFactory,
    MarkdownMemoryStore,
    MCPConfigurationStore,
    MCPConfigurationStoreFactory,
    MCPServerConfiguration,
    MemoryStore,
    MemoryStoreFactory,
    ModelConfiguration,
    ModelConfigurationStore,
    ModelConfigurationStoreFactory,
    RoutingModelConfiguration,
    RoutingModelConfigurationStore,
    RoutingModelConfigurationStoreFactory,
    SkillExecutionMode,
    SkillExecutionStore,
    SkillExecutionStoreFactory,
    SQLiteAuditStore,
    SQLiteCancellationStore,
    SQLiteConversationStore,
    SQLiteHarnessConfigurationStore,
    SQLiteMCPConfigurationStore,
    SQLiteModelConfigurationStore,
    SQLiteRoutingModelConfigurationStore,
    SQLiteSkillExecutionStore,
    SQLiteTraceStore,
    SQLiteWorkspaceConfigurationStore,
    TraceStore,
    TraceStoreFactory,
    WorkspaceConfigurationStore,
    WorkspaceConfigurationStoreFactory,
    open_sqlite_checkpointer,
)
from hostpilot.persistence.sqlite_path import prepare_sqlite_database_path
from hostpilot.policy.confirmation import ConfirmationDecision, ConfirmationRequest
from hostpilot.routing import CommandRouter, Router
from hostpilot.runtime import RuntimeContext, RuntimeHost
from hostpilot.sandbox import SandboxBackendFactory, SandboxBackendSet, sandbox_mode_scope
from hostpilot.skills.plan_plugins import PlanComponentRegistry
from hostpilot.tools.executor import (
    AsyncConfirmationProvider,
    RemoteToolOutcomeUnknownError,
    ToolConfirmationDeniedError,
    ToolDeniedError,
    ToolExecutionError,
)
from hostpilot.tools.facade import ToolFacade
from hostpilot.tools.filesystem import WorkspaceFiles
from hostpilot.tools.governance import ToolExecutorFactory
from hostpilot.workspace import (
    NativeFolderPickerUnavailable,
    WorkspaceAccessError,
    choose_folder_native,
    inspect_workspace,
)

router = APIRouter(prefix="/api/v1")
CHAT_CONFIRMATION_TIMEOUT_SECONDS = 60


class StatusPing(BaseModel):
    """Bounded client heartbeat message for the dashboard WebSocket."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^ping$")
    request_id: str = Field(min_length=1, max_length=128)


class ChatSocketRequest(BaseModel):
    """One bounded chat operation accepted over the chat WebSocket."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^chat.send$")
    session_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=100_000)

    @field_validator("message")
    @classmethod
    def message_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message cannot be blank.")
        return value


class ToolConfirmationDecisionRequest(BaseModel):
    """One explicit response to an outstanding high-impact tool proposal."""

    model_config = ConfigDict(extra="forbid")

    type: str = Field(pattern="^tool.confirmation.decision$")
    request_id: str = Field(min_length=1, max_length=64)
    decision: ConfirmationDecision


class WebSocketConfirmationProvider(AsyncConfirmationProvider):
    """Pause a chat turn and ask its connected browser to approve a tool call."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket
        self._confirmation_lock = asyncio.Lock()

    async def confirm(self, request: ConfirmationRequest) -> ConfirmationDecision:
        """Send a safe summary and wait for its matching approval decision."""
        async with self._confirmation_lock:
            return await self._confirm_locked(request)

    async def _confirm_locked(self, request: ConfirmationRequest) -> ConfirmationDecision:
        await self.websocket.send_json(
            {
                "type": "tool.confirmation.request",
                "data": {
                    "request_id": request.request_id,
                    "tool_name": request.tool_name,
                    "summary": request.summary,
                },
            }
        )
        try:
            async with asyncio.timeout(CHAT_CONFIRMATION_TIMEOUT_SECONDS):
                while True:
                    raw_message = await self.websocket.receive_text()
                    if len(raw_message.encode("utf-8")) > 1024:
                        continue
                    try:
                        response = ToolConfirmationDecisionRequest.model_validate_json(raw_message)
                    except (ValidationError, ValueError, json.JSONDecodeError):
                        continue
                    if response.request_id == request.request_id:
                        return response.decision
        except TimeoutError:
            return ConfirmationDecision.TIMED_OUT
        except WebSocketDisconnect:
            return ConfirmationDecision.CANCELLED


class CreateConversationRequest(BaseModel):
    """Validated fields accepted when opening a new conversation."""

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, max_length=200)


class SandboxUpgradeRequest(BaseModel):
    """Explicit user-approved target for one irreversible session upgrade."""

    model_config = ConfigDict(extra="forbid")

    target_mode: SandboxMode


def _set_runtime_sandbox_mode(application: FastAPI, mode: SandboxMode | None) -> None:
    """Apply a chat mode to MCP handlers whose transport uses persistent server tasks."""
    pending: list[Any] = [
        getattr(application.state, "mcp_provider", None),
        getattr(application.state, "tool_facade", None),
        getattr(application.state, "chat_service", None),
    ]
    seen: set[int] = set()
    while pending:
        candidate = pending.pop()
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        set_mode = getattr(candidate, "set_sandbox_mode", None)
        if callable(set_mode):
            set_mode(mode)
        for attribute in ("provider", "tool_provider", "runtime", "_provider", "_runtime"):
            nested = getattr(candidate, attribute, None)
            if nested is not None:
                pending.append(nested)


class ModelConfigurationRequest(BaseModel):
    """OpenAI-compatible model credentials submitted from the local settings page."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    model_name: str | None = Field(default=None, min_length=1, max_length=200)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    base_url: str | None = Field(default=None, max_length=2048)
    streaming: bool | None = None

    @field_validator("api_key")
    @classmethod
    def api_key_must_not_be_blank(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        secret = value.get_secret_value().strip()
        if not secret:
            raise ValueError("API key cannot be blank.")
        return SecretStr(secret)

    @field_validator("base_url")
    @classmethod
    def base_url_must_be_http(cls, value: str | None) -> str | None:
        if value is not None and value and not value.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://.")
        return value or None


class ModelConfigurationStatus(BaseModel):
    """Safe model runtime status that never includes its API key."""

    configured: bool
    saved: bool = False
    api_key_required: bool = False
    model_name: str | None = None
    base_url: str | None = None
    streaming: bool = True


class RoutingModelConfigurationRequest(BaseModel):
    """Independent optional model-role settings and credentials."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    judge_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    reranker_model: str | None = Field(default=None, min_length=1, max_length=200)
    judge_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    embedding_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    reranker_api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    judge_base_url: str | None = Field(default=None, max_length=2048)
    embedding_base_url: str | None = Field(default=None, max_length=2048)
    reranker_base_url: str | None = Field(default=None, max_length=2048)

    @field_validator("judge_api_key", "embedding_api_key", "reranker_api_key")
    @classmethod
    def routing_api_key_must_not_be_blank(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        secret = value.get_secret_value().strip()
        if not secret:
            raise ValueError("API key cannot be blank.")
        return SecretStr(secret)

    @field_validator("judge_base_url", "embedding_base_url", "reranker_base_url")
    @classmethod
    def routing_base_url_must_be_http(cls, value: str | None) -> str | None:
        if value is not None and value and not value.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://.")
        return value or None


class RoutingModelConfigurationStatus(BaseModel):
    """Safe public status for optional configured model roles."""

    configured: bool
    saved: bool = False
    judge_api_key_configured: bool = False
    embedding_api_key_configured: bool = False
    reranker_api_key_configured: bool = False
    judge_model: str | None = None
    embedding_model: str | None = None
    reranker_model: str | None = None
    judge_base_url: str | None = None
    embedding_base_url: str | None = None
    reranker_base_url: str | None = None


class ModelConnectionTestRequest(BaseModel):
    """One model role's unsaved connection-test inputs."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["llm", "judge", "embedding", "reranker"]
    model_name: str = Field(min_length=1, max_length=200)
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=8192)
    base_url: str | None = Field(default=None, max_length=2048)
    streaming: bool | None = None

    @field_validator("api_key")
    @classmethod
    def model_test_api_key_must_not_be_blank(cls, value: SecretStr | None) -> SecretStr | None:
        if value is None:
            return None
        secret = value.get_secret_value().strip()
        if not secret:
            raise ValueError("API key cannot be blank.")
        return SecretStr(secret)

    @field_validator("base_url")
    @classmethod
    def model_test_base_url_must_be_http(cls, value: str | None) -> str | None:
        if value is not None and value and not value.startswith(("http://", "https://")):
            raise ValueError("Base URL must start with http:// or https://.")
        return value or None


class ModelConnectionTestResponse(BaseModel):
    """Sanitized result of one model role's connection test."""

    role: Literal["llm", "judge", "embedding", "reranker"]
    success: bool
    message: str
    error_code: str | None = None


class ToolCatalogItem(BaseModel):
    """Safe public information about one locally registered tool."""

    name: str
    description: str
    tool_type: ToolType
    transport: ToolConnectionTransport
    trust_level: TrustLevel
    execution_env: ExecutionEnvironment
    execution_location: str
    access_summary: str
    sandbox_status: str
    configured: bool
    high_impact: bool
    confirmation_required: bool
    status_message: str


class ToolCatalogResponse(BaseModel):
    """Tool availability without exposing schemas, handlers, or credentials."""

    runtime_configured: bool
    tools: tuple[ToolCatalogItem, ...]


class ToolConnectionsResponse(BaseModel):
    """Safe configured tool connection lifecycle information."""

    connections: tuple[ToolConnectionStatus, ...]


class MCPServerConfigurationRequest(BaseModel):
    """Non-secret server settings and an optional one-time API key submission."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,31}$")
    transport: Literal["streamable_http", "stdio"] = "streamable_http"
    url: str | None = Field(default=None, min_length=1, max_length=2048)
    command: str | None = Field(default=None, min_length=1, max_length=512)
    args: list[str] = Field(default_factory=list, max_length=128)
    cwd: str | None = Field(default=None, max_length=2048)
    timeout_seconds: float = Field(default=15, gt=0, le=120)
    enabled: bool = True
    description: str = Field(default="", max_length=500)
    api_key_header: str | None = Field(default=None, max_length=128)
    api_key_env: str | None = Field(default=None, max_length=128)
    api_key: SecretStr | None = Field(default=None, max_length=8192)

    @field_validator("url")
    @classmethod
    def url_must_not_embed_credentials(cls, value: str | None) -> str | None:
        from urllib.parse import parse_qsl, urlsplit

        if value is None:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("MCP URL must be an absolute HTTP or HTTPS URL.")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise ValueError("MCP URL must not contain credentials or a fragment.")
        sensitive_query_names = {
            "api_key",
            "apikey",
            "authorization",
            "access_token",
            "token",
            "secret",
            "password",
        }
        if any(name.lower() in sensitive_query_names for name, _ in parse_qsl(parsed.query)):
            raise ValueError("MCP credentials must not be embedded in the URL query.")
        return value

    @field_validator("api_key_header")
    @classmethod
    def header_name_must_be_valid(cls, value: str | None) -> str | None:
        import re

        if value is not None and not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", value):
            raise ValueError("API key header must be a valid HTTP header name.")
        return value

    @field_validator("api_key_env")
    @classmethod
    def environment_name_must_be_valid(cls, value: str | None) -> str | None:
        import re

        if value is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", value):
            raise ValueError("API key environment name must be a valid variable name.")
        return value

    @field_validator("args")
    @classmethod
    def validate_process_arguments(cls, value: list[str]) -> list[str]:
        if any("\x00" in argument or len(argument) > 4096 for argument in value):
            raise ValueError("STDIO arguments must be bounded text without null characters.")
        if contains_credential_argument(value):
            raise ValueError("Credentials must be submitted through the API key field, not args.")
        return value

    @field_validator("api_key")
    @classmethod
    def blank_api_key_is_empty(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not value.get_secret_value().strip():
            return None
        if value is not None and any(
            ord(character) < 32 or ord(character) == 127 for character in value.get_secret_value()
        ):
            raise ValueError("API key must not contain HTTP header control characters.")
        return SecretStr(value.get_secret_value().strip()) if value is not None else None

    @model_validator(mode="after")
    def key_requires_header_name(self) -> MCPServerConfigurationRequest:
        if self.transport == "streamable_http":
            if self.url is None or self.command is not None:
                raise ValueError("Streamable HTTP servers require a URL and no command.")
            if self.api_key_env is not None:
                raise ValueError("Streamable HTTP API keys must use a header name.")
            if self.api_key is not None and self.api_key_header is None:
                raise ValueError("An API key header name is required when an API key is supplied.")
        else:
            if self.command is None or self.url is not None:
                raise ValueError("STDIO servers require a command and no URL.")
            if self.api_key_header is not None:
                raise ValueError("STDIO API keys must use an environment variable name.")
            if self.api_key is not None and self.api_key_env is None:
                raise ValueError("An API key environment name is required when a key is supplied.")
        return self


class MCPServerConfigurationResponse(BaseModel):
    """Safe MCP server metadata and connection state; never includes key values."""

    name: str
    transport: str
    url: str | None
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    timeout_seconds: float
    enabled: bool
    description: str = ""
    api_key_header: str | None
    api_key_env: str | None
    api_key_configured: bool
    managed: bool
    connection_state: str
    error_code: str | None = None


class MCPServerConfigurationsResponse(BaseModel):
    """Configured remote servers visible to the local connection manager."""

    servers: tuple[MCPServerConfigurationResponse, ...]


class SkillCatalogItem(BaseModel):
    """Public metadata for one validated Skill package."""

    skill_id: str
    name: str
    description: str
    execution_format: str
    detected_formats: tuple[Literal["yaml", "skill_md"], ...]
    execution_mode: SkillExecutionMode
    available_execution_modes: tuple[SkillExecutionMode, ...]
    enabled: bool
    source: str


class SkillCatalogResponse(BaseModel):
    """Validated Skill packages and the count of packages that failed validation."""

    skills: tuple[SkillCatalogItem, ...]
    invalid_package_count: int


class SkillFileResponse(BaseModel):
    """One validated, text-only file from a Skill package."""

    relative_path: str
    content: str


class SkillDetailResponse(BaseModel):
    """Text files belonging to one validated Skill package."""

    skill_id: str
    files: tuple[SkillFileResponse, ...]


class SkillEnabledRequest(BaseModel):
    """Requested enabled state for one user-owned Skill."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=128)
    enabled: bool


class SkillExecutionRequest(BaseModel):
    """Requested execution entry for one discovered Skill package."""

    model_config = ConfigDict(extra="forbid")

    skill_id: str = Field(min_length=1, max_length=128)
    execution_mode: SkillExecutionMode

    @field_validator("execution_mode", mode="before")
    @classmethod
    def normalize_legacy_execution_mode(cls, value: Any) -> Any:
        """Accept the retired backend name from older API clients."""
        return "markdown" if value == "deepagents" else value


class CancellationAuditResponse(BaseModel):
    """Sanitized execution cancellation diagnostics without handles or payloads."""

    event_id: str
    request_id: str
    scope: str
    occurred_at: str
    uncertain_operations: tuple[str, ...]
    side_effects: tuple[dict[str, Any], ...]
    compensation_records: tuple[dict[str, Any], ...]


class TraceSummaryResponse(BaseModel):
    """One safe request overview for the Trace page."""

    trace_id: str
    first_occurred_at: str
    last_occurred_at: str
    event_count: int
    last_event: str
    status: str | None
    skill_id: str | None


class TraceEventResponse(BaseModel):
    """One allowlisted trace event without prompt or tool payload data."""

    event_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    occurred_at: str
    attributes: dict[str, str | int | float | bool]


class TraceDetailResponse(BaseModel):
    """Events belonging to one correlated request."""

    trace_id: str
    events: tuple[TraceEventResponse, ...]


class WorkspaceStatusResponse(BaseModel):
    """Current local workspace path and effective filesystem access mode."""

    path: str
    read_only: bool
    selected: bool = True
    message: str | None = None


class AuditEventResponse(BaseModel):
    """One persisted audit decision without request arguments or secrets."""

    event_id: str
    trace_id: str | None
    action: str
    outcome: str
    resource_type: str
    resource_id: str | None
    reason_code: str | None
    occurred_at: str


@router.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Return a lightweight liveness response."""
    return {"status": "ok"}


@router.get(
    "/cancellations",
    response_model=tuple[CancellationAuditResponse, ...],
    tags=["observability"],
)
async def list_cancellations(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
) -> tuple[CancellationAuditResponse, ...]:
    """List sanitized persisted cancellation and compensation outcomes."""
    entries = await request.app.state.cancellations.list_recent(limit=limit)
    return tuple(CancellationAuditResponse(**entry.__dict__) for entry in entries)


@router.get(
    "/observability/traces",
    response_model=tuple[TraceSummaryResponse, ...],
    tags=["observability"],
)
async def list_traces(
    request: Request,
    limit: int = Query(default=100, ge=1, le=500),
) -> tuple[TraceSummaryResponse, ...]:
    """List recent request traces using only persisted safe metadata."""
    entries = await request.app.state.traces.list_recent(limit=limit)
    return tuple(
        TraceSummaryResponse(
            trace_id=str(entry.trace_id),
            first_occurred_at=entry.first_occurred_at.isoformat(),
            last_occurred_at=entry.last_occurred_at.isoformat(),
            event_count=entry.event_count,
            last_event=entry.last_event,
            status=entry.status,
            skill_id=entry.skill_id,
        )
        for entry in entries
    )


@router.get(
    "/observability/traces/{trace_id}",
    response_model=TraceDetailResponse,
    tags=["observability"],
)
async def get_trace(trace_id: UUID, request: Request) -> TraceDetailResponse:
    """Return allowlisted event metadata for one trace identifier."""
    entries = await request.app.state.traces.get_trace(trace_id)
    if not entries:
        raise HTTPException(status_code=404, detail="Trace not found.")
    return TraceDetailResponse(
        trace_id=str(trace_id),
        events=tuple(
            TraceEventResponse(
                event_id=str(entry.event_id),
                span_id=str(entry.event.context.span_id),
                parent_span_id=(
                    str(entry.event.context.parent_span_id)
                    if entry.event.context.parent_span_id
                    else None
                ),
                name=entry.event.name,
                occurred_at=entry.event.occurred_at.isoformat(),
                attributes=entry.event.attributes,
            )
            for entry in entries
        ),
    )


@router.get(
    "/observability/audit",
    response_model=tuple[AuditEventResponse, ...],
    tags=["observability"],
)
async def list_audit_events(
    request: Request,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0, le=1_000_000),
    trace_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> tuple[AuditEventResponse, ...]:
    """List persisted audit decisions with optional trace and time filters."""
    try:
        entries = await request.app.state.audits.list_events(
            limit=limit,
            offset=offset,
            trace_id=trace_id,
            since=since,
            until=until,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return tuple(
        AuditEventResponse(
            event_id=str(entry.event_id),
            trace_id=str(entry.trace_id) if entry.trace_id else None,
            action=entry.action,
            outcome=entry.outcome.value,
            resource_type=entry.resource_type,
            resource_id=entry.resource_id,
            reason_code=entry.reason_code,
            occurred_at=entry.occurred_at.isoformat(),
        )
        for entry in entries
    )


@router.get("/observability/audit/export.csv", tags=["observability"])
async def export_audit_events_csv(
    request: Request,
    trace_id: UUID | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> Response:
    """Export up to 1,000 filtered audit events as spreadsheet-safe CSV."""
    try:
        entries = await request.app.state.audits.list_events(
            limit=1000,
            trace_id=trace_id,
            since=since,
            until=until,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        (
            "event_id",
            "trace_id",
            "action",
            "outcome",
            "resource_type",
            "resource_id",
            "reason_code",
            "occurred_at",
        )
    )
    for entry in entries:
        writer.writerow(
            _spreadsheet_safe(value)
            for value in (
                str(entry.event_id),
                str(entry.trace_id) if entry.trace_id else "",
                entry.action,
                entry.outcome.value,
                entry.resource_type,
                entry.resource_id or "",
                entry.reason_code or "",
                entry.occurred_at.isoformat(),
            )
        )
    return Response(
        content=f"\ufeff{output.getvalue()}",
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="hostpilot-audit.csv"'},
    )


def _spreadsheet_safe(value: str) -> str:
    """Prefix spreadsheet formula initiators so exported fields remain inert."""
    if value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return f"'{value}"
    return value


@router.get("/model/configuration", response_model=ModelConfigurationStatus, tags=["model"])
async def get_model_configuration(request: Request) -> ModelConfigurationStatus:
    """Report whether a model runtime is available without revealing secrets."""
    get_status = cast(
        Callable[[], ModelConfigurationStatus],
        request.app.state.get_model_configuration_status,
    )
    return get_status()


@router.put("/model/configuration", response_model=ModelConfigurationStatus, tags=["model"])
async def configure_model(
    body: ModelConfigurationRequest,
    request: Request,
) -> ModelConfigurationStatus:
    """Create or replace the in-memory OpenAI-compatible model runtime."""
    configure = cast(
        Callable[[ModelConfigurationRequest], Awaitable[ModelConfigurationStatus]],
        request.app.state.configure_model,
    )
    try:
        return await configure(body)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not configure this model provider.",
        ) from error


@router.get(
    "/routing-model/configuration",
    response_model=RoutingModelConfigurationStatus,
    tags=["routing"],
)
async def get_routing_model_configuration(request: Request) -> RoutingModelConfigurationStatus:
    """Report independent routing model settings without exposing credentials."""
    return cast(
        Callable[[], RoutingModelConfigurationStatus],
        request.app.state.get_routing_model_configuration_status,
    )()


@router.put(
    "/routing-model/configuration",
    response_model=RoutingModelConfigurationStatus,
    tags=["routing"],
)
async def configure_routing_model(
    body: RoutingModelConfigurationRequest, request: Request
) -> RoutingModelConfigurationStatus:
    """Configure the remote small model and embedding endpoint used only for routing."""
    configure = cast(
        Callable[
            [RoutingModelConfigurationRequest],
            Awaitable[RoutingModelConfigurationStatus],
        ],
        request.app.state.configure_routing_model,
    )
    try:
        return await configure(body)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not configure the routing model.",
        ) from error


@router.get(
    "/harness/configuration",
    response_model=HarnessConfiguration,
    tags=["configuration"],
)
async def get_harness_configuration(request: Request) -> HarnessConfiguration:
    """Return active request limits and budgets without exposing credentials."""
    return cast(HarnessConfiguration, request.app.state.harness_configuration)


@router.put(
    "/harness/configuration",
    response_model=HarnessConfiguration,
    tags=["configuration"],
)
async def configure_harness(
    configuration: HarnessConfiguration,
    request: Request,
) -> HarnessConfiguration:
    """Persist and apply the complete validated Harness runtime configuration."""
    configure = cast(
        Callable[[HarnessConfiguration], Awaitable[HarnessConfiguration]],
        request.app.state.configure_harness_configuration,
    )
    try:
        return await configure(configuration)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not update Harness configuration.",
        ) from error


@router.get("/workspace", response_model=WorkspaceStatusResponse, tags=["workspace"])
async def get_workspace(request: Request) -> WorkspaceStatusResponse:
    """Return the selected local workspace and its effective access mode."""
    return cast(WorkspaceStatusResponse, request.app.state.workspace_status)


@router.post("/workspace/select", response_model=WorkspaceStatusResponse, tags=["workspace"])
async def select_workspace(request: Request) -> WorkspaceStatusResponse:
    """Open the local native folder picker and apply a readable directory."""
    select = cast(
        Callable[[], Awaitable[WorkspaceStatusResponse]],
        request.app.state.select_workspace_directory,
    )
    try:
        return await select()
    except NativeFolderPickerUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(error),
        ) from error
    except WorkspaceAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


@router.get("/tools", response_model=ToolCatalogResponse, tags=["tools"])
async def list_tools(request: Request) -> ToolCatalogResponse:
    """List local tool descriptions and safe configuration/approval status."""
    app_settings: Settings = request.app.state.settings
    runtime_configured = request.app.state.chat_service is not None
    rag_configured = all(
        (app_settings.rag_base_url, app_settings.rag_user_id, app_settings.rag_key)
    )
    tools = []
    for descriptor in request.app.state.tool_facade.descriptors():
        workspace_is_read_only = request.app.state.workspace_files.read_only
        workspace_write = descriptor.name in {"write_text_file", "write_existing_text_file"}
        sandbox_tool_unavailable = (
            descriptor.execution_env is ExecutionEnvironment.NATIVE_SANDBOX
            and descriptor.name not in request.app.state.mcp_provider.sandboxed_tools
        )
        configured = (
            (descriptor.name != "retrieve_knowledge" or rag_configured)
            and not (workspace_write and workspace_is_read_only)
            and not sandbox_tool_unavailable
        )
        if workspace_write and workspace_is_read_only:
            status_message = "当前工作区为只读模式，文件写入工具已禁用。"
        elif sandbox_tool_unavailable:
            status_message = request.app.state.mcp_provider.sandbox_unavailability_reasons.get(
                descriptor.name,
                "该工具所需运行时或 Linux Bubblewrap 沙箱不可用，已禁用执行。",
            )
        elif not configured:
            status_message = "需要在后端配置 RAG 服务后才能检索。"
        elif not runtime_configured:
            status_message = "请先配置模型运行时，Agent 才能调用此工具。"
        else:
            status_message = "已配置，可由 Agent 按需调用。"
        if descriptor.transport is ToolConnectionTransport.STDIO:
            execution_location = "本机 STDIO 子进程"
        elif descriptor.transport is ToolConnectionTransport.STREAMABLE_HTTP:
            execution_location = "远程 Streamable HTTP Server"
        elif descriptor.execution_env is ExecutionEnvironment.NATIVE_SANDBOX:
            execution_location = "Linux bubblewrap 隔离进程"
        elif descriptor.execution_env is ExecutionEnvironment.SERVICE:
            execution_location = "HostPilot 进程内调用外部服务"
        elif descriptor.execution_env is ExecutionEnvironment.REMOTE:
            execution_location = "远程服务"
        else:
            execution_location = "HostPilot 进程内"
        if descriptor.execution_env in {
            ExecutionEnvironment.NATIVE_SANDBOX,
            ExecutionEnvironment.WASM,
        }:
            sandbox_status = (
                "OS 沙箱已就绪"
                if descriptor.execution_env in request.app.state.mcp_provider.sandboxed_environments
                and descriptor.name in request.app.state.mcp_provider.sandboxed_tools
                else "OS 沙箱不可用；工具已禁用"
            )
        elif descriptor.transport is ToolConnectionTransport.STREAMABLE_HTTP:
            sandbox_status = "远端执行；HostPilot 本机沙箱不适用"
        else:
            sandbox_status = "无 OS 沙箱"
        tools.append(
            ToolCatalogItem(
                name=descriptor.name,
                description=descriptor.description,
                tool_type=descriptor.tool_type,
                transport=descriptor.transport,
                trust_level=descriptor.trust_level,
                execution_env=descriptor.execution_env,
                execution_location=execution_location,
                access_summary=descriptor.access_summary,
                sandbox_status=sandbox_status,
                configured=configured,
                high_impact=descriptor.high_impact,
                confirmation_required=descriptor.high_impact,
                status_message=status_message,
            )
        )
    return ToolCatalogResponse(runtime_configured=runtime_configured, tools=tuple(tools))


@router.get("/mcp/connections", response_model=ToolConnectionsResponse, tags=["tools"])
async def list_mcp_connections(request: Request) -> ToolConnectionsResponse:
    """Return connection state and discovered tool counts without secrets or URLs."""
    return ToolConnectionsResponse(
        connections=(
            request.app.state.mcp_connection_status or request.app.state.tool_facade.connections()
        ),
    )


@router.post(
    "/model/configuration/test",
    response_model=ModelConnectionTestResponse,
    tags=["model"],
)
async def test_model_connection(
    body: ModelConnectionTestRequest,
    request: Request,
) -> ModelConnectionTestResponse:
    """Exercise one model endpoint without saving or exposing its credentials."""
    test_connection = cast(
        Callable[[ModelConnectionTestRequest], Awaitable[ModelConnectionTestResponse]],
        request.app.state.test_model_connection,
    )
    return await test_connection(body)


@router.get(
    "/mcp/servers",
    response_model=MCPServerConfigurationsResponse,
    tags=["tools"],
)
async def list_mcp_servers(request: Request) -> MCPServerConfigurationsResponse:
    """List remote MCP metadata and safe credential/connection state."""
    get_servers = cast(
        Callable[[], Awaitable[MCPServerConfigurationsResponse]],
        request.app.state.list_mcp_servers,
    )
    return await get_servers()


@router.put(
    "/mcp/servers",
    response_model=MCPServerConfigurationsResponse,
    tags=["tools"],
)
async def save_mcp_server(
    body: MCPServerConfigurationRequest,
    request: Request,
) -> MCPServerConfigurationsResponse:
    """Create or update one remote server and optionally provide its API key."""
    save_server = cast(
        Callable[
            [MCPServerConfigurationRequest],
            Awaitable[MCPServerConfigurationsResponse],
        ],
        request.app.state.save_mcp_server,
    )
    try:
        return await save_server(body)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Could not save this MCP server configuration.",
        ) from error


@router.delete("/mcp/servers/{server_name}", status_code=status.HTTP_204_NO_CONTENT, tags=["tools"])
async def delete_mcp_server(server_name: str, request: Request) -> Response:
    """Delete one user-managed remote MCP server."""
    delete_server = cast(Callable[[str], Awaitable[bool]], request.app.state.delete_mcp_server)
    try:
        deleted = await delete_server(server_name)
    except ValueError as error:
        raise HTTPException(
            status_code=409, detail="Environment-managed servers cannot be deleted."
        ) from error
    if not deleted:
        raise HTTPException(status_code=404, detail="MCP server configuration not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/skills", response_model=SkillCatalogResponse, tags=["skills"])
async def list_skills(request: Request) -> SkillCatalogResponse:
    """List unified Skill packages with detected formats and effective execution modes."""
    from hostpilot.skills.registry import SkillRegistry
    from hostpilot.skills.yaml_registry import YamlSkillRegistry

    app_settings: Settings = request.app.state.settings
    bundled_root = Path(__file__).resolve().parents[1] / "skill_packages"
    legacy_registry = SkillRegistry(
        app_settings.skills_dir,
        additional_roots=(bundled_root,),
    )
    legacy_catalog = legacy_registry.discover()
    yaml_registry = YamlSkillRegistry(
        legacy_registry.root,
        additional_roots=(bundled_root,),
        registered_tool_ids=tuple(
            descriptor.name for descriptor in request.app.state.tool_facade.descriptors()
        ),
    )
    yaml_catalog = yaml_registry.discover()
    markdown_by_path = {
        package.source_directory.resolve(): package for package in legacy_catalog.skills
    }
    yaml_by_path = {package.source_directory.resolve(): package for package in yaml_catalog.skills}
    invalid_directories = {issue.path.resolve() for issue in legacy_catalog.issues}
    invalid_directories.update(issue.path.resolve() for issue in yaml_catalog.issues)
    items: list[SkillCatalogItem] = []
    consumed_markdown: set[Path] = set()
    valid_directories: set[Path] = set()
    preferences: dict[str, SkillExecutionMode] = request.app.state.skill_execution_modes

    for directory, yaml_package in yaml_by_path.items():
        markdown_package = markdown_by_path.get(directory)
        formats: tuple[Literal["yaml", "skill_md"], ...] = (
            ("yaml", "skill_md") if markdown_package is not None else ("yaml",)
        )
        available_modes: tuple[SkillExecutionMode, ...] = (
            ("yaml", "markdown", "both") if markdown_package is not None else ("yaml",)
        )
        default_mode: SkillExecutionMode = "yaml"
        preferred = preferences.get(yaml_package.skill_id, default_mode)
        execution_mode = preferred if preferred in available_modes else default_mode
        enabled = yaml_package.manifest.meta.enabled
        if execution_mode == "markdown" and markdown_package is not None:
            enabled = markdown_package.enabled
        elif execution_mode == "both" and markdown_package is not None:
            enabled = enabled and markdown_package.enabled
        items.append(
            SkillCatalogItem(
                skill_id=yaml_package.skill_id,
                name=yaml_package.manifest.meta.name,
                description=yaml_package.manifest.intent_summary,
                execution_format="yaml",
                detected_formats=formats,
                execution_mode=execution_mode,
                available_execution_modes=available_modes,
                enabled=enabled,
                source=("bundled" if directory.is_relative_to(bundled_root.resolve()) else "user"),
            )
        )
        valid_directories.add(directory)
        if markdown_package is not None:
            consumed_markdown.add(directory)

    for directory, markdown_package in markdown_by_path.items():
        if directory in consumed_markdown:
            continue
        # A present but invalid YAML manifest blocks Markdown fallback by ADR-0013.
        manifest_path = directory / "skill.yaml"
        if manifest_path.exists() or manifest_path.is_symlink():
            invalid_directories.add(directory)
            continue
        skill_id = f"legacy/{markdown_package.name}"
        items.append(
            SkillCatalogItem(
                skill_id=skill_id,
                name=markdown_package.name,
                description=markdown_package.description,
                execution_format="skill_md",
                detected_formats=("skill_md",),
                execution_mode="markdown",
                available_execution_modes=("markdown",),
                enabled=markdown_package.enabled,
                source="user",
            )
        )
        valid_directories.add(directory)

    invalid_directories.difference_update(valid_directories)
    items.sort(key=lambda item: item.skill_id)
    return SkillCatalogResponse(
        skills=tuple(items),
        invalid_package_count=len(invalid_directories),
    )


@router.get("/skills/detail", response_model=SkillDetailResponse, tags=["skills"])
async def get_skill_detail(
    request: Request,
    skill_id: str = Query(min_length=1, max_length=128),
) -> SkillDetailResponse:
    """Return validated text files for a Skill selected by its catalog ID."""
    from hostpilot.skills.registry import SkillRegistry
    from hostpilot.skills.yaml_registry import YamlSkillRegistry

    app_settings: Settings = request.app.state.settings
    legacy_registry = SkillRegistry(app_settings.skills_dir)
    if skill_id.startswith("legacy/"):
        name = skill_id.removeprefix("legacy/")
        if not name or "/" in name:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found.")
        try:
            package = legacy_registry.load(name)
        except KeyError as error:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Skill not found.",
            ) from error
        files = [SkillFileResponse(relative_path="SKILL.md", content=package.source_content)]
        files.extend(
            SkillFileResponse(relative_path=path, content=content)
            for path, content in sorted(package.resources.items())
        )
        return SkillDetailResponse(skill_id=skill_id, files=tuple(files))

    bundled_root = Path(__file__).resolve().parents[1] / "skill_packages"
    yaml_registry = YamlSkillRegistry(
        legacy_registry.root,
        additional_roots=(bundled_root,),
        registered_tool_ids=tuple(
            descriptor.name for descriptor in request.app.state.tool_facade.descriptors()
        ),
    )
    try:
        yaml_package = yaml_registry.load(skill_id)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Skill not found.",
        ) from error

    import yaml

    manifest = yaml.safe_dump(
        yaml_package.manifest.model_dump(mode="json", exclude_none=True),
        allow_unicode=True,
        sort_keys=False,
    )
    files = [SkillFileResponse(relative_path="skill.yaml", content=manifest)]
    files.extend(
        SkillFileResponse(relative_path=path, content=content)
        for path, content in sorted(yaml_package.resources.items())
    )
    return SkillDetailResponse(skill_id=skill_id, files=tuple(files))


@router.post(
    "/skills/import",
    response_model=SkillCatalogItem,
    status_code=status.HTTP_201_CREATED,
    tags=["skills"],
)
async def import_skill(request: Request) -> SkillCatalogItem:
    """Validate and install one bounded ZIP Skill package into the user Skill root."""
    from hostpilot.skills.archive import (
        MAX_ARCHIVE_BYTES,
        SkillArchiveImportError,
        import_skill_archive,
    )
    from hostpilot.skills.registry import SkillRegistry

    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {
        "application/zip",
        "application/x-zip-compressed",
        "application/octet-stream",
    }:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="请上传 ZIP 格式的 Skill 包。",
        )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            if int(content_length) > MAX_ARCHIVE_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail="ZIP 文件不能超过 4 MiB。",
                )
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="无效的 Content-Length。",
            ) from error

    archive_bytes = bytearray()
    async for chunk in request.stream():
        if len(archive_bytes) + len(chunk) > MAX_ARCHIVE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="ZIP 文件不能超过 4 MiB。",
            )
        archive_bytes.extend(chunk)

    bundled_root = Path(__file__).resolve().parents[1] / "skill_packages"
    try:
        imported = import_skill_archive(
            bytes(archive_bytes),
            SkillRegistry(request.app.state.settings.skills_dir).root,
            bundled_root=bundled_root,
            registered_tool_ids=tuple(
                descriptor.name for descriptor in request.app.state.tool_facade.descriptors()
            ),
        )
    except SkillArchiveImportError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error

    catalog = await list_skills(request)
    return next(skill for skill in catalog.skills if skill.skill_id == imported.skill_id)


@router.patch("/skills/state", response_model=SkillCatalogItem, tags=["skills"])
async def update_skill_state(
    body: SkillEnabledRequest,
    request: Request,
) -> SkillCatalogItem:
    """Change the enabled state of one validated user Skill."""
    from hostpilot.skills.management import SkillManagementError, set_skill_enabled
    from hostpilot.skills.registry import SkillRegistry

    app_settings: Settings = request.app.state.settings
    root = SkillRegistry(app_settings.skills_dir).root
    tool_ids = tuple(descriptor.name for descriptor in request.app.state.tool_facade.descriptors())
    try:
        async with request.app.state.model_components_lock:
            skill = set_skill_enabled(
                root,
                body.skill_id,
                body.enabled,
                registered_tool_ids=tool_ids,
            )
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found."
        ) from error
    except SkillManagementError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    catalog = await list_skills(request)
    return next(item for item in catalog.skills if item.skill_id == skill.skill_id)


@router.patch("/skills/execution", response_model=SkillCatalogItem, tags=["skills"])
async def update_skill_execution(
    body: SkillExecutionRequest,
    request: Request,
) -> SkillCatalogItem:
    """Persist a compatible execution entry and apply it under the chat runtime lock."""
    catalog = await list_skills(request)
    skill = next((item for item in catalog.skills if item.skill_id == body.skill_id), None)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found or invalid.")
    if body.execution_mode not in skill.available_execution_modes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="所选执行方式与 Skill 包中已校验的文件格式不兼容。",
        )

    async with request.app.state.model_components_lock:
        await request.app.state.skill_execution_store.set(
            body.skill_id,
            body.execution_mode,
        )
        request.app.state.skill_execution_modes[body.skill_id] = body.execution_mode
        chat_service: ChatService | None = request.app.state.chat_service
        runtime = chat_service.runtime if chat_service is not None else None
        update_mode = getattr(runtime, "set_execution_mode", None)
        if callable(update_mode):
            update_mode(body.skill_id, body.execution_mode)

    updated_catalog = await list_skills(request)
    return next(item for item in updated_catalog.skills if item.skill_id == body.skill_id)


@router.delete("/skills", status_code=status.HTTP_204_NO_CONTENT, tags=["skills"])
async def remove_skill(
    request: Request,
    skill_id: str = Query(min_length=1, max_length=128),
) -> Response:
    """Delete one validated Skill from the user directory, never a bundled Skill."""
    from hostpilot.skills.management import SkillManagementError, delete_skill
    from hostpilot.skills.registry import SkillRegistry

    app_settings: Settings = request.app.state.settings
    root = SkillRegistry(app_settings.skills_dir).root
    tool_ids = tuple(descriptor.name for descriptor in request.app.state.tool_facade.descriptors())
    try:
        async with request.app.state.model_components_lock:
            delete_skill(root, skill_id, registered_tool_ids=tool_ids)
            await request.app.state.skill_execution_store.delete(skill_id)
            request.app.state.skill_execution_modes.pop(skill_id, None)
    except KeyError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Skill not found."
        ) from error
    except SkillManagementError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.websocket("/ws/status")
async def status_websocket(websocket: WebSocket) -> None:
    """Push initial backend health and answer bounded client heartbeats."""
    app_settings: Settings = websocket.scope["app"].state.settings
    if not _websocket_origin_allowed(websocket, app_settings.web_origins):
        await websocket.close(code=1008, reason="WebSocket origin is not allowed.")
        return

    await websocket.accept()
    await websocket.send_json(
        {"type": "status.snapshot", "data": {"api": "ok", "protocol_version": 1}}
    )
    try:
        while True:
            raw_message = await websocket.receive_text()
            if len(raw_message.encode("utf-8")) > 1024:
                await websocket.send_json({"type": "error", "data": {"code": "message_too_large"}})
                continue
            try:
                ping = StatusPing.model_validate_json(raw_message)
            except (ValidationError, ValueError, json.JSONDecodeError):
                await websocket.send_json({"type": "error", "data": {"code": "invalid_message"}})
                continue
            await websocket.send_json({"type": "pong", "request_id": ping.request_id})
    except WebSocketDisconnect:
        return


@router.websocket("/ws/chat")
async def chat_websocket(websocket: WebSocket) -> None:
    """Receive chat messages and return completed Agent turns over WebSocket."""
    app_settings: Settings = websocket.scope["app"].state.settings
    if not _websocket_origin_allowed(websocket, app_settings.web_origins):
        await websocket.close(code=1008, reason="WebSocket origin is not allowed.")
        return

    await websocket.accept()
    try:
        while True:
            raw_message = await websocket.receive_text()
            active_settings: Settings = websocket.scope["app"].state.settings
            if len(raw_message.encode("utf-8")) > active_settings.max_request_chars * 4 + 1024:
                await websocket.send_json(
                    {"type": "chat.error", "data": {"code": "message_too_large"}}
                )
                continue
            try:
                body = ChatSocketRequest.model_validate_json(raw_message)
            except (ValidationError, ValueError, json.JSONDecodeError):
                await websocket.send_json(
                    {"type": "chat.error", "data": {"code": "invalid_message"}}
                )
                continue

            chat_service: ChatService | None = websocket.scope["app"].state.chat_service
            if chat_service is None:
                await websocket.send_json(
                    {"type": "chat.error", "data": {"code": "runtime_unavailable"}}
                )
                continue
            try:
                async with websocket.scope["app"].state.model_components_lock:
                    conversation = await websocket.scope["app"].state.conversations.get(
                        body.session_id
                    )
                    if conversation is None:
                        raise ConversationNotFoundError(body.session_id)
                    _set_runtime_sandbox_mode(websocket.scope["app"], conversation.sandbox_mode)
                    try:
                        with sandbox_mode_scope(conversation.sandbox_mode):
                            result = await chat_service.send(
                                body.session_id,
                                body.message,
                                confirmation_provider=WebSocketConfirmationProvider(websocket),
                            )
                    finally:
                        _set_runtime_sandbox_mode(websocket.scope["app"], None)
            except RequestTooLargeError:
                code = "message_too_large"
            except (ConversationNotFoundError, ValueError):
                code = "conversation_not_found"
            except BudgetExceededError:
                code = "budget_exceeded"
            except ToolConfirmationDeniedError as error:
                code = f"tool_confirmation_{error.decision.value}"
            except ToolDeniedError as error:
                code = (
                    "sandbox_unavailable"
                    if "sandbox_tool_unavailable" in str(error)
                    or "execution_environment_unavailable" in str(error)
                    else "tool_denied"
                )
            except RemoteToolOutcomeUnknownError:
                code = "remote_tool_outcome_unknown"
            except ToolExecutionError:
                code = "tool_failed"
            except TimeoutError:
                code = "request_timeout"
            except (OutputValidationError, UnsupportedRouteError):
                code = "request_failed"
            else:
                await websocket.send_json(
                    {
                        "type": "chat.response",
                        "data": ChatResponse(
                            session_id=result.session_id,
                            request_id=result.request_id,
                            assistant_message=result.assistant_message,
                            route=result.route,
                            clarification_prompt=result.clarification_prompt,
                            token_usage=result.token_usage,
                        ).model_dump(mode="json"),
                    }
                )
                continue
            await websocket.send_json({"type": "chat.error", "data": {"code": code}})
    except WebSocketDisconnect:
        return


@router.post(
    "/conversations",
    response_model=ConversationSummary,
    status_code=status.HTTP_201_CREATED,
    tags=["conversations"],
)
async def create_conversation(
    body: CreateConversationRequest,
    request: Request,
) -> ConversationSummary:
    """Create an isolated conversation ID for the local user."""
    store: ConversationStore = request.app.state.conversations
    return await store.create(body.title)


@router.get(
    "/conversations",
    response_model=tuple[ConversationSummary, ...],
    tags=["conversations"],
)
async def list_conversations(request: Request) -> tuple[ConversationSummary, ...]:
    """List local conversation metadata without loading graph state."""
    store: ConversationStore = request.app.state.conversations
    return await store.list()


@router.get(
    "/conversations/{session_id}",
    response_model=ConversationSummary,
    tags=["conversations"],
)
async def get_conversation(
    session_id: str,
    request: Request,
) -> ConversationSummary:
    """Return metadata for one conversation."""
    store: ConversationStore = request.app.state.conversations
    try:
        conversation = await store.get(session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return conversation


@router.post(
    "/conversations/{session_id}/sandbox/upgrade",
    response_model=ConversationSummary,
    tags=["conversations"],
)
async def upgrade_conversation_sandbox(
    session_id: str,
    body: SandboxUpgradeRequest,
    request: Request,
) -> ConversationSummary:
    """Persist one user-approved privilege increase and audit the transition."""
    store: ConversationStore = request.app.state.conversations
    async with request.app.state.model_components_lock:
        try:
            current = await store.get(session_id)
        except ValueError as error:
            raise HTTPException(status_code=404, detail="Conversation not found.") from error
        if current is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        if body.target_mode.rank <= current.sandbox_mode.rank:
            request.app.state.audit_sink.record(
                AuditEvent(
                    action="sandbox.upgrade",
                    outcome=AuditOutcome.DENIED,
                    resource_type="conversation",
                    resource_id=current.session_id,
                    reason_code="mode_must_increase",
                )
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="会话沙箱级别只能向上升级，不能降级或重复升级。",
            )
        try:
            upgraded = await store.upgrade_sandbox_mode(current.session_id, body.target_mode)
        except ConversationNotFoundError as error:
            raise HTTPException(status_code=404, detail="Conversation not found.") from error
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="会话沙箱级别只能向上升级，不能降级或重复升级。",
            ) from error
        request.app.state.audit_sink.record(
            AuditEvent(
                action="sandbox.upgrade",
                outcome=AuditOutcome.ALLOWED,
                resource_type="conversation",
                resource_id=upgraded.session_id,
                reason_code=(
                    f"approved_{current.sandbox_mode.value.replace('-', '_')}_to_"
                    f"{upgraded.sandbox_mode.value.replace('-', '_')}"
                ),
            )
        )
        return upgraded


@router.delete(
    "/conversations/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["conversations"],
)
async def delete_conversation(session_id: str, request: Request) -> Response:
    """Delete a conversation, its graph checkpoints, and its session summary."""
    store: ConversationStore = request.app.state.conversations
    memory: MemoryStore = request.app.state.memory
    checkpointer = request.app.state.checkpointer
    try:
        conversation = await store.get(session_id)
    except ValueError as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    if conversation is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")

    await checkpointer.adelete_thread(conversation.session_id)
    await store.delete(conversation.session_id)
    memory.delete_session_summary(conversation.session_id)
    await request.app.state.memory_review.remove_for_conversation(conversation.session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/memory-proposals",
    response_model=tuple[MemoryWriteProposal, ...],
    tags=["memory"],
)
async def list_memory_proposals(request: Request) -> tuple[MemoryWriteProposal, ...]:
    """Return memory changes awaiting explicit user approval."""
    review: MemoryProposalReview = request.app.state.memory_review
    return await review.list_pending()


@router.post(
    "/memory-proposals",
    response_model=MemoryWriteProposal,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["memory"],
)
async def submit_memory_proposal(
    proposal: MemoryWriteProposal,
    request: Request,
    response: Response,
) -> MemoryWriteProposal:
    """Queue a memory proposal without changing any Markdown file."""
    review: MemoryProposalReview = request.app.state.memory_review
    if (
        proposal.scope is MemoryScope.SESSION_SUMMARY
        and proposal.session_id is not None
        and await request.app.state.conversations.get(proposal.session_id) is None
    ):
        raise HTTPException(status_code=404, detail="Conversation not found.")
    try:
        scored_proposal = await review.submit(proposal)
        if scored_proposal.discarded:
            response.status_code = status.HTTP_200_OK
        return scored_proposal
    except DuplicatePendingProposal as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except MemoryWriteDenied as error:
        raise HTTPException(status_code=413, detail=str(error)) from error


@router.post(
    "/memory-proposals/{proposal_id}/approve",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["memory"],
)
async def approve_memory_proposal(proposal_id: str, request: Request) -> Response:
    """Apply a queued memory change after this explicit approval request."""
    review: MemoryProposalReview = request.app.state.memory_review
    try:
        await review.approve(proposal_id)
    except PendingProposalNotFound as error:
        raise HTTPException(status_code=404, detail="Memory proposal not found.") from error
    except MemoryWriteDenied as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/memory-proposals/{proposal_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["memory"],
)
async def reject_memory_proposal(proposal_id: str, request: Request) -> Response:
    """Discard a pending memory proposal without writing it."""
    review: MemoryProposalReview = request.app.state.memory_review
    try:
        await review.reject(proposal_id)
    except PendingProposalNotFound as error:
        raise HTTPException(status_code=404, detail="Memory proposal not found.") from error
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/conversations/{session_id}/messages",
    response_model=ChatResponse,
    tags=["chat"],
)
async def send_message(
    session_id: str,
    body: ChatRequest,
    request: Request,
) -> ChatResponse:
    """Submit one message to the configured Agent runtime."""
    chat_service: ChatService | None = request.app.state.chat_service
    if chat_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent runtime is not configured; select a model provider first.",
        )
    try:
        async with request.app.state.model_components_lock:
            conversation = await request.app.state.conversations.get(session_id)
            if conversation is None:
                raise ConversationNotFoundError(session_id)
            _set_runtime_sandbox_mode(request.app, conversation.sandbox_mode)
            try:
                with sandbox_mode_scope(conversation.sandbox_mode):
                    result = await chat_service.send(session_id, body.message)
            finally:
                _set_runtime_sandbox_mode(request.app, None)
    except RequestTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except OutputValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent response failed output validation.",
        ) from error
    except (ConversationNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    except BudgetExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Request budget exhausted for {error.resource}.",
        ) from error
    except ToolConfirmationDeniedError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Tool operation was not approved ({error.decision.value}).",
        ) from error
    except ToolDeniedError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Sandbox runtime is unavailable. Check the Tools page for the missing Linux "
                "dependency and follow the Linux installation guide."
                if "sandbox_tool_unavailable" in str(error)
                or "execution_environment_unavailable" in str(error)
                else "Agent tool call was denied by policy or request budget."
            ),
        ) from error
    except RemoteToolOutcomeUnknownError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                "Remote MCP call timed out and its outcome is unknown. "
                "Check the target system before retrying."
            ),
        ) from error
    except ToolExecutionError as error:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent tool execution failed verification or execution.",
        ) from error
    except TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Request exceeded its configured time budget.",
        ) from error
    except UnsupportedRouteError as error:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=f"Route '{error}' is not available yet.",
        ) from error
    return ChatResponse(
        session_id=result.session_id,
        request_id=result.request_id,
        assistant_message=result.assistant_message,
        route=result.route,
        clarification_prompt=result.clarification_prompt,
        token_usage=result.token_usage,
    )


@router.post(
    "/conversations/{session_id}/routing-evaluation",
    response_model=RoutingEvaluationResponse,
    tags=["chat", "evaluation"],
)
async def evaluate_conversation_route(
    session_id: str,
    body: RoutingEvaluationRequest,
    request: Request,
) -> RoutingEvaluationResponse:
    """Evaluate active Skill routing without executing Skills, tools, or chat output."""
    chat_service: ChatService | None = request.app.state.chat_service
    if chat_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent runtime is not configured; select a model provider first.",
        )
    try:
        async with request.app.state.model_components_lock:
            conversation = await request.app.state.conversations.get(session_id)
            if conversation is None:
                raise ConversationNotFoundError(session_id)
            evaluation_context = body.evaluation_context
            result = await chat_service.select_route_only(
                session_id,
                body.message,
                evaluation_messages=(
                    tuple(
                        ConversationMessage(role=MessageRole(item.role), content=item.content)
                        for item in evaluation_context.messages
                    )
                    if evaluation_context is not None
                    else None
                ),
                evaluation_session_summary=(
                    evaluation_context.session_summary if evaluation_context is not None else None
                ),
                evaluation_long_term_memory=(
                    evaluation_context.long_term_memory if evaluation_context is not None else None
                ),
            )
    except RequestTooLargeError as error:
        raise HTTPException(status_code=413, detail=str(error)) from error
    except (ConversationNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    except BudgetExceededError as error:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Request budget exhausted for {error.resource}.",
        ) from error
    except TimeoutError as error:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Routing evaluation exceeded its configured time budget.",
        ) from error
    except UnsupportedRouteError as error:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail=str(error),
        ) from error
    return RoutingEvaluationResponse(
        request_id=result.request_id,
        route=result.route,
        skill_id=result.skill_id,
        reason_code=result.reason_code,
        token_usage=result.token_usage,
    )


@router.get(
    "/conversations/{session_id}/messages",
    response_model=ConversationHistoryResponse,
    tags=["chat"],
)
async def get_messages(
    session_id: str,
    request: Request,
) -> ConversationHistoryResponse:
    """Return persisted messages for one conversation."""
    chat_service: ChatService | None = request.app.state.chat_service
    if chat_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Agent runtime is not configured; select a model provider first.",
        )
    try:
        async with request.app.state.model_components_lock:
            conversation, messages = await chat_service.history(session_id)
    except (ConversationNotFoundError, ValueError) as error:
        raise HTTPException(status_code=404, detail="Conversation not found.") from error
    return ConversationHistoryResponse(conversation=conversation, messages=messages)


def create_app(
    settings: Settings | None = None,
    *,
    chat_agent_runtime_factory: Callable[[Any], ChatAgentRuntime] | None = None,
    agent_router: Router | None = None,
    workspace_picker: Callable[[Path], Path | None] | None = None,
    plan_component_registry: PlanComponentRegistry | None = None,
    chat_agent_runtime_registry: ChatAgentRuntimeRegistry | None = None,
    model_provider_registry: ModelProviderRegistry | None = None,
    conversation_store_factory: ConversationStoreFactory | None = None,
    checkpointer_factory: CheckpointerFactory | None = None,
    model_configuration_store_factory: ModelConfigurationStoreFactory | None = None,
    routing_model_configuration_store_factory: RoutingModelConfigurationStoreFactory | None = None,
    mcp_configuration_store_factory: MCPConfigurationStoreFactory | None = None,
    harness_configuration_store_factory: HarnessConfigurationStoreFactory | None = None,
    workspace_configuration_store_factory: WorkspaceConfigurationStoreFactory | None = None,
    skill_execution_store_factory: SkillExecutionStoreFactory | None = None,
    audit_store_factory: AuditStoreFactory | None = None,
    trace_store_factory: TraceStoreFactory | None = None,
    cancellation_store_factory: CancellationStoreFactory | None = None,
    memory_store_factory: MemoryStoreFactory | None = None,
    tool_executor_factory: ToolExecutorFactory | None = None,
    sandbox_backend_factory: SandboxBackendFactory | None = None,
) -> FastAPI:
    """Build the local API and open persistent stores during application lifespan."""
    app_settings = settings or load_settings()
    database_path = app_settings.data_dir / "hostpilot.sqlite3"
    conversation_store_builder = conversation_store_factory or SQLiteConversationStore

    @asynccontextmanager
    async def lifespan_body(application: FastAPI) -> AsyncIterator[None]:
        nonlocal app_settings
        runtime_host: RuntimeHost = application.state.runtime_host
        runtime_context = runtime_host.context
        mcp_provider_plugin: MCPProviderHostPlugin = application.state.mcp_provider_plugin
        tool_governance_plugin: ToolGovernanceHostPlugin = application.state.tool_governance_plugin
        chat_agent_runtime_plugin: ChatAgentRuntimeHostPlugin = (
            application.state.chat_agent_runtime_plugin
        )
        routing_models_plugin: RoutingModelsHostPlugin = application.state.routing_models_plugin
        services = runtime_context.services
        tool_executor_factory = cast(
            ToolExecutorFactory,
            services.resolve(ToolGovernanceHostPlugin.governance_service_id),
        )
        sandbox_backends = cast(
            SandboxBackendSet,
            services.resolve(SandboxBackendsHostPlugin.service_id),
        )
        trace_store = cast(
            TraceStore,
            services.resolve(ApplicationStoresHostPlugin.trace_store_service_id),
        )
        audit_store = cast(
            AuditStore,
            services.resolve(ApplicationStoresHostPlugin.audit_store_service_id),
        )
        cancellation_store = cast(
            CancellationStore,
            services.resolve(ApplicationStoresHostPlugin.cancellation_store_service_id),
        )
        memory_store = cast(
            MemoryStore,
            services.resolve(ApplicationStoresHostPlugin.memory_store_service_id),
        )
        model_configuration_store = cast(
            ModelConfigurationStore,
            services.resolve(ConfigurationStoresHostPlugin.model_configuration_service_id),
        )
        saved_model_configuration: ModelConfiguration | None = await model_configuration_store.get()
        routing_model_configuration_store = cast(
            RoutingModelConfigurationStore,
            services.resolve(ConfigurationStoresHostPlugin.routing_model_configuration_service_id),
        )
        saved_routing_model_configuration: (
            RoutingModelConfiguration | None
        ) = await routing_model_configuration_store.get()
        routing_model_components: Any | None = None
        routing_model_api_keys: dict[str, str] = {}
        if saved_routing_model_configuration is not None:
            for role, api_key in (
                ("embedding", saved_routing_model_configuration["embedding_api_key"]),
                ("reranker", saved_routing_model_configuration["reranker_api_key"]),
            ):
                if api_key:
                    routing_model_api_keys[role] = api_key
        if saved_routing_model_configuration is not None and routing_model_api_keys:
            from hostpilot.routing.models import create_routing_model_components

            saved_embedding_model = saved_routing_model_configuration["embedding_model"]
            saved_reranker_model = saved_routing_model_configuration["reranker_model"]
            embedding_model = (
                saved_embedding_model if routing_model_api_keys.get("embedding") else None
            )
            reranker_model = (
                saved_reranker_model if routing_model_api_keys.get("reranker") else None
            )
            if embedding_model or reranker_model:
                routing_model_components = create_routing_model_components(
                    app_settings,
                    judge_model=None,
                    judge_api_key=None,
                    judge_base_url=saved_routing_model_configuration["judge_base_url"],
                    embedding_model=embedding_model,
                    embedding_api_key=routing_model_api_keys.get("embedding"),
                    embedding_base_url=saved_routing_model_configuration["embedding_base_url"],
                    reranker_model=reranker_model,
                    reranker_api_key=routing_model_api_keys.get("reranker"),
                    reranker_base_url=saved_routing_model_configuration["reranker_base_url"],
                )
                await routing_models_plugin.replace(routing_model_components)
        mcp_configuration_store = cast(
            MCPConfigurationStore,
            services.resolve(ConfigurationStoresHostPlugin.mcp_configuration_service_id),
        )
        harness_configuration_store = cast(
            HarnessConfigurationStore,
            services.resolve(ConfigurationStoresHostPlugin.harness_configuration_service_id),
        )
        workspace_configuration_store = cast(
            WorkspaceConfigurationStore,
            services.resolve(ConfigurationStoresHostPlugin.workspace_configuration_service_id),
        )
        saved_harness_configuration = await harness_configuration_store.get()
        harness_configuration = saved_harness_configuration or (
            HarnessConfiguration.model_validate(
                {field: getattr(app_settings, field) for field in HarnessConfiguration.model_fields}
            )
        )
        app_settings = app_settings.model_copy(update=harness_configuration.model_dump())
        saved_workspace_path = await workspace_configuration_store.get()
        workspace_message: str | None = None
        try:
            workspace_access = await asyncio.to_thread(
                inspect_workspace,
                saved_workspace_path or Path.cwd(),
            )
        except WorkspaceAccessError:
            workspace_access = await asyncio.to_thread(inspect_workspace, Path.cwd())
            workspace_message = "保存的工作区当前无法读取，暂时使用后端启动目录；请重新选择工作区。"
        workspace_path = workspace_access.path
        workspace_read_only = workspace_access.read_only
        skill_execution_store = cast(
            SkillExecutionStore,
            services.resolve(ConfigurationStoresHostPlugin.skill_execution_service_id),
        )
        saved_skill_execution_modes = await skill_execution_store.list()
        saved_mcp_configurations = await mcp_configuration_store.list()
        mcp_api_keys: dict[str, SecretStr] = {}
        mcp_configuration_errors: dict[str, str] = {}
        sanitized_mcp_configurations: list[MCPServerConfiguration] = []
        for saved in saved_mcp_configurations:
            if contains_credential_argument(saved["args"]):
                sanitized: MCPServerConfiguration = {
                    **saved,
                    "args": (),
                    "enabled": False,
                }
                await mcp_configuration_store.save(sanitized)
                saved = sanitized
                mcp_configuration_errors[saved["name"]] = "CredentialArgumentsRemoved"
            sanitized_mcp_configurations.append(saved)
        saved_mcp_configurations = tuple(sanitized_mcp_configurations)
        runtime_mcp_servers: dict[str, tuple[Any, ...]] = {"servers": app_settings.mcp_servers}
        application.state.skill_execution_modes = saved_skill_execution_modes
        application.state.skill_execution_store = skill_execution_store
        application.state.harness_configuration = harness_configuration
        application.state.harness_configuration_store = harness_configuration_store
        application.state.traces = trace_store
        application.state.audits = audit_store
        audit_sink = CompositeAuditSink(StructuredAuditSink(), audit_store)
        application.state.audit_sink = audit_sink
        application.state.cancellations = cancellation_store
        application.state.memory = memory_store
        from hostpilot.tools.mcp import build_retrieval_mcp_provider

        environment_mcp_servers = app_settings.mcp_servers
        environment_server_names = {server.name for server in environment_mcp_servers}
        saved_names = {server["name"] for server in saved_mcp_configurations}
        if environment_server_names & saved_names:
            raise RuntimeError("Persisted MCP server names cannot shadow environment settings.")

        def effective_mcp_settings() -> Settings:
            persistent_servers: list[RemoteMCPServerSettings] = []
            for saved in saved_mcp_configurations:
                if not saved["enabled"]:
                    continue
                headers: dict[str, SecretStr] = {}
                process_env: dict[str, SecretStr] = {}
                credential_name = (
                    saved["api_key_header"]
                    if saved["transport"] == "streamable_http"
                    else saved["api_key_env"]
                )
                if credential_name is not None:
                    api_key = mcp_api_keys.get(saved["name"])
                    if api_key is None:
                        continue
                    if saved["transport"] == "streamable_http":
                        headers[credential_name] = api_key
                    else:
                        process_env[credential_name] = api_key
                persistent_servers.append(
                    RemoteMCPServerSettings(
                        name=saved["name"],
                        transport=saved["transport"],
                        url=saved["url"],
                        command=saved["command"],
                        args=saved["args"],
                        cwd=Path(saved["cwd"]) if saved["cwd"] else None,
                        timeout_seconds=saved["timeout_seconds"],
                        headers=headers,
                        env=process_env,
                    )
                )
            servers = environment_mcp_servers + tuple(persistent_servers)
            runtime_mcp_servers["servers"] = servers
            return app_settings.model_copy(update={"mcp_servers": servers})

        startup_settings = effective_mcp_settings()
        application.state.settings = startup_settings
        workspace_tools = WorkspaceFiles(workspace_path, read_only=workspace_read_only)
        mcp_provider: Any = build_retrieval_mcp_provider(
            startup_settings,
            workspace_root=workspace_path,
            workspace_read_only=workspace_read_only,
            workspace_instance=workspace_tools,
            trace_sink=trace_store,
            audit_sink=audit_sink,
            executor_factory=tool_executor_factory,
            sandbox_backends=sandbox_backends,
        )
        application.state.workspace_files = workspace_tools
        application.state.mcp_provider = mcp_provider
        await mcp_provider_plugin.replace(mcp_provider)
        tool_governance_plugin.bind(mcp_provider)
        tool_facade = cast(
            ToolFacade,
            runtime_context.services.resolve(ToolGovernanceHostPlugin.service_id),
        )
        application.state.tool_facade = tool_facade
        application.state.mcp_connection_status = None
        runtime: ChatAgentRuntime | None = None
        chat_service: ChatService | None = None
        runtime_lock = asyncio.Lock()
        application.state.model_components_lock = runtime_lock

        def make_workspace_status(*, selected: bool = True) -> WorkspaceStatusResponse:
            return WorkspaceStatusResponse(
                path=str(workspace_tools.root),
                read_only=workspace_tools.read_only,
                selected=selected,
                message=workspace_message,
            )

        application.state.workspace_status = make_workspace_status()

        async def select_workspace_directory() -> WorkspaceStatusResponse:
            nonlocal workspace_path, workspace_read_only, workspace_message
            picker = workspace_picker or choose_folder_native
            selected_path = await asyncio.to_thread(picker, workspace_tools.root)
            if selected_path is None:
                return make_workspace_status(selected=False)
            access = await asyncio.to_thread(inspect_workspace, selected_path)
            async with runtime_lock:
                await workspace_configuration_store.save(str(access.path))
                workspace_tools.set_workspace(access.path, read_only=access.read_only)
                workspace_path = access.path
                workspace_read_only = access.read_only
                workspace_message = None
                application.state.workspace_status = make_workspace_status()
            return make_workspace_status()

        application.state.select_workspace_directory = select_workspace_directory

        def make_chat_service(
            chat_agent_runtime: ChatAgentRuntime,
            tool_provider: Any | None,
        ) -> ChatService:
            return ChatService(
                hosted_conversation_store(),
                ContextBuilder(
                    hosted_conversation_store(),
                    memory_controller,
                    chat_agent_runtime,
                    max_messages=app_settings.max_context_messages,
                    max_memory_chars=app_settings.max_memory_context_chars,
                ),
                chat_agent_runtime,
                budget_limits=BudgetLimits(
                    total_tokens=app_settings.request_token_budget,
                    tool_calls=app_settings.request_tool_call_budget,
                    retrieval_calls=app_settings.request_retrieval_call_budget,
                    timeout_seconds=app_settings.request_timeout_seconds,
                ),
                max_request_chars=app_settings.max_request_chars,
                router=agent_router or CommandRouter(),
                tool_provider=tool_provider,
                output_processor=OutputProcessor(max_chars=app_settings.max_response_chars),
                trace_sink=trace_store,
                turn_lifecycle=runtime_context.turns,
            )

        def hosted_chat_agent_runtime() -> ChatAgentRuntime:
            return cast(
                ChatAgentRuntime,
                runtime_context.services.resolve(ChatAgentRuntimeHostPlugin.service_id),
            )

        def hosted_tool_facade() -> ToolFacade:
            return cast(
                ToolFacade,
                runtime_context.services.resolve(ToolGovernanceHostPlugin.service_id),
            )

        def hosted_conversation_store() -> ConversationStore:
            return cast(
                ConversationStore,
                runtime_context.services.resolve(
                    StateServicesHostPlugin.conversation_store_service_id
                ),
            )

        def hosted_checkpointer() -> Any:
            return runtime_context.services.resolve(StateServicesHostPlugin.checkpointer_service_id)

        def mcp_configuration_response() -> MCPServerConfigurationsResponse:
            statuses = {
                connection.connection_id: connection for connection in tool_facade.connections()
            }
            configured: list[MCPServerConfigurationResponse] = []
            for server in environment_mcp_servers:
                status_item = statuses.get(server.name)
                header_name = next(iter(server.headers), None)
                env_name = next(iter(server.env), None)
                configured.append(
                    MCPServerConfigurationResponse(
                        name=server.name,
                        transport=server.transport,
                        url=server.url,
                        command=server.command,
                        args=server.args,
                        cwd=str(server.cwd) if server.cwd else None,
                        timeout_seconds=server.timeout_seconds,
                        enabled=True,
                        description=(
                            "由本机环境变量管理的 MCP Server；连接用途由本机环境配置维护。"
                        ),
                        api_key_header=header_name,
                        api_key_env=env_name,
                        api_key_configured=bool(server.headers or server.env),
                        managed=False,
                        connection_state=(
                            status_item.state.value if status_item is not None else "disconnected"
                        ),
                        error_code=status_item.error_code if status_item else None,
                    )
                )
            for saved in saved_mcp_configurations:
                status_item = statuses.get(saved["name"])
                credential_name = (
                    saved["api_key_header"]
                    if saved["transport"] == "streamable_http"
                    else saved["api_key_env"]
                )
                if not saved["enabled"]:
                    state = "disabled"
                elif credential_name and saved["name"] not in mcp_api_keys:
                    state = "waiting_for_credentials"
                elif saved["name"] in mcp_configuration_errors:
                    state = "failed"
                else:
                    state = status_item.state.value if status_item is not None else "disconnected"
                configured.append(
                    MCPServerConfigurationResponse(
                        name=saved["name"],
                        transport=saved["transport"],
                        url=saved["url"],
                        command=saved["command"],
                        args=saved["args"],
                        cwd=saved["cwd"],
                        timeout_seconds=saved["timeout_seconds"],
                        enabled=saved["enabled"],
                        description=saved["description"],
                        api_key_header=saved["api_key_header"],
                        api_key_env=saved["api_key_env"],
                        api_key_configured=(
                            saved["name"] in mcp_api_keys or credential_name is None
                        ),
                        managed=True,
                        connection_state=state,
                        error_code=mcp_configuration_errors.get(saved["name"])
                        or (status_item.error_code if status_item else None),
                    )
                )
            return MCPServerConfigurationsResponse(servers=tuple(configured))

        async def refresh_mcp_runtime() -> None:
            nonlocal mcp_provider, tool_facade, runtime, chat_service, workspace_tools
            candidate_settings = effective_mcp_settings()
            candidate_workspace = WorkspaceFiles(
                workspace_path,
                read_only=workspace_read_only,
            )
            candidate_provider = build_retrieval_mcp_provider(
                candidate_settings,
                workspace_root=workspace_path,
                workspace_read_only=workspace_read_only,
                workspace_instance=candidate_workspace,
                trace_sink=trace_store,
                audit_sink=audit_sink,
                executor_factory=tool_executor_factory,
                sandbox_backends=sandbox_backends,
            )
            candidate_facade = ToolFacade(candidate_provider)
            candidate_chat_agent_runtime: ChatAgentRuntime | None = None
            try:
                candidate_tools: list[Any] = []
                should_start = bool(
                    candidate_settings.model_name and candidate_settings.model_api_key
                )
                should_start = should_start or bool(candidate_settings.mcp_servers)
                if should_start:
                    candidate_tools = await candidate_provider.start()
                if candidate_settings.model_name and candidate_settings.model_api_key:
                    if chat_agent_runtime_factory is not None:
                        raise RuntimeError(
                            "A custom runtime factory cannot be rebuilt dynamically."
                        )
                    candidate_chat_agent_runtime = create_configured_chat_agent_runtime(
                        candidate_settings,
                        checkpointer=hosted_checkpointer(),
                        tools=candidate_tools,
                        tool_provider=candidate_facade,
                        memory_controller=memory_controller,
                        cancellation_sink=cancellation_store,
                        skill_execution_modes=saved_skill_execution_modes,
                        trace_sink=trace_store,
                        plan_component_registry=plan_component_registry,
                        chat_agent_runtime_registry=chat_agent_runtime_registry,
                        model_provider_registry=model_provider_registry,
                    )
            except Exception:
                application.state.mcp_connection_status = candidate_facade.connections()
                for connection in candidate_facade.connections():
                    if connection.state.value == "failed" and connection.error_code:
                        mcp_configuration_errors[connection.connection_id] = connection.error_code
                await candidate_provider.aclose()
                return

            async with runtime_lock:
                await chat_agent_runtime_plugin.replace(candidate_chat_agent_runtime)
                await mcp_provider_plugin.replace(candidate_provider)
                tool_governance_plugin.bind(candidate_provider)
                mcp_provider = candidate_provider
                runtime = candidate_chat_agent_runtime
                workspace_tools = candidate_workspace
                application.state.workspace_files = workspace_tools
                application.state.mcp_provider = candidate_provider
                application.state.tool_facade = tool_facade
                application.state.mcp_connection_status = None
                application.state.settings = candidate_settings
                mcp_configuration_errors.clear()
                if candidate_chat_agent_runtime is None:
                    chat_service = None
                elif chat_service is None:
                    chat_service = make_chat_service(
                        hosted_chat_agent_runtime(), hosted_tool_facade()
                    )
                else:
                    chat_service.tool_provider = hosted_tool_facade()
                application.state.chat_service = chat_service

        async def list_mcp_server_configurations() -> MCPServerConfigurationsResponse:
            return mcp_configuration_response()

        async def save_mcp_server_configuration(
            configuration: MCPServerConfigurationRequest,
        ) -> MCPServerConfigurationsResponse:
            nonlocal saved_mcp_configurations
            if chat_agent_runtime_factory is not None:
                raise RuntimeError("MCP configuration is managed by the custom runtime factory.")
            if configuration.name in environment_server_names:
                raise ValueError("Environment-managed MCP servers cannot be changed here.")
            RemoteMCPServerSettings(
                name=configuration.name,
                transport=configuration.transport,
                url=configuration.url,
                command=configuration.command,
                args=tuple(configuration.args),
                cwd=Path(configuration.cwd) if configuration.cwd else None,
                timeout_seconds=configuration.timeout_seconds,
            )
            existing = next(
                (item for item in saved_mcp_configurations if item["name"] == configuration.name),
                None,
            )
            previous_credential_name = (
                existing["api_key_header"]
                if existing is not None and existing["transport"] == "streamable_http"
                else existing["api_key_env"]
                if existing is not None
                else None
            )
            current_credential_name = (
                configuration.api_key_header
                if configuration.transport == "streamable_http"
                else configuration.api_key_env
            )
            if (
                existing is not None and configuration.transport != existing["transport"]
            ) or current_credential_name != previous_credential_name:
                mcp_api_keys.pop(configuration.name, None)
            if configuration.api_key is not None:
                mcp_api_keys[configuration.name] = configuration.api_key
            record: MCPServerConfiguration = {
                "name": configuration.name,
                "transport": configuration.transport,
                "url": configuration.url,
                "command": configuration.command,
                "args": tuple(configuration.args),
                "cwd": configuration.cwd,
                "timeout_seconds": configuration.timeout_seconds,
                "enabled": configuration.enabled,
                "description": configuration.description,
                "api_key_header": configuration.api_key_header,
                "api_key_env": configuration.api_key_env,
            }
            await mcp_configuration_store.save(record)
            saved_mcp_configurations = tuple(
                item for item in saved_mcp_configurations if item["name"] != record["name"]
            ) + (record,)
            mcp_configuration_errors.pop(configuration.name, None)
            await refresh_mcp_runtime()
            return mcp_configuration_response()

        async def delete_mcp_server_configuration(name: str) -> bool:
            nonlocal saved_mcp_configurations
            if name in environment_server_names:
                raise ValueError("Environment-managed MCP servers cannot be deleted.")
            deleted = await mcp_configuration_store.delete(name)
            if deleted:
                saved_mcp_configurations = tuple(
                    item for item in saved_mcp_configurations if item["name"] != name
                )
                mcp_api_keys.pop(name, None)
                mcp_configuration_errors.pop(name, None)
                await refresh_mcp_runtime()
            return deleted

        application.state.list_mcp_servers = list_mcp_server_configurations
        application.state.save_mcp_server = save_mcp_server_configuration
        application.state.delete_mcp_server = delete_mcp_server_configuration

        async def configure_harness_runtime(
            configuration: HarnessConfiguration,
        ) -> HarnessConfiguration:
            nonlocal app_settings
            candidate_settings = app_settings.model_copy(update=configuration.model_dump())
            async with runtime_lock:
                await harness_configuration_store.save(configuration)
                app_settings = candidate_settings
                application.state.harness_configuration = configuration
                application.state.settings = effective_mcp_settings()
                memory_controller.max_recall_chars = configuration.max_memory_context_chars
                if chat_service is not None:
                    chat_service.budget_limits = BudgetLimits(
                        total_tokens=configuration.request_token_budget,
                        tool_calls=configuration.request_tool_call_budget,
                        retrieval_calls=configuration.request_retrieval_call_budget,
                        timeout_seconds=configuration.request_timeout_seconds,
                    )
                    chat_service.max_request_chars = configuration.max_request_chars
                    chat_service.context_builder.max_messages = configuration.max_context_messages
                    chat_service.context_builder.max_memory_chars = (
                        configuration.max_memory_context_chars
                    )
                    chat_service.output_processor.max_chars = configuration.max_response_chars
                if runtime is not None:
                    configure_runtime = getattr(runtime, "configure_harness", None)
                    if callable(configure_runtime):
                        configure_runtime(configuration.model_dump())
            return configuration

        application.state.configure_harness_configuration = configure_harness_runtime

        def model_configuration_status() -> ModelConfigurationStatus:
            has_saved_configuration = saved_model_configuration is not None or bool(
                app_settings.model_name
            )
            return ModelConfigurationStatus(
                configured=runtime is not None,
                saved=has_saved_configuration,
                api_key_required=has_saved_configuration and runtime is None,
                model_name=app_settings.model_name
                or (
                    saved_model_configuration.get("model_name")
                    if saved_model_configuration is not None
                    else None
                ),
                base_url=app_settings.model_base_url
                if app_settings.model_name
                else (
                    saved_model_configuration.get("base_url")
                    if saved_model_configuration is not None
                    else None
                ),
                streaming=app_settings.model_streaming
                if app_settings.model_name
                else (
                    saved_model_configuration["streaming"]
                    if saved_model_configuration is not None
                    else True
                ),
            )

        def routing_model_configuration_status() -> RoutingModelConfigurationStatus:
            saved = saved_routing_model_configuration
            has_optional_roles = bool(
                saved
                and (saved["judge_model"] or saved["embedding_model"] or saved["reranker_model"])
            )
            return RoutingModelConfigurationStatus(
                configured=routing_model_components is not None,
                saved=has_optional_roles,
                judge_api_key_configured=bool(routing_model_api_keys.get("judge")),
                embedding_api_key_configured=bool(routing_model_api_keys.get("embedding")),
                reranker_api_key_configured=bool(routing_model_api_keys.get("reranker")),
                judge_model=saved["judge_model"] if saved else None,
                embedding_model=saved["embedding_model"] if saved else None,
                reranker_model=saved["reranker_model"] if saved else None,
                judge_base_url=saved["judge_base_url"] if saved else None,
                embedding_base_url=saved["embedding_base_url"] if saved else None,
                reranker_base_url=saved["reranker_base_url"] if saved else None,
            )

        async def configure_routing_model_components(
            configuration: RoutingModelConfigurationRequest,
        ) -> RoutingModelConfigurationStatus:
            nonlocal routing_model_components
            nonlocal saved_routing_model_configuration
            nonlocal routing_model_api_keys
            stored = saved_routing_model_configuration or {
                "judge_base_url": None,
                "embedding_base_url": None,
                "reranker_base_url": None,
                "judge_model": None,
                "embedding_model": None,
                "reranker_model": None,
            }
            fields_set = configuration.model_fields_set
            judge_model = (
                configuration.judge_model if "judge_model" in fields_set else stored["judge_model"]
            )
            embedding_model = (
                configuration.embedding_model
                if "embedding_model" in fields_set
                else stored["embedding_model"]
            )
            reranker_model = (
                configuration.reranker_model
                if "reranker_model" in fields_set
                else stored["reranker_model"]
            )
            judge_base_url = (
                configuration.judge_base_url
                if "judge_base_url" in fields_set
                else stored["judge_base_url"]
            )
            embedding_base_url = (
                configuration.embedding_base_url
                if "embedding_base_url" in fields_set
                else stored["embedding_base_url"]
            )
            reranker_base_url = (
                configuration.reranker_base_url
                if "reranker_base_url" in fields_set
                else stored["reranker_base_url"]
            )
            candidate_api_keys = routing_model_api_keys.copy()
            for role in ("judge", "embedding", "reranker"):
                submitted_key = getattr(configuration, f"{role}_api_key")
                if submitted_key is not None:
                    candidate_api_keys[role] = submitted_key.get_secret_value()
            for role, model_name in (
                ("judge", judge_model),
                ("embedding", embedding_model),
                ("reranker", reranker_model),
            ):
                if not model_name:
                    candidate_api_keys.pop(role, None)
            active_judge_model = judge_model if candidate_api_keys.get("judge") else None
            active_embedding_model = (
                embedding_model if candidate_api_keys.get("embedding") else None
            )
            active_reranker_model = reranker_model if candidate_api_keys.get("reranker") else None
            has_optional_roles = any(
                (active_judge_model, active_embedding_model, active_reranker_model)
            )
            from hostpilot.routing.models import create_routing_model_components

            candidate = None
            if has_optional_roles:
                candidate = create_routing_model_components(
                    app_settings,
                    judge_model=active_judge_model,
                    judge_api_key=candidate_api_keys.get("judge"),
                    judge_base_url=judge_base_url,
                    embedding_model=active_embedding_model,
                    embedding_api_key=candidate_api_keys.get("embedding"),
                    embedding_base_url=embedding_base_url,
                    reranker_model=active_reranker_model,
                    reranker_api_key=candidate_api_keys.get("reranker"),
                    reranker_base_url=reranker_base_url,
                )
            try:
                vector_index = None
                if active_embedding_model and candidate is not None:
                    from hostpilot.routing.vector_index import ChromaSkillVectorIndex

                    vector_index = ChromaSkillVectorIndex(
                        app_settings.data_dir / "skill-vectors",
                        embedding_model=active_embedding_model,
                        models=candidate,
                    )
                async with runtime_lock:
                    if runtime is not None:
                        selector = getattr(runtime, "selector", None)
                        if selector is not None:
                            selector.provider = (
                                candidate.chat_provider
                                if candidate is not None and candidate.chat_provider is not None
                                else selector.fallback_provider
                            )
                            selector.reranker_client = (
                                candidate.reranker_client if candidate is not None else None
                            )
                            selector.routing_models = candidate
                            selector.vector_index = vector_index
                            selector.embedding_model = active_embedding_model
                            selector.reranker_model = active_reranker_model
                    await routing_model_configuration_store.save(
                        judge_base_url=judge_base_url,
                        embedding_base_url=embedding_base_url,
                        reranker_base_url=reranker_base_url,
                        embedding_model=embedding_model,
                        judge_model=judge_model,
                        reranker_model=reranker_model,
                        embedding_api_key=candidate_api_keys.get("embedding"),
                        reranker_api_key=candidate_api_keys.get("reranker"),
                    )
            except Exception:
                if candidate is not None:
                    await candidate.aclose()
                raise
            await routing_models_plugin.replace(candidate)
            routing_model_components = candidate
            routing_model_api_keys = candidate_api_keys
            saved_routing_model_configuration = {
                "judge_base_url": judge_base_url,
                "embedding_base_url": embedding_base_url,
                "reranker_base_url": reranker_base_url,
                "embedding_model": embedding_model,
                "judge_model": judge_model,
                "reranker_model": reranker_model,
                "embedding_api_key": candidate_api_keys.get("embedding"),
                "reranker_api_key": candidate_api_keys.get("reranker"),
            }
            return routing_model_configuration_status()

        application.state.get_routing_model_configuration_status = (
            routing_model_configuration_status
        )
        application.state.configure_routing_model = configure_routing_model_components

        async def test_model_connection_runtime(
            configuration: ModelConnectionTestRequest,
        ) -> ModelConnectionTestResponse:
            submitted_key = (
                configuration.api_key.get_secret_value()
                if configuration.api_key is not None
                else None
            )
            role_keys = {
                "llm": (
                    app_settings.model_api_key.get_secret_value()
                    if app_settings.model_api_key is not None
                    else None
                ),
                "judge": routing_model_api_keys.get("judge"),
                "embedding": routing_model_api_keys.get("embedding"),
                "reranker": routing_model_api_keys.get("reranker"),
            }
            api_key = submitted_key or role_keys[configuration.role]
            if not api_key:
                return ModelConnectionTestResponse(
                    role=configuration.role,
                    success=False,
                    message="请先填写此模型角色的 API Key。",
                    error_code="APIKeyRequired",
                )

            from hostpilot.models.contracts import ModelMessage, ModelRequest, ModelRole

            async def check_chat_provider(provider: Any) -> None:
                result = await provider.complete(
                    ModelRequest(
                        messages=(
                            ModelMessage(
                                role=ModelRole.USER,
                                content="Reply with OK.",
                            ),
                        ),
                        max_output_tokens=128,
                        timeout_seconds=45,
                    )
                )
                if not result.text.strip():
                    raise ValueError("The model returned an empty response.")

            try:
                if configuration.role == "llm":
                    from hostpilot.models.factory import create_model_runtime_components

                    model_settings = app_settings.model_copy(
                        update={
                            "model_name": configuration.model_name,
                            "model_api_key": SecretStr(api_key),
                            "model_base_url": configuration.base_url,
                            "model_streaming": (
                                configuration.streaming
                                if configuration.streaming is not None
                                else app_settings.model_streaming
                            ),
                        }
                    )
                    candidate_model_components = create_model_runtime_components(
                        model_settings,
                        registry=model_provider_registry,
                    )
                    try:
                        await check_chat_provider(candidate_model_components.provider)
                    finally:
                        chat_model = getattr(
                            candidate_model_components.agent_chat_model, "provider", None
                        )
                        client = getattr(
                            getattr(chat_model, "chat_model", None), "root_async_client", None
                        )
                        close = getattr(client, "aclose", None)
                        if close is not None:
                            await close()
                else:
                    from hostpilot.routing.models import create_routing_model_components

                    route_model_components = create_routing_model_components(
                        app_settings,
                        judge_model=(
                            configuration.model_name if configuration.role == "judge" else None
                        ),
                        judge_api_key=(api_key if configuration.role == "judge" else None),
                        judge_base_url=(
                            configuration.base_url if configuration.role == "judge" else None
                        ),
                        embedding_model=(
                            configuration.model_name if configuration.role == "embedding" else None
                        ),
                        embedding_api_key=(api_key if configuration.role == "embedding" else None),
                        embedding_base_url=(
                            configuration.base_url if configuration.role == "embedding" else None
                        ),
                        reranker_model=(
                            configuration.model_name if configuration.role == "reranker" else None
                        ),
                        reranker_api_key=(api_key if configuration.role == "reranker" else None),
                        reranker_base_url=(
                            configuration.base_url if configuration.role == "reranker" else None
                        ),
                    )
                    try:
                        if configuration.role == "judge":
                            if route_model_components.chat_provider is None:
                                raise ValueError("The chat model runtime was not created.")
                            await check_chat_provider(route_model_components.chat_provider)
                        elif configuration.role == "embedding":
                            embedding_batch = await route_model_components.embed(
                                ("HostPilot connection test",),
                                model=configuration.model_name,
                            )
                            if not embedding_batch.vectors or not embedding_batch.vectors[0]:
                                raise ValueError("The embedding response was empty.")
                        else:
                            if route_model_components.reranker_client is None:
                                raise ValueError("The reranker client was not created.")
                            reranker_batch = await route_model_components.reranker_client.rerank(
                                "HostPilot connection test",
                                ("HostPilot connection test", "unrelated sample"),
                                model=configuration.model_name,
                                timeout_seconds=15,
                            )
                            if not reranker_batch.indices:
                                raise ValueError("The reranker response was empty.")
                    finally:
                        await route_model_components.aclose()
                return ModelConnectionTestResponse(
                    role=configuration.role,
                    success=True,
                    message="连接成功，模型已返回有效响应。",
                )
            except Exception as error:
                from hostpilot.models.contracts import (
                    ModelProviderError,
                    ModelProviderErrorCategory,
                )

                if isinstance(error, ModelProviderError):
                    category_messages = {
                        ModelProviderErrorCategory.AUTHENTICATION: "模型认证失败，请检查 API Key。",
                        ModelProviderErrorCategory.INVALID_REQUEST: (
                            "模型服务拒绝了当前测试请求，请检查模型名称、接口兼容性和服务商参数要求。"
                        ),
                        ModelProviderErrorCategory.MODEL_NOT_FOUND: (
                            "模型服务未找到该模型，请检查模型名称和账号权限。"
                        ),
                        ModelProviderErrorCategory.RATE_LIMITED: (
                            "模型服务请求过于频繁，请稍后重试。"
                        ),
                        ModelProviderErrorCategory.TIMEOUT: (
                            "模型响应超时，请检查服务状态或稍后重试。"
                        ),
                        ModelProviderErrorCategory.UNAVAILABLE: (
                            "模型服务暂时不可用，请稍后重试。"
                        ),
                        ModelProviderErrorCategory.CANCELLED: "模型连接测试已取消。",
                        ModelProviderErrorCategory.UNKNOWN: "模型服务请求失败。",
                    }
                    status_suffix = (
                        f"（HTTP {error.status_code}）" if error.status_code is not None else ""
                    )
                    return ModelConnectionTestResponse(
                        role=configuration.role,
                        success=False,
                        message=category_messages[error.category] + status_suffix,
                        error_code=error.category.value,
                    )
                return ModelConnectionTestResponse(
                    role=configuration.role,
                    success=False,
                    message="连接失败，请检查模型名、Base URL、API Key 和服务状态。",
                    error_code=type(error).__name__,
                )

        application.state.test_model_connection = test_model_connection_runtime

        async def configure_model_provider(
            configuration: ModelConfigurationRequest,
        ) -> ModelConfigurationStatus:
            nonlocal app_settings, chat_service, mcp_provider, runtime, tool_facade
            nonlocal workspace_tools
            nonlocal saved_model_configuration
            if chat_agent_runtime_factory is not None:
                raise RuntimeError("Model runtime is managed by the configured runtime factory.")

            stored: ModelConfiguration = saved_model_configuration or {
                "model_name": "",
                "base_url": None,
                "streaming": app_settings.model_streaming,
            }
            model_name = (
                configuration.model_name or stored.get("model_name") or app_settings.model_name
            )
            if not model_name:
                raise ValueError("Model name must be provided for the first configuration.")
            base_url = (
                configuration.base_url
                if configuration.model_name is not None
                else stored.get("base_url") or app_settings.model_base_url
            )
            streaming = (
                configuration.streaming
                if configuration.streaming is not None
                else bool(stored.get("streaming", app_settings.model_streaming))
            )
            api_key = configuration.api_key or app_settings.model_api_key
            if api_key is None:
                raise ValueError("An API key is required to configure the LLM Model.")
            candidate_settings = app_settings.model_copy(
                update={
                    "model_name": model_name,
                    "model_api_key": api_key,
                    "model_base_url": base_url,
                    "model_streaming": streaming,
                    "mcp_servers": runtime_mcp_servers["servers"],
                }
            )
            candidate_workspace = WorkspaceFiles(
                workspace_path,
                read_only=workspace_read_only,
            )
            candidate_provider = build_retrieval_mcp_provider(
                candidate_settings,
                workspace_root=workspace_path,
                workspace_read_only=workspace_read_only,
                workspace_instance=candidate_workspace,
                trace_sink=trace_store,
                audit_sink=audit_sink,
                executor_factory=tool_executor_factory,
                sandbox_backends=sandbox_backends,
            )
            candidate_facade = ToolFacade(candidate_provider)
            try:
                candidate_tools = await candidate_provider.start()
                candidate_chat_agent_runtime = create_configured_chat_agent_runtime(
                    candidate_settings,
                    checkpointer=hosted_checkpointer(),
                    tools=candidate_tools,
                    tool_provider=candidate_facade,
                    memory_controller=memory_controller,
                    cancellation_sink=cancellation_store,
                    skill_execution_modes=saved_skill_execution_modes,
                    trace_sink=trace_store,
                    plan_component_registry=plan_component_registry,
                    chat_agent_runtime_registry=chat_agent_runtime_registry,
                    model_provider_registry=model_provider_registry,
                )
                if (
                    routing_model_components is not None
                    and saved_routing_model_configuration is not None
                ):
                    selector = getattr(candidate_chat_agent_runtime, "selector", None)
                    if selector is not None:
                        from hostpilot.routing.vector_index import ChromaSkillVectorIndex

                        if routing_model_components.chat_provider is not None:
                            selector.provider = routing_model_components.chat_provider
                        selector.reranker_client = routing_model_components.reranker_client
                        selector.routing_models = routing_model_components
                        selector.reranker_model = saved_routing_model_configuration[
                            "reranker_model"
                        ]
                        selector.embedding_model = saved_routing_model_configuration[
                            "embedding_model"
                        ]
                        selector.vector_index = (
                            ChromaSkillVectorIndex(
                                app_settings.data_dir / "skill-vectors",
                                embedding_model=selector.embedding_model,
                                models=routing_model_components,
                            )
                            if selector.embedding_model
                            else None
                        )
            except Exception:
                await candidate_provider.aclose()
                application.state.mcp_connection_status = candidate_facade.connections()
                raise

            try:
                await model_configuration_store.save(
                    model_name=model_name,
                    base_url=base_url,
                    streaming=streaming,
                )
            except Exception:
                await candidate_provider.aclose()
                raise
            saved_model_configuration = {
                "model_name": model_name,
                "base_url": base_url,
                "streaming": streaming,
            }

            async with runtime_lock:
                runtime = candidate_chat_agent_runtime
                await chat_agent_runtime_plugin.replace(candidate_chat_agent_runtime)
                await mcp_provider_plugin.replace(candidate_provider)
                tool_governance_plugin.bind(candidate_provider)
                mcp_provider = candidate_provider
                application.state.mcp_provider = candidate_provider
                application.state.tool_facade = tool_facade
                workspace_tools = candidate_workspace
                application.state.workspace_files = workspace_tools
                application.state.mcp_connection_status = None
                app_settings = candidate_settings
                application.state.settings = candidate_settings
                if chat_service is None:
                    chat_service = make_chat_service(
                        hosted_chat_agent_runtime(), hosted_tool_facade()
                    )
                else:
                    chat_service.tool_provider = hosted_tool_facade()
                application.state.chat_service = chat_service
            return model_configuration_status()

        application.state.get_model_configuration_status = model_configuration_status
        application.state.configure_model = configure_model_provider
        application.state.conversations = hosted_conversation_store()
        application.state.checkpointer = hosted_checkpointer()
        memory_review = MemoryProposalReview(
            MemoryWriteService(memory_store, hosted_conversation_store()),
            audit=audit_sink,
        )
        application.state.memory_review = memory_review
        memory_controller = MemoryController(
            memory_store,
            hosted_conversation_store(),
            memory_review,
            max_recall_chars=app_settings.max_memory_context_chars,
            trace=trace_store,
        )
        application.state.memory_controller = memory_controller
        if chat_agent_runtime_factory is not None:
            runtime = chat_agent_runtime_factory(hosted_checkpointer())
            await chat_agent_runtime_plugin.replace(runtime)
        elif app_settings.model_name and app_settings.model_api_key:
            mcp_tools = await mcp_provider.start()
            runtime = create_configured_chat_agent_runtime(
                startup_settings,
                checkpointer=hosted_checkpointer(),
                tools=mcp_tools,
                tool_provider=tool_facade,
                memory_controller=memory_controller,
                cancellation_sink=cancellation_store,
                skill_execution_modes=saved_skill_execution_modes,
                trace_sink=trace_store,
                plan_component_registry=plan_component_registry,
                chat_agent_runtime_registry=chat_agent_runtime_registry,
                model_provider_registry=model_provider_registry,
            )
            await chat_agent_runtime_plugin.replace(runtime)
        elif startup_settings.mcp_servers:
            try:
                await mcp_provider.start()
            except Exception:
                application.state.mcp_connection_status = tool_facade.connections()

        if runtime is not None:
            chat_service = make_chat_service(
                hosted_chat_agent_runtime(),
                tool_facade if mcp_provider._started else None,
            )
        application.state.chat_service = chat_service
        yield

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        prepare_sqlite_database_path(database_path)
        runtime_context = RuntimeContext()
        runtime_host = RuntimeHost(runtime_context)
        configuration_plugin = ConfigurationStoresHostPlugin(
            database_path,
            model_configuration_store_factory=(
                model_configuration_store_factory or SQLiteModelConfigurationStore
            ),
            routing_model_configuration_store_factory=(
                routing_model_configuration_store_factory or SQLiteRoutingModelConfigurationStore
            ),
            mcp_configuration_store_factory=(
                mcp_configuration_store_factory or SQLiteMCPConfigurationStore
            ),
            harness_configuration_store_factory=(
                harness_configuration_store_factory or SQLiteHarnessConfigurationStore
            ),
            workspace_configuration_store_factory=(
                workspace_configuration_store_factory or SQLiteWorkspaceConfigurationStore
            ),
            skill_execution_store_factory=(
                skill_execution_store_factory or SQLiteSkillExecutionStore
            ),
        )
        application_stores_plugin = ApplicationStoresHostPlugin(
            database_path,
            app_settings.memory_dir,
            trace_store_factory=trace_store_factory or SQLiteTraceStore,
            audit_store_factory=audit_store_factory or SQLiteAuditStore,
            cancellation_store_factory=cancellation_store_factory or SQLiteCancellationStore,
            memory_store_factory=memory_store_factory or MarkdownMemoryStore,
        )
        mcp_provider_plugin = MCPProviderHostPlugin(None)
        sandbox_backends_plugin = SandboxBackendsHostPlugin(sandbox_backend_factory)
        tool_governance_plugin = ToolGovernanceHostPlugin(tool_executor_factory)
        routing_models_plugin = RoutingModelsHostPlugin()
        state_services_plugin = StateServicesHostPlugin(
            conversation_store_builder,
            database_path,
            (checkpointer_factory or open_sqlite_checkpointer)(database_path),
        )
        chat_agent_runtime_plugin = ChatAgentRuntimeHostPlugin()
        runtime_host.register_plugin(configuration_plugin)
        runtime_host.register_plugin(application_stores_plugin)
        runtime_host.register_plugin(routing_models_plugin)
        runtime_host.register_plugin(sandbox_backends_plugin)
        runtime_host.register_plugin(mcp_provider_plugin)
        runtime_host.register_plugin(tool_governance_plugin)
        runtime_host.register_plugin(state_services_plugin)
        runtime_host.register_plugin(chat_agent_runtime_plugin)
        application.state.runtime_host = runtime_host
        application.state.mcp_provider_plugin = mcp_provider_plugin
        application.state.tool_governance_plugin = tool_governance_plugin
        application.state.routing_models_plugin = routing_models_plugin
        application.state.chat_agent_runtime_plugin = chat_agent_runtime_plugin
        try:
            await runtime_host.start()
            async with lifespan_body(application):
                yield
        finally:
            await runtime_host.close()

    app = FastAPI(title="HostPilot API", version="1", lifespan=lifespan)
    app.state.settings = app_settings

    @app.exception_handler(RequestValidationError)
    async def redact_validation_inputs(
        _request: Request, error: RequestValidationError
    ) -> JSONResponse:
        """Return field errors without echoing submitted values such as credentials."""
        safe_errors = [
            {
                "loc": item.get("loc", ()),
                "msg": item.get("msg", "Invalid value."),
                "type": item.get("type", "value_error"),
            }
            for item in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    if app_settings.web_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(app_settings.web_origins),
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def reject_untrusted_write_origins(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin is not None:
                host = request.headers.get("host", "")
                same_origin = f"{request.url.scheme}://{host}"
                if origin != same_origin and origin not in app_settings.web_origins:
                    return Response(
                        content="Request Origin is not allowed.",
                        status_code=status.HTTP_403_FORBIDDEN,
                    )
        return await call_next(request)

    app.include_router(router)
    return app


def _websocket_origin_allowed(websocket: WebSocket, allowed_origins: tuple[str, ...]) -> bool:
    origin = websocket.headers.get("origin")
    if origin is None:
        return True
    scheme = "https" if websocket.url.scheme == "wss" else "http"
    same_origin = f"{scheme}://{websocket.headers.get('host', '')}"
    return origin == same_origin or origin in allowed_origins


__all__ = ["CreateConversationRequest", "create_app", "router"]
