import asyncio
import json
from datetime import UTC, datetime

import pytest
from starlette.websockets import WebSocketDisconnect

import hostpilot.api.app as app_module
from hostpilot.api.app import WebSocketTaskCacheApprovalProvider
from hostpilot.runtime.context import TaskCacheApprovalRequest, TaskCacheDecision
from hostpilot.task_results import PlanTaskExecutionSummary, PlanTaskToolUsage


class FakeWebSocket:
    def __init__(self, messages: list[str]) -> None:
        self.messages = iter(messages)
        self.sent: list[dict[str, object]] = []

    async def send_json(self, value: dict[str, object]) -> None:
        self.sent.append(value)

    async def receive_text(self) -> str:
        return next(self.messages)


class WaitingWebSocket(FakeWebSocket):
    async def receive_text(self) -> str:
        await asyncio.Event().wait()
        return ""


class DisconnectingWebSocket(FakeWebSocket):
    async def receive_text(self) -> str:
        raise WebSocketDisconnect(code=1000)


def test_websocket_task_cache_provider_sends_result_and_returns_explicit_decision() -> None:
    websocket = FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "plan.task.cache.decision",
                    "request_id": "cache-approval-1",
                    "decision": "reuse",
                }
            )
        ]
    )
    provider = WebSocketTaskCacheApprovalProvider(websocket)  # type: ignore[arg-type]
    request = TaskCacheApprovalRequest(
        request_id="cache-approval-1",
        task_id="inspect",
        description="Inspect README",
        source_turn_id="turn-previous",
        finished_at=datetime(2026, 11, 22, tzinfo=UTC),
        output={"summary": "HostPilot"},
        execution_summary=PlanTaskExecutionSummary(
            tools=(PlanTaskToolUsage("write_text_file", 1, "irreversible"),),
            confirmation_decisions=("approved",),
        ),
    )

    decision = asyncio.run(provider.decide(request))

    assert decision is TaskCacheDecision.REUSE
    assert websocket.sent == [
        {
            "type": "plan.task.cache.request",
            "data": {
                "request_id": "cache-approval-1",
                "task_id": "inspect",
                "description": "Inspect README",
                "source_turn_id": "turn-previous",
                "finished_at": "2026-11-22T00:00:00+00:00",
                "output": {"summary": "HostPilot"},
                "execution_summary": {
                    "tools": [
                        {
                            "tool_id": "write_text_file",
                            "call_count": 1,
                            "side_effect_level": "irreversible",
                        }
                    ],
                    "confirmation_decisions": ["approved"],
                },
            },
        }
    ]


def test_websocket_task_cache_provider_ignores_unmatched_request_ids() -> None:
    websocket = FakeWebSocket(
        [
            json.dumps(
                {
                    "type": "plan.task.cache.decision",
                    "request_id": "different-request",
                    "decision": "reuse",
                }
            ),
            json.dumps(
                {
                    "type": "plan.task.cache.decision",
                    "request_id": "cache-approval-2",
                    "decision": "execute",
                }
            ),
        ]
    )
    provider = WebSocketTaskCacheApprovalProvider(websocket)  # type: ignore[arg-type]
    request = TaskCacheApprovalRequest(
        request_id="cache-approval-2",
        task_id="inspect",
        description="Inspect README",
        source_turn_id="turn-previous",
        finished_at=datetime(2026, 11, 22, tzinfo=UTC),
        output="result",
    )

    assert asyncio.run(provider.decide(request)) is TaskCacheDecision.EXECUTE


def test_websocket_task_cache_timeout_returns_no_decision(monkeypatch) -> None:
    monkeypatch.setattr(app_module, "CHAT_CONFIRMATION_TIMEOUT_SECONDS", 0)
    websocket = WaitingWebSocket([])
    provider = WebSocketTaskCacheApprovalProvider(websocket)  # type: ignore[arg-type]
    request = TaskCacheApprovalRequest(
        request_id="cache-timeout",
        task_id="inspect",
        description="Inspect README",
        source_turn_id="turn-previous",
        finished_at=datetime(2026, 11, 22, tzinfo=UTC),
        output="result",
    )

    assert asyncio.run(provider.decide(request)) is None


def test_websocket_task_cache_disconnect_cancels_waiting_turn() -> None:
    websocket = DisconnectingWebSocket([])
    provider = WebSocketTaskCacheApprovalProvider(websocket)  # type: ignore[arg-type]
    request = TaskCacheApprovalRequest(
        request_id="cache-disconnect",
        task_id="inspect",
        description="Inspect README",
        source_turn_id="turn-previous",
        finished_at=datetime(2026, 11, 22, tzinfo=UTC),
        output="result",
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.decide(request))
