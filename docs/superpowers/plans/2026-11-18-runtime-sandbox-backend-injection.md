# Runtime Sandbox Backend Injection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the RuntimeHost own the selected process-scoped Sandbox backend set and inject it into MCP Providers.

**Architecture:** Add replaceable Sandbox backend Protocols and a typed set, with the existing Linux bubblewrap wrappers as the default implementation. A required Runtime plugin creates and registers one backend set per Host; initial and candidate MCP Providers reuse it while retaining Workspace, tool registration, governance, and resource responsibilities.

**Tech Stack:** Python 3.11, Protocols/dataclasses, FastAPI lifespan, RuntimeHost plugins, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-18-runtime-sandbox-backend-injection-design.md`

## Global Constraints

- Preserve SandboxPolicy, capability checks, per-call process isolation, network/filesystem limits, error mapping, and host-unavailable behavior.
- Preserve the current lazy host capability probe and its safe availability reporting.
- Keep Workspace ownership, file-operation argument validation, PolicyEngine, ToolExecutionScope, tool registration, mount construction, and subprocess execution outside Runtime.
- Keep direct `build_retrieval_mcp_provider()` calls functional with the default Linux backend factory.
- Candidate MCP Providers reuse the Runtime-owned backend set but retain provider-scoped Workspace and governance state.
- Do not add persistent Sandbox workers, a second backend implementation, or a shutdown protocol in this slice.

## Review Focus

- An unavailable backend must remain unavailable and fail closed; the new factory must not mark it available by default.
- The lazy capability probe must remain lazy; test backend construction without forcing host probing.
- Provider replacement must reuse the same backend set but create a distinct Workspace and Registry; verify identity and behavior through API refresh.
- Direct builder use must still select the default Linux backend factory; test it without RuntimeHost.
- Failure to construct the required backend set must prevent RuntimeHost readiness and leave no registered service; test startup failure cleanup.

---

### Task 1: Define Sandbox backend contracts and the default Linux factory

**Files:**
- Create: `src/hostpilot/sandbox/ports.py`
- Modify: `src/hostpilot/sandbox/__init__.py`
- Test: `tests/unit/sandbox/test_backends.py`

**Interfaces:**
- Produces: `WorkspaceSandboxBackend.execute(workspace, operation, arguments)`, `CodeSandboxBackend.run(source)`, `SandboxBackendSet(filesystem, python, javascript, shell)`, and `SandboxBackendFactory.create() -> SandboxBackendSet`.
- Produces: `LinuxSandboxBackendFactory.create()` returning the existing LinuxWorkspaceSandbox, LinuxPythonSandbox, LinuxJavaScriptSandbox, and LinuxShellSandbox implementations.

- [x] **Step 1: Write failing tests for the default backend factory**

Assert the factory returns the four existing backend implementations in the matching fields, preserves their per-tool availability properties, and does not force the lazy `host_supported` probe during construction.

- [x] **Step 2: Run the tests and verify they fail because the backend contracts/factory are absent**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/sandbox/test_backends.py`
Expected: FAIL because the new `SandboxBackendSet` and `LinuxSandboxBackendFactory` are not defined.

- [x] **Step 3: Add Protocols, the backend-set dataclass, and Linux factory**

Implement only the declared interfaces. The factory must construct the existing wrappers without changing their policies or eager/lazy probe behavior; export the public contracts from `hostpilot.sandbox`.

- [x] **Step 4: Run Sandbox backend and existing sandbox tests**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/sandbox`
Expected: PASS.

### Task 2: Register the backend set as a Runtime plugin service

**Files:**
- Create: `src/hostpilot/orchestration/sandbox_backends_host_plugin.py`
- Modify: `src/hostpilot/orchestration/__init__.py`
- Modify: `src/hostpilot/orchestration/mcp_provider_host_plugin.py`
- Create: `tests/unit/orchestration/test_sandbox_backends_host_plugin.py`
- Modify: `tests/unit/orchestration/test_mcp_provider_host_plugin.py`

**Interfaces:**
- Consumes: Task 1 `SandboxBackendFactory` and `SandboxBackendSet`.
- Produces: `SandboxBackendsHostPlugin.spec.plugin_id == "builtin.sandbox-backends"`, service ID `hostpilot.sandbox-backends`, and one backend set per RuntimeHost.
- Dependency: `builtin.mcp-provider` requires `builtin.sandbox-backends` so the backend service is ready before provider assembly.

- [x] **Step 1: Write failing plugin lifecycle tests**

Verify the plugin creates one set, registers that exact object, and RuntimeHost close removes the service. Verify factory failure prevents Host readiness and leaves the service registry empty. Update the MCP Provider plugin test to register its required Sandbox dependency.

- [x] **Step 2: Run the tests and verify they fail because the plugin/service are absent**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/orchestration/test_sandbox_backends_host_plugin.py tests/unit/orchestration/test_mcp_provider_host_plugin.py`
Expected: FAIL because the plugin and dependency do not exist.

- [x] **Step 3: Implement the required Runtime plugin and dependency**

Create the backend set during plugin setup, register it under `hostpilot.sandbox-backends`, clear the plugin reference on stop, and declare MCP Provider's dependency on `builtin.sandbox-backends`.

- [x] **Step 4: Run Runtime plugin lifecycle tests**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/orchestration/test_sandbox_backends_host_plugin.py tests/unit/orchestration/test_mcp_provider_host_plugin.py tests/unit/orchestration/test_runtime.py`
Expected: PASS.

### Task 3: Inject the backend set into the retrieval MCP builder

**Files:**
- Modify: `src/hostpilot/tools/mcp.py`
- Modify: `tests/unit/tools/test_mcp.py`

**Interfaces:**
- Consumes: Task 1 `SandboxBackendSet` and `SandboxBackendFactory`.
- Produces: `build_retrieval_mcp_provider(..., sandbox_backends: SandboxBackendSet | None = None)`; when omitted, it uses `LinuxSandboxBackendFactory`.

- [x] **Step 1: Write failing builder tests**

Pass a test backend set and verify file operations reach its filesystem backend and code-tool handlers use the corresponding code backend. Verify direct builder calls without an injected set retain default Linux backend instances and preserve the current per-tool availability map.

- [x] **Step 2: Run the tests and verify the injection assertions fail**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/tools/test_mcp.py -k sandbox_backend`
Expected: FAIL because the builder does not accept a backend set.

- [x] **Step 3: Replace direct Sandbox construction in the builder with the injected set**

Use the set's filesystem/Python/JavaScript/Shell backends for the current handlers, Sandbox capability calculation, and `sandbox_unavailability_reasons`. Do not change Workspace, ToolRegistry, governance scopes, approval, or handler arguments.

- [x] **Step 4: Run MCP and Sandbox tests**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/tools/test_mcp.py tests/unit/sandbox`
Expected: PASS, except for any already documented environment-dependent Sandbox failures, which must be named and investigated separately.

### Task 4: Wire RuntimeHost and all Provider assembly paths

**Files:**
- Modify: `src/hostpilot/api/app.py`
- Modify: `tests/unit/api/test_app.py`

**Interfaces:**
- Consumes: Task 2 service `hostpilot.sandbox-backends` and Task 3 builder parameter `sandbox_backends`.
- Produces: optional `create_app(..., sandbox_backend_factory: SandboxBackendFactory | None = None)` injection; initial and candidate Providers resolve and receive the single Runtime-owned backend set.

- [x] **Step 1: Write failing API lifecycle and refresh tests**

Assert Runtime registers the backend set before Provider construction, the initial Provider receives it, candidate Providers reuse the same backend object after both MCP and model refresh paths, and each candidate retains its own Workspace and Registry.

- [x] **Step 2: Run the tests and verify the expected missing-injection failures**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/api/test_app.py -k sandbox_backend`
Expected: FAIL because `create_app` and Provider assembly do not accept or resolve the backend-set service.

- [x] **Step 3: Register and inject the Runtime-owned backend set**

Add the plugin to FastAPI lifespan, pass the optional factory, resolve its service in the app assembly, and pass the set to initial and every candidate call to `build_retrieval_mcp_provider()`.

- [x] **Step 4: Run API lifespan and Provider refresh tests**

Run: `.venv/bin/pytest -q --import-mode=importlib tests/unit/api/test_app.py -k 'sandbox_backend or mcp_provider_refresh'`
Expected: PASS.

### Task 5: Document and verify the completed slice

**Files:**
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/新架构文档.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/test-results/2026-11-18-runtime-turn-lifecycle.md`
- Modify: `docs/testing-catalog.md`
- Modify: `docs/testing-metrics.md`
- Create: `.superpowers/sdd/2026-11-18-runtime-sandbox-backend-injection/progress.md`

- [x] **Step 1: Record the Runtime Sandbox ownership boundary and test results**

Document Runtime ownership of the shared backend set and lazy availability state; retain MCP Provider ownership of Workspace and tool registration. Record exact focused/full test, Ruff, and mypy outcomes and any known sandbox environment failure.

- [x] **Step 2: Run focused style, type, and regression checks**

Run Ruff check/format on changed Python files, `.venv/bin/mypy src/hostpilot/sandbox src/hostpilot/orchestration src/hostpilot/tools`, and the combined sandbox plugin/provider/API tests. Expected: all targeted checks pass.

- [x] **Step 3: Run full pytest and final whitespace checks**

Run: `.venv/bin/pytest -q --import-mode=importlib` and `git diff --check`.
Expected: report exact pass/fail counts; name and distinguish any existing filesystem sandbox temporary-workspace failure.
