# Runtime Persistent Plan Results Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist successful Plan task inputs and outputs and make recent results available as planning context to a new Turn.

**Architecture:** Extend the replaceable TaskStore Port with an independent SQLite result table and bounded session-scoped lookup. Runtime task handles atomically finish successful TaskRuns with result data. The Plan Blackboard carries historical results separately from current states, and LLMPlanner receives that history without treating it as completed work.

**Tech Stack:** Python 3.11, aiosqlite, SQLite, pytest, existing Runtime lifecycle and Plan orchestration.

**Spec:** `docs/superpowers/specs/2026-11-22-runtime-persistent-plan-results-design.md`

## Global Constraints

- Persist results only for successful TaskRuns; never resume an old Plan or automatically replay tools.
- Keep current Plan results separate from historical Session results.
- Session-scope every history query, exclude the current Turn, and cap history to 32 entries / 65,536 bytes.
- Store result content only in the local persistence layer; do not add it to traces, audits, or tool outcome APIs.
- Preserve existing policy, confirmation, and uncertain tool outcome boundaries.

## Review Focus

- A historical task ID collides with a new Plan task ID: historical data must not mark the new task completed.
- A result write fails after task execution: the Plan must report persistence failure and must not claim a durable reusable result.
- A Session has more than 32 results: retrieval stays bounded and returns newest results in chronological order.
- A completed legacy TaskRun has no result row: it must not appear as an empty or fabricated result.
- A failed, cancelled, or interrupted TaskRun has a result row due to corruption: retrieval must filter it out.

---

### Task 1: Persist successful Plan task results

**Files:**
- Modify: `src/hostpilot/persistence/ports.py`
- Modify: `src/hostpilot/persistence/tasks.py`
- Modify: `src/hostpilot/persistence/__init__.py`
- Modify: `src/hostpilot/runtime/lifecycle.py`
- Test: `tests/unit/persistence/test_tasks.py`
- Test: `tests/unit/runtime/test_task_lifecycle.py`

**Steps:**

**Interfaces:**
- Produces: `StoredPlanTaskResult`, TaskStore `record_completed_task_result`/`list_completed_task_results`, and a lifecycle-handle completion payload.
- Consumes: existing TaskRun create/finish lifecycle and SQLite Host lease schema.

- [x] Define a typed stored result record and TaskStore methods; add tests for atomic completed result persistence, reopen, Session filtering, state filtering, bounded ordering, and aggregate byte limit.
- [x] Run the focused tests and confirm they fail because the methods/table are absent.
  - Expected: failures identify the missing result-record/read APIs or result data not surviving reopen.
- [x] Add the separate result table, JSON validation/size limit, atomic completion write, and bounded retrieval.
- [x] Add `TaskRunHandle.complete(...)`; pass successful result data to the store from lifecycle finalization while failures keep metadata-only behavior.
- [x] Run persistence and lifecycle tests.
  - Expected: `pytest tests/unit/persistence/test_tasks.py tests/unit/runtime/test_task_lifecycle.py -q` passes.

### Task 2: Supply historical results to new Plan decisions

**Files:**
- Modify: `src/hostpilot/skills/orchestration.py`
- Modify: `src/hostpilot/skills/model_plan.py`
- Modify: `tests/unit/skills/test_orchestration.py`
- Modify: `tests/unit/skills/test_model_plan.py`

**Steps:**

**Interfaces:**
- Produces: `BlackboardSnapshot.prior_results` and request lifecycle lookup.
- Consumes: TaskStore result query and `StoredPlanTaskResult` from Task 1.

- [x] Add RED tests proving previous completed results are loaded by Session, kept separate from current results, passed to the planner, and do not skip a new task with the same task ID.
- [x] Run focused tests and confirm the expected failures.
  - Expected: planner input lacks prior results and colliding task IDs are incorrectly indistinguishable in the proposed regression setup.
- [x] Add bounded prior-result history to `BlackboardSnapshot`; load it through the request TaskLifecycleManager and include it in planner input as reference context.
- [x] Run focused Plan and model planner tests.
  - Expected: `pytest tests/unit/skills/test_orchestration.py tests/unit/skills/test_model_plan.py -q` passes.

### Task 3: Update architecture and verify the migration slice

**Files:**
- Modify: `docs/roadmap.md`
- Modify: `docs/新架构文档.md`
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/testing-catalog.md`
- Create: `docs/test-results/2026-11-22-runtime-persistent-plan-results.md`

**Interfaces:**
- Consumes: completed TaskStore result schema and `BlackboardSnapshot.prior_results` reference-only behavior from Tasks 1–2.

**Steps:**

- [x] Update claims that TaskRun persistence excludes outputs and document the new reference-only history boundary.
- [x] Run targeted persistence, lifecycle, orchestration, and Runtime recovery tests.
  - Expected: affected persistence/lifecycle/Plan/recovery tests pass.
- [x] Run the full Python suite, Ruff, mypy, formatting checks for changed Python files, and `git diff --check`.
  - Expected: all commands exit 0; the Python suite reports no failures.
- [x] Record commands, outcomes, limitations, and the bounded history behavior in the verification report.
