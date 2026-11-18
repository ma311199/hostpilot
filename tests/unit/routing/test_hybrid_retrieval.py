from hostpilot.routing.hybrid_retrieval import (
    SkillRetrievalDocument,
    fielded_bm25_scores,
    fuse_reranker_order,
    reciprocal_rank_fusion,
)
from hostpilot.routing.vector_index import SemanticSkillMatch


def test_fielded_bm25_applies_name_weight_relative_to_description() -> None:
    documents = (
        SkillRetrievalDocument(
            "business/name-match",
            {"name": ("budget",), "description": ("reporting",)},
        ),
        SkillRetrievalDocument(
            "business/description-match",
            {"name": ("reporting",), "description": ("budget",)},
        ),
    )

    scores = fielded_bm25_scores("budget", documents)

    assert scores["business/name-match"] == 1.5
    assert scores["business/description-match"] == 1.0


def test_missing_metadata_field_uses_mean_of_populated_field_scores() -> None:
    documents = (
        SkillRetrievalDocument(
            "business/tag-match",
            {"name": ("first",), "triggers": ("budget",)},
        ),
        SkillRetrievalDocument(
            "business/tag-miss",
            {"name": ("second",), "triggers": ("reporting",)},
        ),
        SkillRetrievalDocument(
            "legacy/no-triggers",
            {"name": ("third",)},
        ),
    )

    scores = fielded_bm25_scores("budget", documents)

    assert scores["business/tag-match"] == 1.5
    assert scores["business/tag-miss"] == 0.0
    assert scores["legacy/no-triggers"] == 0.75


def test_reciprocal_rank_fusion_uses_equal_weight_for_each_route() -> None:
    documents = (
        SkillRetrievalDocument("business/a", {"name": ("a",)}),
        SkillRetrievalDocument("business/b", {"name": ("b",)}),
    )
    semantic_matches = (
        SemanticSkillMatch("business/b", 0.9),
        SemanticSkillMatch("business/a", 0.8),
    )

    ranked = reciprocal_rank_fusion(
        documents,
        {"business/a": 2.0, "business/b": 1.0},
        semantic_matches,
        limit=2,
    )

    # Each Skill gets one first-place and one second-place contribution.
    assert ranked == ("business/a", "business/b")


def test_reciprocal_rank_fusion_falls_back_to_the_available_route() -> None:
    documents = (
        SkillRetrievalDocument("business/a", {"name": ("a",)}),
        SkillRetrievalDocument("business/b", {"name": ("b",)}),
    )

    lexical_only = reciprocal_rank_fusion(
        documents,
        {"business/b": 2.0, "business/a": 1.0},
        (),
        limit=2,
    )
    semantic_only = reciprocal_rank_fusion(
        documents,
        {},
        (SemanticSkillMatch("business/a", 0.9), SemanticSkillMatch("business/b", 0.7)),
        limit=2,
    )

    assert lexical_only == ("business/b", "business/a")
    assert semantic_only == ("business/a", "business/b")


def test_reranker_fusion_guards_against_a_large_reversal() -> None:
    retrieval = ("business/office", "business/ppt", "business/search", "business/review")
    reranker = ("business/ppt", "business/search", "business/office", "business/review")

    fused = fuse_reranker_order(retrieval, reranker, limit=4)

    assert fused[0] == "business/office"
    assert fused.index("business/office") < reranker.index("business/office")


def test_reranker_fusion_keeps_original_order_when_reranker_is_missing() -> None:
    retrieval = ("business/a", "business/b", "business/c")

    assert fuse_reranker_order(retrieval, (), limit=3) == retrieval


def test_reranker_fusion_does_not_demote_candidate_shared_at_midrank() -> None:
    retrieval = (
        "legacy/multi-search-engine",
        "legacy/ppt-generator",
        "legacy/office-automation",
        "business/code-review-check",
    )
    reranker = (
        "business/code-review-check",
        "legacy/ppt-generator",
        "legacy/office-automation",
        "legacy/multi-search-engine",
    )

    fused = fuse_reranker_order(retrieval, reranker, limit=4)

    assert fused == retrieval


def test_reranker_fusion_uses_reranker_when_its_top_pick_is_supported() -> None:
    retrieval = ("business/a", "business/b", "business/c")
    reranker = ("business/b", "business/a", "business/c")

    fused = fuse_reranker_order(
        retrieval,
        reranker,
        limit=3,
        k=0,
        retrieval_weight=1.0,
        reranker_weight=2.0,
    )

    assert fused[0] == "business/b"
