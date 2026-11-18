# Runtime Sandbox Backend Injection Design

## Intent

Move Sandbox backend selection and process-scoped backend instances under Runtime plugin management. MCP Provider will receive the selected backend set through dependency injection while continuing to own Workspace-bound tool registration and per-call transport behavior.

## Current behavior

`build_retrieval_mcp_provider()` constructs Linux filesystem, Python, JavaScript, and Shell sandbox wrappers directly. These wrappers run isolated subprocesses per operation and cache host capability probes; they do not hold a persistent subprocess that MCP Provider or Runtime must shut down.

## Approaches

1. **Runtime-managed backend set (selected).** A Runtime plugin creates a replaceable `SandboxBackendSet`, registers it under a stable service ID, and shares it with initial and candidate MCP Providers. The default set uses the existing Linux bubblewrap implementations. This moves backend selection and cached host availability under Runtime while preserving provider-owned Workspace and tool registrations.
2. **Move Sandbox execution into Runtime.** Runtime would receive every operation and decide mounts, authorization, and execution. This mixes OS isolation details into the generic Runtime and duplicates the current tool-policy boundary.
3. **Persist Agent/Session state first.** This is a separate, larger state and recovery project and leaves the existing Sandbox construction outside Runtime.

## Goals

- Define replaceable file and code Sandbox backend Protocols and a typed backend set.
- Add a `builtin.sandbox-backends` Runtime plugin that registers a process-scoped backend set before MCP Provider assembly.
- Inject the same Runtime-owned backend set into initial and replacement MCP Providers.
- Keep direct `build_retrieval_mcp_provider()` calls functional with the default Linux backend factory.
- Preserve SandboxPolicy, capability checks, per-call process isolation, network/filesystem limits, error mapping, and host-unavailable behavior.
- Preserve the current lazy host capability probe and its safe availability reporting.

## Non-goals

- Moving Workspace ownership, file-operation argument validation, PolicyEngine, ToolExecutionScope, tool registration, mount construction, or subprocess execution into Runtime.
- Changing Linux bubblewrap profiles, resource limits, approval behavior, or tool outcomes.
- Adding a second sandbox implementation or platform in this slice.
- Adding persistent Sandbox workers or a shutdown protocol; existing backends start and finish a subprocess for each operation.

## Contract and flow

- `SandboxBackendFactory.create() -> SandboxBackendSet` constructs one set containing a file-operation backend and the Python, JavaScript, and Shell code backends.
- `builtin.sandbox-backends` creates the set once per RuntimeHost and registers `hostpilot.sandbox-backends`. MCP Provider assembly resolves that service and passes the set to `build_retrieval_mcp_provider()`.
- The default factory creates the existing Linux wrappers. Direct builders use that default if no backend set is supplied.
- Candidate MCP Providers reuse the Runtime-owned backend set; their Workspace, registries, ToolExecutionScopes, and ToolInvocationCoordinator remain provider-scoped.
- Provider tool handlers continue calling the injected backend with the same per-operation arguments. Sandbox code remains responsible for enforcing OS isolation; Tool Governance continues to enforce capability, policy, approval, budget, timeout, audit, and result checks.
- Backend availability remains lazily probed and exposed through the current per-tool `sandbox_unavailability_reasons` API shape.

## Acceptance

- Runtime tests prove the plugin registers and removes the backend-set service across host lifecycle.
- Provider assembly tests prove initial and candidate Providers receive the same Runtime backend set while keeping provider-owned Workspace and governance state isolated.
- Builder tests prove the default Linux backend path remains available for direct callers.
- Existing file and code Sandbox unit/integration tests retain isolation, resource-limit, denial, and unavailable-host behavior.
- Targeted tests, Ruff, mypy, full pytest, architecture, roadmap, and metrics records cover the implementation and preserve any known environment failure separately.
