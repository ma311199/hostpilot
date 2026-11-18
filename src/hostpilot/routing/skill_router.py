"""Select a registered YAML Skill from its structured routing signature."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hostpilot.skills.yaml_registry import YamlSkillCatalog, YamlSkillPackage

WORD_PATTERN = re.compile(r"[^\w]+", re.UNICODE)
SUMMARY_STOP_WORDS = frozenset({"a", "an", "and", "for", "in", "into", "of", "the", "to", "with"})


@dataclass(frozen=True)
class SkillRouteMatch:
    """A validated selection and the signature that matched the request."""

    skill_id: str
    score: float
    reason_code: str


@dataclass(frozen=True)
class _IndexedSkill:
    package: YamlSkillPackage
    examples: tuple[str, ...]
    tags: tuple[str, ...]
    summary: str
    summary_tokens: frozenset[str]


@dataclass(frozen=True)
class SkillSignatureIndex:
    """Precomputed routing signatures for one validated catalog snapshot."""

    skills: tuple[_IndexedSkill, ...]

    @classmethod
    def from_catalog(cls, catalog: YamlSkillCatalog) -> SkillSignatureIndex:
        """Build an immutable index from the current validated Skill catalog."""
        entries = []
        for package in catalog.skills:
            manifest = package.manifest
            if not manifest.meta.enabled:
                continue
            summary = _normalize(manifest.intent_summary)
            summary_tokens = frozenset(summary.split()) - SUMMARY_STOP_WORDS
            entries.append(
                _IndexedSkill(
                    package=package,
                    examples=tuple(_normalize(value) for value in manifest.examples),
                    tags=tuple(_normalize(value) for value in manifest.tags),
                    summary=summary,
                    summary_tokens=summary_tokens,
                )
            )
        return cls(tuple(entries))


class MetadataSkillRouter:
    """Conservatively match exact examples, tags, then intent summaries.

    This deterministic first routing implementation deliberately returns no match
    when metadata provides weak evidence. A later semantic selector can implement
    the same boundary without changing the Skill manifest contract.
    """

    def __init__(self, *, minimum_score: float = 0.70) -> None:
        if not 0.0 <= minimum_score <= 1.0:
            raise ValueError("minimum_score must be between 0 and 1.")
        self.minimum_score = minimum_score

    def route(self, message: str, index: SkillSignatureIndex) -> SkillRouteMatch | None:
        """Return the best enabled Skill match, if its signature is strong enough."""
        query = _normalize(message)
        if not query:
            return None

        candidates: list[tuple[float, int, str, str]] = []
        for entry in index.skills:
            manifest = entry.package.manifest
            score, reason = _score(query, entry)
            if score >= self.minimum_score:
                candidates.append((score, manifest.meta.priority, manifest.meta.skill_id, reason))
        if not candidates:
            return None

        score, _, skill_id, reason = sorted(
            candidates,
            key=lambda item: (-item[0], -item[1], item[2]),
        )[0]
        return SkillRouteMatch(skill_id=skill_id, score=score, reason_code=reason)


def _score(query: str, entry: _IndexedSkill) -> tuple[float, str]:
    for normalized in entry.examples:
        if query == normalized:
            return 1.0, "exact_example"
        if normalized and normalized in query:
            return 0.9, "example_phrase"

    for normalized in entry.tags:
        if normalized and _contains_phrase(query, normalized):
            return 0.8, "tag_match"

    if entry.summary and _contains_phrase(query, entry.summary):
        return 0.75, "intent_summary_match"
    query_tokens = set(query.split()) - SUMMARY_STOP_WORDS
    if entry.summary_tokens:
        overlap = len(entry.summary_tokens & query_tokens) / len(entry.summary_tokens)
        if overlap >= 0.7:
            return min(0.74, 0.7 + overlap * 0.04), "intent_token_overlap"
    return 0.0, "no_signature_match"


def _normalize(value: str) -> str:
    return " ".join(part for part in WORD_PATTERN.split(value.casefold()) if part)


def _contains_phrase(text: str, phrase: str) -> bool:
    return f" {phrase} " in f" {text} "


__all__ = ["MetadataSkillRouter", "SkillRouteMatch", "SkillSignatureIndex"]
