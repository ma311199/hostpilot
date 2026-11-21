import asyncio
import json

import httpx

from hostpilot.routing.models import RerankerClient


def test_reranker_posts_to_llama_cpp_endpoint_and_orders_scores() -> None:
    requests: list[httpx.Request] = []

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.12},
                ],
                "usage": {"total_tokens": 8},
            },
        )

    async def scenario() -> None:
        client = httpx.AsyncClient(
            base_url="http://reranker.local/v1/",
            transport=httpx.MockTransport(respond),
        )
        reranker = RerankerClient(
            api_key="reranker-secret",
            base_url="http://reranker.local/v1",
            client=client,
        )
        result = await reranker.rerank(
            "review this code",
            ["low relevance", "high relevance"],
            model="qwen-reranker",
            timeout_seconds=5,
        )
        await reranker.aclose()

        assert result.indices == (1, 0)
        assert result.scores == (0.91, 0.12)
        assert result.input_tokens == 8

    asyncio.run(scenario())

    request = requests[0]
    assert request.url.path == "/v1/rerank"
    assert request.headers["authorization"] == "Bearer reranker-secret"
    assert json.loads(request.content) == {
        "model": "qwen-reranker",
        "query": "review this code",
        "documents": ["low relevance", "high relevance"],
    }
