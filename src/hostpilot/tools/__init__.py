"""Tool registration, dispatch, and execution boundaries."""

from hostpilot.tools.executor import (
    AsyncConfirmationProvider,
    ToolDeniedError,
    ToolExecutionError,
    ToolExecutor,
)
from hostpilot.tools.facade import IdempotentToolInvoker, ToolFacade
from hostpilot.tools.filesystem import WorkspaceFileError, WorkspaceFiles, WorkspaceReadOnlyError
from hostpilot.tools.governance import (
    DefaultToolExecutorFactory,
    ToolExecutionScope,
    ToolExecutorFactory,
    ToolInvocationCoordinator,
)
from hostpilot.tools.registry import (
    ConfirmationSummaryBuilder,
    ToolRegistration,
    ToolRegistrationError,
    ToolRegistry,
)
from hostpilot.tools.routing import MCPToolRoutingTable, RemoteToolRouteTable

__all__ = [
    "AsyncConfirmationProvider",
    "ConfirmationSummaryBuilder",
    "ToolDeniedError",
    "ToolExecutionError",
    "ToolFacade",
    "IdempotentToolInvoker",
    "DefaultToolExecutorFactory",
    "ToolExecutionScope",
    "ToolExecutorFactory",
    "ToolInvocationCoordinator",
    "WorkspaceFileError",
    "WorkspaceFiles",
    "WorkspaceReadOnlyError",
    "ToolExecutor",
    "ToolRegistration",
    "ToolRegistrationError",
    "ToolRegistry",
    "MCPToolRoutingTable",
    "RemoteToolRouteTable",
]
