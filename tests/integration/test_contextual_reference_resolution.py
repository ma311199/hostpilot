"""Exercise context building, persistent memory recall, and reference routing together."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from hostpilot.budget import RequestBudget
from hostpilot.context import ContextBuilder
from hostpilot.contracts import BudgetContext, BudgetLimits, ModelTokenUsage
from hostpilot.memory import MemoryController, MemoryWriteService
from hostpilot.memory.review import MemoryProposalReview
from hostpilot.models import ModelResponse
from hostpilot.observability import TraceContext, TraceEvent, trace_context_scope
from hostpilot.persistence import MarkdownMemoryStore, SQLiteConversationStore
from hostpilot.routing.skill_selector import ModelProviderSkillSelector
from hostpilot.runtime import RequestContext
from hostpilot.skills.yaml_registry import YamlSkillRegistry


def request_budget() -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=2_000,
                tool_calls=0,
                retrieval_calls=2,
                timeout_seconds=30,
            )
        )
    )


class EmptyStateReader:
    async def read_messages(self, session_id: str) -> tuple[()]:
        del session_id
        return ()


class ReferenceThenClassifierModel:
    def __init__(self, *, match_text: str | None) -> None:
        self.match_text = match_text
        self.calls = 0
        self.candidates: list[dict[str, str]] = []

    async def complete(self, request: Any) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            prompt = request.messages[0].content
            candidate_json = prompt.rsplit("Candidates:\n", maxsplit=1)[1]
            self.candidates = json.loads(candidate_json)
            matched = next(
                (
                    item
                    for item in self.candidates
                    if self.match_text is not None and self.match_text in item["content"]
                ),
                None,
            )
            return ModelResponse(
                text=json.dumps(
                    {
                        "candidate_id": matched["candidate_id"] if matched else None,
                        "confidence": 0.97 if matched else 0.99,
                        "reason_code": "fixture_match" if matched else "fixture_no_match",
                    }
                ),
                token_usage=ModelTokenUsage(input_tokens=30, output_tokens=5, total_tokens=35),
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
                    "reason_code": "fixture_classification",
                }
            ),
            token_usage=ModelTokenUsage(input_tokens=25, output_tokens=5, total_tokens=30),
        )


def build_context_builder(tmp_path: Path, *, max_memory_chars: int = 8_000):
    conversations = SQLiteConversationStore(tmp_path / "conversations.sqlite3")
    memory_store = MarkdownMemoryStore(tmp_path / "memory")
    controller = MemoryController(
        memory_store,
        conversations,
        MemoryProposalReview(MemoryWriteService(memory_store, conversations)),
        max_recall_chars=max_memory_chars,
    )
    builder = ContextBuilder(
        conversations,
        controller,
        EmptyStateReader(),
        max_memory_chars=max_memory_chars,
    )
    return conversations, memory_store, builder


def test_long_term_recall_keeps_late_chunk_available_for_reference_matching(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        conversations, memory_store, builder = build_context_builder(tmp_path)
        conversation = await conversations.create("reference recall")
        distractors = "\n\n".join(
            f"# Archive {index}\n\n"
            + (f"仓库货品编码 {index} 的盘点日期为周三，记录号 {index}。" * 40)
            for index in range(1, 6)
        )
        target = "发布前必须执行回归测试，并确认数据库迁移脚本已通过检查。"
        memory_store.write_long_term_memory(f"{distractors}\n\n# 发布清单\n\n{target}")

        context = await builder.build(
            conversation.session_id,
            query="把这段关于回归测试的内容整理一下。",
        )

        assert context.long_term_memory is not None
        assert target in context.long_term_memory

        model = ReferenceThenClassifierModel(match_text=target)

        class TraceRecorder:
            def __init__(self) -> None:
                self.events: list[TraceEvent] = []

            def record(self, event: TraceEvent) -> None:
                self.events.append(event)

        trace = TraceRecorder()
        selector = ModelProviderSkillSelector(
            YamlSkillRegistry(tmp_path / "skills"), model, trace_sink=trace
        )
        retrieval_queries: list[str] = []

        async def capture_retrieval(catalog, query, budget, markdown_candidates):
            del catalog, budget
            retrieval_queries.append(query)
            return [], markdown_candidates

        selector._retrieve_candidates = capture_retrieval
        with trace_context_scope(TraceContext()):
            selection = await selector.select(
                "把这段关于回归测试的内容整理一下。",
                RequestContext(context, request_budget()),
            )

        assert selection.skill_id == "builtin/direct_answer"
        assert target in retrieval_queries[0]
        assert any(target in item["content"] for item in model.candidates)
        assert len(model.candidates) == 6
        resolution = next(
            event for event in trace.events if event.name == "skill.reference_resolution"
        )
        target_candidate = next(item for item in model.candidates if target in item["content"])
        assert resolution.attributes["candidate_id"] == target_candidate["candidate_id"]
        assert target not in str(resolution.model_dump())

    asyncio.run(scenario())


def test_unrelated_recalled_memory_clarifies_without_skill_retrieval(tmp_path: Path) -> None:
    async def scenario() -> None:
        conversations, memory_store, builder = build_context_builder(tmp_path)
        conversation = await conversations.create("unrelated reference")
        memory_store.write_long_term_memory("# Gardening\n\n本周需要给阳台的植物浇水，并修剪枯叶。")
        context = await builder.build(
            conversation.session_id,
            query="把这段关于回归测试的内容整理一下。",
        )

        model = ReferenceThenClassifierModel(match_text=None)
        selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path / "skills"), model)

        async def retrieval_must_not_run(*args, **kwargs):
            raise AssertionError("Unmatched memory must be clarified before Skill retrieval.")

        selector._retrieve_candidates = retrieval_must_not_run
        selection = await selector.select(
            "把这段关于回归测试的内容整理一下。",
            RequestContext(context, request_budget()),
        )

        assert selection.skill_id == "builtin/clarify"
        assert selection.reason_code == "query_reference_not_resolved"
        assert model.calls == 1

    asyncio.run(scenario())


def test_ambiguous_memory_candidates_are_not_arbitrarily_selected(tmp_path: Path) -> None:
    async def scenario() -> None:
        conversations, memory_store, builder = build_context_builder(tmp_path)
        conversation = await conversations.create("ambiguous memory reference")
        memory_store.write_long_term_memory(
            "# Option A\n\n第一份待整理方案，内容是季度预算分配。\n\n"
            "# Option B\n\n第二份待整理方案，内容是年度预算分配。"
        )
        context = await builder.build(
            conversation.session_id,
            query="把这段关于预算的内容整理一下。",
        )

        model = ReferenceThenClassifierModel(match_text=None)
        selector = ModelProviderSkillSelector(YamlSkillRegistry(tmp_path / "skills"), model)

        async def retrieval_must_not_run(*args, **kwargs):
            raise AssertionError("Ambiguous references must be clarified before Skill retrieval.")

        selector._retrieve_candidates = retrieval_must_not_run
        selection = await selector.select(
            "把这段关于预算的内容整理一下。",
            RequestContext(context, request_budget()),
        )

        assert len(model.candidates) == 2
        assert selection.skill_id == "builtin/clarify"
        assert selection.reason_code == "query_reference_not_resolved"

    asyncio.run(scenario())
