"""SQLite persistence for the selected local workspace directory."""

from __future__ import annotations

import os
from pathlib import Path

import aiosqlite


class SQLiteWorkspaceConfigurationStore:
    """Store one canonical workspace path without filesystem credentials."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()

    async def initialize(self) -> None:
        """Create the singleton workspace configuration table."""
        if self.database_path.is_symlink():
            raise ValueError("Workspace configuration database cannot be a symbolic link.")
        self.database_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.database_path.parent.is_symlink():
            raise ValueError("Workspace configuration directory cannot be a symbolic link.")
        os.chmod(self.database_path.parent, 0o700)
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS workspace_configuration (
                    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
                    workspace_path TEXT NOT NULL
                )"""
            )
            await connection.commit()
        os.chmod(self.database_path, 0o600)

    async def get(self) -> str | None:
        """Return the selected directory path, if one has been saved."""
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                "SELECT workspace_path FROM workspace_configuration WHERE singleton_id = 1"
            )
            row = await cursor.fetchone()
        return str(row[0]) if row is not None else None

    async def save(self, workspace_path: str) -> None:
        """Persist one canonical workspace path atomically."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """INSERT INTO workspace_configuration (singleton_id, workspace_path)
                   VALUES (1, ?)
                   ON CONFLICT(singleton_id) DO UPDATE SET
                     workspace_path = excluded.workspace_path""",
                (workspace_path,),
            )
            await connection.commit()


__all__ = ["SQLiteWorkspaceConfigurationStore"]
