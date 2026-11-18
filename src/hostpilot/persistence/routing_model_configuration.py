"""SQLite storage for non-secret Skill routing model options."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import aiosqlite


class RoutingModelConfiguration(TypedDict):
    """Persisted optional routing model settings and local embedding/reranker keys."""

    judge_base_url: str | None
    embedding_base_url: str | None
    reranker_base_url: str | None
    embedding_model: str | None
    judge_model: str | None
    reranker_model: str | None
    embedding_api_key: str | None
    reranker_api_key: str | None


class SQLiteRoutingModelConfigurationStore:
    """Persist routing model settings and local embedding/reranker credentials."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()

    async def initialize(self) -> None:
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS routing_model_configuration (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    judge_base_url TEXT,
                    embedding_base_url TEXT,
                    reranker_base_url TEXT,
                    embedding_model TEXT NOT NULL DEFAULT '',
                    judge_model TEXT NOT NULL DEFAULT '',
                    reranker_model TEXT,
                    embedding_api_key TEXT,
                    reranker_api_key TEXT
                )"""
            )
            cursor = await connection.execute("PRAGMA table_info(routing_model_configuration)")
            columns = {str(row[1]) for row in await cursor.fetchall()}
            if "reranker_model" not in columns:
                await connection.execute(
                    "ALTER TABLE routing_model_configuration ADD COLUMN reranker_model TEXT"
                )
            for column in (
                "judge_base_url",
                "embedding_base_url",
                "reranker_base_url",
                "embedding_api_key",
                "reranker_api_key",
            ):
                if column not in columns:
                    await connection.execute(
                        f"ALTER TABLE routing_model_configuration ADD COLUMN {column} TEXT"
                    )
            # Migrate the former shared endpoint to every configured role.
            if "base_url" in columns:
                await connection.execute(
                    """UPDATE routing_model_configuration SET
                         judge_base_url = COALESCE(judge_base_url, base_url),
                         embedding_base_url = COALESCE(embedding_base_url, base_url),
                         reranker_base_url = COALESCE(reranker_base_url, base_url)"""
                )
            await connection.commit()
        self.database_path.chmod(0o600)

    async def get(self) -> RoutingModelConfiguration | None:
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """SELECT judge_base_url, embedding_base_url, reranker_base_url,
                          embedding_model, judge_model, reranker_model,
                          embedding_api_key, reranker_api_key
                   FROM routing_model_configuration WHERE singleton = 1"""
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return RoutingModelConfiguration(
            judge_base_url=row["judge_base_url"],
            embedding_base_url=row["embedding_base_url"],
            reranker_base_url=row["reranker_base_url"],
            embedding_model=str(row["embedding_model"]) if row["embedding_model"] else None,
            judge_model=str(row["judge_model"]) if row["judge_model"] else None,
            reranker_model=str(row["reranker_model"]) if row["reranker_model"] else None,
            embedding_api_key=(str(row["embedding_api_key"]) if row["embedding_api_key"] else None),
            reranker_api_key=(str(row["reranker_api_key"]) if row["reranker_api_key"] else None),
        )

    async def save(
        self,
        *,
        judge_base_url: str | None,
        embedding_base_url: str | None,
        reranker_base_url: str | None,
        embedding_model: str | None,
        judge_model: str | None,
        reranker_model: str | None = None,
        embedding_api_key: str | None = None,
        reranker_api_key: str | None = None,
    ) -> None:
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """INSERT INTO routing_model_configuration
                       (singleton, judge_base_url, embedding_base_url, reranker_base_url,
                        embedding_model, judge_model, reranker_model,
                        embedding_api_key, reranker_api_key)
                   VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET
                     judge_base_url = excluded.judge_base_url,
                     embedding_base_url = excluded.embedding_base_url,
                     reranker_base_url = excluded.reranker_base_url,
                     embedding_model = excluded.embedding_model,
                     judge_model = excluded.judge_model,
                     reranker_model = excluded.reranker_model,
                     embedding_api_key = excluded.embedding_api_key,
                     reranker_api_key = excluded.reranker_api_key""",
                (
                    judge_base_url,
                    embedding_base_url,
                    reranker_base_url,
                    embedding_model or "",
                    judge_model or "",
                    reranker_model,
                    embedding_api_key,
                    reranker_api_key,
                ),
            )
            await connection.commit()


__all__ = ["RoutingModelConfiguration", "SQLiteRoutingModelConfigurationStore"]
