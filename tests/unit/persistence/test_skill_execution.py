import asyncio

import aiosqlite

from hostpilot.persistence.skill_execution import SQLiteSkillExecutionStore


def test_skill_execution_preferences_persist_across_store_instances(tmp_path) -> None:
    database_path = tmp_path / "hostpilot.sqlite3"
    first_store = SQLiteSkillExecutionStore(database_path)

    async def persist_and_reload() -> dict[str, str]:
        await first_store.initialize()
        await first_store.set("business/meeting-notes", "both")
        await first_store.set("legacy/research", "markdown")
        second_store = SQLiteSkillExecutionStore(database_path)
        return await second_store.list()

    assert asyncio.run(persist_and_reload()) == {
        "business/meeting-notes": "both",
        "legacy/research": "markdown",
    }


def test_skill_execution_store_initialization_is_safe_when_workers_start_together(
    tmp_path,
) -> None:
    database_path = tmp_path / "hostpilot.sqlite3"

    async def initialize_concurrently() -> None:
        stores = [SQLiteSkillExecutionStore(database_path) for _ in range(2)]
        await asyncio.gather(*(store.initialize() for store in stores))
        await stores[0].set("legacy/research", "markdown")
        assert await stores[1].list() == {"legacy/research": "markdown"}

    asyncio.run(initialize_concurrently())


def test_skill_execution_store_migrates_legacy_deepagents_preferences(tmp_path) -> None:
    database_path = tmp_path / "hostpilot.sqlite3"

    async def create_legacy_database() -> None:
        async with aiosqlite.connect(database_path) as connection:
            await connection.execute(
                """CREATE TABLE skill_execution_preferences (
                    skill_id TEXT PRIMARY KEY,
                    execution_mode TEXT NOT NULL
                      CHECK (execution_mode IN ('yaml', 'deepagents', 'both'))
                )"""
            )
            await connection.execute(
                "INSERT INTO skill_execution_preferences VALUES (?, ?)",
                ("legacy/research", "deepagents"),
            )
            await connection.commit()

    async def migrate_and_read() -> tuple[dict[str, str], str]:
        await create_legacy_database()
        store = SQLiteSkillExecutionStore(database_path)
        await store.initialize()
        preferences = await store.list()
        async with aiosqlite.connect(database_path) as connection:
            cursor = await connection.execute(
                "SELECT execution_mode FROM skill_execution_preferences WHERE skill_id = ?",
                ("legacy/research",),
            )
            row = await cursor.fetchone()
        return preferences, str(row[0])

    assert asyncio.run(migrate_and_read()) == (
        {"legacy/research": "markdown"},
        "markdown",
    )


def test_skill_execution_store_deletes_preferences(tmp_path) -> None:
    store = SQLiteSkillExecutionStore(tmp_path / "hostpilot.sqlite3")

    async def persist_and_delete() -> dict[str, str]:
        await store.initialize()
        await store.set("business/meeting-notes", "yaml")
        await store.delete("business/meeting-notes")
        return await store.list()

    assert asyncio.run(persist_and_delete()) == {}
