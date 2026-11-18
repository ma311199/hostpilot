# Runtime Tool Invocation Coordinator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make MCP Provider delegate primary and compensation execution coordination to a Runtime-created service.

**Architecture:** Extend the existing Runtime governance factory to create a provider-scoped `ToolInvocationCoordinator` over that Provider's primary and compensation `ToolExecutionScope` objects. MCP Provider retains Registry, route selection, transport callbacks, and resource ownership, while the coordinator chooses which scope executes each request.

**Tech Stack:** Python 3.11, Protocol-based contracts, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-18-runtime-tool-invocation-coordinator-design.md`

## Global Constraints

- Preserve provider-owned primary and compensation registries, scope isolation, transport routing, and resource cleanup.
- Keep authorization, confirmation, budgets, timeout behavior, Audit/Trace, verification, and compensation semantics unchanged.
- Keep direct MCP Provider builders and candidate Provider replacement working through default Runtime-created coordination.
- Remote timeout translation remains around the remote execution in the Provider because only it knows the selected transport and its outcome semantics.
- Do not move MCP discovery, remote route tables, client/session ownership, raw transport calls, timeout policy, or Sandbox implementation into Runtime.

## Review Focus

- Primary and compensation requests must never select the wrong Scope; tests assert each operation reaches only its configured scope.
- Remote calls must retain `RemoteToolOutcomeUnknownError` timeout translation; test the remote timeout path through the coordinator.
- FastMCP intercepted calls must retain request budget and confirmation provider; test both flow through primary coordination.
- Candidate Provider creation must create isolated coordinators without changing stable ToolFacade identity; assert API refresh behavior.
- Direct Provider construction without injected factories must still use default coordination; test the default builder path.

---

### Task 1: Runtime coordinator contract and default factory

**Files:**
- Modify: `src/hostpilot/tools/governance.py`
- Modify: `src/hostpilot/tools/__init__.py`
- Test: `tests/unit/tools/test_governance.py`

**Interfaces:**
- Consumes: `ToolExecutionScope.execute(name, arguments, *, budget, granted_capabilities, confirmation_provider, _invoke)`.
- Produces: `ToolInvocationCoordinator.execute(name, arguments, *, budget, granted_capabilities=frozenset(), confirmation_provider=None, invoke=None)`, `ToolInvocationCoordinator.execute_compensation(name, arguments, *, budget, confirmation_provider=None)`, and `ToolExecutorFactory.create_coordinator(primary_scope, compensation_scope) -> ToolInvocationCoordinator`.

- [ ] **Step 1: Write failing coordinator tests**

Add tests proving primary execution delegates only to the primary Scope and compensation execution delegates only to the compensation Scope. Assert both operations forward name, arguments, budget, and confirmation provider; primary execution also forwards granted capabilities and invocation callback.

- [ ] **Step 2: Run the new tests and verify the expected failure**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/tools/test_governance.py -k coordinator`
Expected: FAIL because the coordinator contract and factory method are absent.

- [ ] **Step 3: Implement the coordinator protocol and default coordinator**

Add `ToolInvocationCoordinator` and a private default implementation in `governance.py`. Add `create_coordinator(primary_scope, compensation_scope)` to `ToolExecutorFactory` and `DefaultToolExecutorFactory`; the default implementation delegates each operation to the matching Scope without changing arguments or results. Export the public contract from `hostpilot.tools`.

- [ ] **Step 4: Run coordinator tests and existing governance tests**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/tools/test_governance.py`
Expected: PASS.

### Task 2: Delegate Provider execution through Runtime coordinator

**Files:**
- Modify: `src/hostpilot/tools/mcp.py`
- Modify: `tests/unit/tools/test_mcp.py`
- Modify: `tests/unit/api/test_app.py`

**Interfaces:**
- Consumes: Task 1 `ToolInvocationCoordinator` and `ToolExecutorFactory.create_coordinator(primary_scope, compensation_scope)`.
- Produces: `MCPToolProvider._tool_coordinator`; Provider no longer calls `ToolExecutionScope.execute` directly.

- [ ] **Step 1: Write failing Provider delegation tests**

Extend the recording factory to create recording coordinators. Assert normal local/remote calls and FastMCP interception delegate through primary coordination, compensation delegates through compensation coordination, remote timeout translation remains unchanged, direct Provider construction uses default coordination, and provider refresh creates a new coordinator while preserving the facade.

- [ ] **Step 2: Run the new tests and verify the expected failure**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/tools/test_mcp.py tests/unit/api/test_app.py -k 'coordinator or provider_refresh_keeps_runtime_tool_facade_stable'`
Expected: FAIL because Provider still calls its Scopes directly and does not create a coordinator.

- [ ] **Step 3: Wire Provider calls through the coordinator**

Create primary and compensation Scopes with the injected factory, create one provider-scoped coordinator, and replace direct Scope calls in local, remote, interceptor, and compensation paths with the matching coordinator operation. Keep transport callbacks and remote timeout translation in MCP Provider. Update API test factories to implement coordinator creation.

- [ ] **Step 4: Run Provider and API tests**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/tools/test_governance.py tests/unit/tools/test_mcp.py tests/unit/api/test_app.py`
Expected: All coordinator-related and API refresh assertions pass; record any known filesystem sandbox temporary-workspace failure separately.

### Task 3: Update architecture, metrics, and validate the slice

**Files:**
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/新架构文档.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/test-results/2026-11-18-runtime-turn-lifecycle.md`
- Modify: `docs/testing-catalog.md`
- Modify: `docs/testing-metrics.md`
- Create: `.superpowers/sdd/2026-11-18-runtime-tool-invocation-coordinator/progress.md`

- [ ] **Step 1: Update the Runtime boundary and test ledger**

Document that Runtime creates a provider-scoped coordinator over separate primary/compensation Scopes; MCP Provider retains route selection, transport calls, timeout translation, registries, and resource cleanup. Record exact targeted and full-suite outcomes and the known sandbox failure if present.

- [ ] **Step 2: Run focused style, type, and tests**

Run Ruff check/format on changed Python files, `.venv/bin/mypy src/hostpilot/tools`, and the focused tests from Task 2. Expected: all pass.

- [ ] **Step 3: Run full pytest and final diff checks**

Run: `.venv/bin/pytest -q --import-mode=importlib` and `git diff --check`.
Expected: report exact pass/fail counts. If the known sandbox failure remains, identify it by test name and keep it distinct from coordinator results.
