# Runtime Tool Outcome Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persistently block a Session after an uncertain side-effecting tool outcome and allow explicit human verification to supply the result to later Turns without automatic replay.

**Architecture:** Add explicit side-effect metadata to tool descriptors, defaulting unknown tools to possibly mutating. A replaceable Store and Runtime manager track only side-effecting calls between dispatch and durable result checkpointing. ChatService gates all Turn execution while an unresolved record exists; recovery APIs and ChatPanel let users submit a verified result or confirm no effect, with the outcome written to the original tool call in the existing checkpoint.

**Tech Stack:** Python 3.11, asyncio, SQLite/aiosqlite, FastAPI/Pydantic, Vue 3, TypeScript, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-21-runtime-tool-outcome-resolution-design.md`.

## Global Constraints

- Never execute a tool classified as possibly side-effecting unless its pending outcome record was durably created first.
- Explicitly read-only tools do not require pending records and may be re-run if their result was not checkpointed.
- Unknown tool effect classification defaults to possibly side-effecting.
- Clear a pending record only after its matching result is durable in the checkpoint or a human resolution is durably written there.
- Never automatically replay an uncertain tool call.
- Do not store tool arguments, credentials, or result text in the outcome metadata table.
- Existing Tool Policy, approvals, budgets, audit, trace, Session leases, and API compatibility remain in force.

## Review Focus

- SQLite failure before a possibly side-effecting call must prevent handler dispatch.
- A checkpoint failure after a read-only tool must not create a persistent recovery block and may retry that read.
- A process failure after checkpoint append but before clearing pending must allow idempotent recovery without conflicting results.
- Concurrent resolution across two Runtime Hosts must not write competing ToolMessages.
- Expired Host resolution claims must be reclaimable without reopening the Session to chat.
- Tool execution exceptions after side-effecting dispatch starts must remain blocked even if no effect is later found.
- User resolution text must never appear in recovery-list responses, logs, or the lifecycle table.
- Non-Chat routes and runtime plugin replacements must not bypass the Session-level gate.

---

### Task 1: Classify effects and persist unresolved side-effecting calls

**Files:**
- Modify: `src/hostpilot/contracts/models.py`, `src/hostpilot/contracts/__init__.py`
- Modify: `src/hostpilot/tools/mcp.py`
- Modify: `src/hostpilot/persistence/ports.py`, `src/hostpilot/persistence/__init__.py`
- Create: `src/hostpilot/persistence/tool_outcomes.py`
- Modify: `src/hostpilot/runtime/lifecycle.py`, `src/hostpilot/runtime/host.py`, `src/hostpilot/runtime/__init__.py`
- Modify: `src/hostpilot/orchestration/state_services_host_plugin.py`, `src/hostpilot/api/app.py`
- Test: `tests/unit/persistence/test_tool_outcomes.py`
- Test: `tests/unit/runtime/test_tool_outcome_lifecycle.py`
- Test: relevant `tests/unit/tools/*` and `tests/unit/orchestration/test_state_services_host_plugin.py`

**Interfaces:**
- Add `ToolSideEffect` classification (`NONE`, `POSSIBLE`) to `ToolDescriptor`, default `POSSIBLE`.
- Explicitly mark local reads and retrieval as `NONE`; keep writes, code execution, and remote tools at `POSSIBLE` unless explicitly declared otherwise.
- Add `ToolOutcomeStore` methods to register/list/claim/release/clear unresolved calls. Persist only Session/Turn/Task/call IDs, tool name, state and timestamps.
- Add a Runtime-owned manager injected by State Services before Host readiness; claims are associated with Runtime Host ownership and are reclaimable after Host lease expiry.

- [ ] Write failing Store/manager and descriptor tests for uniqueness, side-effect defaults, explicit read-only classification, resolution ownership, expired-owner reclaim, and secret-free metadata.
- [ ] Run tests and observe missing classification, Store, and Runtime manager behavior.
- [ ] Implement the Port, SQLite adapter, Runtime manager and injection.
- [ ] Run focused tests, Ruff, changed-file format and mypy.

### Task 2: Instrument execution and gate all new Turns

**Files:**
- Modify: `src/hostpilot/runtime/execution.py`, `src/hostpilot/runtime/execution_loop.py`
- Modify: `src/hostpilot/orchestration/runtime_chat.py`, `src/hostpilot/orchestration/runtime_chat_state.py`, `src/hostpilot/orchestration/runtime.py`
- Modify: `src/hostpilot/orchestration/chat.py`, `src/hostpilot/orchestration/runtime_host_plugin.py`
- Test: `tests/unit/runtime/test_execution_loop.py`, `tests/unit/orchestration/test_runtime_chat.py`, `tests/unit/orchestration/test_chat_routing.py`

**Interfaces:**
- Runtime execution receives the effect classification and pending-call manager; only possibly side-effecting calls create pending records.
- Persist pending before `ToolPort.execute`; clear it only after the matching ToolMessage is checkpointed.
- Existing checkpoint replay protection remains for known results. An unresolved read-only call may execute again; an unresolved side-effecting call blocks.
- ChatService checks unresolved calls inside the Session lease before building context, routing, model, Skill or tool work.
- Human resolution appends a ToolMessage keyed by the original `tool_call_id`; an existing identical result is idempotent, while a conflicting result is rejected.

- [ ] Add failing tests for register-before-dispatch, read-only retry, checkpoint failure preserving side-effect pending state, gate-before-routing, idempotent result handoff, and explicit not-applied result binding.
- [ ] Run tests and observe current tool and Turn behavior.
- [ ] Implement effect-aware execution, fail-closed gating and checkpoint handoff.
- [ ] Run focused execution/chat tests and the backend suite.

### Task 3: Add explicit recovery API and local UI

**Files:**
- Modify: `src/hostpilot/api/app.py`
- Modify: `frontend/src/api/chat.ts`, `frontend/src/ChatPanel.vue`, `frontend/src/style.css`
- Test: `tests/unit/api/test_app.py`
- Test: `frontend/src/api/chat.test.ts`, `frontend/src/ChatPanel.test.ts`

**Interfaces:**
- Read-only endpoint lists sanitized unresolved side-effecting calls for one Session.
- Resolution endpoint accepts `completed` with bounded result text or `not_applied`, uses Session and resolution leases, and updates checkpoint before clearing pending.
- ChatPanel loads unresolved calls and shows a blocking recovery card with tool name/call ID/time, a labelled result field, and explicit not-applied action. Leaving it unresolved makes no state change.

- [ ] Add failing API/UI tests for pending listing, origin/session policy, payload validation, completed/not-applied submission, retry conflicts and blocking-card behavior.
- [ ] Implement endpoints and recovery UI using current API/component patterns.
- [ ] Run backend and frontend focused tests, lint, typecheck and build.

### Task 4: Fault injection, documentation and integration verification

**Files:**
- Create: `tests/integration/test_runtime_tool_outcome_resolution.py`
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`, `docs/roadmap.md`, `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-21-runtime-tool-outcome-resolution.md`

- [ ] Exercise two SQLite-backed Hosts, restart between dispatch and checkpoint, failure between checkpoint and clear, and repeated/conflicting resolutions.
- [ ] Verify read-only failures do not create pending records; side-effecting handlers do not run when pending registration fails; unresolved outcomes block every subsequent Chat route.
- [ ] Record failure cause, repair, rerun counts, storage/privacy limits and commands.
- [ ] Run full pytest, frontend tests/lint/typecheck/build, Ruff, mypy and `git diff --check`.
- [ ] Self-review, commit, and merge to local `main` under the existing authorization; do not push.
