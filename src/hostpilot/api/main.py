"""Uvicorn entry point for the local HostPilot API."""

from __future__ import annotations

import uvicorn

from hostpilot.api.app import create_app
from hostpilot.config import load_settings


def run() -> None:
    """Start the API on the loopback interface by default."""
    settings = load_settings()
    uvicorn.run(
        create_app(settings),
        host=settings.api_host,
        port=settings.api_port,
    )


if __name__ == "__main__":
    run()
