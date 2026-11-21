import asyncio

import aiosqlite

from hostpilot.persistence import SQLiteMCPConfigurationStore


def test_mcp_description_persists_across_store_instances(tmp_path) -> None:
    database_path = tmp_path / "data" / "hostpilot.sqlite3"
    database_path.parent.mkdir(parents=True)

    async def scenario() -> None:
        store = SQLiteMCPConfigurationStore(database_path)
        await store.initialize()
        await store.save(
            {
                "name": "research",
                "transport": "streamable_http",
                "url": "https://mcp.example/mcp",
                "command": None,
                "args": (),
                "cwd": None,
                "timeout_seconds": 15,
                "enabled": True,
                "api_key_header": None,
                "api_key_env": None,
                "description": "Searches the internal research collection.",
            }
        )

        restarted = SQLiteMCPConfigurationStore(database_path)
        await restarted.initialize()
        saved = await restarted.list()

        assert saved[0]["description"] == "Searches the internal research collection."

    asyncio.run(scenario())


def test_mcp_configuration_migrates_existing_rows_to_empty_description(tmp_path) -> None:
    database_path = tmp_path / "legacy.sqlite3"

    async def scenario() -> None:
        async with aiosqlite.connect(database_path) as connection:
            await connection.execute(
                """CREATE TABLE mcp_server_configurations (
                    name TEXT PRIMARY KEY,
                    transport TEXT NOT NULL DEFAULT 'streamable_http',
                    url TEXT,
                    command TEXT,
                    args_json TEXT NOT NULL DEFAULT '[]',
                    cwd TEXT,
                    timeout_seconds REAL NOT NULL,
                    enabled INTEGER NOT NULL,
                    api_key_header TEXT,
                    api_key_env TEXT
                )"""
            )
            await connection.execute(
                """INSERT INTO mcp_server_configurations
                   (name, transport, url, args_json, timeout_seconds, enabled)
                   VALUES ('legacy', 'streamable_http', 'https://mcp.example/mcp', '[]', 15, 1)"""
            )
            await connection.commit()

        store = SQLiteMCPConfigurationStore(database_path)
        await store.initialize()
        saved = await store.list()

        assert saved[0]["name"] == "legacy"
        assert saved[0]["url"] == "https://mcp.example/mcp"
        assert saved[0]["description"] == ""

    asyncio.run(scenario())
