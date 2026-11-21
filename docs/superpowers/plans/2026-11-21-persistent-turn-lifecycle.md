# Persistent Turn Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist Runtime Turn lifecycle records and safely mark Turns interrupted after their owning ASGI worker lease expires.

**Architecture:** Add a replaceable `TurnStore` Port with a SQLite implementation for Host leases and Turn records. Keep active asyncio task cancellation in `TurnLifecycleManager`, make lifecycle tracking awaitable so durable transitions bracket ChatService execution, and let the state-services plugin recover stale owners before serving requests. A Runtime Host heartbeat prevents one live ASGI worker from recovering another worker's active Turns.

**Tech Stack:** Python 3.11, asyncio, aiosqlite, FastAPI lifespan, pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-persistent-turn-lifecycle-design.md`

## Global Constraints

- Persist only Turn metadata: `turn_id`, `session_id`, `runtime_host_id`, state, and timestamps.
- Do not persist user messages, model output, tool arguments, credentials, or raw exception text.
- Recover only Turns owned by a Host whose lease has expired; never mark another live worker's Turns interrupted.
- Do not resume Agent/Plan execution or replay tools automatically.
- Keep the persistence Port replaceable; SQLite is the current implementation.
- Preserve one Runtime Host per ASGI worker, shared RuntimeContext per Host, and existing API behavior.
- Default lease timing is a 5-second heartbeat and 30-second expiration window; tests may inject a clock and shorter periods.

## File Map

- `src/hostpilot/persistence/ports.py`: TurnStore protocol and factory type.
- `src/hostpilot/persistence/turns.py`: SQLite lease and Turn record implementation.
- `src/hostpilot/persistence/__init__.py`: persistence exports.
- `src/hostpilot/runtime/lifecycle.py`: durable Turn state transitions plus in-process cancellation indexes.
- `src/hostpilot/runtime/host.py`: Runtime Host identity and lifecycle context integration.
- `src/hostpilot/orchestration/chat.py`: async Turn tracking around request execution.
- `src/hostpilot/orchestration/state_services_host_plugin.py`: create/register/recover/heartbeat/release TurnStore.
- `src/hostpilot/api/app.py`: start the state-services heartbeat as a RuntimeHost-managed background task.
- Tests in `tests/unit/persistence/test_turns.py`, `tests/unit/runtime/test_lifecycle.py`, `tests/unit/runtime/test_host.py`, `tests/unit/orchestration/test_state_services_host_plugin.py`, `tests/unit/orchestration/test_chat_routing.py`, and `tests/integration/test_turn_lifecycle_persistence.py`.
- `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`, `docs/testing-catalog.md`, `docs/testing-metrics.md`, and a dated `docs/test-results/` report.

## Review Focus

- A second worker starts while the first worker has a valid lease: the first worker's running Turn must remain `running`.
- A worker lease expires during process loss: only its running Turns become `interrupted`, with a terminal timestamp.
- A TurnStore failure before a turn starts: ChatService must not execute the request.
- A TurnStore failure while recording a terminal state: in-memory active tracking must still be cleaned up, and the persistence error must not be hidden.
- Host shutdown while a Turn is active: cancellation is persisted before the Host lease is released.

### Task 1: TurnStore Port and SQLite persistence

**Files:**
- Modify: `src/hostpilot/persistence/ports.py`
- Create: `src/hostpilot/persistence/turns.py`
- Modify: `src/hostpilot/persistence/__init__.py`
- Test: `tests/unit/persistence/test_turns.py`

**Interfaces:**
- Produces a replaceable store that initializes schema, atomically registers a Host while expiring stale Hosts, heartbeats/releases a Host, creates/finishes a Turn, and lists persisted Turn records.
- The record schema uses stable string IDs, constrained lifecycle state values, UTC ISO timestamps, and no request payload fields.

- [x] Write SQLite tests for schema initialization, Turn create/finish/list, process reopen, stale-lease recovery, and valid-lease isolation between two Hosts.
- [x] Run `.venv/bin/pytest -q tests/unit/persistence/test_turns.py` and confirm the tests fail because the Port and implementation are absent.
- [x] Implement `TurnStore`, its factory type, and `SQLiteTurnStore` with transactional stale-host recovery.
- [x] Run the focused tests and confirm all pass; run Ruff and mypy on the new files.

### Task 2: Persist Turn transitions without losing in-process cancellation

**Files:**
- Modify: `src/hostpilot/runtime/lifecycle.py`
- Modify: `src/hostpilot/orchestration/chat.py`
- Modify: `tests/unit/runtime/test_lifecycle.py`
- Modify: `tests/unit/orchestration/test_chat_routing.py`

**Interfaces:**
- `TurnLifecycleManager.track(turn_id, session_id)` becomes an async context manager backed by an injected TurnStore and Host ID.
- Active asyncio task lookup and `cancel_turn`, `cancel_session`, and `cancel_all` remain process-local and synchronous/asynchronous as currently exposed.

- [x] Add lifecycle tests for `running`, `completed`, `failed`, and `cancelled` persistence, start-write failure, and terminal-write cleanup.
- [x] Run focused lifecycle and ChatService tests; the first run exposed the expected sync-to-async context manager incompatibility, then passed after migration.
- [x] Implement async lifecycle tracking, ensuring active-task cleanup occurs even when final persistence raises.
- [x] Update ChatService to await the lifecycle context while preserving its existing request, timeout, cancellation, and error behavior.
- [x] Run `.venv/bin/pytest -q tests/unit/runtime/test_lifecycle.py tests/unit/orchestration/test_chat_routing.py` and confirm all pass.

### Task 3: Runtime Host leases and stale Turn recovery

**Files:**
- Modify: `src/hostpilot/runtime/host.py`
- Modify: `src/hostpilot/orchestration/state_services_host_plugin.py`
- Modify: `tests/unit/runtime/test_host.py`
- Modify: `tests/unit/orchestration/test_state_services_host_plugin.py`

**Interfaces:**
- `RuntimeContext` exposes a unique `runtime_host_id` for its worker process.
- StateServicesHostPlugin accepts an injectable TurnStore factory, registers the stable service ID `hostpilot.turn-store`, starts/reconciles the Host lease before readiness, and releases it on shutdown.
- The plugin exposes a heartbeat coroutine; RuntimeHost owns its task and cancels it before the plugin releases the lease.

- [x] Add tests for unique Host ownership, stale Host recovery, heartbeat renewal, shutdown release, and ensuring a live Host's Turns are untouched.
- [x] Confirm the Host identity test fails before implementation (`RuntimeContext` lacked `runtime_host_id`).
- [x] Implement Host identity, injected store wiring, transactional startup recovery, heartbeat renewal, and orderly release.
- [x] Run `.venv/bin/pytest -q tests/unit/runtime/test_host.py tests/unit/orchestration/test_state_services_host_plugin.py`.

### Task 4: FastAPI wiring and persisted Chat integration

**Files:**
- Modify: `src/hostpilot/api/app.py`
- Create: `tests/integration/test_turn_lifecycle_persistence.py`
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/testing-catalog.md`
- Modify: `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-21-persistent-turn-lifecycle.md`

**Interfaces:**
- FastAPI starts the Store heartbeat through `RuntimeHost.create_task` after the Host reaches READY and before accepting requests.
- `ChatService` receives the Host's configured TurnLifecycleManager; no turn body or secret data is added to persistence.

- [x] Add an integration test that sends a Chat API request and observes its persisted terminal Turn state, then restarts the app and verifies state remains available.
- [x] Run the focused integration test and verify the persisted terminal state survives application restart.
- [x] Wire the heartbeat into FastAPI lifespan and ensure RuntimeHost cancels it before state services close.
- [x] Run the focused integration test plus lifecycle, API and tool-recovery regression tests.
- [x] Update the architecture/testing records with actual counts, failure notes, and the no-auto-replay boundary.
- [x] Run full `.venv/bin/pytest -q`, Ruff lint, changed-file format, `.venv/bin/mypy src/hostpilot`, and `git diff --check`; record exact results. Full-repository format check reports one unrelated existing formatting difference, documented in the report.

## Completion Contract

- All four tasks' tests pass, including the multi-worker live-lease case and restart recovery case.
- The full backend test suite, Ruff lint, changed-file format check, mypy, and `git diff --check` pass; repository-wide formatter output has one unrelated pre-existing difference.
- No tests call a real model or external tool; recovery uses a deterministic clock and SQLite.
- The test-result report records lease timings, recovery behavior, test totals, and known warning(s).
