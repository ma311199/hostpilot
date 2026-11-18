import asyncio

from hostpilot.persistence.skill_execution import SQLiteSkillExecutionStore


def test_skill_execution_preferences_persist_across_store_instances(tmp_path) -> None:
    database_path = tmp_path / "hostpilot.sqlite3"
    first_store = SQLiteSkillExecutionStore(database_path)

    async def persist_and_reload() -> dict[str, str]:
        await first_store.initialize()
        await first_store.set("business/meeting-notes", "both")
        await first_store.set("legacy/research", "deepagents")
        second_store = SQLiteSkillExecutionStore(database_path)
        return await second_store.list()

    assert asyncio.run(persist_and_reload()) == {
        "business/meeting-notes": "both",
        "legacy/research": "deepagents",
    }


def test_skill_execution_store_deletes_preferences(tmp_path) -> None:
    store = SQLiteSkillExecutionStore(tmp_path / "hostpilot.sqlite3")

    async def persist_and_delete() -> dict[str, str]:
        await store.initialize()
        await store.set("business/meeting-notes", "yaml")
        await store.delete("business/meeting-notes")
        return await store.list()

    assert asyncio.run(persist_and_delete()) == {}
