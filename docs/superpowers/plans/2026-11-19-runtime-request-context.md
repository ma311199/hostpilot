# Runtime RequestContext Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Add a request-scoped Runtime context contract and adapt it to the existing LangGraph tool context without changing public chat behavior.

**Architecture:** Add a small Runtime data object that groups the existing context snapshot, budget tracker, and optional confirmation provider. `LangGraphAgentRuntime` converts it into the same `AgentExecutionContext` mapping currently consumed by tools; public `AgentRuntime.respond` arguments remain unchanged.

**Tech Stack:** Python 3.11, dataclasses, TypedDict, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-19-runtime-request-context-design.md`

## Global Constraints

- Keep `RuntimeContext` process-scoped and keep user/session data request-scoped.
- Preserve the LangGraph context keys `snapshot`, `budget`, and `confirmation_provider`.
- Do not change AgentRuntime, Chat HTTP/WebSocket, tool governance, budget, approval, or persistence behavior.

## Review Focus

- Optional approval provider is absent: the generated LangGraph context must still include the key with `None`.
- Request data from two separate contexts must remain separate; neither may be stored on shared `RuntimeContext`.
- `RequestBudget` is mutable: the request context must retain the live tracker instance, not copy its initial state.

## File Structure

- Create `src/hostpilot/runtime/context.py` for `RequestContext`.
- Modify `src/hostpilot/runtime/__init__.py` to export the contract.
- Modify `src/hostpilot/orchestration/runtime.py` to adapt the contract into the existing TypedDict shape.
- Test `tests/unit/runtime/test_context.py` and `tests/unit/orchestration/test_runtime.py` for data retention and compatibility mapping.
- Update ADR-0034, `docs/roadmap.md`, and the existing Runtime test report with the completed boundary and fresh verification results.

## Task 1: Define and integrate RequestContext

**Interfaces:**
- Produces `RequestContext(snapshot: ContextSnapshot, budget: RequestBudget, confirmation_provider: AsyncConfirmationProvider | None)` in `hostpilot.runtime.context`.
- The LangGraph adapter produces `AgentExecutionContext` with exactly the three existing keys and original object instances.

- [x] **Step 1: Write failing Runtime contract tests** in `tests/unit/runtime/test_context.py`. Verify construction retains the exact snapshot and live budget objects, and allows an omitted confirmation provider.
- [x] **Step 2: Run** `.venv/bin/pytest -q tests/unit/runtime/test_context.py`; confirm failure because `RequestContext` is not yet defined.
- [x] **Step 3: Implement** the dataclass in `src/hostpilot/runtime/context.py`, export it from `src/hostpilot/runtime/__init__.py`, and keep `RuntimeContext` unchanged.
- [x] **Step 4: Re-run** `.venv/bin/pytest -q tests/unit/runtime/test_context.py`; expect all contract tests to pass.
- [x] **Step 5: Write a failing adapter test** in `tests/unit/orchestration/test_runtime.py` for conversion to `snapshot`, `budget`, and `confirmation_provider`, including the `None` case.
- [x] **Step 6: Run** `.venv/bin/pytest -q tests/unit/orchestration/test_runtime.py`; confirm the adapter behavior is missing.
- [x] **Step 7: Update** `LangGraphAgentRuntime.respond` to construct `RequestContext` and adapt its fields to the existing `AgentExecutionContext` mapping; keep public signatures unchanged.
- [x] **Step 8: Run** the Runtime and orchestration focused tests; expect them to pass.
- [x] **Step 9: Update** ADR-0034, `docs/roadmap.md`, and `docs/test-results/2026-11-18-runtime-turn-lifecycle.md` to record the contract, compatibility boundary, and verification results.
- [x] **Step 10: Run** focused and full pytest, Ruff lint/format, relevant mypy checks, and `git diff --check`; record actual counts and any unrelated failure in the report and metrics ledger.

## Self-Review

- Spec coverage: contract, compatibility adapter, non-goals, and documentation/verification are included.
- Type consistency: the dataclass fields match the existing `respond` inputs; the adapter keeps the current TypedDict field names.
- Review focus: absent approvals, request isolation, and mutable budgets are covered by tests.
- Proportion: one cohesive task, with no changes to downstream Skill/Tool signatures or generic execution behavior.
