# Runtime Chat Assembly Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route initial and refreshed Chat Runtime construction through a RuntimeHost-managed assembly service while preserving current runtime and configuration lifecycle behavior.

**Architecture:** Add `builtin.chat-runtime-assembly`, which registers `hostpilot.chat-runtime-assembly` and creates unpublished candidate Chat Runtimes. The API continues to prepare candidate settings and MCP providers and perform coordinated configuration replacement; `builtin.chat-execution` remains the owner of Agent generations and leases. Existing composition logic and its factory stay as a compatibility implementation during migration.

**Tech Stack:** Python 3.12, FastAPI lifespan, RuntimeHost plugin/service registry, pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-runtime-chat-assembly-plugin-design.md`

## Global Constraints

- Preserve the existing Chat Runtime plugin API, HTTP/WebSocket behavior, Skill/Plan behavior, tool authorization, approval, budgets, and recovery behavior.
- Do not store request or user state in `RuntimeContext`.
- Candidate assembly must not publish or close the active Chat Runtime.
- Keep current custom `chat_agent_runtime_factory` behavior and the no-model startup path.
- Keep all existing uncommitted work in the workspace; do not commit or push.
- Do not run live model evaluations.

## Review Focus

- Missing Runtime services during assembly plugin setup must fail Host startup clearly; test the dependency/service validation.
- A candidate build failure must leave the currently active Chat Runtime and MCP Provider untouched and close candidate resources according to existing API behavior; test the configuration refresh failure path.
- Model, MCP, and Skill settings must be taken from the candidate configuration, not stale startup settings; assert captured request values for initial and refreshed assembly.
- The custom `chat_agent_runtime_factory` path must remain independent of default assembly; retain and exercise its existing API coverage.
- A configuration without a main model must not trigger model construction; add/retain a startup assertion that the assembly service is not invoked.

---

## File Map

- Create `src/hostpilot/orchestration/chat_runtime_assembly_host_plugin.py`: immutable assembly request, assembly service protocol/implementation, plugin registration, and candidate construction.
- Modify `src/hostpilot/api/app.py`: construct/register the assembly plugin, resolve its service in startup and candidate refresh paths, and stop importing/calling the composition factory directly from API flow.
- Modify `tests/unit/orchestration/test_runtime_plugins.py`: verify assembly plugin dependencies, service registration, and candidate construction inputs/errors.
- Modify `tests/unit/api/test_app.py`: verify startup and configuration refresh use the registered assembly service and preserve custom factory/failure behavior.
- Modify `docs/roadmap.md` and `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`: record the migrated boundary and remaining factory compatibility status.
- Create `docs/test-results/2026-11-23-runtime-chat-assembly-plugin.md`: record relevant verification commands/results after implementation.

## Task 1: Add the Runtime Chat assembly service

**Interfaces:**

- Produces `ChatRuntimeAssemblyRequest(settings, tools, tool_provider, memory_controller, skill_execution_modes)`.
- Produces `ChatRuntimeAssembly.build_candidate(request) -> ChatAgentRuntime`.
- `ChatRuntimeAssemblyHostPlugin.spec.plugin_id` is `builtin.chat-runtime-assembly` and its service ID is `hostpilot.chat-runtime-assembly`.
- The plugin receives the existing Plan, Chat Runtime, and Model Provider registries as construction dependencies. At candidate-build time it resolves the Checkpointer, Trace, and Cancellation services from `RuntimeContext.services`; it forwards explicit candidate settings/tools/provider/memory/skill modes to the composition factory.

**Files:**

- Create: `src/hostpilot/orchestration/chat_runtime_assembly_host_plugin.py`
- Test: `tests/unit/orchestration/test_runtime_plugins.py`

- [x] **Step 1: Write failing plugin contract tests**

Test that `setup()` resolves Checkpointer, Trace, and Cancellation services and registers `hostpilot.chat-runtime-assembly`, and that `build_candidate()` forwards the immutable request plus those services to the composition factory. Test that absent required services cause plugin setup to fail with the service ID in the error.

- [x] **Step 2: Run the focused tests and confirm RED**

Run: `.venv/bin/pytest -q tests/unit/orchestration/test_runtime_plugins.py -k chat_runtime_assembly`

Expected: the new tests fail because the assembly plugin/service does not exist.

- [x] **Step 3: Implement the request and host plugin**

Create the frozen request type and plugin. Declare dependencies on `builtin.state-services` and `builtin.application-stores`; resolve Checkpointer, Trace, and Cancellation by their existing stable service IDs during `setup()` so missing services fail Host startup. Delegate candidate construction to `create_configured_chat_agent_runtime()` with the same arguments currently passed by the API.

- [x] **Step 4: Run focused tests and confirm GREEN**

Run: `.venv/bin/pytest -q tests/unit/orchestration/test_runtime_plugins.py -k chat_runtime_assembly`

Expected: all matching tests pass.

## Task 2: Route startup and configuration refresh through Runtime assembly

**Consumes:** `ChatRuntimeAssemblyHostPlugin` registered by Task 1.

**Files:**

- Modify: `src/hostpilot/api/app.py`
- Test: `tests/unit/api/test_app.py`

- [x] **Step 1: Add failing API integration assertions**

Update/add tests proving initial model-backed startup, MCP refresh, and model configuration refresh call `hostpilot.chat-runtime-assembly`; assert each request uses current settings and candidate provider/tools. Add a failing-candidate test proving the active runtime/provider remain unchanged. Keep the existing custom factory and no-model tests as compatibility checks.

- [x] **Step 2: Run the focused API tests and confirm RED**

Run: `.venv/bin/pytest -q tests/unit/api/test_app.py -k 'runtime_assembly or runtime_reuses_sandbox_backends_across_initial_and_candidate_providers'`

Expected: the new service-path assertions fail while API still calls the module-level factory.

- [x] **Step 3: Register and use the assembly plugin**

Construct `ChatRuntimeAssemblyHostPlugin` with existing injected component registries, register it with RuntimeHost, and resolve its service only within the running lifespan. Replace all direct API calls for startup, MCP configuration changes, and main model configuration changes with `build_candidate(request)`. Preserve candidate cleanup, config-save ordering, `runtime_lock`, `chat_agent_runtime_plugin.replace()`, provider replacement, and the custom runtime factory branch.

- [x] **Step 4: Run focused API tests and confirm GREEN**

Run: `.venv/bin/pytest -q tests/unit/api/test_app.py -k 'runtime_assembly or runtime_reuses_sandbox_backends_across_initial_and_candidate_providers or app_runtime_host_manages_chat_runtime_lifecycle'`

Expected: all matching API tests pass, including existing lifecycle and sandbox backend replacement assertions.

## Task 3: Record migration status and run regression verification

**Consumes:** service path and API wiring from Tasks 1–2.

**Files:**

- Modify: `docs/roadmap.md`
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/新架构文档.md`
- Modify: `docs/testing-catalog.md` and `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-23-runtime-chat-assembly-plugin.md`

- [x] **Step 1: Update architecture progress and verification record**

Document the new plugin/service, its dependency boundary, API's retained configuration-switching role, the compatibility factory, and any verification limitations. Do not mark DeepAgents private state recovery complete.

- [x] **Step 2: Run targeted orchestration and API regressions**

Run: `.venv/bin/pytest -q tests/unit/orchestration/test_runtime.py tests/unit/orchestration/test_runtime_plugins.py tests/unit/api/test_app.py`

Expected: all selected tests pass.

- [x] **Step 3: Run project quality verification required by AGENTS.md**

Run the repository's Python quality script and the established frontend checks because the task changes RuntimeHost wiring used by the full application. Record exact commands and counts in the test result document. Do not run a live model evaluation.

- [x] **Step 4: Check whitespace and review final diff**

Run: `git diff --check`

Expected: no whitespace errors; review confirms no unrelated user changes were reverted or overwritten.
