# Runtime Tool Executor Factory Design

## Intent

Move construction of `PolicyEngine` and `ToolExecutor` behind a Runtime-registered governance service. Runtime and its injected factory become the replaceable owner of governance policy/executor construction; each MCP Provider continues to own its tool registries, transport sessions, handlers, and provider-specific executor references. This is the next incremental Runtime boundary after registering the stable ToolFacade.

## Goals

- Register a `hostpilot.tool-governance` Runtime service from `builtin.tool-governance`.
- Create policy-first executors through that service for the active and candidate MCP Providers.
- Keep distinct registries for normal calls and reversible-file compensation.
- Preserve schema validation, policy decisions, confirmation, budgets, timeout, audit, trace, result verification, and remote timeout semantics.
- Keep the public ToolFacade stable across Provider replacement and keep Provider resource ownership in `MCPProviderHostPlugin`.
- Retain a default executor factory for direct provider construction and existing integrations outside FastAPI Runtime assembly.

## Non-goals

- Move transport selection, MCP sessions, tool registries, raw handlers, or compensation snapshots into Runtime.
- Change PolicyEngine rules, tool permissions, user confirmation, request budgets, or audit/trace schemas.
- Move Sandbox implementations or introduce a general model-to-tool execution loop in this slice.

## Approaches

1. **Move the complete tool execution dispatcher into Runtime.** This gives Runtime direct control over invocation but requires extracting raw invocation from local FastMCP handlers, remote MCP routing, and compensation. It changes several coupled call paths at once.
2. **Runtime-owned executor factory (selected).** Runtime supplies the PolicyEngine/ToolExecutor construction service; each provider owns and uses the resulting scoped executors with its own registries and transport callbacks. This creates a clear replacement point while preserving the current invocation path and limits the migration surface.
3. **Migrate Sandbox lifecycle first.** This is relatively independent, but the current sandbox wrappers are constructed alongside MCP providers and have no common Runtime ownership contract; it would leave policy/executor construction inside the provider.

The selected incremental approach matches the existing Runtime plugin boundary and keeps behavioral changes small. A later slice can move execution dispatch after this seam is verified.

## Runtime service and ownership

- `builtin.tool-governance` additionally registers `hostpilot.tool-governance`.
- The service exposes a factory that accepts a provider-owned `ToolRegistry`, sandbox availability settings, and the existing audit/trace sinks, then returns a configured `ToolExecutor`.
- `MCPToolProvider` receives the factory at construction and requests one executor for its active registry and one for its compensation registry.
- Runtime creates the factory implementation and owns its lifetime. The provider owns the executor references, registries, and tool resources it creates; executors have no external resources to close.
- FastAPI resolves the factory from Runtime services and injects it into both initial and candidate Provider construction. Candidate Providers remain isolated until the existing request lock commits replacement.
- Direct `build_retrieval_mcp_provider` callers retain a default factory so existing tests and integrations remain compatible.

## Failure behavior

- A required Runtime service missing in FastAPI assembly fails startup/provider construction rather than silently using a different production policy path.
- A direct provider builder without an injected service uses the same default factory implementation as Runtime.
- Provider replacement keeps the existing commit lock and ownership order; failed candidates are closed without changing the active facade binding.

## Verification

- Unit tests prove factory construction receives the correct registry, sandbox settings, audit and trace sinks, including separate compensation registry configuration.
- Provider tests prove injected executors are used for normal FastMCP calls, remote MCP calls, and compensation.
- FastAPI lifecycle tests prove the Runtime service is resolved for initial and candidate Providers while the ToolFacade remains stable.
- Targeted Ruff/mypy and full pytest run; retain and report the known temporary-workspace sandbox failure if it remains.
