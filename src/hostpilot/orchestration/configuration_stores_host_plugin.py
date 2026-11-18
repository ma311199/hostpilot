"""Create and own persistent configuration stores for one RuntimeHost."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hostpilot.persistence import (
    HarnessConfigurationStoreFactory,
    MCPConfigurationStoreFactory,
    ModelConfigurationStoreFactory,
    RoutingModelConfigurationStoreFactory,
    SkillExecutionStoreFactory,
    WorkspaceConfigurationStoreFactory,
)
from hostpilot.runtime import RuntimeContext, RuntimePluginSpec


class ConfigurationStoresHostPlugin:
    """Construct, initialize, publish, and close configuration persistence ports."""

    spec = RuntimePluginSpec("builtin.configuration-stores")

    model_configuration_service_id = "hostpilot.model-configuration-store"
    routing_model_configuration_service_id = "hostpilot.routing-model-configuration-store"
    mcp_configuration_service_id = "hostpilot.mcp-configuration-store"
    harness_configuration_service_id = "hostpilot.harness-configuration-store"
    workspace_configuration_service_id = "hostpilot.workspace-configuration-store"
    skill_execution_service_id = "hostpilot.skill-execution-store"

    def __init__(
        self,
        database_path: Path,
        *,
        model_configuration_store_factory: ModelConfigurationStoreFactory,
        routing_model_configuration_store_factory: RoutingModelConfigurationStoreFactory,
        mcp_configuration_store_factory: MCPConfigurationStoreFactory,
        harness_configuration_store_factory: HarnessConfigurationStoreFactory,
        workspace_configuration_store_factory: WorkspaceConfigurationStoreFactory,
        skill_execution_store_factory: SkillExecutionStoreFactory,
    ) -> None:
        self._database_path = database_path
        self._factories = (
            (self.model_configuration_service_id, model_configuration_store_factory),
            (
                self.routing_model_configuration_service_id,
                routing_model_configuration_store_factory,
            ),
            (self.mcp_configuration_service_id, mcp_configuration_store_factory),
            (self.harness_configuration_service_id, harness_configuration_store_factory),
            (self.workspace_configuration_service_id, workspace_configuration_store_factory),
            (self.skill_execution_service_id, skill_execution_store_factory),
        )
        self._stores: list[tuple[str, Any]] = []

    async def setup(self, context: RuntimeContext) -> None:
        try:
            for service_id, factory in self._factories:
                store = factory(self._database_path)
                self._stores.append((service_id, store))
                context.services.register(service_id, store, owner=self.spec.plugin_id)
        except BaseException:
            await self._close_stores()
            raise

    async def start(self, context: RuntimeContext) -> None:
        del context
        try:
            for _, store in self._stores:
                await store.initialize()
        except BaseException:
            await self._close_stores()
            raise

    async def stop(self, context: RuntimeContext) -> None:
        del context
        await self._close_stores()

    async def _close_stores(self) -> None:
        stores, self._stores = self._stores, []
        errors: list[Exception] = []
        for _, store in reversed(stores):
            close = getattr(store, "aclose", None)
            if not callable(close):
                close = getattr(store, "close", None)
            if callable(close):
                try:
                    result = close()
                    if hasattr(result, "__await__"):
                        await result
                except Exception as error:
                    errors.append(error)
        if errors:
            raise ExceptionGroup("Failed to close configuration stores.", errors)


__all__ = ["ConfigurationStoresHostPlugin"]
