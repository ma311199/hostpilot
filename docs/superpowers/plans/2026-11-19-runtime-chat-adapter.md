# Runtime Chat Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans or superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an opt-in Chat Runtime implementation that drives turns with the Runtime Model/Tool loop while preserving LangGraph as the default and as the existing Skill/state adapter.

**Architecture:** Add a normalized session state port that preserves model tool-call history, a prompt builder that marks Memory as untrusted, and a Runtime-backed `ChatAgentRuntime` adapter. Register it as an optional plugin and inject the existing provider, ToolFacade, execution loop, and LangGraph-backed state adapter; keep the current default registry selection unchanged.

**Tech Stack:** Python 3.11, dataclasses, Protocols, pytest, Ruff, mypy, existing ModelProvider/ToolFacade/RuntimeExecutionLoop.

**Spec:** `docs/superpowers/specs/2026-11-19-runtime-chat-adapter-design.md`

## Global Constraints

- The current LangGraph path remains the default; no default switch or dependency removal occurs in this plan.
- Every execution call receives the exact `RequestContext` supplied to `respond`.
- Memory remains explicitly untrusted reference data.
- Runtime model and tool calls continue through `ModelProviderRuntimeAdapter` and `ToolFacadeRuntimeAdapter`.
- A failed, cancelled, budget-exhausted, or max-step turn is never persisted as a completed exchange.
- Skill selection, SkillEngine, DeepAgents Skill, and Plan fallback retain their existing behavior.

## Review Focus

- Existing persisted tool history retains call IDs and definitions when loaded into a new Runtime turn.
- Failure after a Tool side effect does not append a falsely completed session turn.
- The Runtime plugin is opt-in and the default registry continues creating `builtin.deepagents`.
- Missing session IDs and unsupported execution terminal statuses fail explicitly.
- Memory text cannot be interpreted as system instructions or tool authorization.

### Task 1: Add normalized Runtime session state and prompt assembly

**Files:**
- Create: `src/hostpilot/runtime/chat_state.py`
- Create: `src/hostpilot/runtime/chat_prompt.py`
- Create: `src/hostpilot/orchestration/runtime_chat_state.py`
- Modify: `src/hostpilot/runtime/__init__.py`
- Test: `tests/unit/orchestration/test_runtime_chat_state.py`
- Test: `tests/unit/runtime/test_chat_prompt.py`

**Interfaces:**
- Produces the framework-neutral `RuntimeSessionStatePort.load_messages(session_id) -> tuple[ModelMessage, ...]` and `append_messages(session_id, messages) -> None`; LangGraph conversion stays under `orchestration`.
- Produces `RuntimeChatPromptBuilder.build(snapshot, history, user_message, system_prompt) -> tuple[ModelMessage, ...]`.
- LangGraph state conversion must preserve assistant tool proposals, Tool result `tool_call_id`, and roles.

- [x] Write tests for role/call-ID round trips, bounded conversation history, current user placement, and untrusted Memory framing.
- [x] Run the focused tests and confirm they fail because the new modules are absent.
- [x] Implement the normalized state port/adapter and prompt builder with lazy LangGraph imports.
- [x] Run focused Runtime tests, Ruff, and mypy for changed modules.

### Task 2: Implement the opt-in Runtime Chat adapter

**Files:**
- Create: `src/hostpilot/orchestration/runtime_chat.py`
- Modify: `src/hostpilot/orchestration/runtime_plugins.py`
- Modify: `src/hostpilot/orchestration/runtime.py`
- Test: `tests/unit/orchestration/test_runtime_chat.py`
- Test: `tests/unit/orchestration/test_runtime_plugins.py`

**Interfaces:**
- `RuntimeChatAgentRuntime.respond(message, request_context)` assembles state/prompt, invokes `RuntimeExecutionLoop`, then appends returned messages only for `COMPLETED`.
- `read_messages` and `record_turn` preserve the existing `ChatAgentRuntime` contract for the outer Skill adapter.
- New plugin dependencies are optional fields on `ChatAgentRuntimePluginContext`; the existing `builtin.deepagents` builder remains compatible.

- [x] Write tests for exact RequestContext identity, successful state append, no append on max steps/error/cancellation, opt-in plugin creation, and default selection stability.
- [x] Run the focused tests and confirm they fail for the absent adapter/plugin wiring.
- [x] Implement the Runtime-backed adapter and register `builtin.runtime-chat` without changing the default plugin setting.
- [x] Run Chat Runtime, Skill adapter, Runtime loop, and plugin tests.

### Task 3: Verify and document the optional integration

**Files:**
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/roadmap.md`
- Modify: `docs/testing-catalog.md`
- Modify: `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-19-runtime-chat-adapter.md`

- [x] Verify the new plugin is opt-in and LangGraph remains the default.
- [x] Run the full test suite, Ruff lint/format, full mypy, and `git diff --check`.
- [x] Record test failures, fixes, rerun counts, remaining integration limits, and explicitly state that LangGraph removal/default migration is not part of this plan.
