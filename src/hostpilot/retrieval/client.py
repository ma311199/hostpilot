"""HTTP adapter for the independent RAG retrieval service."""

from __future__ import annotations

from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import SecretStr

from hostpilot.contracts import EvidenceSet


class RagRetrievalError(RuntimeError):
    """Raised when the RAG service fails or returns an invalid evidence payload."""


class RagRetrievalClient:
    """Call the RAG retrieval endpoint without exposing credentials to Agent tools."""

    def __init__(
        self,
        *,
        base_url: str,
        user_id: str,
        key: SecretStr,
        retrieval_path: str = "/retrieve",
        timeout_seconds: float = 15,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        parsed_url = httpx.URL(base_url)
        if (
            parsed_url.scheme not in {"http", "https"}
            or not parsed_url.host
            or parsed_url.username
            or parsed_url.password
            or parsed_url.query
            or parsed_url.fragment
        ):
            raise ValueError("RAG base URL must be a credential-free absolute HTTP(S) URL.")
        if not user_id.strip():
            raise ValueError("RAG user ID must not be blank.")
        parsed_path = urlsplit(retrieval_path)
        path_parts = parsed_path.path.strip("/").split("/")
        if (
            not parsed_path.path.strip("/")
            or parsed_path.scheme
            or parsed_path.netloc
            or parsed_path.query
            or parsed_path.fragment
            or ".." in path_parts
        ):
            raise ValueError("RAG retrieval path must stay relative to the configured service.")
        self.retrieval_path = parsed_path.path.lstrip("/")
        self.user_id = user_id
        self.key = key
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
        )

    async def retrieve(self, query: str) -> EvidenceSet:
        """Submit the trusted user identity, secret key, and query to `/retrieve`."""
        request_id = str(uuid4())
        try:
            response = await self.client.post(
                self.retrieval_path,
                json={
                    "user_id": self.user_id,
                    "key": self.key.get_secret_value(),
                    "query": query,
                },
            )
            response.raise_for_status()
            if len(response.content) > 1_048_576:
                raise ValueError("RAG response exceeds the 1 MiB limit.")
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("RAG response must be a JSON object.")
            payload["request_id"] = request_id
            return EvidenceSet.model_validate(payload)
        except (httpx.HTTPError, ValueError, TypeError) as error:
            raise RagRetrievalError("RAG retrieval failed or returned invalid evidence.") from error

    async def aclose(self) -> None:
        """Close the owned HTTP client after the application lifespan ends."""
        if self._owns_client:
            await self.client.aclose()


__all__ = ["RagRetrievalClient", "RagRetrievalError"]
