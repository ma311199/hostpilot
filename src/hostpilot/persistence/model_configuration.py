"""SQLite storage for non-secret model provider settings."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

import aiosqlite


class ModelConfiguration(TypedDict):
    """Persisted model options, intentionally excluding credentials."""

    model_name: str
    base_url: str | None
    streaming: bool


class SQLiteModelConfigurationStore:
    """Persist model options while keeping credentials out of the database."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()

    async def initialize(self) -> None:
        """Create the single-row model configuration table."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS model_configuration (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    model_name TEXT NOT NULL,
                    base_url TEXT,
                    streaming INTEGER NOT NULL
                )"""
            )
            await connection.commit()

    async def get(self) -> ModelConfiguration | None:
        """Return saved non-secret options, if present."""
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """SELECT model_name, base_url, streaming
                   FROM model_configuration WHERE singleton = 1"""
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return ModelConfiguration(
            model_name=str(row["model_name"]),
            base_url=row["base_url"],
            streaming=bool(row["streaming"]),
        )

    async def save(self, *, model_name: str, base_url: str | None, streaming: bool) -> None:
        """Atomically replace model options; this API accepts no credentials."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """INSERT INTO model_configuration (singleton, model_name, base_url, streaming)
                   VALUES (1, ?, ?, ?)
                   ON CONFLICT(singleton) DO UPDATE SET
                     model_name = excluded.model_name,
                     base_url = excluded.base_url,
                     streaming = excluded.streaming""",
                (model_name, base_url, int(streaming)),
            )
            await connection.commit()
