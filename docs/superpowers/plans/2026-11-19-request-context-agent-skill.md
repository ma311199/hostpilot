# RequestContext Agent 与 Skill 传递实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry one Runtime `RequestContext` through Chat Agent and Skill processing while preserving HTTP/WebSocket behavior and adapting only at legacy Model/Tool ports.

**Architecture:** Change the ChatAgentRuntime contract to accept `RequestContext`; update the host proxy, LangGraph adapter, Skill adapter, selector and Skill execution request to pass that instance through. Bump the versioned Chat Runtime plugin API to 2.0. Keep LangGraph and tool/model port payload shapes unchanged.

**Tech Stack:** Python 3.11, Protocols/dataclasses, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-11-19-request-context-agent-skill-design.md`

## Global Constraints

- `RequestContext` contains the original ContextSnapshot, live RequestBudget, and optional approval provider.
- Preserve existing LangGraph context keys and preserve `None` for a missing approval provider.
- Keep HTTP/WebSocket and governed tool/model port contracts unchanged.
- Reject Chat Runtime plugin API 1.0 after the contract change; register the new API as 2.0.

## Review Focus

- Missing approval provider is forwarded as `None` at the LangGraph adapter.
- The same mutable budget instance reaches selection, skill execution and agent execution.
- Failures from skill execution still fall back to Plan with the same request context and uncertainty metadata.
- Custom Chat Runtime factories returning malformed objects still fail registry validation.

### Task 1: Upgrade ChatAgentRuntime request contract

**Files:**
- Modify: `src/hostpilot/orchestration/runtime.py`, `runtime_host_plugin.py`, `runtime_plugins.py`, `chat.py`.
- Test: `tests/unit/orchestration/test_runtime.py`, `test_runtime_plugins.py`, `test_chat_routing.py`, and API chat test fakes.

**Interfaces:**
- `ChatAgentRuntime.respond(message: str, request_context: RequestContext) -> ChatAgentResponse`.
- `LangGraphChatAgentRuntime.respond_with_skill(message: str, request_context: RequestContext, *, skill_id: str) -> ChatAgentResponse`.
- `CHAT_AGENT_RUNTIME_PLUGIN_API_VERSION == "2.0"`.

- [x] Add tests for request object identity through the Host Plugin and the LangGraph adapter; cover missing approval and live budget.
- [x] Run focused tests and confirm the new call contract/version assertions fail before implementation.
- [x] Update ChatService, Protocol, Host Plugin, LangGraph adapter, and plugin registry version; remove field-by-field request reconstruction.
- [x] Run runtime, plugin, API and chat tests.

### Task 2: Carry RequestContext through Skill selection and execution

**Files:**
- Modify: `src/hostpilot/orchestration/skill_runtime.py`, `src/hostpilot/routing/skill_selector.py`, `src/hostpilot/skills/runtime.py`, `src/hostpilot/skills/model_plan.py`.
- Test: `tests/unit/orchestration/test_skill_runtime_adapter.py`, `tests/unit/skills/test_skill_runtime.py`, `test_model_plan.py`, `test_plan_memory.py`, `test_orchestration.py`, `test_code_review_skill.py`.

**Interfaces:**
- `SkillSelectionPort.select(message: str, request_context: RequestContext) -> SkillSelection | None`.
- `SkillExecutionRequest` carries `request_context` instead of separate snapshot, budget and confirmation fields.
- DeepAgents result assessment receives the same `request_context` and reads snapshot/budget from it.

- [x] Add tests asserting identity at selector and SkillEngine boundaries, including Plan fallback.
- [x] Run focused tests and confirm they fail because Skill interfaces still unpack the request.
- [x] Update request construction and downstream reads; unpack only when crossing existing model/tool ports.
- [x] Run focused Skill, Plan, adapter and routing tests.

### Task 3: Update architecture records and verify

**Files:**
- Modify: `docs/decisions/0019-versioned-chat-runtime-plugins.md`, `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`, `docs/新架构文档.md`, `docs/roadmap.md`, testing catalog, metrics, and Runtime report.

- [x] Record Chat Runtime plugin API 2.0 and the RequestContext-to-port adapter boundaries.
- [x] Search for obsolete split Chat Runtime signatures and SkillExecutionRequest construction.
- [x] Run full pytest, Ruff lint/format, full mypy and `git diff --check`; record results and any unrelated warnings.
