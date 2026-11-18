"""SQLite storage for non-secret remote MCP connection settings."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, TypedDict, cast

import aiosqlite


class MCPServerConfiguration(TypedDict):
    """Persisted server settings. Secret header values are intentionally absent."""

    name: str
    transport: Literal["streamable_http", "stdio"]
    url: str | None
    command: str | None
    args: tuple[str, ...]
    cwd: str | None
    timeout_seconds: float
    enabled: bool
    description: str
    api_key_header: str | None
    api_key_env: str | None


class SQLiteMCPConfigurationStore:
    """Store remote MCP metadata without accepting credential values."""

    def __init__(self, database_path: Path | str) -> None:
        self.database_path = Path(database_path).expanduser()

    async def initialize(self) -> None:
        """Create the remote MCP configuration table."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """CREATE TABLE IF NOT EXISTS mcp_server_configurations (
                    name TEXT PRIMARY KEY,
                    transport TEXT NOT NULL DEFAULT 'streamable_http',
                    url TEXT,
                    command TEXT,
                    args_json TEXT NOT NULL DEFAULT '[]',
                    cwd TEXT,
                    timeout_seconds REAL NOT NULL,
                    enabled INTEGER NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    api_key_header TEXT,
                    api_key_env TEXT
                )"""
            )
            cursor = await connection.execute("PRAGMA table_info(mcp_server_configurations)")
            columns = {str(row[1]) for row in await cursor.fetchall()}
            migrations = {
                "transport": "TEXT NOT NULL DEFAULT 'streamable_http'",
                "command": "TEXT",
                "args_json": "TEXT NOT NULL DEFAULT '[]'",
                "cwd": "TEXT",
                "api_key_env": "TEXT",
                "description": "TEXT NOT NULL DEFAULT ''",
            }
            for column, declaration in migrations.items():
                if column not in columns:
                    await connection.execute(
                        f"ALTER TABLE mcp_server_configurations ADD COLUMN {column} {declaration}"
                    )
            await connection.commit()

    async def list(self) -> tuple[MCPServerConfiguration, ...]:
        """Return all saved remote server settings in stable name order."""
        async with aiosqlite.connect(self.database_path) as connection:
            connection.row_factory = aiosqlite.Row
            cursor = await connection.execute(
                """SELECT name, transport, url, command, args_json, cwd,
                          timeout_seconds, enabled, description, api_key_header, api_key_env
                   FROM mcp_server_configurations ORDER BY name"""
            )
            rows = await cursor.fetchall()
        return tuple(
            MCPServerConfiguration(
                name=str(row["name"]),
                transport=cast(Literal["streamable_http", "stdio"], str(row["transport"])),
                url=str(row["url"]) if row["url"] else None,
                command=str(row["command"]) if row["command"] is not None else None,
                args=tuple(json.loads(str(row["args_json"]))),
                cwd=str(row["cwd"]) if row["cwd"] is not None else None,
                timeout_seconds=float(row["timeout_seconds"]),
                enabled=bool(row["enabled"]),
                description=str(row["description"]),
                api_key_header=row["api_key_header"],
                api_key_env=row["api_key_env"],
            )
            for row in rows
        )

    async def save(self, configuration: MCPServerConfiguration) -> None:
        """Create or replace one non-secret remote server configuration."""
        async with aiosqlite.connect(self.database_path) as connection:
            await connection.execute(
                """INSERT INTO mcp_server_configurations
                       (name, transport, url, command, args_json, cwd, timeout_seconds,
                        enabled, description, api_key_header, api_key_env)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(name) DO UPDATE SET
                     transport = excluded.transport,
                     url = excluded.url,
                     command = excluded.command,
                     args_json = excluded.args_json,
                     cwd = excluded.cwd,
                     timeout_seconds = excluded.timeout_seconds,
                     enabled = excluded.enabled,
                     description = excluded.description,
                     api_key_header = excluded.api_key_header,
                     api_key_env = excluded.api_key_env""",
                (
                    configuration["name"],
                    configuration["transport"],
                    configuration["url"] or "",
                    configuration["command"],
                    json.dumps(configuration["args"]),
                    configuration["cwd"],
                    configuration["timeout_seconds"],
                    int(configuration["enabled"]),
                    configuration["description"],
                    configuration["api_key_header"],
                    configuration["api_key_env"],
                ),
            )
            await connection.commit()

    async def delete(self, name: str) -> bool:
        """Delete one server configuration and report whether it existed."""
        async with aiosqlite.connect(self.database_path) as connection:
            cursor = await connection.execute(
                "DELETE FROM mcp_server_configurations WHERE name = ?", (name,)
            )
            await connection.commit()
            return cursor.rowcount > 0
