# Runtime Chat Composition Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Move Chat Runtime composition into a dedicated module used by the Runtime assembly service while retaining the old factory as a compatible public wrapper.

**Architecture:** Extract the existing synchronous composition implementation and its private helpers from `orchestration/runtime.py` into `orchestration/chat_runtime_composition.py`. `ChatRuntimeAssemblyHostPlugin` will call that implementation directly; `create_configured_chat_agent_runtime()` will preserve its signature and exports while delegating to the same function.

**Tech Stack:** Python 3.11+, Pydantic Settings, RuntimeHost services/plugins, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-24-runtime-chat-composition-ownership-design.md`

## Global Constraints

- Preserve the old factory's module path, public export, signature, defaults, return contract, and exception behavior.
- Keep the composition function synchronous and side-effect-compatible; do not publish or close candidate Agent generations from it.
- Keep Agent generation and shutdown ownership in `builtin.chat-execution`; keep candidate Provider/configuration coordination in the existing API paths.
- Preserve Model, ToolFacade, Skill, Plan, budget, approval, checkpoint, and recovery behavior.
- Do not add dependencies, change HTTP/WebSocket contracts, or run live model evaluations.
- Preserve all existing uncommitted workspace changes; do not commit or push.

## Review Focus

- Legacy callers depend on exact factory signature/defaults: test `inspect.signature` and forwarding of optional registries and sinks.
- Candidate configurations contain mutable nested Settings: test that the existing `ChatRuntimeAssemblyRequest` snapshot reaches composition unchanged.
- No ToolFacade is configured: test that the unavailable Tool Port behavior and empty tool allowlist remain unchanged.
- A custom ModelProvider, ChatAgentRuntime, or Plan registry is injected: test that composition uses that registry instead of its built-in default.
- Composition raises during candidate construction: test that the error propagates and no active Runtime publication/Provider replacement is introduced.

## File Map

- Create `src/hostpilot/orchestration/chat_runtime_composition.py`: canonical composition function, runtime-tool descriptor helpers, and unavailable Tool Port implementation.
- Modify `src/hostpilot/orchestration/runtime.py`: retain ChatAgentRuntime interfaces and adapters; make the old public factory a compatibility wrapper and preserve `__all__`.
- Modify `src/hostpilot/orchestration/chat_runtime_assembly_host_plugin.py`: use the canonical composition function instead of importing the compatibility wrapper.
- Modify `tests/unit/orchestration/test_runtime.py`: retain behavior coverage and prove the compatibility wrapper delegates all arguments and propagates results/errors.
- Modify `tests/unit/test_runtime_naming.py`: keep the historical factory's public parameter/signature assertions pinned to the compatibility wrapper.
- Modify `tests/unit/orchestration/test_runtime_plugins.py`: prove Host Plugin uses the canonical composer independently of the legacy function.
- Modify `tests/unit/api/test_app.py`: redirect composition monkeypatches to the canonical composer and preserve startup/refresh failure coverage.
- Modify `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`, `docs/roadmap.md`, `docs/新架构文档.md`, `docs/development-guide.md`, `docs/testing-catalog.md`, and `docs/testing-metrics.md`: record the new implementation boundary and compatibility status.
- Create `docs/test-results/2026-11-24-runtime-chat-composition-ownership.md`: record tests, verification counts, and limitations.

---

### Task 1: Extract the canonical composition implementation

**Files:**
- Create: `src/hostpilot/orchestration/chat_runtime_composition.py`
- Modify: `src/hostpilot/orchestration/runtime.py`
- Test: `tests/unit/orchestration/test_runtime.py`

**Interfaces:**
- Produces `build_configured_chat_agent_runtime(settings: Settings, *, checkpointer: Any, tools: Sequence[Any] = (), tool_provider: Any | None = None, memory_controller: Any | None = None, compensation_registry: CompensationRegistry | None = None, cancellation_sink: CancellationSink | None = None, skill_execution_modes: Mapping[str, str] | None = None, trace_sink: TraceSink | None = None, plan_component_registry: PlanComponentRegistry | None = None, chat_agent_runtime_registry: ChatAgentRuntimeRegistry | None = None, model_provider_registry: ModelProviderRegistry | None = None) -> ChatAgentRuntime`.
- Keeps `create_configured_chat_agent_runtime` in `orchestration/runtime.py` with the identical public signature; it delegates all arguments to the new function.

- [x] **Step 1: Add a compatibility-wrapper test** asserting the old factory forwards every positional/keyword input, returns the canonical composer result, and propagates the composer exception.
- [x] **Step 2: Run the focused test and confirm RED** because the canonical module/function does not exist and the old function still owns composition.
- [x] **Step 3: Move the current factory body and its private-only helpers** `_tool_descriptors`, `_runtime_tool_definitions`, and `_UnavailableRuntimeToolPort` into `chat_runtime_composition.py`; leave Chat Runtime contracts, framework adapters, and the public wrapper in `runtime.py`.
- [x] **Step 4: Run** `.venv/bin/pytest -q tests/unit/orchestration/test_runtime.py tests/unit/test_runtime_naming.py`; verify the compatibility signature check still passes and all composition behavior tests remain green.

### Task 2: Make the Runtime assembly service use the canonical composer

**Files:**
- Modify: `src/hostpilot/orchestration/chat_runtime_assembly_host_plugin.py`
- Test: `tests/unit/orchestration/test_runtime_plugins.py`

**Interfaces:**
- Consumes `build_configured_chat_agent_runtime` from Task 1.
- `ChatRuntimeAssemblyHostPlugin.build_candidate(request)` continues returning an unpublished `ChatAgentRuntime` and keeps the injectable `assembly_factory` test seam.

- [x] **Step 1: Add a Plugin test** that replaces the canonical composer with a recorder, makes the legacy factory fail if called, and asserts the Plugin forwards the Request Settings snapshot, Host Checkpointer/Trace/Cancellation, candidate tools/provider/memory, Skill modes, and injected registries.
- [x] **Step 2: Run the test and confirm RED** because the Plugin still resolves the legacy factory.
- [x] **Step 3: Change the Plugin default path** to call the canonical composer directly; preserve explicit `assembly_factory` injection and setup-time missing-service failures.
- [x] **Step 4: Run** `tests/unit/orchestration/test_runtime_plugins.py -k 'chat_runtime_assembly'` and verify the Plugin is independent of the compatibility symbol.

### Task 3: Update app composition tests and documentation

**Files:**
- Modify: `tests/unit/api/test_app.py`
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/新架构文档.md`
- Modify: `docs/development-guide.md`
- Modify: `docs/testing-catalog.md`
- Modify: `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-24-runtime-chat-composition-ownership.md`

**Interfaces:**
- API production paths continue using the registered assembly service; API tests that need to inject composition failures patch the new canonical composer.

- [x] **Step 1: Update API test doubles/patch targets** to intercept the canonical composition function while preserving assertions for initial assembly, MCP refresh, model refresh, candidate failure, resource cleanup, custom factory, and no-model startup.
- [x] **Step 2: Run** `.venv/bin/pytest -q tests/unit/orchestration/test_runtime.py tests/unit/orchestration/test_runtime_plugins.py tests/unit/api/test_app.py` and verify all targeted composition and lifecycle tests pass.
- [x] **Step 3: Update architecture and test documentation** to state that the Plugin uses the canonical composer and the old exported factory remains a compatibility wrapper; record exact checks and explicitly note no live model evaluation/coverage measurement.
- [x] **Step 4: Run** `bash scripts/check_quality.sh` and `git diff --check`; record exact final output in the test report and metrics ledger.

## Acceptance Criteria

- The assembly Plugin does not import or call `create_configured_chat_agent_runtime`.
- The new composition function is the single canonical implementation.
- The old function and package export retain the same signature and delegate without semantic changes.
- Runtime, Skill, Plan, Tool governance, lifecycle, and recovery tests pass; no frontend changes are required.
- The design, test catalog, metrics ledger, and result report agree on current behavior and verification counts.
