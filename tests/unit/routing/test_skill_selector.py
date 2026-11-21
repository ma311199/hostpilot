import asyncio
import json
from pathlib import Path
from typing import Any

import yaml

from hostpilot.budget import RequestBudget
from hostpilot.contracts import (
    BudgetContext,
    BudgetLimits,
    ContextSnapshot,
    ConversationMessage,
    MessageRole,
    ModelTokenUsage,
)
from hostpilot.models import ModelResponse
from hostpilot.observability import TraceContext, TraceEvent, trace_context_scope
from hostpilot.routing.models import RerankBatch
from hostpilot.routing.skill_selector import ModelProviderSkillSelector
from hostpilot.runtime import RequestContext
from hostpilot.skills.registry import SkillRegistry
from hostpilot.skills.yaml_registry import YamlSkillRegistry

PACKAGED_SKILLS = Path(__file__).resolve().parents[3] / "src/hostpilot/skill_packages"


def budget() -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100,
                tool_calls=5,
                retrieval_calls=2,
                timeout_seconds=30,
            )
        )
    )


def select(selector, message, context, *, budget):
    return selector.select(message, RequestContext(context, budget))


def package_manifest() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "meta": {
            "skill_id": "business/meeting-notes",
            "name": "Meeting notes",
            "version": "1.0.0",
        },
        "intent_summary": "Summarize meeting notes and extract action items.",
        "tags": ["meeting", "minutes"],
        "examples": ["summarize these meeting notes"],
        "input_schema": {
            "type": "object",
            "properties": {
                "notes": {"type": "string", "title": "Meeting notes"},
                "audience": {"type": "string", "title": "Audience"},
            },
            "required": ["notes", "audience"],
            "additionalProperties": False,
        },
        "output_schema": {"type": "object"},
        "side_effect_level": "read_only",
        "execution": {"kind": "steps", "steps": [{"step_id": "draft", "action": "draft"}]},
    }


def write_package(root: Path) -> None:
    package = root / "meeting-notes"
    package.mkdir()
    (package / "skill.yaml").write_text(
        yaml.safe_dump(package_manifest(), sort_keys=False), encoding="utf-8"
    )


class FakeChatModel:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.request = None

    async def complete(self, request):
        self.request = request
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def response(
    decision: str,
    *,
    skill_id: str | None = None,
    payload: dict[str, Any] | None = None,
    clarification_prompt: str | None = None,
    confidence: float = 0.99,
    covers_full_request: bool = True,
    candidate_scores: dict[str, float] | None = None,
) -> ModelResponse:
    return ModelResponse(
        text=json.dumps(
            {
                "decision": decision,
                "skill_id": skill_id,
                "payload": payload or {},
                "clarification_prompt": clarification_prompt,
                "covers_full_request": covers_full_request,
                "confidence": confidence,
                "candidate_scores": candidate_scores
                if candidate_scores is not None
                else ({skill_id: confidence} if skill_id else {}),
                "reason_code": "model_choice",
            }
        ),
        token_usage=ModelTokenUsage(input_tokens=7, output_tokens=3, total_tokens=10),
    )


def test_selector_routes_direct_and_accounts_classifier_usage(tmp_path: Path) -> None:
    model = FakeChatModel(response("direct_answer"))
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)
    request_budget = budget()

    selection = asyncio.run(
        select(
            selector,
            "Explain quantum computing",
            ContextSnapshot(request_id="request-1"),
            budget=request_budget,
        )
    )

    assert selection.skill_id == "builtin/direct_answer"
    assert selection.payload == {"message": "Explain quantum computing"}
    assert selection.token_usage is not None
    assert request_budget.snapshot().usage.total_tokens == 10
    assert "builtin/direct_answer" in str(model.request.messages[0].content)
    assert "Text-only constraints govern execution" in str(model.request.messages[0].content)


def test_selector_traces_business_scores_when_classifier_selects_direct(tmp_path: Path) -> None:
    class RecordingTraceSink:
        def __init__(self) -> None:
            self.events: list[TraceEvent] = []

        def record(self, event: TraceEvent) -> None:
            self.events.append(event)

    trace_sink = RecordingTraceSink()
    model = FakeChatModel(
        response(
            "direct_answer",
            confidence=0.99,
            candidate_scores={"business/code-review-check": 0.91},
        )
    )
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path, additional_roots=(PACKAGED_SKILLS,)),
        model,
        trace_sink=trace_sink,
    )

    with trace_context_scope(TraceContext()):
        selection = asyncio.run(
            select(
                selector,
                "Review this code: def check(value): return eval(value)",
                ContextSnapshot(request_id="classification-trace"),
                budget=budget(),
            )
        )

    classification = next(
        event for event in trace_sink.events if event.name == "skill.classification"
    )
    assert selection.skill_id == "builtin/orchestrator_plan"
    assert selection.reason_code == "skill_routing_evaluation_failed"
    system_prompt = model.request.messages[0].content
    assert "place any supplied snippet or diff in its code_or_diff field" in system_prompt
    assert classification.attributes == {
        "classifier_decision": "direct_answer",
        "selected_skill_id": "",
        "decision_confidence": 0.99,
        "scored_candidate_count": 1,
        "top_candidate_id": "business/code-review-check",
        "top_confidence": 0.91,
        "confidence_margin": 0.91,
    }


def test_selector_parses_json_from_plain_chat_completion(tmp_path: Path) -> None:
    class PlainChatModel:
        async def complete(self, request):
            self.request = request
            return ModelResponse(
                text=json.dumps(
                    {
                        "decision": "direct_answer",
                        "payload": {},
                        "confidence": 0.98,
                        "reason_code": "ordinary_question",
                    }
                ),
                token_usage=ModelTokenUsage(input_tokens=11, output_tokens=5, total_tokens=16),
            )

    model = PlainChatModel()
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)
    request_budget = budget()

    selection = asyncio.run(
        select(
            selector,
            "What does photosynthesis mean?",
            ContextSnapshot(request_id="request-plain"),
            budget=request_budget,
        )
    )

    assert selection.skill_id == "builtin/direct_answer"
    assert selection.payload == {"message": "What does photosynthesis mean?"}
    assert request_budget.snapshot().usage.total_tokens == 16


def test_unparseable_query_is_clarified_before_skill_retrieval(tmp_path: Path) -> None:
    model = FakeChatModel(response("direct_answer"))
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)

    async def retrieval_must_not_run(*args, **kwargs):
        raise AssertionError("Unparseable requests must skip retrieval.")

    selector._retrieve_candidates = retrieval_must_not_run
    selection = asyncio.run(
        select(selector, "???", ContextSnapshot(request_id="unparseable"), budget=budget())
    )

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "query_not_parseable"
    assert model.request is None


def test_explicitly_requested_unavailable_skill_clarifies_before_model_or_plan(
    tmp_path: Path,
) -> None:
    model = FakeChatModel(response("orchestrator_plan"))
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)

    selection = asyncio.run(
        select(
            selector,
            "请调用 `business/payroll-adjust` Skill，把小王工资改成 50000 元并保存。",
            ContextSnapshot(request_id="unknown-explicit-skill"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "requested_skill_unavailable"
    assert "business/payroll-adjust" in selection.payload["clarification_prompt"]
    assert model.request is None


def test_reference_without_context_candidates_clarifies_before_retrieval(tmp_path: Path) -> None:
    model = FakeChatModel(response("direct_answer"))
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)

    async def retrieval_must_not_run(*args, **kwargs):
        raise AssertionError("Unresolved references must skip Skill retrieval.")

    selector._retrieve_candidates = retrieval_must_not_run
    selection = asyncio.run(
        select(
            selector,
            "把这段内容整理一下。",
            ContextSnapshot(request_id="empty-reference-context"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "query_context_ambiguous"
    assert model.request is None


def test_reference_is_resolved_only_from_memory_candidates_before_skill_retrieval(
    tmp_path: Path,
) -> None:
    class SequenceModel:
        def __init__(self) -> None:
            self.requests = []

        async def complete(self, request):
            self.requests.append(request)
            if len(self.requests) == 1:
                assert "long_term_memory" in str(request.messages[0].content)
                return ModelResponse(
                    text=json.dumps(
                        {
                            "candidate_id": "context-1",
                            "confidence": 0.96,
                            "reason_code": "matching_memory_note",
                        }
                    ),
                    token_usage=ModelTokenUsage(input_tokens=9, output_tokens=3, total_tokens=12),
                )
            assert "指代上下文（long_term_memory）" in request.messages[-1].content
            assert "项目复盘：上线前需要完成回归测试" in request.messages[-1].content
            return response("direct_answer")

    model = SequenceModel()
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)
    context = ContextSnapshot(
        request_id="memory-reference",
        long_term_memory="项目复盘：上线前需要完成回归测试，并记录失败用例。",
    )

    selection = asyncio.run(
        select(selector, "把这段关于回归测试的内容整理一下。", context, budget=budget())
    )

    assert selection.skill_id == "builtin/direct_answer"
    assert len(model.requests) == 2
    assert selection.token_usage == ModelTokenUsage(
        input_tokens=16, output_tokens=6, total_tokens=22
    )


def test_generic_reference_does_not_match_unique_unrelated_memory_candidate(
    tmp_path: Path,
) -> None:
    model = FakeChatModel(response("direct_answer"))
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)
    selector._retrieve_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("Unanchored memory references must skip Skill retrieval.")
    )
    context = ContextSnapshot(
        request_id="unanchored-memory-reference",
        long_term_memory="阳台植物每周浇水两次，春季修剪枯叶。",
    )

    selection = asyncio.run(select(selector, "把这段内容整理一下。", context, budget=budget()))

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "query_context_ambiguous"
    assert model.request is None


def test_reference_low_confidence_match_clarifies_without_skill_retrieval(tmp_path: Path) -> None:
    class LowConfidenceModel:
        async def complete(self, request):
            return ModelResponse(
                text=json.dumps(
                    {
                        "candidate_id": "context-1",
                        "confidence": 0.52,
                        "reason_code": "uncertain",
                    }
                ),
                token_usage=ModelTokenUsage(input_tokens=9, output_tokens=3, total_tokens=12),
            )

    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), LowConfidenceModel())
    selector._retrieve_candidates = lambda *args, **kwargs: (_ for _ in ()).throw(
        AssertionError("Unresolved references must skip Skill retrieval.")
    )
    context = ContextSnapshot(
        request_id="low-confidence-reference",
        long_term_memory="项目复盘：上线前需要完成回归测试。",
    )

    selection = asyncio.run(
        select(selector, "把这段关于回归测试的内容整理一下。", context, budget=budget())
    )

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "query_reference_not_resolved"
    assert selection.token_usage == ModelTokenUsage(
        input_tokens=9, output_tokens=3, total_tokens=12
    )


def test_reference_no_match_clarifies_instead_of_choosing_a_candidate(tmp_path: Path) -> None:
    class NoMatchModel:
        async def complete(self, request):
            assert "context-1" in request.messages[0].content
            return ModelResponse(
                text=json.dumps(
                    {
                        "candidate_id": None,
                        "confidence": 0.99,
                        "reason_code": "no_candidate_matches",
                    }
                ),
                token_usage=ModelTokenUsage(input_tokens=9, output_tokens=3, total_tokens=12),
            )

    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), NoMatchModel())
    context = ContextSnapshot(
        request_id="no-match-reference",
        long_term_memory="关于预算的记录：本季度已完成审核。",
    )

    selection = asyncio.run(
        select(selector, "把这段关于预算的内容整理一下。", context, budget=budget())
    )

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "query_reference_not_resolved"


def test_followup_reuses_latest_task_context_without_query_model_call(tmp_path: Path) -> None:
    model = FakeChatModel(response("direct_answer"))
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)
    context = ContextSnapshot(
        request_id="resolved",
        messages=(
            ConversationMessage(role=MessageRole.USER, content="summarize project meeting notes"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="I found notes.md"),
        ),
    )

    selection = asyncio.run(
        select(
            selector,
            "继续",
            context,
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/direct_answer"
    assert "summarize project meeting notes" in model.request.messages[-1].content
    assert "继续" in model.request.messages[-1].content
    assert selection.token_usage.total_tokens == 10


def test_selector_delegates_missing_slots_to_selected_skill_runtime(tmp_path: Path) -> None:
    write_package(tmp_path)
    model = FakeChatModel(
        response(
            "business_skill",
            skill_id="business/meeting-notes",
            payload={"notes": "Decision: ship"},
        )
    )
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        model,
    )

    selection = asyncio.run(
        select(
            selector,
            "summarize these meeting notes",
            ContextSnapshot(request_id="r"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "business/meeting-notes"
    assert selection.payload == {"notes": "Decision: ship"}
    assert selection.reason_code == "business_skill_slots_pending_execution_clarification"
    assert "the Skill execution layer will ask for missing required inputs" in str(
        model.request.messages[0].content
    )


def test_missing_skill_slots_clarify_when_classifier_marks_scope_incomplete(
    tmp_path: Path,
) -> None:
    write_package(tmp_path)
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        FakeChatModel(
            response(
                "business_skill",
                skill_id="business/meeting-notes",
                payload={},
                covers_full_request=False,
            )
        ),
    )

    selection = asyncio.run(
        select(
            selector,
            "Summarize these meeting notes",
            ContextSnapshot(request_id="missing-slot-and-incomplete-scope"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "business/meeting-notes"
    assert selection.payload == {}
    assert selection.reason_code == "business_skill_slots_pending_execution_clarification"


def test_partial_skill_coverage_routes_compound_request_to_plan(tmp_path: Path) -> None:
    write_package(tmp_path)
    model = FakeChatModel(
        response(
            "business_skill",
            skill_id="business/meeting-notes",
            payload={"notes": "Friday 3 PM review release plan"},
            covers_full_request=False,
        )
    )
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)

    selection = asyncio.run(
        select(
            selector,
            "Summarize these notes, create a calendar event, and email the attendees.",
            ContextSnapshot(request_id="partial-skill-coverage"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/orchestrator_plan"
    assert selection.reason_code == "compound_action_request_requires_plan"
    assert "covers_full_request" in str(model.request.messages[0].content)


def test_explicitly_compound_actions_cannot_use_skill_fast_path(tmp_path: Path) -> None:
    write_package(tmp_path)
    model = FakeChatModel(
        response(
            "business_skill",
            skill_id="business/meeting-notes",
            payload={"notes": "周五 15 点评审发布方案"},
            covers_full_request=True,
        )
    )
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)

    selection = asyncio.run(
        select(
            selector,
            (
                "把这段会议记录整理成纪要，然后创建日历事件并给三位参会人发送邮件。"
                "记录：周五 15 点评审发布方案。"
            ),
            ContextSnapshot(request_id="plan-004-compound-actions"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/orchestrator_plan"
    assert selection.reason_code == "compound_action_request_requires_plan"


def test_contract_inspection_can_plan_to_discover_missing_service_paths(tmp_path: Path) -> None:
    write_package(tmp_path)
    model = FakeChatModel(
        response(
            "clarify",
            skill_id="builtin/clarify",
            clarification_prompt="Please provide the service names and contract files.",
            payload={},
        )
    )
    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)

    selection = asyncio.run(
        select(
            selector,
            "检查两个服务的 API 契约是否一致，整理差异，并为需要的代码调整拟定分步计划。",
            ContextSnapshot(request_id="plan-006-discover-contracts"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/orchestrator_plan"
    assert selection.reason_code == "workspace_contract_inspection_can_discover_inputs"


def test_selector_sends_incomplete_high_confidence_clarification_to_plan(tmp_path: Path) -> None:
    markdown_registry = SkillRegistry(tmp_path / "markdown")
    markdown_registry.save("office-helper", "Handle office tasks.", "Organize supplied notes.")
    model = FakeChatModel(
        response(
            "clarify",
            skill_id="builtin/clarify",
            clarification_prompt="Please provide the code to review.",
            candidate_scores={"business/code-review-check": 0.98},
        )
    )
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path / "yaml", additional_roots=(PACKAGED_SKILLS,)),
        model,
        markdown_registry=markdown_registry,
        markdown_skill_ids_by_name={"office-helper": "legacy/office-helper"},
        execution_modes={"legacy/office-helper": "markdown"},
    )

    selection = asyncio.run(
        select(
            selector,
            "Please review my code",
            ContextSnapshot(request_id="high-score-clarify-mismatch"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/orchestrator_plan"
    assert selection.reason_code == "skill_routing_evaluation_failed"
    assert selection.payload["evaluation_failure_reason"] == "skill_routing_evaluation_failed"
    assert selection.candidate_skill_ids == ("business/code-review-check",)


def test_selector_can_route_to_packaged_code_review_skill(tmp_path: Path) -> None:
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path, additional_roots=(PACKAGED_SKILLS,)),
        FakeChatModel(
            response(
                "business_skill",
                skill_id="business/code-review-check",
                payload={"code_or_diff": "def check(value): return eval(value)"},
            )
        ),
    )

    selection = asyncio.run(
        select(
            selector,
            "Review this code: def check(value): return eval(value)",
            ContextSnapshot(request_id="review-route"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "business/code-review-check"
    assert selection.payload == {"code_or_diff": "def check(value): return eval(value)"}


def test_selector_sends_low_confidence_business_request_to_plan(tmp_path: Path) -> None:
    write_package(tmp_path)
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        FakeChatModel(
            response(
                "business_skill",
                skill_id="business/meeting-notes",
                payload={"notes": "Decision", "audience": "team"},
                confidence=0.4,
            )
        ),
    )

    selection = asyncio.run(
        select(
            selector, "summarize meeting notes", ContextSnapshot(request_id="r"), budget=budget()
        )
    )

    assert selection.skill_id == "builtin/orchestrator_plan"
    assert selection.payload["task_description"] == "summarize meeting notes"
    assert (
        selection.payload["evaluation_failure_reason"] == "business_skill_confidence_gate_not_met"
    )
    assert selection.reason_code == "business_skill_confidence_gate_not_met"


def test_selector_requires_minimum_margin_for_business_fast_path(tmp_path: Path) -> None:
    write_package(tmp_path)
    other_manifest = package_manifest()
    other_manifest["meta"]["skill_id"] = "business/other"
    other_manifest["meta"]["name"] = "Other notes"
    other_directory = tmp_path / "other-notes"
    other_directory.mkdir()
    (other_directory / "skill.yaml").write_text(
        yaml.safe_dump(other_manifest, sort_keys=False), encoding="utf-8"
    )
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        FakeChatModel(
            response(
                "business_skill",
                skill_id="business/meeting-notes",
                payload={"notes": "Decision", "audience": "team"},
                candidate_scores={
                    "business/meeting-notes": 0.90,
                    "business/other": 0.70,
                },
            )
        ),
    )

    selection = asyncio.run(
        select(
            selector, "summarize meeting notes", ContextSnapshot(request_id="r"), budget=budget()
        )
    )

    assert selection.skill_id == "builtin/orchestrator_plan"
    assert selection.reason_code == "multiple_medium_confidence_skills"
    assert selection.candidate_skill_ids == (
        "business/meeting-notes",
        "business/other",
    )


def test_selector_passes_at_most_five_medium_yaml_candidates_to_plan(tmp_path: Path) -> None:
    candidate_scores = {}
    for index in range(6):
        manifest = package_manifest()
        skill_id = f"business/notes-{index}"
        manifest["meta"]["skill_id"] = skill_id
        manifest["meta"]["name"] = f"Notes {index}"
        package = tmp_path / f"notes-{index}"
        package.mkdir()
        (package / "skill.yaml").write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
        candidate_scores[skill_id] = 0.80 - index * 0.03

    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        FakeChatModel(
            response(
                "orchestrator_plan",
                candidate_scores=candidate_scores,
            )
        ),
    )

    selection = asyncio.run(
        select(
            selector,
            "Summarize the meeting notes",
            ContextSnapshot(request_id="r"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/orchestrator_plan"
    assert len(selection.candidate_skill_ids) == 5
    assert selection.candidate_skill_ids == tuple(candidate_scores)[:5]
    assert selection.reason_code == "multiple_medium_confidence_skills"


def test_selector_reranks_hybrid_candidates_without_using_scores_as_confidence(
    tmp_path: Path,
) -> None:
    write_package(tmp_path)
    other_manifest = package_manifest()
    other_manifest["meta"]["skill_id"] = "business/other"
    other_manifest["meta"]["name"] = "Other notes"
    other_directory = tmp_path / "other-notes"
    other_directory.mkdir()
    (other_directory / "skill.yaml").write_text(
        yaml.safe_dump(other_manifest, sort_keys=False), encoding="utf-8"
    )

    class FakeReranker:
        documents: list[str] = []

        async def rerank(self, query, documents, *, model, timeout_seconds):
            self.documents = list(documents)
            return RerankBatch(
                indices=tuple(reversed(range(len(documents)))),
                scores=tuple(float(rank) for rank in reversed(range(len(documents)))),
                input_tokens=0,
            )

    reranker = FakeReranker()

    class RecordingTraceSink:
        def __init__(self) -> None:
            self.events: list[TraceEvent] = []

        def record(self, event: TraceEvent) -> None:
            self.events.append(event)

    trace_sink = RecordingTraceSink()
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        FakeChatModel(response("direct_answer")),
        trace_sink=trace_sink,
    )
    selector.reranker_client = reranker
    selector.reranker_model = "qwen-reranker"
    with trace_context_scope(TraceContext()):
        result = asyncio.run(
            selector._business_candidates(
                selector.registry.discover(), "summarize meeting notes", budget()
            )
        )

    assert len(result) == 2
    assert result[0]["skill_id"] == "business/meeting-notes"
    assert result[0]["reranker_score"] == 0.0
    assert result[1]["skill_id"] == "business/other"
    assert result[1]["reranker_score"] == 1.0
    assert all(document for document in reranker.documents)
    assert all(
        "name:" in document and "description:" in document for document in reranker.documents
    )
    retrieval_event = next(event for event in trace_sink.events if event.name == "skill.retrieval")
    assert retrieval_event.attributes["reranker_fusion_applied"] is True


def test_selector_rejects_model_selected_unknown_skill(tmp_path: Path) -> None:
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        FakeChatModel(response("business_skill", skill_id="business/not-registered")),
    )

    selection = asyncio.run(
        select(selector, "perform this task", ContextSnapshot(request_id="r"), budget=budget())
    )

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "business_skill_not_available"


def test_selector_routes_to_markdown_skill_only_when_execution_mode_allows_it(
    tmp_path: Path,
) -> None:
    markdown_root = tmp_path / "markdown"
    registry = SkillRegistry(markdown_root)
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path / "yaml"),
        FakeChatModel(response("markdown_skill", skill_id="legacy/meeting-notes")),
        markdown_registry=registry,
        execution_modes={"legacy/meeting-notes": "markdown"},
    )

    selection = asyncio.run(
        select(selector, "summarize notes", ContextSnapshot(request_id="r"), budget=budget())
    )

    assert selection.skill_id == "legacy/meeting-notes"
    assert selection.execution_mode == "markdown"


def test_selector_normalizes_business_decision_for_available_markdown_skill(
    tmp_path: Path,
) -> None:
    registry = SkillRegistry(tmp_path / "markdown")
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path / "yaml"),
        FakeChatModel(response("business_skill", skill_id="legacy/meeting-notes")),
        markdown_registry=registry,
        execution_modes={"legacy/meeting-notes": "markdown"},
    )

    selection = asyncio.run(
        select(selector, "summarize notes", ContextSnapshot(request_id="r"), budget=budget())
    )

    assert selection.skill_id == "legacy/meeting-notes"
    assert selection.execution_mode == "markdown"
    assert selection.reason_code == "markdown_skill_selected_as_business"


def test_markdown_skill_result_assessment_reports_semantic_failure(tmp_path: Path) -> None:
    class AssessmentModel:
        async def complete(self, request):
            assert "Assess only whether" in request.messages[0].content
            return ModelResponse(
                text=json.dumps(
                    {
                        "satisfied": False,
                        "confidence": 0.96,
                        "reason_code": "required_decisions_missing",
                    }
                ),
                token_usage=ModelTokenUsage(input_tokens=5, output_tokens=2, total_tokens=7),
            )

    registry = SkillRegistry(tmp_path / "markdown")
    registry.save("meeting-notes", "Write meeting minutes.", "List decisions and owners.")
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path / "yaml"),
        AssessmentModel(),
        markdown_registry=registry,
        execution_modes={"legacy/meeting-notes": "markdown"},
    )

    accepted, reason_code, usage = asyncio.run(
        selector.assess_markdown_result(
            "legacy/meeting-notes",
            "Summarize the decisions and owners",
            "Here is a general summary.",
            RequestContext(
                ContextSnapshot(request_id="markdown-assessment"),
                budget(),
            ),
        )
    )

    assert accepted is False
    assert reason_code == "required_decisions_missing"
    assert usage == ModelTokenUsage(input_tokens=5, output_tokens=2, total_tokens=7)


def test_selector_rejects_markdown_skill_when_yaml_mode_is_selected(tmp_path: Path) -> None:
    registry = SkillRegistry(tmp_path / "markdown")
    registry.save("meeting-notes", "Write minutes.", "List decisions.")
    model = FakeChatModel(response("markdown_skill", skill_id="legacy/meeting-notes"))
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path / "yaml"),
        model,
        markdown_registry=registry,
        execution_modes={"legacy/meeting-notes": "yaml"},
    )

    selection = asyncio.run(
        select(selector, "summarize notes", ContextSnapshot(request_id="r"), budget=budget())
    )

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "markdown_skill_not_available"


def test_selector_clarifies_when_classifier_fails_closed(tmp_path: Path) -> None:
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path), FakeChatModel(RuntimeError())
    )

    selection = asyncio.run(
        select(selector, "do something", ContextSnapshot(request_id="r"), budget=budget())
    )

    assert selection.skill_id == "builtin/clarify"
    assert selection.reason_code == "skill_selection_unavailable"


def test_selector_uses_main_model_when_lightweight_model_fails(tmp_path: Path) -> None:
    lightweight_model = FakeChatModel(RuntimeError("lightweight model unavailable"))
    main_model = FakeChatModel(response("direct_answer"))
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        lightweight_model,
        fallback_provider=main_model,
    )

    selection = asyncio.run(
        select(
            selector,
            "Explain dependency injection",
            ContextSnapshot(request_id="r"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/direct_answer"
    assert selection.reason_code == "model_choice"
    assert lightweight_model.request is not None
    assert main_model.request is not None


def test_selector_uses_main_model_when_optional_lightweight_model_is_absent(
    tmp_path: Path,
) -> None:
    main_model = FakeChatModel(response("direct_answer"))
    selector = ModelProviderSkillSelector(
        YamlSkillRegistry(tmp_path),
        None,
        fallback_provider=main_model,
    )

    selection = asyncio.run(
        select(
            selector,
            "Explain dependency injection",
            ContextSnapshot(request_id="r"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/direct_answer"
    assert main_model.request is not None


def test_selector_falls_back_to_bm25_when_embedding_recall_fails(tmp_path: Path) -> None:
    write_package(tmp_path)
    model = FakeChatModel(response("direct_answer"))

    class FailingVectorIndex:
        async def search(self, *args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            raise RuntimeError("embedding service unavailable")

    selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path), model)
    selector.routing_models = object()
    selector.vector_index = FailingVectorIndex()

    selection = asyncio.run(
        select(
            selector,
            "Explain dependency injection",
            ContextSnapshot(request_id="r"),
            budget=budget(),
        )
    )

    assert selection.skill_id == "builtin/direct_answer"
    assert selector.vector_index is not None
