import asyncio
import json
import sqlite3

import pytest
from pydantic import ValidationError

from hostpilot.persistence.harness_configuration import (
    HarnessConfiguration,
    SQLiteHarnessConfigurationStore,
)


def test_harness_configuration_store_persists_validated_settings(tmp_path) -> None:
    store = SQLiteHarnessConfigurationStore(tmp_path / "data" / "hostpilot.sqlite3")
    configuration = HarnessConfiguration(
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

    async def save_and_reload() -> HarnessConfiguration | None:
        await store.initialize()
        assert await store.get() is None
        await store.save(configuration)
        second_store = SQLiteHarnessConfigurationStore(store.database_path)
        return await second_store.get()

    assert asyncio.run(save_and_reload()) == configuration


def test_harness_configuration_rejects_out_of_range_budgets() -> None:
    with pytest.raises(ValidationError):
        HarnessConfiguration(request_tool_call_budget=101)
    with pytest.raises(ValidationError):
        HarnessConfiguration(routing_minimum_business_confidence=0.49)
    with pytest.raises(ValidationError):
        HarnessConfiguration(routing_minimum_plan_candidate_confidence=0.49)


def test_harness_configuration_defaults_and_validates_plan_result_history_limits() -> None:
    configuration = HarnessConfiguration()

    assert configuration.plan_result_history_limit == 32
    assert configuration.plan_result_history_max_chars == 65_536
    assert HarnessConfiguration(plan_result_history_limit=1).plan_result_history_limit == 1
    assert HarnessConfiguration(plan_result_history_limit=128).plan_result_history_limit == 128
    assert HarnessConfiguration(plan_result_history_max_chars=1).plan_result_history_max_chars == 1
    assert (
        HarnessConfiguration(plan_result_history_max_chars=262_144).plan_result_history_max_chars
        == 262_144
    )
    with pytest.raises(ValidationError):
        HarnessConfiguration(plan_result_history_limit=0)
    with pytest.raises(ValidationError):
        HarnessConfiguration(plan_result_history_limit=129)
    with pytest.raises(ValidationError):
        HarnessConfiguration(plan_result_history_max_chars=0)
    with pytest.raises(ValidationError):
        HarnessConfiguration(plan_result_history_max_chars=262_145)


def test_harness_configuration_loads_defaults_for_pre_history_limit_records(tmp_path) -> None:
    store = SQLiteHarnessConfigurationStore(tmp_path / "data" / "hostpilot.sqlite3")
    legacy_payload = HarnessConfiguration().model_dump(
        exclude={"plan_result_history_limit", "plan_result_history_max_chars"}
    )

    async def save_legacy_configuration() -> HarnessConfiguration | None:
        await store.initialize()
        with sqlite3.connect(store.database_path) as connection:
            connection.execute(
                "INSERT INTO harness_configuration (singleton_id, configuration_json) "
                "VALUES (1, ?)",
                (json.dumps(legacy_payload),),
            )
        return await store.get()

    restored = asyncio.run(save_legacy_configuration())

    assert restored is not None
    assert restored.plan_result_history_limit == 32
    assert restored.plan_result_history_max_chars == 65_536


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
