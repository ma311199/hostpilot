from hostpilot.evaluation.routing_metrics import enabled_skill_coverage


def test_enabled_skill_coverage_reads_expected_skill_id_from_case() -> None:
    cases = [
        {"expected": {"skill_id": "business/code-review-check"}},
        {"expected": {"skill_id": "legacy/office-automation"}},
        {"expected": {"skill_id": None}},
    ]

    covered, uncovered = enabled_skill_coverage(
        {
            "business/code-review-check",
            "legacy/office-automation",
            "legacy/ppt-generator",
        },
        cases,
    )

    assert covered == {"business/code-review-check", "legacy/office-automation"}
    assert uncovered == {"legacy/ppt-generator"}
