"""Explicit structlog setup for development and production environments."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import structlog

from hostpilot.observability.events import AuditEvent, CostEvent

if TYPE_CHECKING:
    from hostpilot.config.settings import Settings


def configure_logging(settings: Settings) -> None:
    """Configure structured logs without adding request data implicitly."""
    renderer: structlog.typing.Processor
    if settings.environment.value == "development":
        renderer = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    level_number = logging.getLevelName(settings.log_level)
    if not isinstance(level_number, int):
        raise ValueError(f"Unsupported log level: {settings.log_level}")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level_number),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(*args: Any, **kwargs: Any) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger; callers must pass only approved metadata."""
    return cast(structlog.stdlib.BoundLogger, structlog.get_logger(*args, **kwargs))


class StructuredCostSink:
    """Write typed usage measurements without prompts or credentials."""

    def __init__(self) -> None:
        self.logger = get_logger("hostpilot.cost")

    def record(self, event: CostEvent) -> None:
        """Export one cost event containing only request and usage metadata."""
        self.logger.info("cost_event", **event.model_dump(mode="json"))


class StructuredAuditSink:
    """Write approved audit metadata as structured logs without payload contents."""

    def __init__(self) -> None:
        self.logger = get_logger("hostpilot.audit")

    def record(self, event: AuditEvent) -> None:
        """Export one typed audit event; event schemas exclude raw prompts and arguments."""
        self.logger.info("audit_event", **event.model_dump(mode="json"))


__all__ = ["StructuredAuditSink", "StructuredCostSink", "configure_logging", "get_logger"]
