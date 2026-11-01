#!/usr/bin/env bash
set -euo pipefail

uv run --locked --group dev --group agents ruff format --check src/hostpilot tests
uv run --locked --group dev --group agents ruff check src/hostpilot tests
uv run --locked --group dev --group agents mypy src/hostpilot
uv run --locked --group dev --group agents pytest -q
uv lock --check
