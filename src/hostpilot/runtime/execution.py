"""Framework-neutral contracts for the Runtime execution loop."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from hostpilot.contracts import ModelTokenUsage, ToolSideEffect
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
    message_sink: RuntimeExecutionMessageSink | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.request_context, RequestContext):
            raise TypeError("Runtime execution requires a RequestContext instance.")
        messages = tuple(self.messages)
        tools = tuple(self.tools)
        allowed_tool_ids = frozenset(self.allowed_tool_ids)
        tool_replays = tuple(self.tool_replays)
        tool_side_effects = dict(self.tool_side_effects)
        object.__setattr__(self, "messages", messages)
        object.__setattr__(self, "tools", tools)
        object.__setattr__(self, "allowed_tool_ids", allowed_tool_ids)
        object.__setattr__(self, "tool_replays", tool_replays)
        object.__setattr__(self, "tool_side_effects", tool_side_effects)

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
    "RuntimeToolPort",
    "runtime_tool_call_key",
]
