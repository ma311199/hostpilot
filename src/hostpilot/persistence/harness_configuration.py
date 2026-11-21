"""Validated, non-secret runtime controls stored for the local Harness."""

from __future__ import annotations

import json
import os
from pathlib import Path

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field


class HarnessConfiguration(BaseModel):
    """Runtime limits that can be changed without rebuilding model or MCP clients."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_context_messages: int = Field(default=40, gt=0, le=500)
    max_memory_context_chars: int = Field(default=16_000, gt=0, le=100_000)
    max_request_chars: int = Field(default=32_000, gt=0, le=100_000)
    max_response_chars: int = Field(default=100_000, gt=0, le=200_000)
    request_token_budget: int = Field(default=20_000, gt=0)
    request_tool_call_budget: int = Field(default=5, ge=0, le=100)
    request_retrieval_call_budget: int = Field(default=2, ge=0, le=100)
    request_timeout_seconds: float = Field(default=60, gt=0, le=600)
    plan_max_tasks: int = Field(default=10, gt=0, le=100)
    plan_tool_agent_max_turns: int = Field(default=5, gt=0, le=32)
    plan_tool_candidate_limit: int = Field(default=5, gt=0, le=20)
    routing_minimum_business_confidence: float = Field(default=0.85, ge=0.5, le=1)
    routing_minimum_business_margin: float = Field(default=0.30, ge=0, le=1)
    routing_minimum_plan_candidate_confidence: float = Field(default=0.50, ge=0.5, le=1)
    routing_max_plan_candidates: int = Field(default=5, gt=0, le=20)
    routing_max_business_candidates: int = Field(default=32, gt=0, le=100)


class SQLiteHarnessConfigurationStore:
    """Persist one validated Harness runtime configuration as a JSON document."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()

    async def initialize(self) -> None:
        """Create the singleton configuration table."""
        if self.database_path.is_symlink():
            raise ValueError("Harness configuration database cannot be a symbolic link.")
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.database_path.parent.is_symlink():
            raise ValueError("Harness configuration directory cannot be a symbolic link.")
        os.chmod(self.database_path.parent, 0o700)
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS harness_configuration (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    configuration_json TEXT NOT NULL
                )"""
            )
            await connection.commit()
        os.chmod(self.database_path, 0o600)

    async def get(self) -> HarnessConfiguration | None:
        """Load the persisted runtime controls, if a user has saved them."""
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                "SELECT configuration_json FROM harness_configuration WHERE singleton_id = 1"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        return HarnessConfiguration.model_validate(payload)

    async def save(self, configuration: HarnessConfiguration) -> None:
        """Replace the complete validated configuration atomically."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """INSERT INTO harness_configuration (singleton_id, configuration_json)
                   VALUES (1, ?)
                   ON CONFLICT(singleton_id) DO UPDATE SET
                     configuration_json = excluded.configuration_json""",
                (configuration.model_dump_json(),),
            )
            await connection.commit()


__all__ = ["HarnessConfiguration", "SQLiteHarnessConfigurationStore"]
