"""LangGraph checkpoint adapter for the framework-neutral Runtime session state Port."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpilot.models import ModelMessage, ModelRole, ModelToolCall
from hostpilot.persistence import session_thread_config


class LangGraphRuntimeSessionStateAdapter:
    """Read and append normalized Runtime messages through a LangGraph checkpoint."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph

    async def load_messages(self, session_id: str) -> tuple[ModelMessage, ...]:
        """Load checkpoint messages without dropping tool-call correlation data."""
        snapshot = await self.graph.aget_state(session_thread_config(session_id))
        raw_messages = snapshot.values.get("messages", ())
        return tuple(_from_framework_message(message) for message in raw_messages)

    async def append_messages(
        self,
        session_id: str,
        messages: tuple[ModelMessage, ...],
    ) -> None:
        """Append normalized Runtime message deltas to the existing checkpoint."""
        if not messages:
            return
        converted = tuple(_to_framework_message(message) for message in messages)
        await self.graph.aupdate_state(
            session_thread_config(session_id),
            {"messages": list(converted)},
        )


def _from_framework_message(message: Any) -> ModelMessage:
    """Convert one LangChain message, rejecting history that cannot be preserved."""
    from langchain_core.messages import get_buffer_string

    message_type = getattr(message, "type", None)
    if not isinstance(message_type, str):
        raise ValueError("Checkpoint messages must include a supported type.")
    role = {
        "human": ModelRole.USER,
        "user": ModelRole.USER,
        "ai": ModelRole.ASSISTANT,
        "assistant": ModelRole.ASSISTANT,
        "system": ModelRole.SYSTEM,
        "tool": ModelRole.TOOL,
    }.get(message_type)
    if role is None:
        raise ValueError(f"Unsupported checkpoint message type: {message_type!r}.")
    content = getattr(message, "content", "")
    if not isinstance(content, str):
        content = get_buffer_string((message,))
    tool_call_id = getattr(message, "tool_call_id", None)
    additional_kwargs = getattr(message, "additional_kwargs", {})
    turn_id = (
        additional_kwargs.get("hostpilot_turn_id")
        if isinstance(additional_kwargs, Mapping)
        else None
    )
    if turn_id is not None and (
        not isinstance(turn_id, str) or not turn_id or turn_id.strip() != turn_id
    ):
        raise ValueError("Checkpoint contains an invalid HostPilot Turn association.")
    tool_calls: list[ModelToolCall] = []
    for call in getattr(message, "tool_calls", ()):
        if not isinstance(call, Mapping):
            raise ValueError("Checkpoint contains a malformed model tool call.")
        call_id = call.get("id")
        name = call.get("name")
        arguments = call.get("args")
        if (
            not isinstance(call_id, str)
            or not call_id
            or not isinstance(name, str)
            or not isinstance(arguments, Mapping)
        ):
            raise ValueError("Checkpoint contains an incomplete model tool call.")
        tool_calls.append(ModelToolCall(call_id, name, dict(arguments)))
    if role is ModelRole.TOOL and not isinstance(tool_call_id, str):
        raise ValueError("Checkpoint tool messages must include tool_call_id.")
    return ModelMessage(
        role=role,
        content=content,
        tool_call_id=tool_call_id,
        tool_calls=tuple(tool_calls),
        turn_id=turn_id,
    )


def _to_framework_message(message: ModelMessage) -> Any:
    """Convert one normalized Runtime message to its LangChain checkpoint form."""
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

    if not isinstance(message, ModelMessage):
        raise TypeError("Runtime session messages must use ModelMessage values.")
    if message.turn_id is not None and (
        not isinstance(message.turn_id, str)
        or not message.turn_id
        or message.turn_id.strip() != message.turn_id
    ):
        raise ValueError("Runtime messages require a valid Turn association.")
    additional_kwargs = (
        {"hostpilot_turn_id": message.turn_id} if message.turn_id is not None else {}
    )
    if message.role is ModelRole.USER:
        return HumanMessage(content=message.content, additional_kwargs=additional_kwargs)
    if message.role is ModelRole.SYSTEM:
        return SystemMessage(content=message.content, additional_kwargs=additional_kwargs)
    if message.role is ModelRole.TOOL:
        if not message.tool_call_id:
            raise ValueError("Runtime tool messages must include tool_call_id.")
        return ToolMessage(
            content=message.content,
            tool_call_id=message.tool_call_id,
            additional_kwargs=additional_kwargs,
        )
    return AIMessage(
        content=message.content,
        additional_kwargs=additional_kwargs,
        tool_calls=[
            {
                "id": call.call_id,
                "name": call.name,
                "args": dict(call.arguments),
                "type": "tool_call",
            }
            for call in message.tool_calls
        ],
    )


__all__ = ["LangGraphRuntimeSessionStateAdapter"]
