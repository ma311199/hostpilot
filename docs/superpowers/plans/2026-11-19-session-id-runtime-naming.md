# Session ID and Runtime Naming Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rename the session identifier to `session_id` across persistence, API, WebSocket, frontend, tests, and active documentation, and make Runtime-related type names reflect their layer and role.

**Architecture:** Keep Conversation as the resource concept and keep `RuntimeHost` as the process-level lifecycle owner. Rename the conversation identifier field and SQLite column with an in-place schema-v3 migration; rename chat, Skill, model, and Harness types according to the design spec without changing runtime behavior.

**Tech Stack:** Python 3.11, FastAPI, SQLite/aiosqlite, Vue 3, TypeScript, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-19-session-id-and-runtime-naming-design.md`

## Global Constraints

- Keep `/conversations` as the resource route and use `session_id` for its identifier.
- Migrate SQLite schema version 2 to 3 in place, preserve data and UUID values, and make startup migration idempotent.
- Keep existing LangGraph checkpoint thread IDs unchanged.
- Do not change Runtime lifecycle behavior, service behavior, model selection, or Skill execution.
- Do not provide a `conversation_id` API compatibility alias.

## Review Focus

- Existing version-2 databases must retain rows, values, and indexes through the identifier-column migration; test migration plus a second initialization.
- Invalid or absent `session_id` values must continue to fail API validation at the same boundary; test a valid value and a missing/blank value.
- WebSocket approval and chat messages must use the renamed identifier consistently; test one chat send and one approval round trip.
- LangGraph checkpoint routing must preserve the same UUID thread key after the name change; test `conversation_thread_config`.
- Runtime factories must reject invalid returned components and preserve close behavior after symbol renames; retain and update existing factory/host lifecycle tests.

---

### Task 1: Rename Runtime-related types by responsibility

**Files:**
- Modify: `src/hostpilot/orchestration/runtime.py`, `runtime_plugins.py`, `runtime_host_plugin.py`, package exports and imports across `src/` and `tests/`.
- Modify: `src/hostpilot/skills/runtime.py`, package exports and imports; rename `SkillRuntime` to `SkillEngine`.
- Modify: `src/hostpilot/models/factory.py` and exports; rename the component pair to `ModelRuntimeComponents` and registry to `ModelProviderRegistry`.
- Modify: `src/hostpilot/routing/models.py`; rename `RoutingModelRuntime` to `RoutingModelComponents`.
- Modify: `src/hostpilot/persistence/harness_configuration.py` and frontend Harness API types; rename `HarnessRuntimeConfiguration` to `HarnessConfiguration`.
- Test: corresponding orchestration, skills, models, routing and persistence unit tests; `tests/unit/routing/test_routing_models.py` uses a unique module name.

**Interfaces:**
- Keep `RuntimeHost`, `RuntimeContext`, `RuntimePlugin`, and `RuntimeServiceRegistry` unchanged.
- Chat symbols use `ChatAgentRuntime*`; Skill executor type is `SkillEngine`.
- Chat output is `ChatAgentResponse`, and the assembly function is `create_configured_chat_agent_runtime`.
- Model bundle is `ModelRuntimeComponents`, provider registry is `ModelProviderRegistry`; routing bundle is `RoutingModelComponents`.

- [x] Add/update tests first to import the new public names and assert unchanged component creation/lifecycle behavior; run them to observe missing-name failures.
- [x] Rename production symbols and all imports/exports with no duplicate compatibility aliases.
- [x] Run focused Runtime, chat, Skill, model, routing, and Harness tests.

### Task 2: Rename domain and persistence session identifier

**Files:**
- Modify: `src/hostpilot/contracts/models.py`, `src/hostpilot/persistence/ports.py`, conversation/checkpointer/memory stores, Runtime turn lifecycle, and related tests.
- Modify: `src/hostpilot/persistence/conversations.py` for schema migration and SQL column usage.
- Test: persistence conversation tests, checkpointer tests, memory tests, Runtime lifecycle tests.

**Interfaces:**
- Public Python fields and parameters use `session_id`.
- Conversation table remains `conversations`; its ID column becomes `session_id`.
- Schema version advances from 2 to 3 and migrates the existing column in place.

- [x] Add tests using a real schema-v2 fixture with existing rows, asserting the v3 column, retained values, and idempotent reinitialization; run RED.
- [x] Add assertions for session identifier validation and unchanged checkpointer UUID mapping; run RED.
- [x] Implement the migration and rename Python contract/store/memory/lifecycle identifiers; run focused tests GREEN.

### Task 3: Rename API and WebSocket identifier fields

**Files:**
- Modify: `src/hostpilot/api/schemas.py`, `src/hostpilot/api/app.py`, chat orchestration APIs and API/WebSocket tests.
- Test: API conversation, chat, routing-evaluation, sandbox, memory, and WebSocket tests.

**Interfaces:**
- Keep `/conversations` routes; route parameter becomes `{session_id}`.
- JSON and WebSocket payloads use `session_id`; old `conversation_id` payload fields are rejected or absent.

- [x] Update API contract tests for `session_id`, including one old-name rejection case; run RED.
- [x] Update application and chat orchestration parameter names and payload creation; run API-focused tests GREEN.

### Task 4: Rename frontend fields and tests

**Files:**
- Modify: `frontend/src/api/chat.ts`, `chatSocket.ts`, `ChatPanel.vue`, `HarnessSettingsPanel.vue`, other API contract types, and affected frontend tests.
- Test: affected Vitest API and component tests.

**Interfaces:**
- API TypeScript types and message payloads mirror backend `session_id` fields.
- UI concepts and component names may continue to use “conversation”.

- [x] Update API and component tests to provide/assert `session_id`; run Vitest to observe RED.
- [x] Update frontend type definitions and all identifier reads/writes; run Vitest and frontend typecheck GREEN.

### Task 5: Synchronize naming documentation and verify repository-wide consistency

**Files:**
- Modify: `docs/development-guide.md`, `docs/frontend-development.md`, `docs/新架构文档.md`, relevant ADRs and current Runtime/API/persistence reports.
- Test: all applicable project checks and a residual-symbol search.

- [x] Document Python/TypeScript naming and Runtime role suffix rules, plus the intentional Conversation resource naming.
- [x] Update current architecture and decision records for the new names and schema-v3/API contract.
- [x] Search active source, tests and documentation for obsolete identifiers; review each remaining historical mention.
- [x] Run full pytest, frontend Vitest, frontend lint/build, Python lint/type checks and `git diff --check`.

