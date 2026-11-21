"""Application runtime host and in-process plugin lifecycle contracts."""

from hostpilot.runtime.chat_prompt import RuntimeChatPromptBuilder
from hostpilot.runtime.chat_state import RuntimeSessionStatePort
from hostpilot.runtime.context import RequestContext
from hostpilot.runtime.execution import (
    RuntimeExecutionLoop,
    RuntimeExecutionMessageSink,
    RuntimeExecutionRequest,
    RuntimeExecutionResult,
    RuntimeExecutionStatus,
    RuntimeModelPort,
    RuntimeSkillPort,
    RuntimeToolOutcomePort,
    RuntimeToolPort,
    RuntimeToolReplay,
    runtime_tool_call_key,
)
from hostpilot.runtime.execution_loop import (
    DefaultRuntimeExecutionLoop,
    RuntimeToolProposalError,
)
from hostpilot.runtime.host import (
    RuntimeContext,
    RuntimeHost,
    RuntimeHostState,
    RuntimePlugin,
    RuntimePluginError,
    RuntimePluginSpec,
    RuntimeServiceRegistry,
)
from hostpilot.runtime.lifecycle import (
    AgentLifecycleManager,
    SessionLifecycleManager,
    TaskLifecycleManager,
    TaskRecord,
    TaskRunHandle,
    TaskState,
    ToolOutcomeLifecycleManager,
    TurnLifecycleManager,
    TurnRecord,
    TurnState,
)

__all__ = [
    "RuntimeContext",
    "RuntimeHost",
    "RuntimeHostState",
    "RuntimePlugin",
    "RuntimePluginError",
    "RuntimePluginSpec",
    "RuntimeServiceRegistry",
    "RequestContext",
    "RuntimeChatPromptBuilder",
    "RuntimeSessionStatePort",
    "RuntimeExecutionLoop",
    "RuntimeExecutionMessageSink",
    "DefaultRuntimeExecutionLoop",
    "RuntimeExecutionRequest",
    "RuntimeExecutionResult",
    "AgentLifecycleManager",
    "SessionLifecycleManager",
    "RuntimeExecutionStatus",
    "RuntimeModelPort",
    "RuntimeSkillPort",
    "RuntimeToolPort",
    "RuntimeToolReplay",
    "RuntimeToolOutcomePort",
    "runtime_tool_call_key",
    "RuntimeToolProposalError",
    "TurnLifecycleManager",
    "TurnRecord",
    "TurnState",
    "TaskLifecycleManager",
    "ToolOutcomeLifecycleManager",
    "TaskRecord",
    "TaskRunHandle",
    "TaskState",
]
