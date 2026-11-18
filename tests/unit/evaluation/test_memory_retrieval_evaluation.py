from pathlib import Path

import pytest

from hostpilot.evaluation.memory_retrieval import (
    evaluate_memory_retrieval,
    load_memory_evaluation_dataset,
    rank_memory_entries,
    split_markdown_chunks,
)

DATASET = (
    Path(__file__).resolve().parents[3] / "docs/evaluations/memory-retrieval-v1.yaml"
)


def test_memory_evaluation_dataset_is_scoped_and_produces_retrieval_metrics() -> None:
    entries, cases = load_memory_evaluation_dataset(DATASET)
    report = evaluate_memory_retrieval(entries, cases, char_budget=220)

    assert report.case_count == 9
    assert report.char_budget == 220
    assert report.baseline_recall_at_budget >= 0
    assert report.lexical_mrr_at_budget > report.baseline_mrr_at_budget
    assert report.hybrid_recall_at_budget >= report.lexical_recall_at_budget
    assert report.hybrid_mrr_at_budget > report.baseline_mrr_at_budget
    assert all(
        case["baseline_characters"] <= report.char_budget
        and case["lexical_characters"] <= report.char_budget
        and case["hybrid_characters"] <= report.char_budget
        for case in report.cases
    )
    assert all(
        "session-beta-private" not in case["lexical_ids"]
        and "session-beta-private" not in case["hybrid_ids"]
        for case in report.cases
    )


def test_lexical_ranker_finds_paraphrased_model_key_question_without_other_sessions() -> None:
    entries, cases = load_memory_evaluation_dataset(DATASET)
    case = next(case for case in cases if case.case_id == "model-key-after-restart")

    ranked = rank_memory_entries(
        case.query,
        entries,
        conversation_id=case.conversation_id,
    )

    assert ranked[0].entry_id == "model-credential-lifecycle"
    assert all(entry.entry_id != "session-beta-private" for entry in ranked)


def test_lexical_ranker_exposes_true_synonym_gap_for_manual_evaluation() -> None:
    entries, cases = load_memory_evaluation_dataset(DATASET)
    case = next(case for case in cases if case.case_id == "credential-after-exit-paraphrase")

    ranked = rank_memory_entries(
        case.query,
        entries,
        conversation_id=case.conversation_id,
    )

    assert ranked == ()


def test_hybrid_fallback_preserves_baseline_for_unmatched_paraphrase() -> None:
    entries, cases = load_memory_evaluation_dataset(DATASET)
    case = next(case for case in cases if case.case_id == "credential-after-exit-paraphrase")
    report = evaluate_memory_retrieval(entries, (case,), char_budget=220)
    result = report.cases[0]

    assert result["lexical_ids"] == []
    assert result["hybrid_ids"] == result["baseline_ids"]
    assert result["hybrid_recall"] == result["baseline_recall"] == 1.0


def test_markdown_chunker_retains_heading_context_and_obeys_chunk_bound() -> None:
    chunks = split_markdown_chunks(
        "# 配置说明\n\n模型名称保存。Base URL 保存。API Key 重启后重新填写。\n\n"
        "## 工作区\n\n目录只读时禁止文件写入。",
        max_chunk_chars=24,
    )

    assert len(chunks) > 2
    assert all(len(chunk) <= 24 for chunk in chunks)
    assert all(chunk.startswith(("# 配置说明", "## 工作区")) for chunk in chunks)


def test_dataset_rejects_relevance_labels_from_another_session(tmp_path) -> None:
    dataset = tmp_path / "memory.yaml"
    dataset.write_text(
        """dataset_id: memory-retrieval-evaluation
entries:
  - entry_id: private
    scope: session_summary
    conversation_id: conversation-a
    content: secret
cases:
  - case_id: leaked-label
    conversation_id: conversation-b
    query: secret
    relevant_ids: [private]
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="out-of-scope session entry"):
        load_memory_evaluation_dataset(dataset)
