import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hostpilot.observability import TraceContext, TraceEvent
from hostpilot.persistence import SQLiteTraceStore


def test_trace_store_groups_events_and_persists_only_allowlisted_attributes(tmp_path) -> None:
    store = SQLiteTraceStore(tmp_path / "data" / "hostpilot.sqlite3")
    trace_id = uuid4()
    occurred_at = datetime.now(UTC)
    first_event = TraceEvent(
        context=TraceContext(trace_id=trace_id),
        name="memory.recall",
        occurred_at=occurred_at,
        attributes={
            "status": "completed",
            "chunk_count": 2,
            "query": "private conversation text",
        },
    )
    final_event = TraceEvent(
        context=TraceContext(trace_id=trace_id),
        name="chat.turn",
        occurred_at=occurred_at + timedelta(milliseconds=2),
        attributes={"status": "completed", "route": "direct", "total_tokens": 42},
    )
    skill_event = TraceEvent(
        context=TraceContext(trace_id=trace_id),
        name="skill.execute",
        occurred_at=occurred_at + timedelta(milliseconds=1),
        attributes={
            "status": "completed",
            "skill_id": "business/demo",
            "execution_mode": "yaml",
            "tool_id": "write_text_file",
        },
    )

    async def exercise() -> None:
        await store.initialize()
        store.record(first_event)
        store.record(skill_event)
        store.record(final_event)
        summaries = await store.list_recent()
        details = await store.get_trace(trace_id)

        assert len(summaries) == 1
        assert summaries[0].trace_id == trace_id
        assert summaries[0].event_count == 3
        assert summaries[0].last_event == "chat.turn"
        assert summaries[0].status == "completed"
        assert summaries[0].skill_id == "business/demo"
        assert [entry.event.name for entry in details] == [
            "memory.recall",
            "skill.execute",
            "chat.turn",
        ]
        assert "query" not in details[0].event.attributes
        assert details[1].event.attributes["execution_mode"] == "yaml"
        assert details[1].event.attributes["tool_id"] == "write_text_file"
        assert details[2].event.attributes["total_tokens"] == 42

    asyncio.run(exercise())


def test_trace_store_enforces_event_count_limit(tmp_path) -> None:
    store = SQLiteTraceStore(tmp_path / "traces.sqlite3", max_events=1)

    async def exercise() -> None:
        await store.initialize()
        store.record(TraceEvent(context=TraceContext(), name="first"))
        store.record(TraceEvent(context=TraceContext(), name="second"))
        summaries = await store.list_recent()
        assert len(summaries) == 1
        assert summaries[0].last_event == "second"

    asyncio.run(exercise())


def test_trace_store_keeps_bounded_routing_diagnostics_without_request_content(tmp_path) -> None:
    store = SQLiteTraceStore(tmp_path / "traces.sqlite3")
    trace_id = uuid4()
    event = TraceEvent(
        context=TraceContext(trace_id=trace_id),
        name="skill.retrieval",
        attributes={
            "retrieval_version": "fielded_bm25_rrf_reranker_fusion_v2",
            "candidate_count": 1,
            "embedding_status": "completed",
            "embedding_duration_ms": 12,
            "reranker_status": "completed",
            "reranker_duration_ms": 8,
            "rrf_skill_ids": "business/demo," + "x" * 9000,
            "reranker_raw_skill_ids": "business/second,business/first",
            "reranker_score_summary": "{" + "x" * 9000,
            "reranked_skill_ids": "business/first,business/second",
            "query": "request content must not be persisted",
        },
    )

    async def exercise() -> None:
        await store.initialize()
        store.record(event)
        details = await store.get_trace(trace_id)

        attributes = details[0].event.attributes
        assert attributes["retrieval_version"] == "fielded_bm25_rrf_reranker_fusion_v2"
        assert attributes["candidate_count"] == 1
        assert attributes["embedding_duration_ms"] == 12
        assert attributes["reranker_duration_ms"] == 8
        assert len(attributes["rrf_skill_ids"]) == 8192
        assert attributes["reranker_raw_skill_ids"] == "business/second,business/first"
        assert len(attributes["reranker_score_summary"]) == 8192
        assert "query" not in attributes

    asyncio.run(exercise())


def test_trace_store_persists_classifier_scores_without_request_content(tmp_path) -> None:
    store = SQLiteTraceStore(tmp_path / "traces.sqlite3")
    trace_id = uuid4()
    event = TraceEvent(
        context=TraceContext(trace_id=trace_id),
        name="skill.classification",
        attributes={
            "classifier_decision": "direct_answer",
            "selected_skill_id": "",
            "decision_confidence": 0.99,
            "scored_candidate_count": 1,
            "top_candidate_id": "business/code-review-check",
            "top_confidence": 0.91,
            "confidence_margin": 0.91,
            "request": "private user prompt",
        },
    )

    async def exercise() -> None:
        await store.initialize()
        store.record(event)
        details = await store.get_trace(trace_id)

        attributes = details[0].event.attributes
        assert attributes["classifier_decision"] == "direct_answer"
        assert attributes["top_candidate_id"] == "business/code-review-check"
        assert attributes["decision_confidence"] == 0.99
        assert attributes["scored_candidate_count"] == 1
        assert "request" not in attributes

    asyncio.run(exercise())


def test_trace_store_persists_safe_reference_match_metadata_only(tmp_path) -> None:
    store = SQLiteTraceStore(tmp_path / "traces.sqlite3")
    trace_id = uuid4()
    event = TraceEvent(
        context=TraceContext(trace_id=trace_id),
        name="skill.reference_resolution",
        attributes={
            "status": "matched",
            "candidate_count": 3,
            "candidate_id": "context-2",
            "source": "long_term_memory",
            "confidence": 0.93,
            "candidate_content": "private remembered text",
        },
    )

    async def exercise() -> None:
        await store.initialize()
        store.record(event)
        details = await store.get_trace(trace_id)

        assert details[0].event.attributes == {
            "status": "matched",
            "candidate_count": 3,
            "candidate_id": "context-2",
            "source": "long_term_memory",
            "confidence": 0.93,
        }
        assert "private remembered text" not in str(details[0].event.model_dump())

    asyncio.run(exercise())


def test_trace_store_persists_plan_task_progress_without_task_payloads(tmp_path) -> None:
    store = SQLiteTraceStore(tmp_path / "traces.sqlite3")
    trace_id = uuid4()
    event = TraceEvent(
        context=TraceContext(trace_id=trace_id),
        name="plan.task",
        attributes={
            "task_id": "review_diff",
            "execution_type": "reasoning_task",
            "source_task_id": "review_source",
            "status": "failed",
            "duration_ms": 25,
            "reason_code": "provider_unavailable",
            "invoked_tool_count": 0,
            "side_effect_count": 0,
            "outcome_uncertain": False,
            "task_description": "private task description",
            "output": "private task output",
            "tool_arguments": "private tool input",
            "compensation_handle": "private handle",
        },
    )

    async def exercise() -> None:
        await store.initialize()
        store.record(event)
        details = await store.get_trace(trace_id)

        assert details[0].event.attributes == {
            "task_id": "review_diff",
            "execution_type": "reasoning_task",
            "source_task_id": "review_source",
            "status": "failed",
            "duration_ms": 25,
            "reason_code": "provider_unavailable",
            "invoked_tool_count": 0,
            "side_effect_count": 0,
            "outcome_uncertain": False,
        }
        encoded = str(details[0].event.model_dump())
        assert "private task description" not in encoded
        assert "private task output" not in encoded
        assert "private tool input" not in encoded
        assert "private handle" not in encoded

    asyncio.run(exercise())
