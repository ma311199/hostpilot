# Runtime Plan Task Cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Reuse exact matching successful Plan task results only after a per-hit human decision, and provide bounded failure summaries to future planning.

**Architecture:** Extend the TaskStore with deterministic task fingerprints and separate safe failure summaries. The Plan executor checks candidates only when dependencies are resolved; an injected asynchronous WebSocket approval provider either returns a historical result as a reused state or allows normal execution. Trace records candidates and decisions without task payloads.

**Tech Stack:** Python 3.11, SQLite/aiosqlite, FastAPI WebSocket, Vue 3, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-22-runtime-plan-task-cache-design.md`

## Global Constraints

- Every successful result hit requires an explicit user choice.
- Cache candidates match the versioned task definition, explicit input, expected output, and resolved dependency outputs.
- Session-scope cache lookup and failure reference queries.
- No decision, disconnect, or cancellation must never default to reuse.
- Failure summaries contain safe metadata only and never auto-retry tasks.
- Trace records cache IDs/status/decision only and excludes task input/output.

## Review Focus

- A matching task with missing approval provider: the executor must not silently reuse.
- A cache decision of `execute`: exactly one fresh TaskExecutor call must occur.
- An approved hit: no TaskExecutor call occurs and dependents receive the stored output.
- Two Sessions have identical fingerprints: neither can see the other's results.
- A failed task has partial output or side effects: neither output nor raw exception is fed to future planning, and no automatic retry is triggered.

---

### Task 1: Exact fingerprints, reusable results, and failure summaries

**Files:** `src/hostpilot/task_results.py`, `src/hostpilot/persistence/ports.py`, `src/hostpilot/persistence/tasks.py`, `src/hostpilot/runtime/lifecycle.py`; tests in `tests/unit/persistence/test_tasks.py` and `tests/unit/runtime/test_task_lifecycle.py`.

**Interfaces:** Persist `task_fingerprint`; query by `(session_id, fingerprint)`; list bounded failed summaries. TaskRun completion/failure handles pass safe data to the Store.

- [x] Add RED tests for fingerprint lookup, failed summary persistence/filtering, and rejection of unsafe failure data.
- [x] Implement versioned fingerprint storage/query and bounded failure summaries without persisting failure output or exception text.
- [x] Run persistence/lifecycle tests.

### Task 2: Plan cache lookup and approval boundary

**Files:** `src/hostpilot/runtime/context.py`, `src/hostpilot/runtime/lifecycle.py`, `src/hostpilot/skills/orchestration.py`, `src/hostpilot/skills/model_plan.py`, `src/hostpilot/api/app.py`; tests in orchestration, Runtime and API suites.

**Interfaces:** Inject a Plan cache decision provider; each decision request has a request ID, task ID, source run ID, description, completion time, and result. Decisions are `reuse` or `execute`.

- [x] Add RED tests for exact match, both decisions, missing provider, cancellation, task ID collisions, dependency result fingerprints, and downstream dependency injection.
- [x] Add task fingerprint calculation and per-task cache lookup after dependency resolution.
- [x] Reuse only after explicit `reuse`; record the hit/decision in Trace; `execute` follows the normal TaskExecutor path.
- [x] Run orchestration and Runtime tests.

### Task 3: WebSocket approval UI and failure context

**Files:** `src/hostpilot/api/app.py`, `src/hostpilot/skills/model_plan.py`, `frontend/src/api/chatSocket.ts`, `frontend/src/ChatPanel.vue`, `frontend/src/TracesPanel.vue`; API/Vue tests.

**Interfaces:** Add `plan.task.cache.request` and `plan.task.cache.decision` WebSocket messages; Planner receives a bounded `prior_failed_tasks` list. Trace UI labels cache candidate, reused, ignored, and decision.

- [x] Add RED tests for message parsing/decision round trip, approved reuse UI controls, and Planner receipt of sanitized failure summaries.
- [x] Implement the WebSocket provider and ChatPanel result review controls; treat close/cancel as non-reuse.
- [x] Add failure summaries to Planner input and trace labels/statuses.
- [x] Run API, frontend unit tests, and production build.

### Task 4: Documentation and verification

**Files:** `docs/roadmap.md`, `docs/新架构文档.md`, `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`, `docs/testing-catalog.md`, `docs/testing-metrics.md`, and a dated verification report.

- [x] Document exact matching, per-hit approval, failure reference behavior, and no automatic retry.
- [x] Run targeted and full Python/frontend test suites, Ruff, mypy, frontend lint/type/build, format checks, and `git diff --check`.
- [x] Record outcomes, limitations, and the local merge commit.
