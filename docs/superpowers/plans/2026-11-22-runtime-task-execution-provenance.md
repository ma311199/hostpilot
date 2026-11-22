# Runtime Plan 子任务执行摘要实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在成功子任务缓存审批中展示实际工具调用、副作用等级与人工确认结果，让用户能判断复用或重新执行的风险。

**Architecture:** 将不含调用参数/结果的执行摘要作为成功结果元数据与 TaskRun 原子持久化。Plan 工具适配器收集工具 ID、调用次数和效果等级；Orchestrator 观察任务期间确认 Provider 的决策。摘要通过缓存审批 WebSocket 发给前端并进入 allowlisted Trace 属性；旧数据库记录摘要保持为空并明确展示未知。

**Tech Stack:** Python 3.11、dataclasses、SQLite/aiosqlite、FastAPI WebSocket、Vue 3、pytest、Vitest。

**Spec:** `docs/superpowers/specs/2026-11-22-runtime-task-execution-provenance-design.md`

## Global Constraints

- 不保存工具参数、工具输出、凭证、幂等键、补偿令牌或确认摘要。
- 只记录实际成功的工具调用；未知效果等级明确为 unknown，不推断成只读。
- TaskRun 完成状态、成功输出与执行摘要在同一 SQLite 事务提交。
- 旧结果迁移后摘要字段可空；界面必须把缺失显示为未知。
- 复用不再次调用旧工具；选择执行可能重复副作用，且仍经过现有工具 Policy 和确认门。
- Trace 记录摘要元数据，不记录任务输出或工具结果正文。

## Review Focus

- 一个任务重复调用同一工具：摘要正确聚合调用次数，Trace/审批不会暴露参数或结果。
- 多个工具具有不同效果等级：每个工具与等级对应正确，不把等级集合错误地关联到工具。
- 一个任务内发生多次人工确认：批准、拒绝、取消和超时计数不混淆；成功任务仅展示本次 Turn 观察到的结果。
- 旧 SQLite schema 与旧成功结果：启动迁移成功，缺失摘要在 UI 显示未知且仍可人工选择。
- Task Executor/工具适配器无法提供效果等级：摘要显示 unknown；不得自动归为只读或可安全重放。

---

### Task 1: 执行摘要类型、TaskStore 持久化与旧库迁移

**Files:** `src/hostpilot/task_results.py`, `src/hostpilot/persistence/tasks.py`, `src/hostpilot/persistence/ports.py`, `src/hostpilot/runtime/lifecycle.py`; tests in `tests/unit/persistence/test_tasks.py` and `tests/unit/runtime/test_task_lifecycle.py`.

- [x] Add RED test: successful result survives SQLite reopen with tool IDs, aggregated counts, effect levels and confirmation decisions.
- [x] Add RED tests: legacy result table migrates with nullable summary; invalid/oversized summaries and sensitive free-form fields are rejected; summary and terminal state roll back together.
- [x] Define frozen `PlanTaskToolUsage` and `PlanTaskExecutionSummary` data contracts; add optional `execution_summary` to completed/stored results and `TaskRunHandle.complete`.
- [x] Add nullable `execution_summary_json` schema migration; serialize only allowlisted tool ID, count, effect level and decision values within existing result size limit.
- [x] Run persistence and lifecycle tests.

### Task 2: Capture Plan task tool and confirmation provenance

**Files:** `src/hostpilot/skills/orchestration.py`, `src/hostpilot/skills/model_plan.py`; tests in `tests/unit/skills/test_orchestration.py` and `tests/unit/skills/test_model_plan.py`.

- [x] Add RED tests: repeated and distinct tool calls produce correct per-tool counts/levels; confirmation Provider decisions are captured for the current task; unknown level stays unknown; summary reaches TaskStore and Trace without payload fields.
- [x] Add a task-scoped recording confirmation Provider that delegates unchanged decisions and records only decision enum values.
- [x] Have `GovernedPlanTaskExecutor` derive per-tool usage from successful calls and registered descriptors; business Skill results use available descriptors and mark unavailable levels unknown.
- [x] Merge usage and confirmation decisions into `PlanTaskExecutionSummary` before successful TaskRun completion; write allowlisted summary fields to `plan.task` Trace.
- [x] Run orchestration and model-plan tests.

### Task 3: Cache approval WebSocket and Chat UI

**Files:** `src/hostpilot/api/app.py`, `frontend/src/api/chatSocket.ts`, `frontend/src/ChatPanel.vue`, `frontend/src/style.css`; tests in `tests/unit/api/test_task_cache_approval.py` and `frontend/src/ChatPanel.test.ts`.

- [x] Add RED tests: WebSocket cache request carries safe summary; UI renders tools, effects and confirmation counts; legacy missing summary displays unknown; reuse and execute both state their behavior accurately.
- [x] Extend the cache approval request type and WebSocket payload with optional execution summary.
- [x] Render a compact per-tool history and confirmation status; show the missing-summary warning for old records; state that reuse skips these calls and execute may repeat side effects.
- [x] Keep both explicit choices and existing tool confirmation behavior intact; no summary field may contain arguments or outputs.
- [x] Run API approval tests, all frontend tests, lint, type check and production build.

### Task 4: Documentation and final verification

**Files:** `docs/decisions/0037-plan-task-execution-provenance.md`, `docs/新架构文档.md`, `docs/roadmap.md`, `docs/testing-catalog.md`, `docs/testing-metrics.md`, and dated `docs/test-results/` report.

- [x] Document stored provenance, missing legacy summaries, and the distinction between reusing a result and re-executing its tools.
- [x] Run full Python and frontend suites, Ruff lint/format, mypy, ESLint, TypeScript, frontend production build, and `git diff --check`.
- [x] Record failures, fixes, final metrics, review status, and local merge commit `01e6496`; do not push.
