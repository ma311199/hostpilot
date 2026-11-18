"""Load model credentials for opt-in local evaluation commands."""

from __future__ import annotations

import getpass
import os
from collections.abc import Mapping
from typing import Any


def model_configuration_from_environment(
    active_model: Mapping[str, Any], *, interactive: bool
) -> tuple[str, str | None, str, bool]:
    """Combine partial HOSTPILOT model variables with local app metadata.

    Evaluation tools deliberately avoid constructing application ``Settings`` here:
    model credentials may be held only in the running local app, while the command
    obtains the API key separately through a hidden terminal prompt.
    """
    env_model_name = _non_empty_environment_value("HOSTPILOT_MODEL_NAME")
    model_name = env_model_name or _non_empty(active_model.get("model_name"))
    if model_name is None:
        raise SystemExit(
            "No model is configured. Configure HOSTPILOT_MODEL_NAME, or start the local "
            "app with a configured model."
        )

    base_url = _non_empty_environment_value("HOSTPILOT_MODEL_BASE_URL") or _non_empty(
        active_model.get("base_url")
    )
    streaming_value = _non_empty_environment_value("HOSTPILOT_MODEL_STREAMING")
    if streaming_value is not None:
        streaming = _parse_boolean(streaming_value)
    elif env_model_name is not None:
        streaming = True
    else:
        streaming = _active_streaming(active_model.get("streaming"))

    api_key = _non_empty_environment_value("HOSTPILOT_MODEL_API_KEY")
    if api_key is None:
        if not interactive:
            raise SystemExit(
                "The running app keeps its API key in memory and does not expose it. "
                "Run this command in a terminal to enter the key securely, or set "
                "HOSTPILOT_MODEL_API_KEY in the process environment."
            )
        api_key = getpass.getpass("Model API key (input hidden): ").strip()
    if not api_key:
        raise SystemExit("A non-empty model API key is required.")
    return model_name, base_url, api_key, streaming


def _non_empty_environment_value(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else None


def _non_empty(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _parse_boolean(value: str) -> bool:
    normalized = value.casefold()
    if normalized in {"1", "true", "t", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "f", "no", "n", "off"}:
        return False
    raise SystemExit("HOSTPILOT_MODEL_STREAMING must be a boolean value.")


def _active_streaming(value: Any) -> bool:
    return value if isinstance(value, bool) else True


__all__ = ["model_configuration_from_environment"]
