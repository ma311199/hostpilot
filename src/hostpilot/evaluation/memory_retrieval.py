"""Offline evaluation for bounded memory chunk retrieval strategies."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

from hostpilot.memory.retrieval import rank_text_indexes, split_memory_text

MemoryScopeName = Literal["session_summary", "long_term"]


@dataclass(frozen=True)
class MemoryEvaluationEntry:
    """One synthetic memory paragraph with its allowed scope."""

    entry_id: str
    scope: MemoryScopeName
    content: str
    session_id: str | None = None


@dataclass(frozen=True)
class MemoryEvaluationCase:
    """One query and its manually labeled relevant memory IDs."""

    case_id: str
    session_id: str
    query: str
    relevant_ids: tuple[str, ...]


@dataclass(frozen=True)
class MemoryRetrievalEvaluation:
    """Aggregate quality for fixed-order, lexical-only, and hybrid recall."""

    case_count: int
    char_budget: int
    baseline_recall_at_budget: float
    lexical_recall_at_budget: float
    hybrid_recall_at_budget: float
    baseline_mrr_at_budget: float
    lexical_mrr_at_budget: float
    hybrid_mrr_at_budget: float
    baseline_mean_returned_chunks: float
    lexical_mean_returned_chunks: float
    hybrid_mean_returned_chunks: float
    cases: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        """Serialize metrics and per-case outcomes for reports."""
        return {
            "case_count": self.case_count,
            "char_budget": self.char_budget,
            "baseline": {
                "recall_at_budget": self.baseline_recall_at_budget,
                "mrr_at_budget": self.baseline_mrr_at_budget,
                "mean_returned_chunks": self.baseline_mean_returned_chunks,
            },
            "lexical": {
                "recall_at_budget": self.lexical_recall_at_budget,
                "mrr_at_budget": self.lexical_mrr_at_budget,
                "mean_returned_chunks": self.lexical_mean_returned_chunks,
            },
            "hybrid": {
                "recall_at_budget": self.hybrid_recall_at_budget,
                "mrr_at_budget": self.hybrid_mrr_at_budget,
                "mean_returned_chunks": self.hybrid_mean_returned_chunks,
            },
            "cases": list(self.cases),
        }


def load_memory_evaluation_dataset(
    path: Path,
) -> tuple[tuple[MemoryEvaluationEntry, ...], tuple[MemoryEvaluationCase, ...]]:
    """Load and validate a versioned local memory retrieval dataset."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise ValueError("Could not read memory retrieval evaluation dataset.") from error
    if not isinstance(raw, dict) or raw.get("dataset_id") != "memory-retrieval-evaluation":
        raise ValueError("Memory retrieval evaluation dataset has an invalid identity.")
    raw_entries = raw.get("entries")
    raw_cases = raw.get("cases")
    if not isinstance(raw_entries, list) or not isinstance(raw_cases, list):
        raise ValueError("Memory retrieval dataset requires entries and cases lists.")

    entries: list[MemoryEvaluationEntry] = []
    entry_ids: set[str] = set()
    for item in raw_entries:
        if not isinstance(item, dict):
            raise ValueError("Memory retrieval entries must be mappings.")
        entry_id = _required_string(item, "entry_id")
        scope = _required_string(item, "scope")
        content = _required_string(item, "content")
        session_id = item.get("session_id")
        if scope not in {"session_summary", "long_term"}:
            raise ValueError("Memory retrieval entry scope is unsupported.")
        if scope == "session_summary" and not isinstance(session_id, str):
            raise ValueError("Session memory entries require a session_id.")
        if scope == "long_term" and session_id is not None:
            raise ValueError("Long-term memory entries cannot have a session_id.")
        if entry_id in entry_ids:
            raise ValueError("Memory retrieval entry IDs must be unique.")
        entry_ids.add(entry_id)
        entries.append(
            MemoryEvaluationEntry(
                entry_id=entry_id,
                scope=scope,  # type: ignore[arg-type]
                content=content,
                session_id=session_id,
            )
        )

    cases: list[MemoryEvaluationCase] = []
    case_ids: set[str] = set()
    entries_by_id = {entry.entry_id: entry for entry in entries}
    for item in raw_cases:
        if not isinstance(item, dict):
            raise ValueError("Memory retrieval cases must be mappings.")
        case_id = _required_string(item, "case_id")
        session_id = _required_string(item, "session_id")
        query = _required_string(item, "query")
        relevant_ids = item.get("relevant_ids")
        if not isinstance(relevant_ids, list) or not relevant_ids:
            raise ValueError("Memory retrieval cases require relevant_ids.")
        if not all(
            isinstance(entry_id, str) and entry_id in entry_ids for entry_id in relevant_ids
        ):
            raise ValueError("Memory retrieval case references an unknown entry.")
        for entry_id in relevant_ids:
            relevant_entry = entries_by_id[entry_id]
            if (
                relevant_entry.scope == "session_summary"
                and relevant_entry.session_id != session_id
            ):
                raise ValueError("Memory retrieval case labels an out-of-scope session entry.")
        if case_id in case_ids:
            raise ValueError("Memory retrieval case IDs must be unique.")
        case_ids.add(case_id)
        cases.append(
            MemoryEvaluationCase(
                case_id=case_id,
                session_id=session_id,
                query=query,
                relevant_ids=tuple(relevant_ids),
            )
        )
    if not entries or not cases:
        raise ValueError("Memory retrieval dataset cannot be empty.")
    return tuple(entries), tuple(cases)


def split_markdown_chunks(content: str, *, max_chunk_chars: int = 1200) -> tuple[str, ...]:
    """Split Markdown at headings and paragraphs while retaining heading context."""
    return split_memory_text(content, max_chunk_chars=max_chunk_chars)


def rank_memory_entries(
    query: str,
    entries: tuple[MemoryEvaluationEntry, ...],
    *,
    session_id: str,
) -> tuple[MemoryEvaluationEntry, ...]:
    """Rank allowed memory entries with a dependency-free BM25 lexical baseline."""
    allowed = tuple(
        entry for entry in entries if entry.scope == "long_term" or entry.session_id == session_id
    )
    indexes = rank_text_indexes(query, tuple(entry.content for entry in allowed))
    return tuple(allowed[index] for index in indexes)


def evaluate_memory_retrieval(
    entries: tuple[MemoryEvaluationEntry, ...],
    cases: tuple[MemoryEvaluationCase, ...],
    *,
    char_budget: int = 1200,
) -> MemoryRetrievalEvaluation:
    """Compare fixed-order, lexical-only, and fallback recall under one char budget."""
    if char_budget < 1:
        raise ValueError("char_budget must be positive.")
    case_results: list[dict[str, Any]] = []
    baseline_recalls: list[float] = []
    lexical_recalls: list[float] = []
    hybrid_recalls: list[float] = []
    baseline_rr: list[float] = []
    lexical_rr: list[float] = []
    hybrid_rr: list[float] = []
    baseline_counts: list[int] = []
    lexical_counts: list[int] = []
    hybrid_counts: list[int] = []

    for case in cases:
        allowed = tuple(
            entry
            for entry in entries
            if entry.scope == "long_term" or entry.session_id == case.session_id
        )
        fixed_order = tuple(
            sorted(
                allowed,
                key=lambda entry: (
                    0 if entry.scope == "session_summary" else 1,
                    entries.index(entry),
                ),
            )
        )
        lexical_order = rank_memory_entries(
            case.query,
            allowed,
            session_id=case.session_id,
        )
        lexical_ids_set = {entry.entry_id for entry in lexical_order}
        hybrid_order = lexical_order + tuple(
            entry for entry in fixed_order if entry.entry_id not in lexical_ids_set
        )
        baseline_ids = _within_budget(fixed_order, char_budget)
        lexical_ids = _within_budget(lexical_order, char_budget)
        hybrid_ids = _within_budget(hybrid_order, char_budget)
        relevant = set(case.relevant_ids)
        baseline_retrieved = relevant.intersection(baseline_ids)
        lexical_retrieved = relevant.intersection(lexical_ids)
        hybrid_retrieved = relevant.intersection(hybrid_ids)
        baseline_characters = _characters_used(fixed_order, baseline_ids, char_budget)
        lexical_characters = _characters_used(lexical_order, lexical_ids, char_budget)
        hybrid_characters = _characters_used(hybrid_order, hybrid_ids, char_budget)
        baseline_recall = len(baseline_retrieved) / len(relevant)
        lexical_recall = len(lexical_retrieved) / len(relevant)
        hybrid_recall = len(hybrid_retrieved) / len(relevant)
        baseline_rank = _first_relevant_rank(fixed_order, baseline_ids, relevant)
        lexical_rank = _first_relevant_rank(lexical_order, lexical_ids, relevant)
        hybrid_rank = _first_relevant_rank(hybrid_order, hybrid_ids, relevant)
        baseline_recalls.append(baseline_recall)
        lexical_recalls.append(lexical_recall)
        hybrid_recalls.append(hybrid_recall)
        baseline_rr.append(1 / baseline_rank if baseline_rank else 0.0)
        lexical_rr.append(1 / lexical_rank if lexical_rank else 0.0)
        hybrid_rr.append(1 / hybrid_rank if hybrid_rank else 0.0)
        baseline_counts.append(len(baseline_ids))
        lexical_counts.append(len(lexical_ids))
        hybrid_counts.append(len(hybrid_ids))
        case_results.append(
            {
                "case_id": case.case_id,
                "query": case.query,
                "relevant_ids": list(case.relevant_ids),
                "baseline_ids": list(baseline_ids),
                "lexical_ids": list(lexical_ids),
                "hybrid_ids": list(hybrid_ids),
                "baseline_characters": baseline_characters,
                "lexical_characters": lexical_characters,
                "hybrid_characters": hybrid_characters,
                "baseline_recall": baseline_recall,
                "lexical_recall": lexical_recall,
                "hybrid_recall": hybrid_recall,
                "baseline_reciprocal_rank": 1 / baseline_rank if baseline_rank else 0.0,
                "lexical_reciprocal_rank": 1 / lexical_rank if lexical_rank else 0.0,
                "hybrid_reciprocal_rank": 1 / hybrid_rank if hybrid_rank else 0.0,
            }
        )

    return MemoryRetrievalEvaluation(
        case_count=len(cases),
        char_budget=char_budget,
        baseline_recall_at_budget=_mean(baseline_recalls),
        lexical_recall_at_budget=_mean(lexical_recalls),
        hybrid_recall_at_budget=_mean(hybrid_recalls),
        baseline_mrr_at_budget=_mean(baseline_rr),
        lexical_mrr_at_budget=_mean(lexical_rr),
        hybrid_mrr_at_budget=_mean(hybrid_rr),
        baseline_mean_returned_chunks=_mean(baseline_counts),
        lexical_mean_returned_chunks=_mean(lexical_counts),
        hybrid_mean_returned_chunks=_mean(hybrid_counts),
        cases=tuple(case_results),
    )


def _within_budget(entries: tuple[MemoryEvaluationEntry, ...], char_budget: int) -> tuple[str, ...]:
    selected: list[str] = []
    remaining = char_budget
    for entry in entries:
        if remaining <= 0:
            break
        selected.append(entry.entry_id)
        remaining -= min(remaining, len(entry.content))
    return tuple(selected)


def _first_relevant_rank(
    ordered: tuple[MemoryEvaluationEntry, ...],
    selected_ids: tuple[str, ...],
    relevant_ids: set[str],
) -> int | None:
    selected = set(selected_ids)
    for rank, entry in enumerate(ordered, start=1):
        if entry.entry_id in selected and entry.entry_id in relevant_ids:
            return rank
    return None


def _characters_used(
    ordered: tuple[MemoryEvaluationEntry, ...], selected_ids: tuple[str, ...], char_budget: int
) -> int:
    selected = set(selected_ids)
    remaining = char_budget
    for entry in ordered:
        if remaining <= 0:
            break
        if entry.entry_id in selected:
            remaining -= min(remaining, len(entry.content))
    return char_budget - remaining


def _required_string(item: dict[str, Any], name: str) -> str:
    value = item.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Memory retrieval dataset field {name!r} must be non-empty text.")
    return value.strip()


def _mean(values: list[float] | list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


__all__ = [
    "MemoryEvaluationCase",
    "MemoryEvaluationEntry",
    "MemoryRetrievalEvaluation",
    "evaluate_memory_retrieval",
    "load_memory_evaluation_dataset",
    "rank_memory_entries",
    "split_markdown_chunks",
]
