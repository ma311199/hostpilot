"""Conservative resolution of short, context-dependent routing queries."""

from __future__ import annotations

import re
from dataclasses import dataclass

from hostpilot.contracts import ContextSnapshot, MessageRole

_CONTINUATION = re.compile(
    r"^(?:继续(?:刚才|上面|之前)?(?:的任务|的方案)?|按(?:刚才|上面|之前)(?:的方案)?继续|"
    r"照(?:刚才|上面|之前)(?:的方案)?继续|continue(?: with that| the task)?|go on|proceed)"
    r"[。！!？?]*$",
    re.IGNORECASE,
)
_REFERENTIAL = re.compile(
    r"(?:它|这个|那个|这些|那些|这段(?:内容|文字|代码|文本)?|那段(?:内容|文字|代码|文本)?|"
    r"这份(?:内容|文件|文档)?|那份(?:内容|文件|文档)?|这条(?:内容|记录|信息)?|"
    r"上述|上面提到的|刚才提到的|前面提到的|同样|再(?:改|做|运行|检查)|"
    r"\b(?:it|the above|the same|this|that|these|those)(?:\s+one)?[.!?。！？]*$)"
)
_QUOTED = re.compile(r"`([^`]{1,160})`|[“「『\"]([^”」』\"]{1,160})[”」』\"]")
_INLINE_MATERIAL = re.compile(
    r"(?:记录|内容|文字|文本|材料|代码|diff|对话|邮件)\s*[:：]\s*(?P<material>\S.*)",
    re.IGNORECASE | re.DOTALL,
)
_INLINE_REFERENTIAL_MATERIAL = re.compile(
    r"(?:从)?(?:这段|这份|以下|下面(?:的)?).*?"
    r"(?:讨论|对话|笔记|记录|材料|内容)(?:里|中)?[^:：]{0,50}[:：]\s*(?P<material>\S.*)",
    re.IGNORECASE | re.DOTALL,
)
_PATH = re.compile(r"(?<![\w])(?:[\w.-]+/)+[\w.-]+(?:\.[A-Za-z0-9]+)?")
_GENERIC_REFERENCE_TERMS = (
    "上面提到的",
    "前面提到的",
    "刚才提到的",
    "之前提到的",
    "这段内容",
    "那段内容",
    "这段文字",
    "那段文字",
    "这段文本",
    "那段文本",
    "这段",
    "那段",
    "这份",
    "那份",
    "这条",
    "那条",
    "这个",
    "那个",
    "这些",
    "那些",
    "上述",
    "内容",
    "文字",
    "文本",
    "材料",
    "记录",
    "方案",
    "文档",
    "文件",
    "任务",
    "事情",
    "部分",
    "段落",
    "整理",
    "改写",
    "润色",
    "总结",
    "检查",
    "修改",
    "处理",
    "分析",
    "生成",
    "列出",
    "概括",
    "提取",
    "翻译",
    "解释",
    "转换",
    "复述",
    "压缩",
    "扩写",
    "帮我",
    "麻烦",
    "请",
    "把",
    "将",
    "关于",
    "相关",
    "其中",
    "里面",
    "一下",
    "一遍",
    "出来",
    "给我",
    "并",
    "的",
    "成",
    "为",
    "它",
    "这",
    "那",
    "please",
    "could",
    "would",
    "help",
    "organize",
    "summarize",
    "rewrite",
    "review",
    "check",
    "modify",
    "edit",
    "process",
    "analyze",
    "translate",
    "explain",
    "list",
    "extract",
    "format",
    "content",
    "material",
    "document",
    "file",
    "text",
    "this",
    "that",
    "these",
    "those",
    "same",
    "above",
    "one",
    "it",
    "the",
    "you",
    "me",
    "for",
    "into",
    "with",
    "and",
    "again",
    "一下",
)


@dataclass(frozen=True)
class QueryResolution:
    """Retrieval query or a clarification to return before retrieval starts."""

    retrieval_query: str | None
    clarification_prompt: str | None = None
    reason_code: str = "query_preserved"
    requires_candidate_matching: bool = False


def resolve_query(message: str, context: ContextSnapshot) -> QueryResolution:
    """Preserve standalone text and conservatively attach the current task context."""
    raw = message.strip()
    if not raw:
        return QueryResolution(
            None,
            "请补充你希望我完成的目标。",
            "query_empty",
        )
    if re.search(r"[A-Za-z0-9\u4e00-\u9fff]", raw) is None:
        return QueryResolution(
            None,
            "我无法从这条消息中识别出请求内容，请补充你希望我完成的目标。",
            "query_not_parseable",
        )

    recent = [
        item
        for item in context.messages[-8:]
        if item.role in {MessageRole.USER, MessageRole.ASSISTANT}
    ]
    latest_user_index = next(
        (
            index
            for index in range(len(recent) - 1, -1, -1)
            if recent[index].role is MessageRole.USER
        ),
        None,
    )
    latest_user = recent[latest_user_index].content.strip() if latest_user_index is not None else ""
    assistant_after_user = [
        item.content.strip()
        for item in recent[
            (latest_user_index + 1) if latest_user_index is not None else len(recent) :
        ]
        if item.role is MessageRole.ASSISTANT and item.content.strip()
    ]
    latest_assistant = assistant_after_user[-1] if assistant_after_user else ""
    summary = (context.session_summary or "").strip()
    focus_parts = [value for value in (latest_user, latest_assistant) if value]
    focus = "\n".join(focus_parts) or summary

    if _CONTINUATION.fullmatch(raw):
        if not focus:
            return _ambiguous()
        return QueryResolution(
            retrieval_query=f"当前请求：{raw}\n当前任务上下文：{focus}",
            reason_code="query_continuation_resolved",
        )

    if _REFERENTIAL.search(raw):
        # Keep self-contained requests such as "review this diff: `...`" intact.
        # Quoted code, paths, or labeled material included in this turn resolve
        # the reference without needing prior conversation context.
        if _extract_referents(raw) or _has_inline_material(raw):
            return QueryResolution(retrieval_query=raw)
        if not focus and not context.long_term_memory:
            return _ambiguous(raw)
        if not focus and context.long_term_memory and not _has_topical_anchor(raw):
            return _ambiguous(raw)
        referents = _extract_referents(focus)
        if len(referents) == 1:
            context_hint = referents[0]
            return QueryResolution(
                retrieval_query=f"当前请求：{raw}\n指代上下文：{context_hint}",
                reason_code="query_reference_resolved",
            )
        return QueryResolution(
            retrieval_query=None,
            reason_code="query_reference_needs_candidate_matching",
            requires_candidate_matching=True,
        )

    return QueryResolution(retrieval_query=raw)


def _extract_referents(text: str) -> tuple[str, ...]:
    candidates = [match for groups in _QUOTED.findall(text) for match in groups if match]
    candidates.extend(_PATH.findall(text))
    unique = tuple(dict.fromkeys(value.strip() for value in candidates if value.strip()))
    return unique


def _has_inline_material(message: str) -> bool:
    """Return whether a labeled payload in the current turn resolves its reference."""
    match = _INLINE_MATERIAL.search(message) or _INLINE_REFERENTIAL_MATERIAL.search(message)
    if match is None:
        return False
    material = re.sub(r"\s+", "", match.group("material"))
    return len(material) >= 2


def _has_topical_anchor(message: str) -> bool:
    """Reject memory-only resolution when the request has no topic beyond its pronoun/task."""
    remainder = re.sub(r"[^\w\u4e00-\u9fff]", "", message.casefold())
    for term in sorted(_GENERIC_REFERENCE_TERMS, key=len, reverse=True):
        remainder = remainder.replace(term, "")
    return len(remainder) >= 2


def _ambiguous(message: str = "") -> QueryResolution:
    if re.search(
        r"(?:整理|改写|润色).*(?:内容|文字|文本)|(?:内容|文字|文本).*(?:整理|改写|润色)", message
    ):
        prompt = "请粘贴需要整理的内容，并说明希望整理成什么格式。"
    elif re.search(r"修改|改一下|编辑", message):
        prompt = "请说明“它”具体指什么，并告诉我希望如何修改。"
    elif re.search(r"分析", message):
        prompt = "请补充要分析的问题或材料；如果有关注重点，也请一并说明。"
    else:
        prompt = "这句话需要结合前文理解，但我无法唯一确定你指的内容。请补充具体任务或对象。"
    return QueryResolution(
        None,
        prompt,
        "query_context_ambiguous",
    )


__all__ = ["QueryResolution", "resolve_query"]
