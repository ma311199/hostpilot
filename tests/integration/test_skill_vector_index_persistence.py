from __future__ import annotations

import asyncio
import hashlib
import math

import pytest

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits
from hostpilot.routing.models import EmbeddingBatch
from hostpilot.routing.vector_index import ChromaSkillVectorIndex, skill_vector_document

pytest.importorskip("chromadb")


class DeterministicEmbeddingRuntime:
    """Provide stable local vectors so persistence can be tested without a remote API."""

    async def embed(self, texts: list[str], *, model: str) -> EmbeddingBatch:
        del model
        vectors = tuple(_vector(text) for text in texts)
        return EmbeddingBatch(vectors=vectors, input_tokens=0)


def _vector(text: str) -> tuple[float, ...]:
    raw = hashlib.sha256(text.casefold().encode("utf-8")).digest()
    values = [raw[index] / 255.0 for index in range(16)]
    norm = math.sqrt(sum(value * value for value in values))
    return tuple(value / norm for value in values)


def _budget() -> RequestBudget:
    return RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100,
                tool_calls=1,
                retrieval_calls=1,
                timeout_seconds=30,
            )
        )
    )


def test_chroma_index_persists_sync_updates_and_deletions_across_clients(tmp_path) -> None:
    index_path = tmp_path / "skill-vectors"
    first_runtime = DeterministicEmbeddingRuntime()
    first_index = ChromaSkillVectorIndex(
        index_path,
        embedding_model="test-embedding-v1",
        models=first_runtime,
    )
    meeting = skill_vector_document(
        "business/meeting-minutes", ["整理会议记录", "提取决策和行动项"]
    )
    code_review = skill_vector_document(
        "business/code-review", ["检查代码变更", "解释缺陷和安全风险"]
    )

    first_results = asyncio.run(
        first_index.search(
            "整理会议记录并提取行动项",
            [meeting, code_review],
            budget=_budget(),
            limit=2,
        )
    )

    assert {result.skill_id for result in first_results} == {
        "business/meeting-minutes",
        "business/code-review",
    }

    # A new index instance simulates an application restart and must read the same DB.
    restarted_index = ChromaSkillVectorIndex(
        index_path,
        embedding_model="test-embedding-v1",
        models=DeterministicEmbeddingRuntime(),
    )
    updated_meeting = skill_vector_document(
        "business/meeting-minutes", ["整理访谈材料", "输出主题和后续行动"]
    )
    remaining_results = asyncio.run(
        restarted_index.search(
            "整理访谈材料\n输出主题和后续行动",
            [updated_meeting],
            budget=_budget(),
            limit=2,
        )
    )

    assert [result.skill_id for result in remaining_results] == ["business/meeting-minutes"]

    # A final restart confirms updated content is searchable and removed Skills are gone.
    final_index = ChromaSkillVectorIndex(
        index_path,
        embedding_model="test-embedding-v1",
        models=DeterministicEmbeddingRuntime(),
    )
    final_results = asyncio.run(
        final_index.search(
            "整理访谈材料\n输出主题和后续行动",
            [updated_meeting],
            budget=_budget(),
            limit=2,
        )
    )

    assert [result.skill_id for result in final_results] == ["business/meeting-minutes"]
    assert final_results[0].similarity == pytest.approx(1.0)
