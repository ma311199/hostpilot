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

    def __init__(
        self,
        *,
        sandboxed_environments: frozenset[ExecutionEnvironment] = frozenset(),
        sandboxed_tools: frozenset[str] | None = None,
    ) -> None:
        if not sandboxed_environments.issubset(
            {ExecutionEnvironment.WASM, ExecutionEnvironment.NATIVE_SANDBOX}
        ):
            raise ValueError("Only isolated execution environments may be enabled here.")
        self.sandboxed_environments = sandboxed_environments
        self.sandboxed_tools = sandboxed_tools

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
        if (
            descriptor.execution_env
            in {ExecutionEnvironment.WASM, ExecutionEnvironment.NATIVE_SANDBOX}
            and descriptor.execution_env not in self.sandboxed_environments
        ) or descriptor.execution_env is ExecutionEnvironment.REMOTE:
            return PolicyDecision(False, "execution_environment_unavailable")
        if (
            descriptor.execution_env is ExecutionEnvironment.NATIVE_SANDBOX
            and self.sandboxed_tools is not None
            and descriptor.name not in self.sandboxed_tools
        ):
            return PolicyDecision(False, "sandbox_tool_unavailable")
        return PolicyDecision(True, "authorized")


__all__ = ["PolicyDecision", "PolicyEngine"]
