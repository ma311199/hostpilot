"""Framework-neutral contracts for the Runtime execution loop."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from hostpilot.contracts import ModelTokenUsage, ToolRetryClass, ToolSideEffect
from hostpilot.models import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelToolCall,
    ModelToolDefinition,
)
from hostpilot.runtime.context import RequestContext


class RuntimeExecutionStatus(StrEnum):
    """Stable terminal outcomes returned by a Runtime execution loop."""

    COMPLETED = "completed"
    CLARIFICATION = "clarification"
    REFUSED = "refused"
    MAX_STEPS_REACHED = "max_steps_reached"


@dataclass(frozen=True)
class RuntimeToolReplay:
    """Previously proposed tool call in an interrupted turn and its known result."""

    tool_name: str
    arguments_json: str
    result: str | None

    def __post_init__(self) -> None:
        if not self.tool_name or self.tool_name.strip() != self.tool_name:
            raise ValueError("Runtime tool replay requires a valid tool name.")
        if not isinstance(self.arguments_json, str):
            raise TypeError("Runtime tool replay arguments must be canonical JSON text.")
        if self.result is not None and not isinstance(self.result, str):
            raise TypeError("Runtime tool replay result must be text or None.")


def runtime_tool_call_key(tool_call: ModelToolCall) -> tuple[str, str]:
    """Return a stable tool/argument key that ignores mapping insertion order."""
    if not isinstance(tool_call, ModelToolCall):
        raise TypeError("Runtime tool replay keys require a ModelToolCall.")
    try:
        arguments_json = json.dumps(
            dict(tool_call.arguments),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Runtime tool arguments must be JSON serializable.") from error
    return tool_call.name, arguments_json


RuntimeExecutionMessageSink = Callable[[tuple[ModelMessage, ...]], Awaitable[None]]


def stable_tool_invocation_identity(
    *,
    plan_run_id: str,
    task_id: str,
    call_index: int,
    tool_id: str,
    arguments: Mapping[str, Any],
    business_fields: tuple[str, ...] = (),
) -> tuple[str, str]:
    """Build a stable opaque idempotency key and canonical request fingerprint."""
    for value, label in (
        (plan_run_id, "PlanRun"),
        (task_id, "Task"),
        (tool_id, "Tool"),
    ):
        if not isinstance(value, str) or not value or value.strip() != value:
            raise ValueError(f"{label} ID must be non-empty and trimmed.")
    if isinstance(call_index, bool) or not isinstance(call_index, int) or call_index < 1:
        raise ValueError("Tool invocation call index must be a positive integer.")
    if not isinstance(arguments, Mapping):
        raise TypeError("Tool invocation arguments must be a mapping.")
    if len(set(business_fields)) != len(business_fields):
        raise ValueError("Idempotency business fields must be unique.")
    if any(field not in arguments for field in business_fields):
        raise ValueError("Idempotency business fields must exist in the tool arguments.")
    try:
        canonical_arguments = json.dumps(
            dict(arguments),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        canonical_identity = json.dumps(
            {
                "version": 1,
                "plan_run_id": plan_run_id,
                "task_id": task_id,
                "call_index": call_index,
                "tool_id": tool_id,
                "business_values": {field: arguments[field] for field in sorted(business_fields)},
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise ValueError("Tool invocation data must be JSON serializable.") from error
    key_digest = hashlib.sha256(canonical_identity.encode("utf-8")).hexdigest()
    request_digest = hashlib.sha256(canonical_arguments.encode("utf-8")).hexdigest()
    return f"hostpilot:v1:{key_digest}", f"sha256:{request_digest}"


class ToolInvocationRecoveryAction(StrEnum):
    """Safe next step for one invocation whose completion was not checkpointed."""

    DISPATCH = "dispatch"
    RETRY_READ = "retry_read"
    REPLAY_WITH_KEY = "replay_with_key"
    RECONCILE = "reconcile"


@dataclass(frozen=True)
class ToolInvocationRecoveryDecision:
    """Recovery action plus a safe reason code for logs and user prompts."""

    action: ToolInvocationRecoveryAction
    reason_code: str


def decide_tool_invocation_recovery(
    *,
    retry_class: ToolRetryClass,
    prepared_record_exists: bool,
    request_fingerprint: str,
    now: datetime,
    prior_request_fingerprint: str | None = None,
    idempotency_key: str | None = None,
    idempotency_expires_at: datetime | None = None,
    idempotency_supported: bool = False,
) -> ToolInvocationRecoveryDecision:
    """Fail closed unless a pending call is a read or a proven same-key replay."""
    for value, label in (
        (request_fingerprint, "Request fingerprint"),
        (prior_request_fingerprint, "Prior request fingerprint"),
        (idempotency_key, "Idempotency key"),
    ):
        if value is not None and (not value or value.strip() != value):
            raise ValueError(f"{label} must be non-empty and trimmed.")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Recovery decision time must be timezone-aware.")
    if idempotency_expires_at is not None and (
        idempotency_expires_at.tzinfo is None or idempotency_expires_at.utcoffset() is None
    ):
        raise ValueError("Idempotency expiration must be timezone-aware.")
    if not prepared_record_exists:
        return ToolInvocationRecoveryDecision(
            ToolInvocationRecoveryAction.DISPATCH, "no_prepared_record"
        )
    if prior_request_fingerprint != request_fingerprint:
        return ToolInvocationRecoveryDecision(
            ToolInvocationRecoveryAction.RECONCILE,
            "request_fingerprint_changed",
        )
    if retry_class is ToolRetryClass.PURE_READ:
        return ToolInvocationRecoveryDecision(ToolInvocationRecoveryAction.RETRY_READ, "pure_read")
    if retry_class is ToolRetryClass.UNKNOWN:
        return ToolInvocationRecoveryDecision(
            ToolInvocationRecoveryAction.RECONCILE,
            "unknown_retry_class",
        )
    if idempotency_key is None:
        return ToolInvocationRecoveryDecision(
            ToolInvocationRecoveryAction.RECONCILE,
            "idempotency_key_missing",
        )
    if not idempotency_supported:
        return ToolInvocationRecoveryDecision(
            ToolInvocationRecoveryAction.RECONCILE,
            "idempotency_not_supported",
        )
    if idempotency_expires_at is None or now >= idempotency_expires_at:
        return ToolInvocationRecoveryDecision(
            ToolInvocationRecoveryAction.RECONCILE,
            "idempotency_window_expired",
        )
    return ToolInvocationRecoveryDecision(
        ToolInvocationRecoveryAction.REPLAY_WITH_KEY,
        "valid_idempotency_contract",
    )


class RuntimeToolOutcomePort(Protocol):
    """Persist side-effecting calls until their tool result is checkpointed."""

    async def begin_invocation(
        self,
        *,
        session_id: str,
        turn_id: str,
        task_run_id: str | None,
        tool_call_id: str,
        tool_name: str,
    ) -> None: ...

    async def result_checkpointed(self, session_id: str, tool_call_id: str) -> None: ...

    async def claim_resolution(self, session_id: str, tool_call_id: str) -> bool: ...

    async def release_resolution_claim(self, session_id: str, tool_call_id: str) -> None: ...


class RuntimeToolInvocationPort(Protocol):
    """Persist every tool dispatch before invocation and complete after checkpoint."""

    async def prepare_invocation(
        self,
        *,
        invocation_id: str,
        session_id: str,
        turn_id: str,
        plan_run_id: str | None,
        task_run_id: str | None,
        task_id: str,
        call_index: int,
        tool_id: str,
        retry_class: ToolRetryClass,
        idempotency_key: str | None,
        request_fingerprint: str,
        arguments_json: str,
        idempotency_window_seconds: int | None,
    ) -> None: ...

    async def complete_invocation(self, invocation_id: str, *, plan_run_id: str | None) -> None: ...


@dataclass(frozen=True)
class RuntimeExecutionRequest:
    """Input and bounded capabilities for one execution loop invocation."""

    request_context: RequestContext
    messages: tuple[ModelMessage, ...]
    tools: tuple[ModelToolDefinition, ...] = ()
    allowed_tool_ids: frozenset[str] = frozenset()
    max_steps: int = 8
    tool_replays: tuple[RuntimeToolReplay, ...] = ()
    tool_side_effects: Mapping[str, ToolSideEffect] = field(default_factory=dict)
    tool_outcomes: RuntimeToolOutcomePort | None = None
    tool_invocations: RuntimeToolInvocationPort | None = None
    tool_retry_classes: Mapping[str, ToolRetryClass] = field(default_factory=dict)
    idempotency_windows: Mapping[str, int] = field(default_factory=dict)
    idempotency_business_fields: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    plan_run_id: str | None = None
    task_run_id: str | None = None
    task_id: str = "runtime_chat"
    tool_call_index_start: int = 1
    require_tool_invocation_journal: bool = False
    message_sink: RuntimeExecutionMessageSink | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_context, RequestContext):
            raise TypeError("Runtime execution requires a RequestContext instance.")
        messages = tuple(self.messages)
        tools = tuple(self.tools)
        allowed_tool_ids = frozenset(self.allowed_tool_ids)
        tool_replays = tuple(self.tool_replays)
        tool_side_effects = dict(self.tool_side_effects)
        tool_retry_classes = dict(self.tool_retry_classes)
        idempotency_windows = dict(self.idempotency_windows)
        idempotency_business_fields = {
            tool_id: tuple(fields) for tool_id, fields in self.idempotency_business_fields.items()
        }
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "allowed_tool_ids", allowed_tool_ids)
        object.__setattr__(self, "tool_replays", tool_replays)
        object.__setattr__(self, "tool_side_effects", tool_side_effects)
        object.__setattr__(self, "tool_retry_classes", tool_retry_classes)
        object.__setattr__(self, "idempotency_windows", idempotency_windows)
        object.__setattr__(self, "idempotency_business_fields", idempotency_business_fields)

        if not messages:
            raise ValueError("Runtime execution requires at least one message.")
        if any(not isinstance(message, ModelMessage) for message in messages):
            raise TypeError("Runtime execution messages must use ModelMessage values.")
        if (
            isinstance(self.max_steps, bool)
            or not isinstance(self.max_steps, int)
            or self.max_steps < 1
        ):
            raise ValueError("Runtime execution max_steps must be a positive integer.")

        tool_ids = tuple(tool.name for tool in tools)
        if len(set(tool_ids)) != len(tool_ids):
            raise ValueError("Runtime execution tool definitions must have unique IDs.")
        if any(not tool_id or tool_id.strip() != tool_id for tool_id in tool_ids):
            raise ValueError("Runtime execution tool IDs must be non-empty and trimmed.")
        if not allowed_tool_ids.issubset(tool_ids):
            raise ValueError("Runtime execution allowlist contains an unknown tool ID.")
        if any(not isinstance(replay, RuntimeToolReplay) for replay in tool_replays):
            raise TypeError("Runtime execution tool_replays must use RuntimeToolReplay values.")
        if any(not isinstance(effect, ToolSideEffect) for effect in tool_side_effects.values()):
            raise TypeError("Runtime tool side-effect classifications must use ToolSideEffect.")
        if any(not isinstance(kind, ToolRetryClass) for kind in tool_retry_classes.values()):
            raise TypeError("Runtime tool retry classifications must use ToolRetryClass.")
        if not set(tool_retry_classes).issubset(tool_ids):
            raise ValueError("Runtime retry classifications contain an unknown tool ID.")
        if not set(idempotency_windows).issubset(tool_ids):
            raise ValueError("Runtime idempotency windows contain an unknown tool ID.")
        if any(
            isinstance(seconds, bool) or not isinstance(seconds, int) or seconds < 1
            for seconds in idempotency_windows.values()
        ):
            raise ValueError("Runtime idempotency windows must be positive integer seconds.")
        if not set(idempotency_business_fields).issubset(tool_ids):
            raise ValueError("Runtime idempotency fields contain an unknown tool ID.")
        if not self.task_id or self.task_id.strip() != self.task_id:
            raise ValueError("Runtime task ID must be non-empty and trimmed.")
        if (
            isinstance(self.tool_call_index_start, bool)
            or not isinstance(self.tool_call_index_start, int)
            or self.tool_call_index_start < 1
        ):
            raise ValueError("Runtime tool call index must be a positive integer.")
        if self.require_tool_invocation_journal and self.tool_invocations is None:
            raise ValueError("Runtime execution requires a durable tool invocation journal.")
        if self.message_sink is not None and not callable(self.message_sink):
            raise TypeError("Runtime execution message_sink must be callable or None.")


@dataclass(frozen=True)
class RuntimeExecutionResult:
    """Normalized terminal result of one Runtime execution loop invocation."""

    status: RuntimeExecutionStatus
    response_text: str | None
    messages: tuple[ModelMessage, ...]
    token_usage: ModelTokenUsage | None = None
    steps: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.status, RuntimeExecutionStatus):
            raise TypeError("Runtime execution status must use RuntimeExecutionStatus.")
        object.__setattr__(self, "messages", tuple(self.messages))
        if isinstance(self.steps, bool) or not isinstance(self.steps, int) or self.steps < 0:
            raise ValueError("Runtime execution steps must be a non-negative integer.")
        if self.response_text is not None and not isinstance(self.response_text, str):
            raise TypeError("Runtime execution response_text must be text or None.")
        if self.status in {
            RuntimeExecutionStatus.COMPLETED,
            RuntimeExecutionStatus.CLARIFICATION,
            RuntimeExecutionStatus.REFUSED,
        } and not (self.response_text and self.response_text.strip()):
            raise ValueError("Runtime execution terminal responses must contain text.")


class RuntimeModelPort(Protocol):
    """Generate normalized model output within the current request context."""

    async def complete(
        self,
        request_context: RequestContext,
        request: ModelRequest,
    ) -> ModelResponse: ...


class RuntimeToolPort(Protocol):
    """Execute an untrusted model tool proposal through governed capabilities."""

    async def execute(
        self,
        request_context: RequestContext,
        tool_call: ModelToolCall,
    ) -> str: ...


class RuntimeSkillPort(Protocol):
    """Run one selected Skill without exposing its framework implementation."""

    async def execute(
        self,
        request_context: RequestContext,
        skill_id: str,
        payload: Mapping[str, Any],
    ) -> RuntimeExecutionResult: ...


class RuntimeExecutionLoop(Protocol):
    """Drive one bounded request through injected Model, Tool and Skill ports."""

    async def run(self, request: RuntimeExecutionRequest) -> RuntimeExecutionResult: ...


__all__ = [
    "RuntimeExecutionLoop",
    "RuntimeExecutionRequest",
    "RuntimeExecutionResult",
    "RuntimeExecutionStatus",
    "RuntimeExecutionMessageSink",
    "RuntimeModelPort",
    "RuntimeSkillPort",
    "RuntimeToolReplay",
    "RuntimeToolOutcomePort",
    "RuntimeToolInvocationPort",
    "RuntimeToolPort",
    "runtime_tool_call_key",
    "stable_tool_invocation_identity",
]
