"""Tool registration, dispatch, and execution boundaries."""

from hostpilot.tools.executor import (
    AsyncConfirmationProvider,
    ToolDeniedError,
    ToolExecutionError,
    ToolExecutor,
)
from hostpilot.tools.filesystem import WorkspaceFileError, WorkspaceFiles
from hostpilot.tools.registry import (
    ConfirmationSummaryBuilder,
    ToolRegistration,
    ToolRegistrationError,
    ToolRegistry,
)

__all__ = [
    "AsyncConfirmationProvider",
    "ConfirmationSummaryBuilder",
    "ToolDeniedError",
    "ToolExecutionError",
    "WorkspaceFileError",
    "WorkspaceFiles",
    "ToolExecutor",
    "ToolRegistration",
    "ToolRegistrationError",
    "ToolRegistry",
]
