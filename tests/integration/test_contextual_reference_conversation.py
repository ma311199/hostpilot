"""Verify clarification recovery and context isolation through the chat API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from hostpilot.api import create_app
from hostpilot.budget import RequestBudget
from hostpilot.config import Settings
from hostpilot.contracts import ConversationMessage, ModelTokenUsage
from hostpilot.models import ModelResponse
from hostpilot.observability import TraceEvent
from hostpilot.orchestration.runtime import ChatAgentResponse
from hostpilot.orchestration.skill_runtime import SkillEngineAgentAdapter
from hostpilot.routing.skill_selector import ModelProviderSkillSelector
from hostpilot.skills.runtime import (
    SkillEngine,
    SkillExecutionRequest,
    SkillExecutionResult,
    SkillExecutionStatus,
)
from hostpilot.skills.yaml_registry import YamlSkillCatalog, YamlSkillRegistry


class InMemoryChatRuntime:
    def __init__(self) -> None:
        self.messages_by_id: dict[str, list[ConversationMessage]] = {}

    async def read_messages(self, session_id: str) -> tuple[ConversationMessage, ...]:
        return tuple(self.messages_by_id.get(session_id, ()))

    async def respond(
        self,
        message: str,
        request_context,
    ) -> ChatAgentResponse:
        del message, request_context
        raise AssertionError("The selector-backed chat runtime should handle every turn.")

    async def record_turn(
        self,
        session_id: str,
        user_message: str,
        assistant_message: str,
    ) -> None:
        from hostpilot.contracts import MessageRole

        self.messages_by_id.setdefault(session_id, []).extend(
            (
                ConversationMessage(role=MessageRole.USER, content=user_message),
                ConversationMessage(role=MessageRole.ASSISTANT, content=assistant_message),
            )
        )


class SequenceRoutingModel:
    def __init__(self, clarified_detail: str) -> None:
        self.clarified_detail = clarified_detail
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> ModelResponse:
        self.requests.append(request)
        system_prompt = request.messages[0].content
        if "Resolve the user's reference" in system_prompt:
            assert self.clarified_detail in system_prompt
            assert "context-2" in system_prompt
            return ModelResponse(
                text=json.dumps(
                    {
                        "candidate_id": "context-2",
                        "confidence": 0.97,
                        "reason_code": "clarification_detail_match",
                    }
                ),
                token_usage=ModelTokenUsage(input_tokens=12, output_tokens=4, total_tokens=16),
            )
        return ModelResponse(
            text=json.dumps(
                {
                    "decision": "direct_answer",
                    "skill_id": None,
                    "payload": {},
                    "clarification_prompt": None,
                    "confidence": 0.99,
                    "candidate_scores": {},
                    "reason_code": "conversation_followup",
                }
            ),
            token_usage=ModelTokenUsage(input_tokens=10, output_tokens=3, total_tokens=13),
        )


class FakeSkillEngine:
    def __init__(self) -> None:
        self.requests: list[SkillExecutionRequest] = []

    async def execute(self, request: SkillExecutionRequest) -> SkillExecutionResult:
        self.requests.append(request)
        if request.skill_id == "builtin/clarify":
            prompt = request.payload["clarification_prompt"]
            return SkillExecutionResult(
                SkillExecutionStatus.CLARIFICATION,
                output={"message": prompt},
                clarification_prompt=prompt,
            )
        assert request.skill_id == "builtin/direct_answer"
        message = request.payload["message"]
        answer = "收到你补充的内容。" if "发布前回归测试方案" in message else "已整理为清单。"
        return SkillExecutionResult(
            SkillExecutionStatus.COMPLETED,
            output={"message": answer},
        )


class TrackingSelector(ModelProviderSkillSelector):
    def __init__(
        self,
        registry: YamlSkillRegistry,
        provider: SequenceRoutingModel,
        trace_sink: TraceRecorder,
    ) -> None:
        super().__init__(registry, provider, trace_sink=trace_sink)
        self.retrieval_queries: list[str] = []

    async def _retrieve_candidates(
        self,
        catalog: YamlSkillCatalog,
        message: str,
        budget: RequestBudget,
        markdown_candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        self.retrieval_queries.append(message)
        return await super()._retrieve_candidates(
            catalog,
            message,
            budget,
            markdown_candidates,
        )


class TraceRecorder:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def record(self, event: TraceEvent) -> None:
        self.events.append(event)


def test_chat_clarification_recovers_from_followup_and_stays_conversation_scoped(
    tmp_path: Path,
) -> None:
    clarified_detail = "发布前回归测试方案：执行完整回归，修复失败项，确认通过后再发布。"
    settings = Settings(data_dir=tmp_path / "data", memory_dir=tmp_path / "memory")
    chat_runtime = InMemoryChatRuntime()
    model = SequenceRoutingModel(clarified_detail)
    trace = TraceRecorder()
    selector = TrackingSelector(YamlSkillRegistry(tmp_path / "skills"), model, trace)
    skill_runtime = FakeSkillEngine()
    runtime = SkillEngineAgentAdapter(chat_runtime, cast(SkillEngine, skill_runtime), selector)

    with TestClient(create_app(settings, chat_agent_runtime_factory=lambda _: runtime)) as client:
        first = client.post("/api/v1/conversations", json={"title": "clarification recovery"})
        assert first.status_code == 201
        session_id = first.json()["session_id"]

        unclear = client.post(
            f"/api/v1/conversations/{session_id}/messages",
            json={"message": "把这段内容整理一下。"},
        )
        assert unclear.status_code == 200
        assert unclear.json()["route"] == "clarify"
        assert unclear.json()["assistant_message"]
        assert len(model.requests) == 0
        assert selector.retrieval_queries == []

        supplied = client.post(
            f"/api/v1/conversations/{session_id}/messages",
            json={"message": clarified_detail},
        )
        assert supplied.status_code == 200
        assert supplied.json()["route"] == "direct"

        followup = client.post(
            f"/api/v1/conversations/{session_id}/messages",
            json={"message": "把刚才那段整理成清单。"},
        )
        assert followup.status_code == 200
        assert followup.json()["route"] == "direct"
        assert followup.json()["assistant_message"] == "已整理为清单。"

        history = client.get(f"/api/v1/conversations/{session_id}/messages")
        isolated = client.post("/api/v1/conversations", json={"title": "isolated session"})
        isolated_id = isolated.json()["session_id"]
        isolated_clarification = client.post(
            f"/api/v1/conversations/{isolated_id}/messages",
            json={"message": "把这段内容整理一下。"},
        )
        isolated_history = client.get(f"/api/v1/conversations/{isolated_id}/messages")

    assert supplied.json()["assistant_message"] == "收到你补充的内容。"
    assert [item["content"] for item in history.json()["messages"]] == [
        "把这段内容整理一下。",
        unclear.json()["assistant_message"],
        clarified_detail,
        "收到你补充的内容。",
        "把刚才那段整理成清单。",
        "已整理为清单。",
    ]
    assert len(model.requests) == 3
    assert len(selector.retrieval_queries) == 2
    assert clarified_detail in selector.retrieval_queries[-1]
    assert len(skill_runtime.requests) == 4
    assert skill_runtime.requests[0].skill_id == "builtin/clarify"
    assert skill_runtime.requests[2].skill_id == "builtin/direct_answer"
    assert (
        skill_runtime.requests[2].request_context.snapshot.messages[-2].content == clarified_detail
    )
    assert isolated_clarification.status_code == 200
    assert isolated_clarification.json()["route"] == "clarify"
    assert len(model.requests) == 3
    assert [item["content"] for item in isolated_history.json()["messages"]] == [
        "把这段内容整理一下。",
        isolated_clarification.json()["assistant_message"],
    ]
    assert all(
        request.request_context.snapshot.session_id == isolated_id
        for request in skill_runtime.requests[3:]
    )

    reference_events = [
        event for event in trace.events if event.name == "skill.reference_resolution"
    ]
    assert len(reference_events) == 1
    assert reference_events[0].attributes["status"] == "matched"
    assert reference_events[0].attributes["candidate_id"] == "context-2"
    assert clarified_detail not in str(reference_events[0].model_dump())
