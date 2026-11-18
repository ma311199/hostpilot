import asyncio

from hostpilot.persistence import SQLiteWorkspaceConfigurationStore


def test_workspace_configuration_persists_across_store_instances(tmp_path) -> None:
    database_path = tmp_path / "data" / "hostpilot.sqlite3"

    async def scenario() -> None:
        first = SQLiteWorkspaceConfigurationStore(database_path)
        await first.initialize()
        assert await first.get() is None
        await first.save("/tmp/selected-workspace")

        restarted = SQLiteWorkspaceConfigurationStore(database_path)
        assert await restarted.get() == "/tmp/selected-workspace"

    asyncio.run(scenario())
