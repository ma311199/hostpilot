"""OpenAI-compatible remote clients dedicated to Skill routing."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from hostpilot.config import Settings
from hostpilot.models import ModelProvider
from hostpilot.models.factory import create_model_runtime


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: tuple[tuple[float, ...], ...]
    input_tokens: int


@dataclass(frozen=True)
class RerankBatch:
    """Candidate indices ordered by relevance, with optional server token usage."""

    indices: tuple[int, ...]
    scores: tuple[float, ...]
    input_tokens: int


class RerankerClient:
    """Call a llama.cpp compatible ``/v1/rerank`` endpoint."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        endpoint = (base_url or "https://api.openai.com/v1").rstrip("/") + "/"
        self._client = client or httpx.AsyncClient(base_url=endpoint, timeout=30.0)
        self._client.headers["Authorization"] = f"Bearer {api_key}"

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        model: str,
        timeout_seconds: float,
    ) -> RerankBatch:
        if not documents:
            return RerankBatch((), (), 0)
        response = await self._client.post(
            "rerank",
            json={"model": model, "query": query, "documents": list(documents)},
            timeout=max(0.001, timeout_seconds),
        )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(results, list):
            raise ValueError("Reranker response must contain a results array.")
        ranked: list[tuple[int, float]] = []
        seen: set[int] = set()
        for item in results:
            if not isinstance(item, dict):
                raise ValueError("Reranker result entries must be objects.")
            index = item.get("index")
            score = item.get("relevance_score")
            if (
                not isinstance(index, int)
                or isinstance(index, bool)
                or index < 0
                or index >= len(documents)
                or index in seen
                or not isinstance(score, (int, float))
                or isinstance(score, bool)
                or not math.isfinite(float(score))
            ):
                raise ValueError("Reranker returned an invalid candidate score.")
            seen.add(index)
            ranked.append((index, float(score)))
        ranked.sort(key=lambda item: item[1], reverse=True)
        usage = payload.get("usage") if isinstance(payload, dict) else None
        input_tokens = (
            usage.get("total_tokens", 0)
            if isinstance(usage, dict)
            else 0
        )
        return RerankBatch(
            indices=tuple(index for index, _ in ranked),
            scores=tuple(score for _, score in ranked),
            input_tokens=(
                input_tokens if isinstance(input_tokens, int) and input_tokens > 0 else 0
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class RoutingModelRuntime:
    """Optional lightweight, reranker, and embedding clients for routing."""

    def __init__(
        self,
        *,
        chat_provider: ModelProvider | None,
        reranker_client: RerankerClient | None,
        embedding_client: Any | None,
    ) -> None:
        self.chat_provider = chat_provider
        self.reranker_client = reranker_client
        self.embedding_client = embedding_client

    async def embed(self, texts: Sequence[str], *, model: str) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch((), 0)
        if self.embedding_client is None:
            raise RuntimeError("Embedding model is not configured.")
        response = await self.embedding_client.embeddings.create(model=model, input=list(texts))
        ordered = sorted(response.data, key=lambda item: item.index)
        return EmbeddingBatch(
            vectors=tuple(tuple(float(value) for value in item.embedding) for item in ordered),
            input_tokens=int(getattr(response.usage, "total_tokens", 0) or 0),
        )

    async def aclose(self) -> None:
        clients: list[Any] = []
        for provider in (self.chat_provider,):
            chat_model = getattr(provider, "chat_model", None)
            client = getattr(chat_model, "root_async_client", None)
            if client is not None and all(client is not item for item in clients):
                clients.append(client)
        embedding_close = getattr(self.embedding_client, "close", None)
        for client in clients:
            await client.close()
        if embedding_close is not None:
            await embedding_close()
        if self.reranker_client is not None:
            await self.reranker_client.aclose()


def create_routing_model_runtime(
    settings: Settings,
    *,
    judge_model: str | None,
    judge_api_key: str | None,
    judge_base_url: str | None,
    embedding_model: str | None = None,
    embedding_api_key: str | None = None,
    embedding_base_url: str | None = None,
    reranker_model: str | None = None,
    reranker_api_key: str | None = None,
    reranker_base_url: str | None = None,
) -> RoutingModelRuntime:
    """Build optional model clients with independent credentials and endpoints."""

    def create_chat_provider(
        model_name: str | None, api_key: str | None, base_url: str | None
    ) -> ModelProvider | None:
        if model_name is None or not model_name.strip():
            return None
        if api_key is None or not api_key.strip():
            raise ValueError(f"An API key is required for model {model_name.strip()}.")
        role_settings = settings.model_copy(
            update={
                "model_name": model_name.strip(),
                "model_api_key": SecretStr(api_key),
                "model_base_url": base_url,
                "model_streaming": False,
            }
        )
        return create_model_runtime(role_settings).provider

    chat_provider = create_chat_provider(judge_model, judge_api_key, judge_base_url)
    reranker_client = None
    if reranker_model is not None and reranker_model.strip():
        if reranker_api_key is None or not reranker_api_key.strip():
            raise ValueError(f"An API key is required for model {reranker_model.strip()}.")
        reranker_client = RerankerClient(
            api_key=reranker_api_key,
            base_url=reranker_base_url,
        )
    embedding_client = None
    try:
        if embedding_model is not None and embedding_model.strip():
            if embedding_api_key is None or not embedding_api_key.strip():
                raise ValueError(f"An API key is required for model {embedding_model.strip()}.")
            from openai import AsyncOpenAI

            embedding_client = AsyncOpenAI(api_key=embedding_api_key, base_url=embedding_base_url)
    except ImportError as error:
        raise RuntimeError(
            "Install the agents dependency group to use remote Embedding models."
        ) from error
    return RoutingModelRuntime(
        chat_provider=chat_provider,
        reranker_client=reranker_client,
        embedding_client=embedding_client,
    )


__all__ = [
    "EmbeddingBatch",
    "RerankBatch",
    "RerankerClient",
    "RoutingModelRuntime",
    "create_routing_model_runtime",
]
