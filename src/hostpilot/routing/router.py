"""Request routing contracts and the conservative Phase 1 fallback router."""

from __future__ import annotations

from typing import Protocol

from hostpilot.contracts import ContextSnapshot, Route, RouteDecision


class Router(Protocol):
    """Select a supported execution path without granting tool permissions."""

    def decide(self, message: str, context: ContextSnapshot) -> RouteDecision: ...


class DirectRouter:
    """Route to the configured conversational runtime until classifiers are added.

    Tool selection remains disabled by default. Callers can inject a classifier, while
    authorization remains a separate Policy Engine decision.
    """

    def decide(self, message: str, context: ContextSnapshot) -> RouteDecision:
        """Return a direct response decision for the Phase 1 chat fallback."""
        del message
        return RouteDecision(
            request_id=context.request_id,
            route=Route.DIRECT,
            reason_code="direct_fallback",
        )


class CommandRouter:
    """Route explicit local file commands and keep all other messages conversational."""

    def __init__(self, fallback: Router | None = None) -> None:
        self.fallback = fallback or DirectRouter()

    def decide(self, message: str, context: ContextSnapshot) -> RouteDecision:
        """Recognize `/read` and `/write` commands without guessing user intent."""
        command, separator, remainder = message.strip().partition(" ")
        if command == "/read":
            path = remainder.strip()
            if not path:
                return RouteDecision(
                    request_id=context.request_id,
                    route=Route.CLARIFY,
                    reason_code="read_path_required",
                    clarification_prompt="请提供工作区内的相对文件路径，例如：/read README.md",
                )
            return RouteDecision(
                request_id=context.request_id,
                route=Route.TOOL_CALL,
                reason_code="explicit_read_command",
                tool_name="read_text_file",
                tool_arguments={"path": path},
            )
        if command == "/write":
            path, newline, content = remainder.partition("\n")
            path = path.strip()
            if not separator or not newline or not path or not content:
                return RouteDecision(
                    request_id=context.request_id,
                    route=Route.CLARIFY,
                    reason_code="write_path_and_content_required",
                    clarification_prompt=(
                        "请按 `/write 相对路径` 后换行并提供内容，"
                        "例如：/write notes.txt\n待写入内容"
                    ),
                )
            return RouteDecision(
                request_id=context.request_id,
                route=Route.TOOL_CALL,
                reason_code="explicit_write_command",
                tool_name="write_text_file",
                tool_arguments={"path": path, "content": content},
            )
        return self.fallback.decide(message, context)


__all__ = ["CommandRouter", "DirectRouter", "Router"]
