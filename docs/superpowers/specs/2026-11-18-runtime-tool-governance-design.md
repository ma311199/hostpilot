# Runtime Tool Governance Service Design

## Intent

Expose one stable, Runtime-registered `ToolFacade` to API and orchestration code so those consumers depend on the tool contract instead of constructing transport wrappers themselves. This continues the Runtime Core migration while preserving the existing centralized authorization and execution chain.

## Approaches

1. **Stable facade proxy plugin (chosen):** a `builtin.tool-governance` Host plugin registers one `ToolFacade` backed by a delegating provider. Binding a new MCP provider changes the proxy target. `MCPProviderHostPlugin` remains the owner of provider connections and closes replaced providers.
2. **Move Policy and Executor into a new plugin:** this would split objects that `MCPToolProvider` currently constructs and invokes together, creating a second execution path and requiring a wider refactor.
3. **Keep API-owned facades:** this preserves the current arrangement but leaves consumers coupled to API assembly and requires manually replacing each facade reference.

## Goals

- Register `hostpilot.tool-facade` during Runtime startup and resolve it through `RuntimeServiceRegistry`.
- Keep one stable facade object while MCP providers are replaced.
- Route every call through the currently bound provider, preserving its Tool Registry, Policy Engine, Tool Executor, confirmation, budget, audit, trace, and verification behavior.
- Clear the proxy binding when the Host stops. The MCP provider plugin remains responsible for closing provider resources.
- Keep HTTP/WebSocket behavior and MCP configuration replacement semantics unchanged.

## Non-goals

- Move Policy Engine or Tool Executor construction out of `MCPToolProvider`.
- Change tool authorization, confirmation, budgeting, compensation, or sandbox rules.
- Add dynamic plugin installation, untrusted plugin isolation, or new tool transports.
- Migrate OS Sandbox lifecycle; providers continue to close resources they construct.

## Constraints

- Runtime plugins are trusted in-process Python code registered at application startup.
- `builtin.tool-governance` depends on `builtin.mcp-provider`; `builtin.chat-execution` depends on the tool service as well as MCP and State services.
- Candidate runtimes may use a candidate-specific facade while they are assembled; the stable facade is bound to the candidate provider only when the replacement is committed.
- Existing request locking remains the synchronization boundary for active provider replacement.

## Acceptance

- Consumers can resolve the stable facade service from Runtime context.
- Descriptors and calls through that facade follow the currently bound provider after replacement.
- Provider replacement does not close the prior provider through the facade plugin; MCP Provider ownership remains singular.
- Host shutdown clears the facade binding and closes plugins in dependency order.
