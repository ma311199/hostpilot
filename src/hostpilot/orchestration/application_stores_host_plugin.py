"""Create, initialize, publish, and close application persistence services."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hostpilot.persistence import (
    AuditStoreFactory,
    CancellationStoreFactory,
    MemoryStoreFactory,
    TraceStoreFactory,
)
from hostpilot.runtime import RuntimeContext, RuntimePluginSpec


class ApplicationStoresHostPlugin:
    """Own observability, cancellation, and memory store implementations."""

    spec = RuntimePluginSpec("builtin.application-stores")

    trace_store_service_id = "hostpilot.trace-store"
    audit_store_service_id = "hostpilot.audit-store"
    cancellation_store_service_id = "hostpilot.cancellation-store"
    memory_store_service_id = "hostpilot.memory-store"

    def __init__(
        self,
        database_path: Path,
        memory_path: Path,
        *,
        trace_store_factory: TraceStoreFactory,
        audit_store_factory: AuditStoreFactory,
        cancellation_store_factory: CancellationStoreFactory,
        memory_store_factory: MemoryStoreFactory,
    ) -> None:
        self._factories = (
            (self.trace_store_service_id, trace_store_factory, database_path, True),
            (self.audit_store_service_id, audit_store_factory, database_path, True),
            (
                self.cancellation_store_service_id,
                cancellation_store_factory,
                database_path,
                True,
            ),
            (self.memory_store_service_id, memory_store_factory, memory_path, False),
        )
        self._stores: list[tuple[str, Any]] = []

    async def setup(self, context: RuntimeContext) -> None:
        try:
            for service_id, factory, path, _ in self._factories:
                store = factory(path)
                self._stores.append((service_id, store))
                context.services.register(service_id, store, owner=self.spec.plugin_id)
        except BaseException:
            await self._close_stores()
            raise

    async def start(self, context: RuntimeContext) -> None:
        del context
        try:
            for (_, store), (_, _, _, should_initialize) in zip(
                self._stores, self._factories, strict=True
            ):
                if should_initialize:
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
            raise ExceptionGroup("Failed to close application stores.", errors)


__all__ = ["ApplicationStoresHostPlugin"]
