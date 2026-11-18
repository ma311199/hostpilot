"""Run an isolated API and seed one Trace through its SQLite store factory."""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import uvicorn

from hostpilot.api.app import create_app
from hostpilot.config import load_settings
from hostpilot.observability import TraceContext, TraceEvent
from hostpilot.persistence.traces import SQLiteTraceStore


class SeededTraceStore(SQLiteTraceStore):
    """Use the production Trace store and add deterministic fixture metadata."""

    async def initialize(self) -> None:
        await super().initialize()
        self.record(
            TraceEvent(
                context=TraceContext(trace_id=UUID("55d5e46c-f3f9-47ed-ae8d-416055a8d93a")),
                name="chat.turn",
                attributes={
                    "status": "completed",
                    "route": "direct",
                    "duration_ms": 7,
                    "message": "private E2E fixture content",
                },
            )
        )


class E2EWorkspacePicker:
    """Return a temporary workspace once, then restore the API startup folder."""

    def __init__(self, selected_workspace: Path) -> None:
        self.selected_workspace = selected_workspace
        self.selection_count = 0

    def __call__(self, _current: Path) -> Path:
        self.selection_count += 1
        if self.selection_count == 1:
            return self.selected_workspace
        return Path.cwd()


def main() -> None:
    """Start FastAPI using the isolated Playwright environment configuration."""
    settings = load_settings()
    selected_workspace = Path(os.environ["HOSTPILOT_E2E_WORKSPACE"])
    app = create_app(
        settings,
        trace_store_factory=SeededTraceStore,
        workspace_picker=E2EWorkspacePicker(selected_workspace),
    )
    uvicorn.run(app, host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
