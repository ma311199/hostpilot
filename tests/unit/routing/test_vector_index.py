import asyncio
import sys
from types import SimpleNamespace
from typing import Any

from hostpilot.budget import RequestBudget
from hostpilot.contracts import BudgetContext, BudgetLimits
from hostpilot.routing.models import EmbeddingBatch
from hostpilot.routing.vector_index import (
    ChromaSkillVectorIndex,
    skill_vector_document,
)


class FakeCollection:
    def __init__(self) -> None:
        self.documents: dict[str, tuple[str, list[float], dict[str, Any]]] = {}

    def count(self) -> int:
        return len(self.documents)

    def get(self, *, include: list[str]) -> dict[str, Any]:
        del include
        return {
            "ids": list(self.documents),
            "metadatas": [self.documents[item][2] for item in self.documents],
        }

    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]],
    ) -> None:
        for skill_id, document, embedding, metadata in zip(
            ids, documents, embeddings, metadatas, strict=True
        ):
            self.documents[skill_id] = (document, embedding, metadata)

    def delete(self, *, ids: list[str]) -> None:
        for skill_id in ids:
            self.documents.pop(skill_id, None)

    def query(self, *, query_embeddings: list[list[float]], n_results: int, include):
        del query_embeddings, include
        selected = list(self.documents.items())[:n_results]
        return {
            "ids": [[skill_id for skill_id, _ in selected]],
            "distances": [[0.1 for _ in selected]],
            "metadatas": [[value[2] for _, value in selected]],
        }


class FakeModels:
    async def embed(self, texts, *, model: str) -> EmbeddingBatch:
        del model
        return EmbeddingBatch(
            vectors=tuple((float(len(text)), 1.0) for text in texts),
            input_tokens=sum(len(text.split()) for text in texts),
        )


def test_chroma_index_synchronizes_local_skill_documents_and_queries(
    tmp_path, monkeypatch
) -> None:
    collection = FakeCollection()

    class FakeClient:
        def get_or_create_collection(self, *, name, metadata):
            assert name.startswith("skill-route-")
            assert metadata["hnsw:space"] == "cosine"
            return collection

    monkeypatch.setitem(
        sys.modules,
        "chromadb",
        SimpleNamespace(PersistentClient=lambda path: FakeClient()),
    )
    index = ChromaSkillVectorIndex(
        tmp_path / "vectors",
        embedding_model="embedding-small",
        models=FakeModels(),
    )
    budget = RequestBudget.start(
        BudgetContext(
            limits=BudgetLimits(
                total_tokens=100,
                tool_calls=1,
                retrieval_calls=1,
                timeout_seconds=30,
            )
        )
    )
    first = skill_vector_document("business/notes", ["summarize meeting notes"])
    results = asyncio.run(index.search("meeting minutes", [first], budget=budget, limit=5))

    assert results[0].skill_id == "business/notes"
    assert results[0].similarity == 0.95
    assert collection.documents["business/notes"][2]["content_hash"] == first.content_hash

    updated = skill_vector_document("business/notes", ["extract action items"])
    asyncio.run(index.search("actions", [updated], budget=budget, limit=5))
    assert collection.documents["business/notes"][0] == "extract action items"
