# ChatService RequestContext Integration Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Make each ChatService turn carry one Runtime `RequestContext` through its private execution method while preserving existing external interfaces.

**Architecture:** `ChatService.send()` builds the context snapshot and budget as today, then creates `RequestContext(snapshot, budget, confirmation_provider)`. `_execute_turn()` accepts that object and unpacks fields only at existing AgentRuntime and Tool port calls. Route-only evaluation remains unchanged.

**Tech Stack:** Python 3.11, asyncio, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-19-chat-request-context-design.md`

## Global Constraints

- Preserve `ChatService.send()`, `AgentRuntime.respond()`, Skill/Tool Port, and HTTP/WebSocket contracts.
- Keep the per-turn `ContextSnapshot`, mutable `RequestBudget`, and optional approval provider together in one `RequestContext` inside the normal Chat execution path.
- Leave route-only evaluation and Runtime process-scoped `RuntimeContext` behavior unchanged.

## Review Focus

- Missing approval provider remains `None` and is forwarded to existing ports.
- Approval provider identity and mutable budget identity are preserved at the legacy call boundary.
- Snapshot budget metadata reflects the initial budget snapshot, while the live `RequestBudget` remains available for accounting.

## Files

- Modify `src/hostpilot/orchestration/chat.py` to create and use the request context.
- Test `tests/unit/orchestration/test_chat_routing.py` for one-object internal handoff and compatibility forwarding.
- Update ADR-0034, architecture/roadmap documents, testing catalog/metrics, and the Runtime test report.

## Task 1: Carry RequestContext through ChatService

**Interfaces:**
- Consumes `RequestContext(snapshot: ContextSnapshot, budget: RequestBudget, confirmation_provider: AsyncConfirmationProvider | None)` from `hostpilot.runtime`.
- Produces `_execute_turn(request: RequestContext, started_at: float) -> ChatTurnResult`; `send()` and all external ports keep their current signatures.

- [x] **Step 1: Add a failing ChatService test** in `tests/unit/orchestration/test_chat_routing.py`. A capturing subclass records `_execute_turn` input; send a tool-routed request with a concrete approval provider and assert the captured RequestContext contains the built snapshot, live budget, and provider, while the ToolProvider receives the same budget/provider objects.
- [x] **Step 2: Run** `.venv/bin/pytest -q tests/unit/orchestration/test_chat_routing.py`; confirm it fails because ChatService still calls `_execute_turn` with separate arguments.
- [x] **Step 3: Change** `ChatService.send()` to create one RequestContext after adding the initial budget snapshot, use its snapshot for Turn tracking, and pass it with `started_at` to `_execute_turn()`.
- [x] **Step 4: Change** `_execute_turn(request: RequestContext, started_at: float)` to read the snapshot, budget, and approval provider from the request object; preserve all existing port call signatures and forwarding behavior.
- [x] **Step 5: Run** `.venv/bin/pytest -q tests/unit/orchestration/test_chat_routing.py`; expect all tests to pass.
- [x] **Step 6: Update** ADR-0034, `docs/新架构文档.md`, `docs/roadmap.md`, `docs/testing-catalog.md`, `docs/testing-metrics.md`, and the Runtime test report with the internal Chat boundary and actual verification results.
- [x] **Step 7: Run** the Chat/Runtime/Skill/Tool focused regression, full pytest, Ruff lint/format, relevant mypy, and `git diff --check`; record results and existing unrelated failures.

## Self-Review

- Spec coverage: one RequestContext is constructed per `send()` and used across the private Chat execution method; all legacy call boundaries and route-only behavior remain compatible.
- Review focus: the integration test covers request state identity and compatibility forwarding; existing tests cover route-only behavior.
- Proportion: one cohesive ChatService migration step; no AgentRuntime or Skill/Tool public signature changes.
