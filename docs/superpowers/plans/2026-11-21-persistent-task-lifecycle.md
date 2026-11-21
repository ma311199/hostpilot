# Persistent Task Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist Plan subtask lifecycle metadata and mark residual tasks interrupted when their owning Runtime Host is inactive, without restoring or replaying execution.

**Architecture:** Add a replaceable `TaskStore` Port and SQLite implementation sharing the Runtime Host lease table with TurnStore. A Runtime-owned `TaskLifecycleManager` brackets each actual Plan task execution, while ChatService passes it through per-request `RequestContext`. Inactive-host Task recovery runs before the StateServices plugin becomes ready.

**Tech Stack:** Python 3.11, asyncio, aiosqlite, FastAPI lifespan, pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-persistent-task-lifecycle-design.md`

## Global Constraints

- Persist only TaskRun ID, Plan-local task ID, Turn ID, Session ID, Runtime Host ID, status, and timestamps.
- Do not persist task description, input, dependencies, output, tool arguments, credentials, side-effect handles, or raw exception text.
- Recover only TaskRuns owned by Host rows already marked expired or stopped; never alter a live Host's records.
- Do not automatically resume, retry, or replay TaskRuns or tools.
- Preserve current ConversationStore, LangGraph checkpoint, TurnStore, API, and cancellation behavior.
- Keep 5-second heartbeat and 30-second Host lease defaults; tests use injected clocks.

## File Map

- `src/hostpilot/persistence/ports.py`: `TaskStore` Protocol and factory.
- `src/hostpilot/persistence/tasks.py`: SQLite TaskRun persistence and inactive-host recovery.
- `src/hostpilot/persistence/__init__.py`: exports.
- `src/hostpilot/runtime/lifecycle.py`: `TaskState`, `TaskRecord`, and async `TaskLifecycleManager`.
- `src/hostpilot/runtime/host.py`: one TaskLifecycleManager per RuntimeContext.
- `src/hostpilot/runtime/context.py`: optional request-local reference to the Runtime TaskLifecycleManager.
- `src/hostpilot/orchestration/chat.py`: inject the Runtime manager into RequestContext.
- `src/hostpilot/skills/orchestration.py`: persist each actual Plan subtask invocation.
- `src/hostpilot/orchestration/state_services_host_plugin.py`: construct, register, recover, and release TaskStore.
- `src/hostpilot/api/app.py`: injectable TaskStore factory.
- Tests in `tests/unit/persistence/test_tasks.py`, `tests/unit/runtime/test_lifecycle.py`, `tests/unit/runtime/test_host.py`, `tests/unit/orchestration/test_state_services_host_plugin.py`, `tests/unit/orchestration/test_chat_routing.py`, `tests/unit/skills/test_orchestration.py`, and `tests/integration/test_task_lifecycle_persistence.py`.
- Update ADR-0034, testing catalog, metrics ledger, and a dated test report.

## Review Focus

- TaskStore start failure must prevent TaskExecutor invocation.
- Terminal Store failure must not be converted into a normal PlanTaskResult or swallowed at Host shutdown.
- Concurrent Plan tasks must each receive distinct TaskRun IDs, even when Plan-local task IDs repeat in replans.
- Only expired or stopped Hosts' `running` records become `interrupted`; live Hosts remain unchanged.
- Persisted rows must never contain task bodies, outputs, tool arguments, credentials, or exception text.

### Task 1: TaskStore Protocol and SQLite implementation

**Files:** persistence ports, new SQLite TaskStore module/exports, `tests/unit/persistence/test_tasks.py`.

- [x] Add RED tests for create/finish/list, process reopen, constrained states, payload-free schema, and expired-versus-live Host recovery.
- [x] Run the new persistence tests and observe the missing Port/implementation failure.
- [x] Implement `TaskStore`, `TaskStoreFactory`, and `SQLiteTaskStore` using the existing Host lease table.
- [x] Run focused tests, Ruff, and mypy on changed persistence files.

### Task 2: Runtime Task lifecycle manager and request propagation

**Files:** Runtime lifecycle/context/host, ChatService, their focused tests.

- [x] Add RED tests for running/terminal Task states, cancellation, duplicate Plan-local IDs producing separate TaskRuns, start-write fail-closed, and terminal-write cleanup/error propagation.
- [x] Implement a request-linked `TaskLifecycleManager` owned by RuntimeContext and add an optional reference to RequestContext.
- [x] Pass the Host manager through ChatService without adding request payload data to RuntimeContext.
- [x] Run lifecycle, Host, ChatService, mypy, and Ruff checks.

### Task 3: Plan task execution integration

**Files:** `src/hostpilot/skills/orchestration.py`, `tests/unit/skills/test_orchestration.py`.

- [x] Add RED tests asserting each actual `execute_task` call has one persisted TaskRun, with completed/failed/cancelled mapping and no persisted task payload.
- [x] Wrap executor invocation with TaskLifecycleManager only when supplied; preserve standalone Plan executor behavior when absent.
- [x] Ensure failed Store writes do not call the executor and terminal Store errors are not converted to a normal Plan result.
- [x] Run focused Plan orchestration and Runtime Chat no-replay tests.

### Task 4: Host plugin wiring, recovery, integration, and records

**Files:** StateServicesHostPlugin, FastAPI, integration test, ADR/catalog/metrics/report.

- [x] Add RED tests for two-host lease isolation, startup recovery of inactive TaskRuns, and persisted Task terminal state through Chat API restart.
- [x] Inject `TaskStoreFactory`, recover after TurnStore has reconciled inactive Host rows, register `hostpilot.task-store`, and configure RuntimeContext.tasks before readiness.
- [x] Add SQLite + Chat API integration coverage with deterministic Plan task execution; no real model or external tool calls.
- [x] Update architecture and test records with actual counts, warnings, formatter findings, and no-auto-resume boundary.
- [x] Run full pytest, Ruff lint/changed-file format, full mypy, and `git diff --check`.

## Completion Contract

- All TaskRun lifecycle and multi-Host tests pass.
- Existing Turn persistence and Runtime Chat checkpoint/no-replay tests remain green.
- Full backend pytest, Ruff lint, changed-file format check, mypy, and diff check results are recorded.
- No tests call a real model or external tool; database recovery uses deterministic timestamps and SQLite.

## Execution ruling

- Ruling: recover running TaskRuns from both expired and stopped Hosts — shutdown normally persists cancellation before releasing its lease, but a terminal-write failure can leave a running row under a stopped Host; the stopped Host cannot own live execution, so the next startup must mark that residual row interrupted. Cost if wrong: the record may be interrupted even if an external side effect completed, so recovery never retries or replays it.

Final review: self-review (no subagent tool available); found no Critical or Important findings.
Final: minor (deferred): TaskRun retention/cleanup policy remains unspecified as an explicit non-goal; records grow until a separate retention policy is designed.
