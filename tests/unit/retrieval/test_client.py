import asyncio
import json
from datetime import UTC, datetime

import httpx
from pydantic import SecretStr

from hostpilot.retrieval import RagRetrievalClient, RagRetrievalError


def test_retrieval_client_sends_server_credentials_and_validates_evidence() -> None:
    received: list[httpx.Request] = []
    payload = {
        "evidence": [
            {
                "source_id": "doc-1",
                "confidence": 0.9,
                "retrieved_at": datetime.now(UTC).isoformat(),
                "retrieval_path": ["vector"],
                "content": "Relevant evidence",
            }
        ]
    }

    def respond(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200, json=payload)

    async def scenario() -> None:
        http_client = httpx.AsyncClient(
            base_url="https://rag.example/api/",
            transport=httpx.MockTransport(respond),
        )
        client = RagRetrievalClient(
            base_url="https://rag.example/api/",
            user_id="user-1",
            key=SecretStr("private-key"),
            client=http_client,
        )
        result = await client.retrieve("find the policy")
        await client.aclose()

        assert result.request_id
        assert result.evidence[0].source_id == "doc-1"

    asyncio.run(scenario())

    request = received[0]
    assert request.url.path == "/api/retrieve"
    assert json.loads(request.content) == {
        "user_id": "user-1",
        "key": "private-key",
        "query": "find the policy",
    }


def test_retrieval_client_rejects_invalid_evidence_without_leaking_key() -> None:
    async def scenario() -> None:
        http_client = httpx.AsyncClient(
            base_url="https://rag.example/",
            transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"evidence": [{}]})),
        )
        client = RagRetrievalClient(
            base_url="https://rag.example/",
            user_id="user-1",
            key=SecretStr("private-key"),
            client=http_client,
        )
        try:
            try:
                await client.retrieve("query")
            except RagRetrievalError as error:
                assert "private-key" not in str(error)
            else:
                raise AssertionError("Invalid response must fail validation.")
        finally:
            await http_client.aclose()

    asyncio.run(scenario())


def test_retrieval_client_rejects_non_http_urls_and_customizes_endpoint_path() -> None:
    try:
        RagRetrievalClient(base_url="file:///tmp/rag", user_id="user-1", key=SecretStr("secret"))
    except ValueError as error:
        assert "HTTP(S)" in str(error)
    else:
        raise AssertionError("Non-HTTP service URLs must fail validation.")

    received: list[httpx.Request] = []

    async def scenario() -> None:
        http_client = httpx.AsyncClient(
            base_url="https://rag.example/api/",
            transport=httpx.MockTransport(
                lambda request: (
                    received.append(request) or httpx.Response(200, json={"evidence": []})
                )
            ),
        )
        client = RagRetrievalClient(
            base_url="https://rag.example/api/",
            user_id="user-1",
            key=SecretStr("secret"),
            retrieval_path="v2/query",
            client=http_client,
        )
        await client.retrieve("query")
        await http_client.aclose()

    asyncio.run(scenario())
    assert received[0].url.path == "/api/v2/query"


def test_retrieval_path_cannot_override_the_configured_service_host() -> None:
    for unsafe_path in (
        "https://other.example/retrieve",
        "//other.example/retrieve",
        "../retrieve",
    ):
        try:
            RagRetrievalClient(
                base_url="https://rag.example/",
                user_id="user-1",
                key=SecretStr("secret"),
                retrieval_path=unsafe_path,
            )
        except ValueError as error:
            assert "relative" in str(error)
        else:
            raise AssertionError("RAG path must not override the configured service host.")
