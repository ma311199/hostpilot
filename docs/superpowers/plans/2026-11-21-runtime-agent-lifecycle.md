# Runtime Agent Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Let Runtime safely lease, replace, and close Chat Agent instances without interrupting an in-flight Turn during configuration refresh.

**Architecture:** Add a Runtime-owned `AgentLifecycleManager` that publishes one current Agent generation and waits for its active leases before closing it. `ChatService` acquires one lease for the whole Turn; `ChatAgentRuntimeHostPlugin` delegates replacement and Host shutdown to the manager.

**Tech Stack:** Python 3.11, asyncio, pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-runtime-agent-lifecycle-design.md`; lifecycle ownership from `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`.

## Global Constraints

- Keep one Runtime Host and one current Chat Agent per ASGI worker process.
- Do not change Session/Conversation/Turn persistence, API payloads, or HTTP/WebSocket contracts.
- Agent factories and framework adapters remain replaceable plugin implementations.
- A replaced Agent closes only after its in-flight Turn leases have ended.
- Host shutdown prevents new leases; RuntimeHost cancels active Turns before plugin shutdown.

## Review Focus

- Replacement during history loading cannot mix Agent generations within one Turn.
- Replacement while `respond()` is in flight cannot close that Agent prematurely.
- Two concurrent replacements cannot close an Agent twice or publish a closed Agent.
- Host shutdown racing with a new lease must reject the new lease and close resources once.
- Agent close errors remain visible to the replacing configuration operation or Host plugin lifecycle.

### Task 1: Runtime Agent Lifecycle Manager

**Files:**
- Modify: `src/hostpilot/runtime/lifecycle.py`
- Modify: `src/hostpilot/runtime/__init__.py`
- Test: `tests/unit/runtime/test_agent_lifecycle.py`

**Interfaces:**
- Consumes: Any ChatAgentRuntime-compatible object with optional async `aclose()`.
- Produces: `AgentLifecycleManager.lease()`, `.replace(agent)`, and `.aclose()`.

- [x] Add tests for lease acquisition/release, atomic replacement, deferred close while leased, serialized concurrent replacement, shutdown rejection, and exactly-once close.
- [x] Run the new tests and observe the missing manager/export failures.
- [x] Implement the minimal manager with a replacement lock and a condition-protected active lease count.
- [x] Run the focused lifecycle tests, Ruff, and mypy for changed Runtime files.

### Task 2: Hold one Agent lease for each Chat Turn

**Files:**
- Modify: `src/hostpilot/context/builder.py`
- Modify: `src/hostpilot/orchestration/chat.py`
- Test: `tests/unit/orchestration/test_chat_routing.py`

**Interfaces:**
- Consumes: Task 1 `AgentLifecycleManager.lease()`.
- Produces: `ContextBuilder.build(..., runtime=...)` override and ChatService whole-Turn leasing for provider objects that expose `lease()`; raw runtimes remain supported.

- [x] Add a test proving the same leased runtime serves context loading, `respond()`, and `record_turn()` through one Turn.
- [x] Run the regression test and observe the current unleased behavior.
- [x] Implement the optional context reader override and wrap ChatService Turn execution in one Agent lease.
- [x] Run ChatService and ContextBuilder focused tests.

### Task 3: Integrate the manager with the Host plugin and document migration

**Files:**
- Modify: `src/hostpilot/orchestration/runtime_host_plugin.py`
- Modify: `src/hostpilot/api/app.py`
- Modify: `tests/unit/orchestration/test_runtime.py` and relevant API/plugin tests
- Modify: ADR-0034, `docs/roadmap.md`, testing catalog, metrics, and a dated report

- [x] Add tests for replacement during an in-flight Turn and Host shutdown cleanup.
- [x] Make the Host plugin own the manager, expose it as the ChatService runtime provider, and close it during plugin stop.
- [x] Verify configuration refresh publishes the new Agent while the old generation drains.
- [x] Update architecture and test records with actual counts and any limits.
- [x] Run full pytest, Ruff lint, changed-file format, full mypy, and `git diff --check`.

## Completion Contract

- In-flight Turns never observe a prematurely closed Agent or mix Agent generations.
- The newest configured Agent handles newly started Turns immediately.
- Host shutdown closes each Agent generation once after its active leases drain.
- Full backend and quality checks are recorded.
