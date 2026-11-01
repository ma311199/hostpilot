"""Default-deny policy decisions for registered tool operations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from hostpilot.contracts import ExecutionEnvironment, ToolDescriptor, TrustLevel


@dataclass(frozen=True)
class PolicyDecision:
    """Policy result; permission is explicit and independent of model routing."""

    allowed: bool
    reason_code: str


class PolicyEngine:
    """Check capabilities and trust boundaries before any tool side effect."""

    def authorize(
        self,
        descriptor: ToolDescriptor,
        arguments: Mapping[str, Any],
        *,
        granted_capabilities: frozenset[str] = frozenset(),
    ) -> PolicyDecision:
        """Return an allow decision only when every declared boundary is satisfied."""
        del arguments
        missing = descriptor.capabilities - granted_capabilities
        if missing:
            return PolicyDecision(False, "capability_not_granted")
        if descriptor.trust_level is TrustLevel.UNTRUSTED and descriptor.execution_env in {
            ExecutionEnvironment.HOST,
            ExecutionEnvironment.SERVICE,
        }:
            return PolicyDecision(False, "untrusted_tool_requires_isolation")
        if (
            descriptor.trust_level is TrustLevel.THIRD_PARTY
            and descriptor.execution_env is ExecutionEnvironment.HOST
        ):
            return PolicyDecision(False, "third_party_tool_requires_boundary")
        if descriptor.execution_env in {
            ExecutionEnvironment.WASM,
            ExecutionEnvironment.NATIVE_SANDBOX,
            ExecutionEnvironment.REMOTE,
        }:
            return PolicyDecision(False, "execution_environment_unavailable")
        return PolicyDecision(True, "authorized")


__all__ = ["PolicyDecision", "PolicyEngine"]
