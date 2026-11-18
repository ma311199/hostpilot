import asyncio

import pytest
from pydantic import ValidationError

from hostpilot.persistence.harness_configuration import (
    HarnessRuntimeConfiguration,
    SQLiteHarnessConfigurationStore,
)


def test_harness_configuration_store_persists_validated_settings(tmp_path) -> None:
    store = SQLiteHarnessConfigurationStore(tmp_path / "data" / "hostpilot.sqlite3")
    configuration = HarnessRuntimeConfiguration(
        max_context_messages=25,
        request_token_budget=12_000,
        request_timeout_seconds=45,
        plan_max_tasks=10,
        plan_tool_agent_max_turns=5,
        plan_tool_candidate_limit=4,
        routing_minimum_business_confidence=0.82,
        routing_minimum_business_margin=0.25,
        routing_minimum_plan_candidate_confidence=0.5,
        routing_max_plan_candidates=5,
        routing_max_business_candidates=24,
    )

    async def save_and_reload() -> HarnessRuntimeConfiguration | None:
        await store.initialize()
        assert await store.get() is None
        await store.save(configuration)
        second_store = SQLiteHarnessConfigurationStore(store.database_path)
        return await second_store.get()

    assert asyncio.run(save_and_reload()) == configuration


def test_harness_configuration_rejects_out_of_range_budgets() -> None:
    with pytest.raises(ValidationError):
        HarnessRuntimeConfiguration(request_tool_call_budget=101)
    with pytest.raises(ValidationError):
        HarnessRuntimeConfiguration(routing_minimum_business_confidence=0.49)
    with pytest.raises(ValidationError):
        HarnessRuntimeConfiguration(routing_minimum_plan_candidate_confidence=0.49)


def test_harness_configuration_store_rejects_database_symlink(tmp_path) -> None:
    target = tmp_path / "target.sqlite3"
    target.touch()
    database_path = tmp_path / "linked.sqlite3"
    database_path.symlink_to(target)
    store = SQLiteHarnessConfigurationStore(database_path)

    with pytest.raises(ValueError, match="database cannot be a symbolic link"):
        asyncio.run(store.initialize())


def test_harness_configuration_store_rejects_directory_symlink(tmp_path) -> None:
    target_directory = tmp_path / "target"
    target_directory.mkdir()
    linked_directory = tmp_path / "linked"
    linked_directory.symlink_to(target_directory, target_is_directory=True)
    store = SQLiteHarnessConfigurationStore(linked_directory / "hostpilot.sqlite3")

    with pytest.raises(ValueError, match="directory cannot be a symbolic link"):
        asyncio.run(store.initialize())
