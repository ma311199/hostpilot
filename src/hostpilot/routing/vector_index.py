"""Local ChromaDB index for non-sensitive business Skill routing metadata."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from hostpilot.budget import RequestBudget
from hostpilot.routing.models import RoutingModelRuntime


@dataclass(frozen=True)
class SkillVectorDocument:
    skill_id: str
    text: str
    content_hash: str


@dataclass(frozen=True)
class SemanticSkillMatch:
    skill_id: str
    similarity: float


class SkillVectorSearch(Protocol):
    async def search(
        self,
        query: str,
        documents: Sequence[SkillVectorDocument],
        *,
        budget: RequestBudget,
        limit: int,
    ) -> tuple[SemanticSkillMatch, ...]: ...


class ChromaSkillVectorIndex:
    """Persist Skill metadata vectors locally and query them with remote embeddings."""

    def __init__(
        self,
        path: Path | str,
        *,
        embedding_model: str,
        models: RoutingModelRuntime,
        max_batch_size: int = 64,
    ) -> None:
        if max_batch_size < 1:
            raise ValueError("Embedding batch size must be positive.")
        self.path = Path(path).expanduser()
        self.embedding_model = embedding_model.strip()
        if not self.embedding_model:
            raise ValueError("Embedding model name cannot be blank.")
        self.models = models
        self.max_batch_size = max_batch_size
        suffix = hashlib.sha256(self.embedding_model.encode("utf-8")).hexdigest()[:12]
        self.collection_name = f"skill-route-{suffix}"
        self._client: Any | None = None
        self._collection: Any | None = None
        self._lock = asyncio.Lock()

    async def search(
        self,
        query: str,
        documents: Sequence[SkillVectorDocument],
        *,
        budget: RequestBudget,
        limit: int,
    ) -> tuple[SemanticSkillMatch, ...]:
        if limit < 1 or not documents or not query.strip():
            return ()
        async with self._lock:
            collection = await asyncio.to_thread(self._get_collection)
            await self._synchronize(collection, documents, budget=budget)
            if await asyncio.to_thread(collection.count) == 0:
                return ()
            query_batch = await self.models.embed([query], model=self.embedding_model)
            if query_batch.input_tokens:
                budget.consume_tokens(query_batch.input_tokens)
            result = await asyncio.to_thread(
                collection.query,
                query_embeddings=[list(query_batch.vectors[0])],
                n_results=min(limit, len(documents)),
                include=["distances", "metadatas"],
            )
        ids = _first_list(result.get("ids"))
        distances = _first_list(result.get("distances"))
        metadatas = _first_list(result.get("metadatas"))
        matches: list[SemanticSkillMatch] = []
        for skill_id, distance, metadata in zip(ids, distances, metadatas, strict=False):
            if not isinstance(skill_id, str) or not isinstance(distance, (float, int)):
                continue
            stored_hash = metadata.get("content_hash") if isinstance(metadata, dict) else None
            current = next((item for item in documents if item.skill_id == skill_id), None)
            if current is None or stored_hash != current.content_hash:
                continue
            # Chroma cosine distance is in [0, 2]; normalize it to [0, 1].
            similarity = min(1.0, max(0.0, 1.0 - float(distance) / 2.0))
            matches.append(SemanticSkillMatch(skill_id=skill_id, similarity=similarity))
        return tuple(matches)

    def _get_collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        try:
            import chromadb
        except ImportError as error:
            raise RuntimeError("ChromaDB is required for semantic Skill routing.") from error
        self.path.mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=str(self.path))
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine", "embedding_model": self.embedding_model},
        )
        return self._collection

    async def _synchronize(
        self,
        collection: Any,
        documents: Sequence[SkillVectorDocument],
        *,
        budget: RequestBudget,
    ) -> None:
        existing = await asyncio.to_thread(collection.get, include=["metadatas"])
        old_ids = existing.get("ids", [])
        old_metadata = existing.get("metadatas", [])
        hashes = {
            skill_id: metadata.get("content_hash")
            for skill_id, metadata in zip(old_ids, old_metadata, strict=False)
            if isinstance(skill_id, str) and isinstance(metadata, dict)
        }
        current_by_id = {document.skill_id: document for document in documents}
        removed = sorted(set(hashes) - set(current_by_id))
        if removed:
            await asyncio.to_thread(collection.delete, ids=removed)
        changed = [
            document
            for document in documents
            if hashes.get(document.skill_id) != document.content_hash
        ]
        for start in range(0, len(changed), self.max_batch_size):
            batch = changed[start : start + self.max_batch_size]
            embedded = await self.models.embed(
                [document.text for document in batch], model=self.embedding_model
            )
            if embedded.input_tokens:
                budget.consume_tokens(embedded.input_tokens)
            await asyncio.to_thread(
                collection.upsert,
                ids=[document.skill_id for document in batch],
                documents=[document.text for document in batch],
                embeddings=[list(vector) for vector in embedded.vectors],
                metadatas=[{"content_hash": document.content_hash} for document in batch],
            )


def skill_vector_document(skill_id: str, fields: Sequence[str]) -> SkillVectorDocument:
    """Create a stable hashed vector document from validated routing metadata."""
    text = "\n".join(value.strip() for value in fields if value.strip())
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return SkillVectorDocument(skill_id=skill_id, text=text, content_hash=digest)


def _first_list(value: Any) -> list[Any]:
    if not isinstance(value, list) or not value:
        return []
    first = value[0]
    return first if isinstance(first, list) else []


__all__ = [
    "ChromaSkillVectorIndex",
    "SemanticSkillMatch",
    "SkillVectorDocument",
    "SkillVectorSearch",
    "skill_vector_document",
]
