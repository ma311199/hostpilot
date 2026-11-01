"""Execution result validation contracts."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from hostpilot.tools.registry import ToolResultVerifier


class TaskVerificationError(RuntimeError):
    """Raised when a tool result fails its task-specific verifier."""


def verify_tool_result(
    verifier: ToolResultVerifier,
    result: Any,
    arguments: Mapping[str, Any],
) -> None:
    """Run a registered verifier and normalize its failure into a domain error."""
    try:
        verifier(result, arguments)
    except TaskVerificationError:
        raise
    except Exception as error:
        raise TaskVerificationError("Tool result did not satisfy its task contract.") from error


__all__ = ["TaskVerificationError", "verify_tool_result"]
