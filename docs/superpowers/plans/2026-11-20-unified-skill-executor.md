# Unified Skill Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Route YAML and Markdown Skills through one SkillExecutor contract owned by HostPilot while retaining format-specific execution adapters.

**Architecture:** Define the shared executor protocol beside `SkillExecutionRequest` and `SkillExecutionResult`. Keep `SkillEngine` as the YAML implementation and wrap the existing `ChatAgentRuntime.respond_with_skill()` path in a Markdown/DeepAgents adapter. `SkillEngineAgentAdapter` resolves the selected format to an executor, then applies the same result/fallback/history path. Runtime remains responsible for request context, budget, cancellation, tracing and fallback policy.

**Tech Stack:** Python 3.11, Protocol/dataclasses, pytest, existing ChatAgentRuntime and SkillEngine.

**Spec:** `docs/decisions/0009-skill-runtime-and-yaml-contract.md`, `docs/decisions/0013-dual-skill-formats-and-execution-selection.md`, and `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`.

## Global Constraints

- Preserve the two supported Skill formats; do not convert Markdown instructions into YAML steps or execute both formats for one request.
- Keep `RequestContext` identity, budget accounting, cancellation propagation, fallback semantics, and trace privacy unchanged.
- Do not change the persisted `yaml` / `deepagents` / `both` setting values in this task; decoupling the legacy `deepagents` name from Markdown format is a follow-up migration.
- Framework adapters may execute a Skill only through the injected executor contract and existing governed services.

## Review Focus

- A selected Markdown Skill must not be sent to the YAML SkillEngine — test the executor selected by `execution_mode`.
- A selected YAML Skill must not call the ChatAgentRuntime Skill hook — assert the opposite executor remains untouched.
- Markdown token usage must be charged once, and cancellation must propagate — test accounting and cancellation.
- Executor exceptions or rejected results must preserve Plan fallback behavior and uncertain-side-effect signaling — test failure and fallback inputs.
- Missing or unsupported format executors must fail closed — test explicit rejection instead of silent fallback to another format.

---

### Task 1: Define the shared Skill executor boundary and adapt Markdown execution

**Files:**
- Modify: `src/hostpilot/skills/runtime.py`
- Modify: `src/hostpilot/skills/__init__.py`
- Modify: `src/hostpilot/orchestration/skill_runtime.py`
- Test: `tests/unit/orchestration/test_skill_runtime_adapter.py`

**Interfaces:**
- Consumes: `SkillExecutionRequest`, `SkillExecutionResult`, and `ChatAgentRuntime.respond_with_skill()`.
- Produces: `SkillExecutor` Protocol with `execute(request: SkillExecutionRequest) -> SkillExecutionResult`; `DeepAgentsSkillExecutionAdapter` implements it for Markdown Skills.

- [x] **Step 1: Write failing behavior tests**
  - Verify YAML execution uses the injected YAML executor and Markdown execution uses `DeepAgentsSkillExecutionAdapter` through the same `SkillExecutor.execute()` call path.
  - Verify Markdown output, token usage, budget accounting, cancellation, and exceptions preserve existing behavior.
  - Verify absent Markdown executor fails closed and never falls through to YAML.
- [x] **Step 2: Run the focused tests and confirm expected failures**

  Run: `.venv/bin/pytest -q tests/unit/orchestration/test_skill_runtime_adapter.py`

  Expected: the shared protocol/adapter path is missing; existing behavior assertions continue to pass.
- [x] **Step 3: Add `SkillExecutor` and Markdown adapter**
  - Add the Protocol in `skills/runtime.py` and export it from `skills/__init__.py`.
  - Add `DeepAgentsSkillExecutionAdapter` in `orchestration/skill_runtime.py`; translate `SkillExecutionRequest` to `respond_with_skill`, consume unaccounted Token usage once, and return a normalized `SkillExecutionResult`.
- [x] **Step 4: Route format-specific executors through the common contract**
  - Inject the YAML and Markdown executors into `SkillEngineAgentAdapter`.
  - Resolve `yaml` / `deepagents` selections to their executor; retain existing assessment, fallback, trace, and turn persistence behavior.
- [x] **Step 5: Run focused tests**

  Run: `.venv/bin/pytest -q tests/unit/orchestration/test_skill_runtime_adapter.py tests/unit/skills/test_skill_runtime.py tests/unit/orchestration/test_runtime.py`

  Expected: all pass with both format paths using the common executor interface.

### Task 2: Verify default adapter wiring and document the implemented boundary

**Files:**
- Modify: `docs/decisions/0013-dual-skill-formats-and-execution-selection.md`
- Modify: `docs/roadmap.md`
- Test: `tests/unit/orchestration/test_skill_runtime_adapter.py`

**Interfaces:**
- Consumes: Task 1 `SkillExecutor` and `DeepAgentsSkillExecutionAdapter`.
- Produces: proof that the default Chat Skill coordinator supplies both adapters through the common contract; updated architecture progress.

- [x] **Step 1: Add a default-wiring regression test if existing tests do not cover it**
  - Verify the adapter's default YAML and Markdown executors both use the common contract and retains the current setting default.
- [x] **Step 2: Run the adapter tests and confirm both format paths**

  Run: `.venv/bin/pytest -q tests/unit/orchestration/test_skill_runtime_adapter.py`

  Expected: YAML and Markdown complete through their injected/default executor under the common `execute()` contract.
- [x] **Step 3: Update architecture progress**
  - Record that the executor contract is now implemented while format parsing and backends remain distinct.
  - Keep `deepagents` as the legacy configuration value and record decoupling it to `markdown` as a follow-up.
- [x] **Step 4: Run complete verification**

  Run: `.venv/bin/pytest -q`, `.venv/bin/ruff check src tests scripts`, `.venv/bin/ruff format --check src tests scripts`, `.venv/bin/mypy src/hostpilot`, and `git diff --check`.

  Expected: all commands pass; report any pre-existing warning separately.
