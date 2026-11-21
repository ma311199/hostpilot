# Runtime Session Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Runtime coordinate Session use, deletion, and Host shutdown across ASGI workers without changing conversation API contracts.

**Architecture:** Add a replaceable `SessionLifecycleStore` Port and SQLite implementation that atomically grants Session operation leases while the Session is active. A Runtime-owned `SessionLifecycleManager` holds one lease across each operation, gates deletion, waits for valid leases to drain, and coordinates cleanup. Host leases determine whether a worker-owned Session lease is still live.

**Tech Stack:** Python 3.11, asyncio, SQLite/aiosqlite, FastAPI, pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-runtime-session-lifecycle-design.md`; lifecycle ownership from `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`.

## Global Constraints

- One Runtime Host exists per ASGI worker process; Session lifecycle coordination must work across workers sharing persistence.
- A closing Session rejects new operation leases and is deleted only after valid leases drain.
- Session deletion waits for existing operations; it does not cancel model or tool work.
- Host shutdown rejects new Session leases, cancels active Turns/Tasks, drains local Session operations, then releases the Host lease.
- Keep existing Conversation/Session HTTP and WebSocket payloads unchanged.
- Keep ConversationStore, SessionLifecycleStore, Checkpointer, and Memory implementations replaceable.
- Preserve existing request budgets, policy, approvals, audit, and checkpoint behavior.

## Review Focus

- Two workers race to acquire a Session lease and close the Session: the store must serialize the transition so either the operation is leased and drained or it is rejected before work starts.
- A worker crashes while holding a Session lease: its lease must stop blocking deletion when the associated Host lease expires.
- Checkpoint or Memory cleanup fails after the Session enters closing: retry must continue cleanup without reopening the Session or requiring the Conversation row to remain.
- Host shutdown races with a history or route-only request: the Host must not release its durable lease before local Session operation leases drain.
- Two concurrent delete requests target the same Session: cleanup operations must remain safe and both callers must observe consistent completion/not-found semantics.

---

### Task 1: Define and persist Session lifecycle leases

**Files:**
- Modify: `src/hostpilot/persistence/ports.py`
- Modify: `src/hostpilot/persistence/__init__.py`
- Create: `src/hostpilot/persistence/sessions.py`
- Modify: `src/hostpilot/orchestration/state_services_host_plugin.py`
- Test: `tests/unit/persistence/test_sessions.py`
- Test: `tests/unit/orchestration/test_state_services_plugin.py`

**Interfaces:**
- Produces `SessionLifecycleStore.register_session(session_id)`, `acquire_lease(session_id, lease_id, runtime_host_id, now)`, `release_lease(lease_id, runtime_host_id)`, `begin_close(session_id)`, `active_lease_count(session_id, now)`, and idempotent `finish_delete(session_id)`.
- Produces `SQLiteSessionLifecycleStore(database_path)`; lease liveness is determined by joining `runtime_session_leases` to `runtime_hosts` and checking active status plus `lease_expires_at > now`.
- State Services initializes the ConversationStore and TurnStore before the SessionLifecycleStore, backfills legacy Conversation IDs as active, then configures RuntimeContext's Session manager before Host readiness.

- [x] Add tests for lease acquisition/release, rejecting new leases after close, two Store instances sharing the database, Host lease expiry, legacy Session backfill, and idempotent close completion.
- [x] Run those tests and observe missing Port/table behavior.
- [x] Implement the SessionLifecycleStore Port and SQLite adapter with transactional state checks.
- [x] Add injected Store factory support to State Services and configure the Runtime Session manager before readiness.
- [x] Run persistence and State Services focused tests, Ruff, and mypy on changed persistence/runtime files.

### Task 2: Add Runtime Session lifecycle coordination

**Files:**
- Modify: `src/hostpilot/runtime/lifecycle.py`
- Modify: `src/hostpilot/runtime/__init__.py`
- Modify: `src/hostpilot/runtime/host.py`
- Test: `tests/unit/runtime/test_session_lifecycle.py`
- Test: `tests/unit/runtime/test_host.py`

**Interfaces:**
- Produces `SessionLifecycleManager.configure_persistence(store, runtime_host_id)`, `.lease(session_id)`, `.close_session(session_id, cleanup)`, `.begin_shutdown()`, and `.aclose()`.
- `RuntimeContext.sessions` is the single process-local manager; it owns only in-flight operation bookkeeping while the injected Store owns cross-worker Session state and leases.
- `close_session()` sets closing before waiting; failed cleanup leaves the state closing so a later call can retry. `aclose()` rejects new leases and waits for local operations without deleting Sessions.

- [x] Add failing tests for same-Session lease blocking/release, close gating, cleanup retry after error, active lease draining across two manager/Store instances, and Host shutdown draining.
- [x] Run the tests and confirm the current Runtime lacks Session lifecycle behavior.
- [x] Implement manager coordination using a local condition plus the persistent Store; use bounded polling for leases held by other workers.
- [x] Update RuntimeHost shutdown to reject Session leases before cancelling Turns and to drain local Session operations before State Services releases Host ownership.
- [x] Run Runtime lifecycle, Host, and State Services focused tests.

### Task 3: Route Session operations through Runtime lifecycle

**Files:**
- Modify: `src/hostpilot/api/app.py`
- Modify: `src/hostpilot/orchestration/chat.py`
- Modify: `tests/unit/api/test_app.py`
- Modify: `tests/unit/orchestration/test_chat_routing.py`
- Modify: `tests/integration/test_session_lifecycle.py`

**Interfaces:**
- ChatService accepts an optional Session lifecycle provider for raw standalone callers; the production API injects RuntimeContext's manager.
- Chat send, history, route-only evaluation, single-Session metadata reads, and SandboxMode upgrades hold a Session lease for their complete operation.
- Session creation registers lifecycle state before returning. Session deletion uses `close_session()` and supplies the existing Checkpointer/Memory/Conversation cleanup as an idempotent callback.
- Closing Sessions are unavailable to new single-Session operations; global listing and response schemas remain unchanged.

- [x] Add regressions for operation rejection after closing begins, a Turn lease spanning context/model/tool work, deletion waiting for a second worker's lease, cleanup retry after injected failure, and Session creation/deletion API compatibility.
- [x] Run the regressions and observe current deletion/Turn behavior.
- [x] Integrate ChatService and API Session operations with the Runtime manager.
- [x] Verify two SQLite-backed workers coordinate a Session close while only one performs active work; no real model or external tool is used.
- [x] Run ChatService, API, and persistence integration tests.

### Task 4: Document migration and verify the complete change

**Files:**
- Modify: ADR-0034
- Modify: `docs/roadmap.md`
- Modify: `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-21-runtime-session-lifecycle.md`

- [x] Update architecture and roadmap status, clearly separating Session lifecycle from Session storage implementation.
- [x] Record test commands, counts, multi-worker scenarios, cleanup retry behavior, and known limits.
- [x] Run full pytest, Ruff lint, changed-file format, full mypy, and `git diff --check`.
- [x] Self-review the complete branch, fix Critical/Important issues with red-green tests, commit, and merge to local `main` under the existing user authorization; do not push.

## Completion Contract

- Session operations cannot begin after Session closing starts.
- Deletion waits for Session operation leases across workers and safely handles expired Host owners.
- Cleanup failure leaves a retryable closing Session and cannot make it active again.
- Host shutdown drains local Session operations before the durable Host lease is released.
- Existing API payloads and normal create/read/delete flows remain compatible.
- Full backend and quality checks are recorded.
