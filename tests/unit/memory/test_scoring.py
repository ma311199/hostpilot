from hostpilot.contracts import MemoryImportanceTier
from hostpilot.memory.scoring import assess_memory_content


def test_explicit_durable_preference_scores_high_and_keeps_original_text() -> None:
    content = "请记住：我偏好开发答复使用中文，并先说明改动再列验证结果。"

    assessment = assess_memory_content(content)

    assert assessment.score == 100
    assert assessment.tier is MemoryImportanceTier.HIGH
    assert assessment.summary is None
    assert "explicit_save_intent" in assessment.reason_codes


def test_substantive_neutral_information_scores_medium_and_gets_summary() -> None:
    content = " ".join(
        ["The service stores local metadata in a protected application directory."] * 8
    )

    assessment = assess_memory_content(content)

    assert assessment.score == 50
    assert assessment.tier is MemoryImportanceTier.MEDIUM
    assert assessment.summary is not None
    assert len(assessment.summary) <= 800


def test_transient_logs_acknowledgements_and_credentials_score_low() -> None:
    candidates = (
        "好的。",
        "临时 debug log: tool output and traceback from this request.",
        "api_key: sk-1234567890abcdef1234567890",
    )

    assessments = tuple(assess_memory_content(candidate) for candidate in candidates)

    assert all(assessment.tier is MemoryImportanceTier.LOW for assessment in assessments)
    assert assessments[-1].score == 0
    assert "credential_like_content" in assessments[-1].reason_codes
