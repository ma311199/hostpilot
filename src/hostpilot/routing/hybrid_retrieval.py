"""Field-aware lexical retrieval and reciprocal-rank fusion for Skills."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from hostpilot.routing.vector_index import SemanticSkillMatch

TOKEN_PATTERN = re.compile(r"[a-z0-9_]+|[\u4e00-\u9fff]", re.IGNORECASE)
FIELD_WEIGHTS: Mapping[str, float] = {
    "name": 1.5,
    "description": 1.0,
    "triggers": 1.5,
    "tags": 1.5,
    "examples": 1.5,
    "aliases": 1.5,
}
RRF_K = 60


@dataclass(frozen=True)
class SkillRetrievalDocument:
    """Normalized metadata shared by YAML and Markdown Skill packages."""

    skill_id: str
    fields: Mapping[str, tuple[str, ...]]

    def values(self, field: str) -> tuple[str, ...]:
        return self.fields.get(field, ())

    @property
    def description(self) -> str:
        return "\n".join(self.values("description"))

    @property
    def lexical_text(self) -> str:
        return "\n".join(
            value for field in FIELD_WEIGHTS for value in self.values(field) if value.strip()
        )

    @property
    def reranker_text(self) -> str:
        """Keep metadata field names visible to cross-encoder rerankers."""
        sections = []
        for field in FIELD_WEIGHTS:
            values = tuple(value.strip() for value in self.values(field) if value.strip())
            if values:
                sections.append(f"{field}: {'; '.join(values)}")
        return "\n".join(sections)


def fielded_bm25_scores(
    query: str,
    documents: Sequence[SkillRetrievalDocument],
) -> dict[str, float]:
    """Score each metadata field independently, neutralize missing fields, then weight."""
    query_terms = TOKEN_PATTERN.findall(query.casefold())
    if not query_terms or not documents:
        return {document.skill_id: 0.0 for document in documents}

    total_scores = {document.skill_id: 0.0 for document in documents}
    for field, weight in FIELD_WEIGHTS.items():
        terms_by_id = {
            document.skill_id: TOKEN_PATTERN.findall(" ".join(document.values(field)).casefold())
            for document in documents
        }
        populated = {skill_id: terms for skill_id, terms in terms_by_id.items() if terms}
        if not populated:
            continue
        raw_scores = _bm25_field(query_terms, terms_by_id)
        maximum = max(raw_scores.values(), default=0.0)
        normalized = {
            skill_id: score / maximum if maximum else 0.0 for skill_id, score in raw_scores.items()
        }
        neutral_score = sum(normalized[skill_id] for skill_id in populated) / len(populated)
        for skill_id, terms in terms_by_id.items():
            field_score = normalized[skill_id] if terms else neutral_score
            total_scores[skill_id] += weight * field_score
    return total_scores


def reciprocal_rank_fusion(
    documents: Sequence[SkillRetrievalDocument],
    lexical_scores: Mapping[str, float],
    semantic_matches: Sequence[SemanticSkillMatch],
    *,
    limit: int,
    k: int = RRF_K,
) -> tuple[str, ...]:
    """Fuse lexical and semantic top-k lists with equal reciprocal-rank weights."""
    if limit < 1 or k < 0:
        raise ValueError("RRF limit must be positive and k cannot be negative.")
    known_ids = {document.skill_id for document in documents}
    lexical_order = sorted(
        (
            skill_id
            for skill_id, score in lexical_scores.items()
            if skill_id in known_ids and score > 0
        ),
        key=lambda skill_id: (-lexical_scores[skill_id], skill_id),
    )[:limit]
    semantic_order: list[str] = []
    seen_semantic: set[str] = set()
    for match in semantic_matches:
        if match.skill_id in known_ids and match.skill_id not in seen_semantic:
            semantic_order.append(match.skill_id)
            seen_semantic.add(match.skill_id)
        if len(semantic_order) == limit:
            break

    if not lexical_order:
        return tuple(semantic_order[:limit])
    if not semantic_order:
        return tuple(lexical_order[:limit])

    fused: dict[str, float] = {}
    for ranking in (lexical_order, semantic_order):
        for rank, skill_id in enumerate(ranking, start=1):
            fused[skill_id] = fused.get(skill_id, 0.0) + 1.0 / (k + rank)
    return tuple(sorted(fused, key=lambda skill_id: (-fused[skill_id], skill_id))[:limit])


def fuse_reranker_order(
    retrieval_order: Sequence[str],
    reranker_order: Sequence[str],
    *,
    limit: int,
    k: int = RRF_K,
    retrieval_weight: float = 3.0,
    reranker_weight: float = 1.0,
    top_candidate_support_limit: int = 2,
) -> tuple[str, ...]:
    """Blend rankings while giving retrieval three times the weight as the reranker."""
    if (
        limit < 1
        or k < 0
        or not math.isfinite(retrieval_weight)
        or not math.isfinite(reranker_weight)
        or retrieval_weight <= 0
        or reranker_weight <= 0
        or top_candidate_support_limit < 1
    ):
        raise ValueError("RRF limit, k, and rank weights must be valid positive values.")
    base = tuple(dict.fromkeys(retrieval_order))
    reranked = tuple(skill_id for skill_id in dict.fromkeys(reranker_order) if skill_id in base)
    if not base:
        return ()
    base_rank = {skill_id: rank for rank, skill_id in enumerate(base)}
    if reranked and base_rank[reranked[0]] >= top_candidate_support_limit:
        return base[:limit]

    scores: dict[str, float] = {skill_id: 0.0 for skill_id in base}
    for ranking, weight in ((base, retrieval_weight), (reranked, reranker_weight)):
        for rank, skill_id in enumerate(ranking, start=1):
            scores[skill_id] += weight / (k + rank)
    return tuple(
        sorted(scores, key=lambda skill_id: (-scores[skill_id], base_rank[skill_id]))[:limit]
    )


def _bm25_field(
    query_terms: Sequence[str],
    terms_by_id: Mapping[str, Sequence[str]],
) -> dict[str, float]:
    document_frequency: dict[str, int] = {}
    for terms in terms_by_id.values():
        for term in set(terms):
            document_frequency[term] = document_frequency.get(term, 0) + 1
    document_count = len(terms_by_id)
    average_length = sum(map(len, terms_by_id.values())) / max(document_count, 1)
    scores: dict[str, float] = {}
    for skill_id, terms in terms_by_id.items():
        frequencies: dict[str, int] = {}
        for term in terms:
            frequencies[term] = frequencies.get(term, 0) + 1
        score = 0.0
        for term in query_terms:
            frequency = frequencies.get(term, 0)
            if not frequency:
                continue
            inverse_frequency = math.log1p(
                (document_count - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5)
            )
            denominator = frequency + 1.5 * (
                1 - 0.75 + 0.75 * len(terms) / max(average_length, 1.0)
            )
            score += inverse_frequency * frequency * 2.5 / denominator
        scores[skill_id] = score
    return scores


__all__ = [
    "FIELD_WEIGHTS",
    "RRF_K",
    "SkillRetrievalDocument",
    "fielded_bm25_scores",
    "fuse_reranker_order",
    "reciprocal_rank_fusion",
]
