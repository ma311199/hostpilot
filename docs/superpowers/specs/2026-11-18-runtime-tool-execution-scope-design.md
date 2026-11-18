# Runtime Tool Execution Scope Design

## Intent

Continue the Tool Governance boundary by having the Runtime factory create a provider-scoped execution object. MCP Provider call paths will depend on this execution contract instead of concrete `ToolExecutor` instances, while keeping their own registries, raw handlers, MCP sessions, routing, and compensation state.

## Approaches

1. **Leave Provider calling `ToolExecutor` directly.** The factory makes construction replaceable, but local, remote, intercepted, and compensation paths still couple MCP Provider to the concrete executor.
2. **Runtime-created execution scope (selected).** The factory creates a scoped governance object exposing only its Registry and `execute` contract. Provider passes validated/request inputs to that scope; the default Runtime factory wraps the existing `ToolExecutor`. This creates one replaceable execution seam without moving transport callbacks or changing authorization semantics.
3. **Move transport dispatch into Runtime.** Runtime would own remote route lookup, local FastMCP invocation, and compensation. This would mix transport-specific state with general governance before a common ToolPort contract exists.

## Goals

- Change `ToolExecutorFactory` to create a provider-scoped `ToolExecutionScope` contract.
- Keep the concrete `ToolExecutor` implementation behind the default scope implementation.
- Route all four MCP Provider paths—normal local/remote, FastMCP interceptor calls, and compensation—through the injected scope.
- Keep primary and compensation registries separate and provider-owned.
- Preserve Policy, confirmation, budget, timeout, Audit/Trace, verification, and remote timeout behavior.
- Keep direct provider builders working through the default factory and keep the Runtime service replaceable through `create_app`.

## Non-goals

- Move raw MCP transport calls or remote route tables into Runtime.
- Move provider registries, FastMCP servers, handlers, or compensation snapshot state.
- Change the `ToolExecutor` algorithm, PolicyEngine rules, or Sandbox implementation.
- Implement the generic `Context → Model → Tool/Skill` loop.

## Contract

- `ToolExecutionScope` exposes `registry` and async `execute` with the current ToolExecutor call contract.
- `ToolExecutorFactory.create_scope(...)` accepts a provider-owned registry and policy/observability configuration and returns a scope.
- `DefaultToolExecutorFactory` wraps a `ToolExecutor` inside a private default scope.
- `MCPToolProvider` retains one primary and one compensation scope; its compensation tool is registered into the compensation scope's registry.
- The governance service remains registered as `hostpilot.tool-governance`. Candidate Providers get isolated scopes from the same factory before replacement commit.

## Failure behavior

- Missing providers still fail in `ToolFacade` before an execution scope is reached.
- Missing or failing factory construction fails provider assembly; no permissive fallback is added in FastAPI.
- Direct Provider construction keeps the same default Runtime factory implementation.

## Verification

- Tests prove the default scope preserves registry, policy, audit, trace, and ToolExecutor behavior.
- A recording custom scope proves all MCP invocation paths delegate through injected scopes, including remote timeout wrapping and compensation.
- API refresh tests prove candidate Providers receive new scopes while facade/service identity remains stable.
- Run targeted Ruff/mypy, focused tests, full pytest, and `git diff --check`; preserve diagnosis of the existing filesystem sandbox failure.
