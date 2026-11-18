"""Metric helpers shared by the live routing evaluator and its unit tests."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any


def percentile(values: Sequence[float], percentile_value: float) -> float | None:
    """Return a nearest-rank percentile for a small evaluation sample."""
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(percentile_value * len(ordered)) - 1)
    return round(ordered[index], 3)


def reciprocal_rank(skill_ids: Sequence[str], expected_skill_id: str | None) -> int | None:
    """Return the one-based candidate rank for a labeled Skill, if retrieved."""
    if expected_skill_id is None:
        return None
    try:
        return skill_ids.index(expected_skill_id) + 1
    except ValueError:
        return None


def retrieval_metric_summary(
    cases: Sequence[Mapping[str, Any]],
    *,
    ranking_key: str,
) -> dict[str, Any]:
    """Calculate relevant-skill recall, MRR, and Recall@K from per-case ranks."""
    business = [case for case in cases if case.get("expected_skill_id")]
    ranks = [case[ranking_key] for case in business if case.get(ranking_key) is not None]
    retrieved = sum(case.get(ranking_key) is not None for case in business)
    return {
        "relevant_skill_recall": {
            "retrieved": retrieved,
            "total": len(business),
            "rate": round(retrieved / len(business), 4) if business else None,
        },
        "mrr": round(sum(1 / rank for rank in ranks) / len(business), 4) if business else None,
        "recall_at": {
            str(k): (
                round(
                    sum(
                        rank is not None and rank <= k
                        for rank in (case.get(ranking_key) for case in business)
                    )
                    / len(business),
                    4,
                )
                if business
                else None
            )
            for k in (1, 3, 5, 10)
        },
    }


def split_skill_ids(value: Any) -> list[str]:
    """Parse the trace's bounded, comma-delimited candidate ID list."""
    if not isinstance(value, str) or not value:
        return []
    return [skill_id for skill_id in value.split(",") if skill_id]


def enabled_skill_coverage(
    enabled_skill_ids: set[str], cases: Sequence[Mapping[str, Any]]
) -> tuple[set[str], set[str]]:
    """Return expected Skill IDs covered by cases and enabled IDs lacking cases."""
    covered: set[str] = set()
    for case in cases:
        expected = case.get("expected")
        if isinstance(expected, Mapping):
            skill_id = expected.get("skill_id")
            if isinstance(skill_id, str) and skill_id:
                covered.add(skill_id)
    return covered, enabled_skill_ids - covered


__all__ = [
    "enabled_skill_coverage",
    "percentile",
    "reciprocal_rank",
    "retrieval_metric_summary",
    "split_skill_ids",
]
