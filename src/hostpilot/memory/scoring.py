"""Local, deterministic importance scoring for memory write proposals."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hostpilot.contracts import MemoryImportanceTier

LOW_SCORE_MAX = 39
MEDIUM_SCORE_MAX = 74
MEDIUM_SUMMARY_MAX_CHARS = 800

_EXPLICIT_SAVE_PATTERN = re.compile(
    r"记住|请记|以后(?:都|要|请)|从今以后|默认(?:使用|采用)|"
    r"remember|keep in mind|from now on|always use|my preference",
    re.IGNORECASE,
)
_STABLE_PREFERENCE_PATTERN = re.compile(
    r"偏好|习惯|喜欢|希望|要求|约定|长期|固定|始终|必须|不得|不能|"
    r"prefer|preference|habit|always|must|constraint|decision",
    re.IGNORECASE,
)
_EPHEMERAL_PATTERN = re.compile(
    r"临时|这一轮|当前请求|今天|刚才|稍后|一次性|草稿|临时状态|"
    r"temporary|one[- ]off|for now|today|draft|intermediate",
    re.IGNORECASE,
)
_DEBUG_PATTERN = re.compile(
    r"stack trace|traceback|debug log|tool output|工具原始输出|调试日志|运行日志|"
    r"exception at|报错堆栈|stderr|stdout",
    re.IGNORECASE,
)
_ACKNOWLEDGEMENT_PATTERN = re.compile(
    r"^(?:好的|谢谢|明白|收到|没问题|ok|okay|thanks|got it)[。.!！\s]*$",
    re.IGNORECASE,
)
_SECRET_PATTERN = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{12,}|gh[pousr]_[a-z0-9]{20,}|"
    r"bearer\s+[a-z0-9._~+/=-]{12,}|(?:api[_ -]?key|password|token)\s*[:=]\s*[^\s,;]{8,})"
)
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[。！？.!?；;])\s*")


@dataclass(frozen=True)
class MemoryAssessment:
    """Score, tier, compact text, and non-sensitive reasons for one proposal."""

    score: int
    tier: MemoryImportanceTier
    summary: str | None
    reason_codes: tuple[str, ...]


def assess_memory_content(content: str) -> MemoryAssessment:
    """Apply the version-one local rubric without sending content to a model."""
    normalized = content.strip()
    score = 40
    reasons: list[str] = []
    has_explicit_save = bool(_EXPLICIT_SAVE_PATTERN.search(normalized))
    has_stable_signal = bool(_STABLE_PREFERENCE_PATTERN.search(normalized))

    if has_explicit_save:
        score += 40
        reasons.append("explicit_save_intent")
    if has_stable_signal:
        score += 25
        reasons.append("durable_preference_or_constraint")
    if len(normalized) >= 160:
        score += 10
        reasons.append("substantive_content")
    if _EPHEMERAL_PATTERN.search(normalized):
        score -= 30
        reasons.append("ephemeral_content")
    if _DEBUG_PATTERN.search(normalized):
        score -= 50
        reasons.append("operational_log_content")
    if len(normalized) <= 48 and _ACKNOWLEDGEMENT_PATTERN.fullmatch(normalized):
        score -= 50
        reasons.append("acknowledgement_only")
    if _SECRET_PATTERN.search(normalized):
        score = 0
        reasons.append("credential_like_content")

    score = min(100, max(0, score))
    tier = (
        MemoryImportanceTier.LOW
        if score <= LOW_SCORE_MAX
        else MemoryImportanceTier.MEDIUM
        if score <= MEDIUM_SCORE_MAX
        else MemoryImportanceTier.HIGH
    )
    summary = _extractive_summary(normalized) if tier is MemoryImportanceTier.MEDIUM else None
    return MemoryAssessment(score, tier, summary, tuple(reasons))


def _extractive_summary(content: str) -> str:
    """Choose durable sentences while preserving source wording and order."""
    if len(content) <= MEDIUM_SUMMARY_MAX_CHARS:
        return content
    sentences = tuple(
        part.strip() for part in _SENTENCE_SPLIT_PATTERN.split(content) if part.strip()
    )
    durable = tuple(
        sentence for sentence in sentences if _STABLE_PREFERENCE_PATTERN.search(sentence)
    )
    selected = durable or sentences[:3]
    summary = " ".join(selected)
    if len(summary) > MEDIUM_SUMMARY_MAX_CHARS:
        summary = summary[:MEDIUM_SUMMARY_MAX_CHARS].rsplit(" ", 1)[0].rstrip()
    return summary or content[:MEDIUM_SUMMARY_MAX_CHARS]


__all__ = ["MemoryAssessment", "assess_memory_content"]
