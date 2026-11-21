# Runtime 执行循环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the Runtime-owned bounded Model/Tool loop using replaceable ports and the existing shared budget and governed ToolFacade.

**Architecture:** The loop consumes a request-local message history and returns only messages generated for the current turn. A ModelProvider adapter applies `complete_with_budget`; a ToolFacade adapter preserves policy, permission and confirmation checks. Chat/Session adapters retain checkpoint ownership, and the existing LangGraph path remains active.

**Tech Stack:** Python 3.11, dataclasses, asyncio, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-19-runtime-execution-loop-design.md`

## Global Constraints

- Every call uses the exact RequestContext supplied in RuntimeExecutionRequest.
- Every model round checks the shared RequestBudget; Provider-accounted Token usage is never charged twice.
- Tool proposals are untrusted and must match both definitions and allowlist before any proposal in the batch is executed.
- Tools execute only through ToolFacade and existing governance; the loop must not depend on MCP clients or ToolExecutor.
- Cancellation and execution errors propagate; no result may claim success after either occurs.
- The loop does not own conversation persistence or replace the current LangGraph Chat Runtime.

## Review Focus

- A mixed valid/invalid proposal batch produces zero Tool Port calls.
- Duplicate/empty call IDs and non-Mapping arguments are rejected before side effects.
- `max_steps` prevents execution of tool proposals returned on the final allowed model round.
- Budget exhaustion prevents the next model/tool call, and provider-accounted Token usage is not duplicated.
- Tool results preserve each originating call ID, including multiple sequential tool proposals.
- Cancellation during Tool Port execution propagates without a synthetic completed result.

### Task 1: Implement bounded Model/Tool execution loop

**Files:**
- Create: `src/hostpilot/runtime/execution_loop.py`
- Modify: `src/hostpilot/runtime/__init__.py`
- Test: `tests/unit/runtime/test_execution_loop.py`

**Steps:**
- [x] Write failing tests for final text, model-tool-model completion, call ID feedback, allowlist rejection, max steps, budget and cancellation.
- [x] Run focused tests and confirm they fail because the loop implementation is absent.
- [x] Implement `DefaultRuntimeExecutionLoop` with full-batch proposal validation before dispatch and sequential governed tool execution.
- [x] Run focused Runtime tests and static checks.

### Task 2: Adapt existing ModelProvider and ToolFacade

**Files:**
- Create: `src/hostpilot/runtime/adapters.py`
- Modify: `src/hostpilot/runtime/__init__.py`
- Test: `tests/unit/runtime/test_execution_adapters.py`

**Steps:**
- [x] Write failing adapter tests for shared budget accounting, response accounting flags, request-context identity, and confirmation forwarding.
- [x] Run focused tests and confirm the Runtime adapter module is absent.
- [x] Implement a budget-aware ModelProvider adapter using `complete_with_budget` and a ToolFacade adapter forwarding budget and confirmation.
- [x] Run focused adapter and Tool governance tests.

### Task 3: Record boundaries and verify

**Files:**
- Modify: Runtime ADR, architecture, roadmap, testing catalog and metrics.
- Create: `docs/test-results/2026-11-19-runtime-execution-loop.md`

**Steps:**
- [x] Record request-in/result-delta state ownership, safety rules and non-integration with current LangGraph route.
- [x] Run the full pytest suite, Ruff lint/format, full mypy and `git diff --check`.
- [x] Record initial failures, final counts, warning and remaining integration work.
