import asyncio
import sqlite3
import stat

from hostpilot.persistence.routing_model_configuration import (
    SQLiteRoutingModelConfigurationStore,
)


def test_routing_model_configuration_persists_role_endpoints_and_model_names(tmp_path) -> None:
    store = SQLiteRoutingModelConfigurationStore(tmp_path / "hostpilot.sqlite3")

    async def scenario() -> None:
        await store.initialize()
        assert await store.get() is None
        assert stat.S_IMODE((tmp_path / "hostpilot.sqlite3").stat().st_mode) == 0o600
        await store.save(
            judge_base_url="https://light.example/v1",
            embedding_base_url="https://embed.example/v1",
            reranker_base_url="https://rerank.example/v1",
            embedding_model="embedding-small",
            judge_model="routing-small",
            reranker_model="rerank-small",
            embedding_api_key="embedding-secret",
            reranker_api_key="reranker-secret",
        )
        assert await store.get() == {
            "judge_base_url": "https://light.example/v1",
            "embedding_base_url": "https://embed.example/v1",
            "reranker_base_url": "https://rerank.example/v1",
            "embedding_model": "embedding-small",
            "judge_model": "routing-small",
            "reranker_model": "rerank-small",
            "embedding_api_key": "embedding-secret",
            "reranker_api_key": "reranker-secret",
        }

    asyncio.run(scenario())


def test_shared_endpoint_is_migrated_to_each_optional_model_role(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE routing_model_configuration (
                 singleton INTEGER PRIMARY KEY,
                 base_url TEXT,
                 embedding_model TEXT,
                 judge_model TEXT
               )"""
        )
        connection.execute(
            "INSERT INTO routing_model_configuration VALUES (1, ?, ?, ?)",
            ("https://legacy.example/v1", "embed", "judge"),
        )

    store = SQLiteRoutingModelConfigurationStore(database_path)

    async def scenario() -> None:
        await store.initialize()
        assert await store.get() == {
            "judge_base_url": "https://legacy.example/v1",
            "embedding_base_url": "https://legacy.example/v1",
            "reranker_base_url": "https://legacy.example/v1",
            "embedding_model": "embed",
            "judge_model": "judge",
            "reranker_model": None,
            "embedding_api_key": None,
            "reranker_api_key": None,
        }

    asyncio.run(scenario())
