from hostpilot.contracts import ContextSnapshot, ConversationMessage, MessageRole
from hostpilot.routing.query_resolution import resolve_query


def test_standalone_request_is_preserved_without_model_rewrite() -> None:
    result = resolve_query("帮我整理项目会议纪要", ContextSnapshot(request_id="standalone"))

    assert result.retrieval_query == "帮我整理项目会议纪要"
    assert result.reason_code == "query_preserved"


def test_continuation_uses_recent_task_context() -> None:
    context = ContextSnapshot(
        request_id="follow-up",
        messages=(
            ConversationMessage(role=MessageRole.USER, content="检查 src/hostpilot/api/app.py"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="我检查了 API 配置处理。"),
        ),
    )

    result = resolve_query("继续", context)

    assert result.retrieval_query is not None
    assert "当前任务上下文：检查 src/hostpilot/api/app.py" in result.retrieval_query
    assert "我检查了 API 配置处理。" in result.retrieval_query
    assert result.reason_code == "query_continuation_resolved"


def test_ambiguous_reference_is_deferred_to_bounded_candidate_matching() -> None:
    context = ContextSnapshot(
        request_id="ambiguous-reference",
        messages=(
            ConversationMessage(role=MessageRole.USER, content="检查两个文件"),
            ConversationMessage(
                role=MessageRole.ASSISTANT,
                content="我对比了 `src/first.py` 和 `src/second.py`。",
            ),
        ),
    )

    result = resolve_query("修改这个", context)

    assert result.retrieval_query is None
    assert result.reason_code == "query_reference_needs_candidate_matching"
    assert result.requires_candidate_matching


def test_unique_reference_is_resolved_from_one_explicit_context_object() -> None:
    context = ContextSnapshot(
        request_id="unique-reference",
        messages=(
            ConversationMessage(
                role=MessageRole.USER,
                content="请检查 `src/hostpilot/api/app.py`。",
            ),
        ),
    )

    result = resolve_query("修改这个", context)

    assert result.retrieval_query == "当前请求：修改这个\n指代上下文：src/hostpilot/api/app.py"
    assert result.reason_code == "query_reference_resolved"


def test_reference_without_explicit_context_object_requests_clarification() -> None:
    context = ContextSnapshot(
        request_id="implicit-reference",
        messages=(
            ConversationMessage(role=MessageRole.USER, content="帮我总结项目情况。"),
            ConversationMessage(role=MessageRole.ASSISTANT, content="已经总结好了。"),
        ),
    )

    result = resolve_query("修改这个", context)

    assert result.retrieval_query is None
    assert result.reason_code == "query_reference_needs_candidate_matching"
    assert result.requires_candidate_matching


def test_semantic_reference_requires_candidate_matching_from_memory() -> None:
    result = resolve_query(
        "把这段关于回归测试的内容整理一下。",
        ContextSnapshot(
            request_id="memory-reference",
            long_term_memory="项目复盘：上线前需要完成回归测试，并记录失败用例。",
        ),
    )

    assert result.retrieval_query is None
    assert result.requires_candidate_matching
    assert result.reason_code == "query_reference_needs_candidate_matching"


def test_generic_reference_with_memory_but_no_topic_requests_clarification() -> None:
    result = resolve_query(
        "把这段内容整理一下。",
        ContextSnapshot(
            request_id="unanchored-memory-reference",
            long_term_memory="阳台植物每周浇水两次，春季修剪枯叶。",
        ),
    )

    assert result.retrieval_query is None
    assert not result.requires_candidate_matching
    assert result.reason_code == "query_context_ambiguous"


def test_evaluation_reference_phrasings_are_recognized() -> None:
    context = ContextSnapshot(
        request_id="reference-phrases",
        long_term_memory="发布前需要回归测试。",
    )

    for message in (
        "请把上线前那段关于回归检查的记录整理成清单。",
        "把刚才提到的数据升级准备工作列出来。",
        "把上面提到的发布检查内容列成清单。",
        "把刚才提到的上线前验证步骤改写成待办项。",
    ):
        result = resolve_query(message, context)
        assert result.requires_candidate_matching, message


def test_semantic_reference_without_context_requests_clarification() -> None:
    result = resolve_query("把这段内容整理一下。", ContextSnapshot(request_id="no-memory"))

    assert result.retrieval_query is None
    assert result.reason_code == "query_context_ambiguous"
    assert result.clarification_prompt == "请粘贴需要整理的内容，并说明希望整理成什么格式。"


def test_ambiguous_edit_and_analysis_requests_name_the_missing_information() -> None:
    edit = resolve_query("帮我修改一下它。", ContextSnapshot(request_id="edit-no-context"))
    analysis = resolve_query(
        "分析一下这个问题。", ContextSnapshot(request_id="analysis-no-context")
    )

    assert edit.clarification_prompt == "请说明“它”具体指什么，并告诉我希望如何修改。"
    assert (
        analysis.clarification_prompt == "请补充要分析的问题或材料；如果有关注重点，也请一并说明。"
    )


def test_explicit_quoted_code_resolves_reference_in_current_request() -> None:
    result = resolve_query(
        "评审这个 diff：`if user.is_admin: grant_all()`。请说明风险和依据。",
        ContextSnapshot(request_id="quoted-code-reference"),
    )

    assert result.retrieval_query == (
        "评审这个 diff：`if user.is_admin: grant_all()`。请说明风险和依据。"
    )
    assert result.reason_code == "query_preserved"


def test_labeled_material_resolves_reference_in_current_request() -> None:
    message = "把这段会议记录整理成纪要。记录：周五 15 点评审发布方案。"

    result = resolve_query(message, ContextSnapshot(request_id="inline-material"))

    assert result.retrieval_query == message
    assert result.reason_code == "query_preserved"


def test_discussion_material_after_request_colon_resolves_reference() -> None:
    message = "从这段讨论里提炼决策和行动项：先修复登录超时，再发布移动端；陈晨负责修复。"

    result = resolve_query(message, ContextSnapshot(request_id="inline-discussion"))

    assert result.retrieval_query == message
    assert result.reason_code == "query_preserved"


def test_punctuation_only_request_skips_retrieval() -> None:
    result = resolve_query("???", ContextSnapshot(request_id="symbols"))

    assert result.retrieval_query is None
    assert result.reason_code == "query_not_parseable"
