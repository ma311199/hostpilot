"""Exercise explicit PlanRun recovery across SQLite-backed Host restarts."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from hostpilot.api import create_app
from hostpilot.budget import RequestBudget
from hostpilot.config import Settings
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    ExecutionEnvironment,
    ModelTokenUsage,
    TimeoutProfile,
    ToolDescriptor,
    ToolRetryClass,
    ToolType,
    TrustLevel,
)
from hostpilot.models import ModelResponse, ModelToolCall
from hostpilot.orchestration.runtime import ChatAgentResponse
from hostpilot.persistence import SQLiteTaskStore, SQLiteTurnStore
from hostpilot.runtime import RequestContext, TaskLifecycleManager
from hostpilot.runtime.execution import stable_tool_invocation_identity
from hostpilot.skills.model_plan import GovernedPlanTaskExecutor
from hostpilot.skills.orchestration import (
    BlackboardSnapshot,
    PlanExecutionType,
    PlanTask,
    PlanTaskCall,
)
from hostpilot.skills.runtime import (
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
)


class DeterministicRecoveryRuntime:
    def __init__(self) -> None:
        self.resume_calls: list[tuple[str, Any]] = []

    async def read_messages(self, session_id: str) -> tuple[()]:
        del session_id
        return ()

    async def respond(self, message: str, request_context) -> ChatAgentResponse:
        del message, request_context
        raise AssertionError("This recovery test must not start a new chat turn.")

    async def resume_plan_run(self, plan_run_id: str, request_context) -> SkillExecutionResult:
        self.resume_calls.append((plan_run_id, request_context))
        lifecycle = request_context.task_lifecycle
        assert lifecycle is not None
        stored = await lifecycle.get_plan_run(plan_run_id)
        assert stored is not None
        await lifecycle.update_plan_run(
            plan_run_id,
            state="completed",
            snapshot=stored.snapshot,
            updated_at=datetime.now(UTC),
        )
        return SkillExecutionResult(SkillExecutionStatus.COMPLETED, output={"recovered": True})

    async def record_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        del session_id, user_message, assistant_message


def test_plan_run_recovery_survives_host_restart_and_requires_user_action(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    first_runtime = DeterministicRecoveryRuntime()

    with TestClient(
        create_app(settings, chat_agent_runtime_factory=lambda _: first_runtime)
    ) as client:
        conversation = client.post("/api/v1/conversations", json={}).json()
        session_id = conversation["session_id"]
        tasks = client.app.state.runtime_host.context.tasks
        now = datetime.now(UTC)

        async def create_interrupted_plan() -> None:
            await tasks.create_plan_run(
                plan_run_id="plan-restart",
                turn_id="turn-before-restart",
                session_id=session_id,
                goal="Deliver the report",
                snapshot={
                    "version": 1,
                    "plan": {
                        "tasks": [
                            {"task_id": "prepare", "description": "Prepare report"},
                            {"task_id": "deliver", "description": "Deliver report"},
                        ]
                    },
                    "states": [
                        {
                            "task_id": "prepare",
                            "status": "completed",
                            "output": {"report": "ready"},
                        },
                        {"task_id": "deliver", "status": "running", "output": None},
                    ],
                },
                started_at=now,
            )
            await tasks.prepare_invocation(
                invocation_id="invocation-before-restart",
                session_id=session_id,
                turn_id="turn-before-restart",
                plan_run_id="plan-restart",
                task_run_id="task-run-before-restart",
                task_id="deliver",
                call_index=1,
                tool_id="send_message",
                retry_class="non_idempotent_write",
                idempotency_key=None,
                request_fingerprint="private-fingerprint",
                arguments_json='{"message":"private message"}',
                idempotency_window_seconds=None,
            )
            await tasks.update_plan_run(
                "plan-restart",
                state="interrupted",
                snapshot={
                    "version": 1,
                    "plan": {
                        "tasks": [
                            {"task_id": "prepare", "description": "Prepare report"},
                            {"task_id": "deliver", "description": "Deliver report"},
                        ]
                    },
                    "states": [
                        {
                            "task_id": "prepare",
                            "status": "completed",
                            "output": {"report": "ready"},
                        },
                        {"task_id": "deliver", "status": "running", "output": None},
                    ],
                },
                updated_at=now + timedelta(seconds=1),
            )

        asyncio.run(create_interrupted_plan())

    second_runtime = DeterministicRecoveryRuntime()
    with TestClient(
        create_app(settings, chat_agent_runtime_factory=lambda _: second_runtime)
    ) as client:
        recoverable = client.get(f"/api/v1/conversations/{session_id}/plan-runs/recoverable")
        assert recoverable.status_code == 200
        assert recoverable.json()[0]["tasks"][0]["output"] == {"report": "ready"}
        assert "private-fingerprint" not in recoverable.text
        assert "private message" not in recoverable.text
        assert second_runtime.resume_calls == []

        resumed = client.post(f"/api/v1/conversations/{session_id}/plan-runs/plan-restart/resume")

    assert resumed.status_code == 200
    assert resumed.json()["state"] == "completed"
    assert resumed.json()["status"] == "completed"
    assert len(second_runtime.resume_calls) == 1
    plan_run_id, request_context = second_runtime.resume_calls[0]
    assert plan_run_id == "plan-restart"
    assert request_context.plan_run_id == "plan-restart"
    assert request_context.snapshot.session_id == session_id


def test_plan_idempotent_tool_replays_same_key_after_sqlite_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "runtime.sqlite3"
    plan_run_id = "plan-idempotent-recovery"
    session_id = "session-idempotent-recovery"
    task_id = "send_invoice"
    tool_id = "send_invoice"
    arguments = {"invoice_id": "invoice-42", "amount": 125}
    now = datetime.now(UTC)
    idempotency_key, fingerprint = stable_tool_invocation_identity(
        plan_run_id=plan_run_id,
        task_id=task_id,
        call_index=1,
        tool_id=tool_id,
        arguments=arguments,
        business_fields=("invoice_id",),
    )
    external_effects = {idempotency_key: (arguments, {"invoice_id": "invoice-42", "sent": True})}
    side_effect_attempts = {"count": 1}

    async def prepare_interrupted_invocation() -> None:
        turns = SQLiteTurnStore(database_path)
        await turns.initialize()
        await turns.register_host(
            "host-before-restart",
            now=now,
            lease_expires_at=now + timedelta(seconds=30),
        )
        tasks = SQLiteTaskStore(database_path)
        await tasks.create_plan_run(
            plan_run_id=plan_run_id,
            turn_id="turn-before-restart",
            session_id=session_id,
            runtime_host_id="host-before-restart",
            goal="Send invoice 42",
            snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
            started_at=now,
        )
        await tasks.record_tool_invocation(
            invocation_id=f"invocation:{idempotency_key}",
            plan_run_id=plan_run_id,
            task_run_id="task-run-before-restart",
            task_id=task_id,
            call_index=1,
            tool_id=tool_id,
            retry_class="idempotent_write",
            idempotency_key=idempotency_key,
            request_fingerprint=fingerprint,
            arguments_json='{"amount":125,"invoice_id":"invoice-42"}',
            created_at=now,
            runtime_host_id="host-before-restart",
            idempotency_window_seconds=3600,
        )
        await tasks.update_plan_run(
            plan_run_id,
            runtime_host_id="host-before-restart",
            state="interrupted",
            snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
            updated_at=now + timedelta(seconds=1),
        )

    asyncio.run(prepare_interrupted_invocation())

    class ReplayModel:
        def __init__(self) -> None:
            self.calls = 0

        async def complete(self, request) -> ModelResponse:
            del request
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    text="",
                    tool_calls=(ModelToolCall("send-1", tool_id, arguments),),
                    token_usage=ModelTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                )
            return ModelResponse(
                text="Invoice sent.",
                token_usage=ModelTokenUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    class IdempotentToolProvider:
        def __init__(self) -> None:
            self.replays: list[tuple[str, dict[str, Any]]] = []

        async def call_tool(self, name, call_arguments, *, budget, confirmation_provider=None):
            raise AssertionError(f"Idempotent call bypassed the keyed Provider: {name}")

        async def call_tool_with_idempotency_key(
            self,
            name,
            call_arguments,
            *,
            idempotency_key,
            budget,
            confirmation_provider=None,
        ):
            del confirmation_provider
            budget.consume_tool_call()
            self.replays.append((idempotency_key, dict(call_arguments)))
            if idempotency_key in external_effects:
                saved_arguments, saved_result = external_effects[idempotency_key]
                assert saved_arguments == call_arguments
                return saved_result
            assert name == tool_id
            side_effect_attempts["count"] += 1
            result = {"invoice_id": call_arguments["invoice_id"], "sent": True}
            external_effects[idempotency_key] = (dict(call_arguments), result)
            return result

    async def resume_with_reopened_store():
        resume_now = now + timedelta(seconds=2)
        turns = SQLiteTurnStore(database_path)
        await turns.register_host(
            "host-after-restart",
            now=resume_now,
            lease_expires_at=resume_now + timedelta(seconds=30),
        )
        tasks = SQLiteTaskStore(database_path)
        lifecycle = TaskLifecycleManager(store=tasks, runtime_host_id="host-after-restart")
        assert await lifecycle.claim_plan_run(plan_run_id) is True
        await lifecycle.update_plan_run(
            plan_run_id,
            state="running",
            snapshot={"version": 1, "plan": {"tasks": []}, "states": []},
            updated_at=resume_now,
        )
        request_context = RequestContext(
            snapshot=ContextSnapshot(request_id="turn-after-restart", session_id=session_id),
            budget=RequestBudget.start(
                BudgetContext(
                    limits=BudgetLimits(
                        total_tokens=100,
                        tool_calls=5,
                        retrieval_calls=2,
                        timeout_seconds=30,
                    )
                )
            ),
            task_lifecycle=lifecycle,
            plan_run_id=plan_run_id,
        )
        planned_request = SkillExecutionRequest(
            skill_id="builtin/orchestrator_plan",
            payload={},
            request_context=request_context,
        )
        descriptor = ToolDescriptor(
            name=tool_id,
            description="Send one invoice.",
            access_summary="发送一张发票",
            input_schema={
                "type": "object",
                "properties": {
                    "invoice_id": {"type": "string"},
                    "amount": {"type": "integer"},
                },
                "required": ["invoice_id", "amount"],
                "additionalProperties": False,
            },
            tool_type=ToolType.ACTION,
            trust_level=TrustLevel.HOST,
            execution_env=ExecutionEnvironment.HOST,
            timeout_profile=TimeoutProfile(timeout_seconds=5),
            retry_class=ToolRetryClass.IDEMPOTENT_WRITE,
            idempotency_window_seconds=3600,
            rejects_idempotency_body_conflict=True,
            idempotency_business_fields=("invoice_id",),
        )
        provider = IdempotentToolProvider()
        model = ReplayModel()
        result = await GovernedPlanTaskExecutor(
            model,
            skills=None,
            tool_provider=provider,
            tool_descriptors={tool_id: descriptor},
        ).execute_task(
            PlanTaskCall(
                request=planned_request,
                task=PlanTask(
                    task_id=task_id,
                    description="Send invoice 42",
                    execution_type=PlanExecutionType.TOOL_TASK,
                ),
                dependencies={},
                blackboard=BlackboardSnapshot(()),
                idempotency_key="plan-task-invoice-42",
            )
        )
        return result, provider, model

    result, provider, model = asyncio.run(resume_with_reopened_store())

    assert result.succeeded is True
    assert provider.replays == [(idempotency_key, arguments)]
    assert model.calls == 2
    assert side_effect_attempts["count"] == 1
