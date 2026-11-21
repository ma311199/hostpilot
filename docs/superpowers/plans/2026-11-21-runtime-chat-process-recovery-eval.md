# Runtime Chat Process Recovery Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a real-model evaluation that resumes a Runtime Chat session in a fresh Python process after a tool result has been checkpointed.

**Architecture:** The public evaluator prompts for the model key once, creates a temporary shared data directory, and launches isolated prepare/resume worker processes. The first worker persists a tool result then injects a model error; the second reopens the SQLite checkpoint and resumes the same `session_id`. A final report combines both phases and verifies the tool handler ran exactly once.

**Tech Stack:** Python 3.12, asyncio, subprocess, SQLite checkpoint, pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-runtime-chat-process-recovery-eval-design.md`

## Global Constraints

- Use only in-memory fixture tools; real tool side effects remain 0.
- Never put the API key in argv, a report, or a temporary file.
- Keep the current default Chat Runtime unchanged.
- Explicitly distinguish fresh-process Runtime reconstruction from forced OS process termination.

## Review Focus

- Child process failures must not be mistaken for successful recovery; assert each phase's structured result and exit status.
- The resume worker must use the identical session ID and checkpoint file; assert both through phase metadata.
- Tool replay must not execute the handler a second time; count executions across both workers.
- Child output and report must not contain the API key.
- Temporary storage must be scoped to the parent command and removed on completion.

---

### Task 1: Cross-process recovery evaluator

**Files:**
- Modify: `scripts/evaluate_runtime_chat.py`
- Test: `tests/unit/evaluation/test_runtime_chat_eval_tools.py`
- Update: `docs/test-results/2026-11-20-runtime-chat-live-evaluation.md`
- Update: `docs/testing-metrics.md`
- Update: `docs/roadmap.md`

**Interfaces:**
- Consumes: `RuntimeChatStubTools`, `FailOnceAfterToolModel`, `open_sqlite_checkpointer`, and existing Runtime Chat factory.
- Produces: case ID `recovery-after-process-restart` and JSON fields for phase results, shared session ID, combined tool calls, total handler runs, tokens, latency, and `passed`.

- [ ] **Step 1: Add failing contract tests** for the new case ID, subprocess command secrecy, and aggregation requiring a successful prepare phase plus a successful resume phase with exactly one total handler run.
- [ ] **Step 2: Run the focused tests and confirm they fail** because the case and process-recovery contracts are absent.
- [ ] **Step 3: Implement the parent/worker flow** with hidden key prompting in the parent, worker-only phase arguments, temporary shared SQLite state, a clean process boundary, and redacted structured reports.
- [ ] **Step 4: Run the focused evaluation tests**, then the full pytest suite and script lint/type checks.
- [ ] **Step 5: Run `git diff --check`** and review the complete patch for secret leakage and accurate scope claims.
- [ ] **Step 6: Give the user one command** to run the live model evaluation; update live metrics only after the user returns its output.
