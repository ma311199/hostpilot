# Runtime Tool Invocation Coordinator Design

## Intent

Move the coordination of governed tool execution out of `MCPToolProvider` and into a Runtime-created service. This continues the `ToolExecutionScope` boundary while keeping MCP transport discovery, route resolution, and transport callbacks with the MCP Provider.

## Goals

- Give Runtime one explicit service for coordinating primary and compensation tool execution.
- Make MCP Provider provide the tool identity, arguments, request budget, confirmation provider, and transport invocation callback without calling `ToolExecutionScope.execute` itself.
- Preserve provider-owned primary and compensation registries, scope isolation, transport routing, and resource cleanup.
- Keep authorization, confirmation, budgets, timeout behavior, Audit/Trace, verification, and compensation semantics unchanged.
- Keep direct MCP Provider builders and candidate Provider replacement working through default Runtime-created coordination.

## Approaches

1. **Keep execution coordination in MCP Provider.** The current provider chooses a Scope and calls it directly. This has the smallest change but leaves the just-added Runtime execution seam unused as a Runtime service boundary.
2. **Runtime-created provider-scoped coordinator (selected).** The Runtime governance plugin creates a coordinator for each Provider, configured with that Provider's primary and compensation scopes. The Provider resolves local/remote transport and supplies an invocation callback; the coordinator chooses the appropriate scope and executes the governed request. This centralizes execution coordination without moving MCP-specific routing into Runtime.
3. **Move transport dispatch and MCP routing into Runtime.** This would make Runtime aware of MCP sessions, remote route tables, and timeout translation. It would combine generic governance with transport-specific behavior and expand this slice beyond the shared Runtime boundary.

## Contract and flow

- Define a `ToolInvocationCoordinator` contract with explicit primary and compensation operations. Requests carry tool ID, arguments, `RequestBudget`, optional confirmation provider, and an optional async invocation callback receiving validated arguments.
- The Runtime tool-governance factory creates a provider-scoped coordinator from the provider-owned Registry configuration and returns it with the matching `ToolExecutionScope` instances. No Registry or MCP session is moved into the shared Runtime context.
- `MCPToolProvider` continues resolving whether a tool is local or remote and creates the corresponding transport callback. It delegates normal calls and FastMCP interceptor calls to the coordinator's primary operation, and compensation calls to its compensation operation.
- Remote timeout translation remains around the remote execution in the Provider because only it knows the selected transport and its outcome semantics.
- Initial and candidate Providers receive isolated coordinator/scope sets. The stable `ToolFacade` continues routing requests to the active Provider, and `MCPProviderHostPlugin` remains the sole owner of Provider resource shutdown.

## Non-goals

- Moving MCP discovery, remote route tables, client/session ownership, raw transport calls, or timeout policy into Runtime.
- Changing ToolExecutor/PolicyEngine behavior, compensation snapshot ownership, authorization, confirmation, budgets, Audit/Trace, result verification, or sandbox implementation.
- Implementing the generic `Context → Model → Tool/Skill` loop or Agent/Session persistence.

## Acceptance

- Provider production paths do not call `ToolExecutionScope.execute` directly; they delegate primary and compensation work through the injected coordinator.
- Tests verify local, remote, intercepted, and compensation paths use the correct operation and retain existing timeout and governance behavior.
- API provider refresh creates isolated candidate coordination while preserving stable ToolFacade identity and Provider-only cleanup ownership.
- Focused tests, Ruff, mypy, full pytest, and documentation/metrics updates record the outcome, including the existing filesystem sandbox temporary-workspace failure if present.
