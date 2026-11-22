"""SQLite persistence for per-Skill execution entry preferences."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, TypedDict, cast

import aiosqlite

SkillExecutionMode = Literal["yaml", "markdown", "both"]


class SkillExecutionPreference(TypedDict):
    skill_id: str
    execution_mode: SkillExecutionMode


class SQLiteSkillExecutionStore:
    """Persist explicit Skill execution choices; defaults stay derived from package files."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()

    async def initialize(self) -> None:
        """Create or migrate the execution preference table."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                cursor = await connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type = 'table' "
                    "AND name = 'skill_execution_preferences'"
                )
                row = await cursor.fetchone()
                if row is None:
                    await connection.execute(_create_table_sql("skill_execution_preferences"))
                elif "'deepagents'" in str(row[0]).lower().replace(" ", ""):
                    await connection.execute(_create_table_sql("skill_execution_preferences_new"))
                    await connection.execute(
                        """INSERT INTO skill_execution_preferences_new (skill_id, execution_mode)
                           SELECT skill_id,
                                  CASE execution_mode
                                      WHEN 'deepagents' THEN 'markdown'
                                      ELSE execution_mode
                                  END
                           FROM skill_execution_preferences"""
                    )
                    await connection.execute("DROP TABLE skill_execution_preferences")
                    await connection.execute(
                        "ALTER TABLE skill_execution_preferences_new "
                        "RENAME TO skill_execution_preferences"
                    )
            except Exception:
                await connection.rollback()
                raise
            else:
                await connection.commit()

    async def list(self) -> dict[str, SkillExecutionMode]:
        """Return explicitly saved modes in stable key order."""
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                "SELECT skill_id, execution_mode FROM skill_execution_preferences ORDER BY skill_id"
            )
            rows = await cursor.fetchall()
        return {str(skill_id): cast(SkillExecutionMode, str(mode)) for skill_id, mode in rows}

    async def set(self, skill_id: str, execution_mode: SkillExecutionMode) -> None:
        """Save one validated preference without storing Skill content."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """INSERT INTO skill_execution_preferences (skill_id, execution_mode)
                   VALUES (?, ?)
                   ON CONFLICT(skill_id) DO UPDATE SET execution_mode = excluded.execution_mode""",
                (skill_id, execution_mode),
            )
            await connection.commit()

    async def delete(self, skill_id: str) -> None:
        """Remove a preference when its Skill package is deleted."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                "DELETE FROM skill_execution_preferences WHERE skill_id = ?", (skill_id,)
            )
            await connection.commit()


def _create_table_sql(table_name: str) -> str:
    return f"""CREATE TABLE {table_name} (
        skill_id TEXT PRIMARY KEY,
        execution_mode TEXT NOT NULL
          CHECK (execution_mode IN ('yaml', 'markdown', 'both'))
    )"""


__all__ = ["SQLiteSkillExecutionStore", "SkillExecutionMode", "SkillExecutionPreference"]
