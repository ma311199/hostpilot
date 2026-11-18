import asyncio

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ExecutionEnvironment,
    TimeoutProfile,
    ToolDescriptor,
    ToolType,
    TrustLevel,
)
from hostpilot.observability import AuditEvent
from hostpilot.tools.executor import ToolDeniedError
from hostpilot.tools.governance import DefaultToolExecutorFactory
from hostpilot.tools.registry import ToolRegistration, ToolRegistry


def _budget() -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100,
                tool_calls=2,
                retrieval_calls=1,
                timeout_seconds=5,
            )
        )
    )


class _RecordingScope:
    def __init__(self, result: str) -> None:
        self.result = result
        self.calls: list[dict[str, object]] = []

    async def execute(self, name, arguments, **options):
        self.calls.append({"name": name, "arguments": arguments, **options})
        return self.result


def test_default_coordinator_uses_primary_scope_and_forwards_execution_context() -> None:
    async def scenario() -> None:
        primary_scope = _RecordingScope("primary-result")
        compensation_scope = _RecordingScope("compensation-result")
        confirmation_provider = object()
        granted_capabilities = frozenset({"workspace.write"})
        budget = _budget()
        invocation_arguments: list[dict[str, str]] = []

        async def invoke(arguments: dict[str, str]) -> str:
            invocation_arguments.append(arguments)
            return "invoked"

        coordinator = DefaultToolExecutorFactory().create_coordinator(
            primary_scope, compensation_scope
        )

        result = await coordinator.execute(
            "write_text_file",
            {"path": "notes.txt"},
            budget=budget,
            granted_capabilities=granted_capabilities,
            confirmation_provider=confirmation_provider,
            invoke=invoke,
        )

        assert result == "primary-result"
        assert primary_scope.calls == [
            {
                "name": "write_text_file",
                "arguments": {"path": "notes.txt"},
                "budget": budget,
                "granted_capabilities": granted_capabilities,
                "confirmation_provider": confirmation_provider,
                "_invoke": invoke,
            }
        ]
        assert compensation_scope.calls == []
        assert invocation_arguments == []

    asyncio.run(scenario())


def test_default_coordinator_uses_compensation_scope() -> None:
    async def scenario() -> None:
        primary_scope = _RecordingScope("primary-result")
        compensation_scope = _RecordingScope("restored")
        confirmation_provider = object()
        budget = _budget()
        coordinator = DefaultToolExecutorFactory().create_coordinator(
            primary_scope, compensation_scope
        )

        result = await coordinator.execute_compensation(
            "_restore_existing_text_file",
            {"token": "snapshot-1"},
            budget=budget,
            confirmation_provider=confirmation_provider,
        )

        assert result == "restored"
        assert compensation_scope.calls == [
            {
                "name": "_restore_existing_text_file",
                "arguments": {"token": "snapshot-1"},
                "budget": budget,
                "confirmation_provider": confirmation_provider,
            }
        ]
        assert primary_scope.calls == []

    asyncio.run(scenario())


def test_default_factory_returns_scope_over_provider_registry_and_executes() -> None:
    async def scenario() -> None:
        registry = ToolRegistry()
        audit_events: list[AuditEvent] = []
        calls: list[dict[str, str]] = []

        async def handler(arguments):
            calls.append(arguments)
            return "ok"

        registry.register(
            ToolRegistration(
                descriptor=ToolDescriptor(
                    name="run_in_sandbox",
                    description="Run a sandboxed test operation",
                    tool_type=ToolType.ACTION,
                    trust_level=TrustLevel.HOST,
                    execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                    timeout_profile=TimeoutProfile(timeout_seconds=5),
                ),
                validate_arguments=lambda arguments: arguments,
                handler=handler,
                verify_result=lambda _result, _arguments: None,
            )
        )

        def record_audit(event: AuditEvent) -> None:
            audit_events.append(event)

        scope = DefaultToolExecutorFactory().create_scope(
            registry,
            sandboxed_environments=frozenset({ExecutionEnvironment.NATIVE_SANDBOX}),
            sandboxed_tools=frozenset({"run_in_sandbox"}),
            audit=record_audit,
            trace_sink=None,
        )

        assert scope.registry is registry
        assert await scope.execute("run_in_sandbox", {}, budget=_budget()) == "ok"
        assert calls == [{}]
        assert [event.action for event in audit_events] == ["tool.execute"]

    asyncio.run(scenario())


def test_default_scope_denies_tool_when_sandbox_is_not_available() -> None:
    async def scenario() -> None:
        registry = ToolRegistry()
        calls: list[str] = []

        async def handler(_arguments):
            calls.append("called")
            return "unexpected"

        registry.register(
            ToolRegistration(
                descriptor=ToolDescriptor(
                    name="sandboxed_action",
                    description="Requires sandbox",
                    tool_type=ToolType.ACTION,
                    trust_level=TrustLevel.HOST,
                    execution_env=ExecutionEnvironment.NATIVE_SANDBOX,
                    timeout_profile=TimeoutProfile(timeout_seconds=5),
                ),
                validate_arguments=lambda arguments: arguments,
                handler=handler,
                verify_result=lambda _result, _arguments: None,
            )
        )
        scope = DefaultToolExecutorFactory().create_scope(
            registry,
            sandboxed_environments=frozenset(),
            sandboxed_tools=frozenset(),
            audit=lambda _event: None,
            trace_sink=None,
        )

        with pytest.raises(ToolDeniedError):
            await scope.execute("sandboxed_action", {}, budget=_budget())
        assert calls == []

    asyncio.run(scenario())
