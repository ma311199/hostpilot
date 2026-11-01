"""Framework-independent data contracts shared across HostPilot modules."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Self
from uuid import UUID, uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ContractModel(BaseModel):
    """Base for immutable contracts that reject undeclared fields."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MessageRole(StrEnum):
    """Roles allowed in a minimal conversation snapshot."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ConversationMessage(ContractModel):
    """One message included in the context required for a request."""

    role: MessageRole
    content: str


class ConversationSummary(ContractModel):
    """Metadata used to list and identify one local conversation."""

    conversation_id: NonEmptyString
    title: NonEmptyString
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at")
    @classmethod
    def timestamps_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Conversation timestamps must include a timezone.")
        return value


class ContextSnapshot(ContractModel):
    """Minimal, request-scoped conversation context passed to the agent."""

    request_id: NonEmptyString
    conversation_id: NonEmptyString | None = None
    messages: tuple[ConversationMessage, ...] = ()
    session_summary: str | None = None
    long_term_memory: str | None = None
    budget: BudgetContext | None = None
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("captured_at")
    @classmethod
    def captured_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone.")
        return value


class MemoryScope(StrEnum):
    """Markdown memory area targeted by an explicit write proposal."""

    SESSION_SUMMARY = "session_summary"
    LONG_TERM = "long_term"


class MemoryWriteProposal(ContractModel):
    """Complete replacement content awaiting explicit user approval."""

    proposal_id: NonEmptyString = Field(default_factory=lambda: str(uuid4()), max_length=64)
    scope: MemoryScope
    conversation_id: NonEmptyString | None = None
    content: str = Field(min_length=1, max_length=262_144)

    @model_validator(mode="after")
    def validate_target_and_content(self) -> Self:
        if not self.content.strip():
            raise ValueError("Memory proposal content cannot be blank.")
        if self.scope is MemoryScope.SESSION_SUMMARY:
            if self.conversation_id is None:
                raise ValueError("Session summary proposals require conversation_id.")
            try:
                normalized_id = str(UUID(self.conversation_id))
            except ValueError as error:
                raise ValueError("conversation_id must be a UUID.") from error
            object.__setattr__(self, "conversation_id", normalized_id)
        elif self.conversation_id is not None:
            raise ValueError("Long-term memory proposals cannot target a conversation.")
        return self


class Route(StrEnum):
    """Supported top-level execution decisions."""

    DIRECT = "direct"
    CLARIFY = "clarify"
    TOOL_CALL = "tool_call"
    PLAN_EXECUTE = "plan_execute"


class ModelTokenUsage(ContractModel):
    """Provider-reported token usage for one completed agent turn."""

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class ChatTurnResult(ContractModel):
    """Result of one completed user/assistant chat turn."""

    conversation_id: NonEmptyString
    request_id: NonEmptyString
    assistant_message: str
    route: Route = Route.DIRECT
    clarification_prompt: str | None = None
    token_usage: ModelTokenUsage | None = None


class BudgetLimits(ContractModel):
    """Explicit upper bounds for one request's resource consumption."""

    total_tokens: int = Field(gt=0)
    tool_calls: int = Field(ge=0)
    retrieval_calls: int = Field(ge=0)
    timeout_seconds: float = Field(gt=0)


class BudgetUsage(ContractModel):
    """Resource consumption observed so far for a request."""

    total_tokens: int = Field(default=0, ge=0)
    tool_calls: int = Field(default=0, ge=0)
    retrieval_calls: int = Field(default=0, ge=0)
    elapsed_seconds: float = Field(default=0, ge=0)


class BudgetContext(ContractModel):
    """Limits and observed usage carried explicitly through request processing."""

    limits: BudgetLimits
    usage: BudgetUsage = Field(default_factory=BudgetUsage)

    @property
    def is_exhausted(self) -> bool:
        """Return whether any configured budget has been reached or exceeded."""
        return (
            self.usage.total_tokens >= self.limits.total_tokens
            or self.usage.tool_calls >= self.limits.tool_calls
            or self.usage.retrieval_calls >= self.limits.retrieval_calls
            or self.usage.elapsed_seconds >= self.limits.timeout_seconds
        )


class RouteDecision(ContractModel):
    """A router result; it describes a path but does not grant authorization."""

    request_id: NonEmptyString
    route: Route
    reason_code: NonEmptyString = "unspecified"
    tool_name: NonEmptyString | None = None
    tool_arguments: dict[str, Any] = Field(default_factory=dict)
    clarification_prompt: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_route_details(self) -> Self:
        if self.route is Route.TOOL_CALL and self.tool_name is None:
            raise ValueError("tool_call decisions require tool_name.")
        if self.route is not Route.TOOL_CALL and self.tool_name is not None:
            raise ValueError("tool_name is only valid for tool_call decisions.")
        if self.route is not Route.TOOL_CALL and self.tool_arguments:
            raise ValueError("tool_arguments are only valid for tool_call decisions.")
        if self.route is Route.CLARIFY and self.clarification_prompt is None:
            raise ValueError("clarify decisions require clarification_prompt.")
        if self.route is not Route.CLARIFY and self.clarification_prompt is not None:
            raise ValueError("clarification_prompt is only valid for clarify decisions.")
        return self


class ToolType(StrEnum):
    """Broad categories used for policy and execution routing."""

    RETRIEVAL = "retrieval"
    ACTION = "action"
    CODE = "code"


class TrustLevel(StrEnum):
    """Declared trust class for a tool implementation."""

    HOST = "host"
    THIRD_PARTY = "third_party"
    UNTRUSTED = "untrusted"


class ExecutionEnvironment(StrEnum):
    """Execution boundary selected for a tool."""

    HOST = "host"
    SERVICE = "service"
    WASM = "wasm"
    NATIVE_SANDBOX = "native_sandbox"
    REMOTE = "remote"


class TimeoutProfile(ContractModel):
    """Per-call timeout bounds."""

    timeout_seconds: float = Field(gt=0)


class ToolDescriptor(ContractModel):
    """Declarative tool metadata; it is not itself a policy grant."""

    name: NonEmptyString
    description: NonEmptyString
    tool_type: ToolType
    trust_level: TrustLevel
    execution_env: ExecutionEnvironment
    timeout_profile: TimeoutProfile
    capabilities: frozenset[NonEmptyString] = frozenset()
    high_impact: bool = False


class RetrievalToolRequest(ContractModel):
    """Self-contained request passed to the independent retrieval service."""

    query: NonEmptyString
    mode: NonEmptyString
    filters: dict[str, str | int | float | bool | list[str]] = Field(default_factory=dict)
    budget: BudgetLimits


class Evidence(ContractModel):
    """Retrieved source material with provenance metadata."""

    source_id: NonEmptyString
    confidence: float = Field(ge=0, le=1)
    retrieved_at: datetime
    retrieval_path: tuple[NonEmptyString, ...]
    content: str

    @field_validator("retrieved_at")
    @classmethod
    def retrieved_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("retrieved_at must include a timezone.")
        return value


class EvidenceSet(ContractModel):
    """Validated evidence returned by a retrieval tool."""

    request_id: NonEmptyString
    evidence: tuple[Evidence, ...]


class SandboxResourceLimits(ContractModel):
    """Hard resource ceilings for a future isolated execution."""

    cpu_seconds: float = Field(gt=0)
    memory_bytes: int = Field(gt=0)
    max_processes: int = Field(gt=0)


class SandboxPolicy(ContractModel):
    """Default-deny capability decision and resource limits for a sandbox."""

    allowed: bool = False
    capabilities: frozenset[NonEmptyString] = frozenset()
    network_access: bool = False
    filesystem_access: bool = False
    resource_limits: SandboxResourceLimits

    @model_validator(mode="after")
    def denied_policy_has_no_capabilities(self) -> Self:
        if not self.allowed and (
            self.capabilities or self.network_access or self.filesystem_access
        ):
            raise ValueError("Denied sandbox policies cannot grant capabilities.")
        return self


__all__ = [
    "BudgetContext",
    "BudgetLimits",
    "BudgetUsage",
    "ContextSnapshot",
    "ChatTurnResult",
    "ConversationSummary",
    "ConversationMessage",
    "Evidence",
    "EvidenceSet",
    "ExecutionEnvironment",
    "MessageRole",
    "MemoryScope",
    "MemoryWriteProposal",
    "RetrievalToolRequest",
    "Route",
    "RouteDecision",
    "SandboxPolicy",
    "SandboxResourceLimits",
    "TimeoutProfile",
    "ToolDescriptor",
    "ToolType",
    "TrustLevel",
]
