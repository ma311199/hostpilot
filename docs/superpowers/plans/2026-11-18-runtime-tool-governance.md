# Runtime Tool Governance Implementation Plan

> **For agentic workers:** implement inline with the `superpowers:executing-plans` workflow and `superpowers:test-driven-development` for every behavior change.

**Goal:** Register one stable governed ToolFacade service in Runtime and keep it pointed at the active tool provider across provider replacement.

**Architecture:** Add `builtin.tool-governance`, which creates a stable ToolFacade over a small delegating ToolInvoker. The MCP provider plugin continues to own provider resources; API configuration flows bind the candidate provider only when committing a replacement. Chat execution depends on the governance service.

**Tech Stack:** Python 3.11+, FastAPI lifespan, RuntimeHost, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-18-runtime-tool-governance-design.md`

## Global Constraints

- Preserve current HTTP/WebSocket contracts and tool authorization behavior.
- Keep Provider connection and resource ownership in `MCPProviderHostPlugin`.
- Use existing ToolFacade, ToolInvoker, ToolRegistry, PolicyEngine, and ToolExecutor contracts.
- Runtime plugins are trusted in-process code; do not add dynamic loading or sandboxing claims.

## Review Focus

- Provider unbound at startup: calls must fail clearly and must not execute a tool.
- Provider replaced during a request: existing request locking must prevent observing a mixed runtime.
- Candidate runtime assembly: it must receive the candidate provider's descriptors and governed tool wrappers.
- Host shutdown: the facade proxy must clear its binding while MCP Provider remains solely responsible for closing resources.
- Optional compensation and sandbox-mode forwarding: both must reach the currently bound provider and preserve unsupported-operation behavior.

---

### Task 1: Runtime-owned stable ToolFacade service

**Files:**
- Create: `src/hostpilot/orchestration/tool_governance_host_plugin.py`
- Modify: `src/hostpilot/orchestration/runtime_host_plugin.py`
- Modify: `src/hostpilot/api/app.py`
- Modify: `src/hostpilot/orchestration/__init__.py`
- Test: `tests/unit/orchestration/test_tool_governance_host_plugin.py`
- Test: `tests/unit/api/test_app.py`
- Update: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Update: `docs/新架构文档.md`
- Update: `docs/roadmap.md`
- Update: `docs/test-results/2026-11-18-runtime-turn-lifecycle.md`
- Update: `docs/testing-catalog.md`
- Update: `docs/testing-metrics.md`

**Interfaces:**
- Consumes: `ToolInvoker` from `hostpilot.tools.facade`, `ToolFacade`, `RuntimeContext`, `RuntimePluginSpec`, and the existing `MCPProviderHostPlugin` lifecycle.
- Produces: `ToolGovernanceHostPlugin.spec = RuntimePluginSpec("builtin.tool-governance", dependencies=("builtin.mcp-provider",))`; `service_id = "hostpilot.tool-facade"`; `facade` returns one stable `ToolFacade`; `bind(provider: ToolInvoker | None) -> None` switches the delegating target without closing it.

- [x] **Step 1: Write failing unit tests**
  - `test_runtime_service_facade_forwards_to_bound_provider`
  - `test_runtime_service_facade_uses_replacement_provider_without_replacing_facade`
  - `test_runtime_service_facade_clears_provider_on_host_shutdown`
  - `test_facade_forwards_optional_compensation_and_sandbox_mode`
- [x] **Step 2: Run those tests and verify they fail because `ToolGovernanceHostPlugin` is missing.**
- [x] **Step 3: Implement the Host plugin and provider proxy; expose the plugin from orchestration.**
- [x] **Step 4: Run the plugin tests and verify all pass.**
- [x] **Step 5: Add the plugin to FastAPI RuntimeHost setup, bind each committed active provider, resolve `tool_facade` from Runtime services, and declare Chat Runtime's dependency.**
- [x] **Step 6: Extend the API lifespan test to assert service identity and provider rebinding; run API/Runtime integration tests.**
- [x] **Step 7: Update ADR-0034, architecture, roadmap, test catalog, metrics, and the Runtime test report.**
- [x] **Step 8: Run Ruff, targeted mypy, the complete pytest suite, and `git diff --check`; record the known filesystem sandbox failure if it remains.**
