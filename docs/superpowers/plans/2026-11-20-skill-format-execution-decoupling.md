# Skill Format and Executor Decoupling Implementation Plan

> **For agentic workers:** Use `superpowers:executing-plans` inline. Steps use checkbox syntax for tracking.

**Goal:** Make `yaml` and `markdown` the canonical Skill format preferences while preserving existing users' `deepagents` settings.

**Architecture:** Skill routing and public configuration select a format (`yaml`, `markdown`, or `both`). The Runtime resolves the selected format to an injected `SkillExecutor`; the current Markdown implementation may still use DeepAgents. SQLite migrates legacy `deepagents` values to `markdown`, and the API accepts the old value as a compatibility alias while returning canonical values.

**Tech Stack:** Python 3.11, Pydantic, SQLite/aiosqlite, Vue 3/TypeScript, pytest, Vitest.

**Spec:** `docs/decisions/0013-dual-skill-formats-and-execution-selection.md`; `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`.

## Global Constraints

- Preserve the two supported formats and execute at most one selected format per request.
- A missing format executor fails closed; it must not switch to another format.
- Keep existing `deepagents` user settings effective after migration.
- DeepAgents remains an implementation choice and can be replaced behind the Markdown executor contract.

## Review Focus

- Existing SQLite data containing `deepagents` remains readable and is canonicalized without losing `skill_id` rows.
- Old API clients sending `deepagents` receive a canonical `markdown` response and persist a working preference.
- YAML-only packages do not gain an unavailable Markdown mode.
- A Markdown selection invokes only the Markdown executor and preserves Plan fallback behavior.
- Selector prompts, normalized decisions, traces, and UI labels do not imply that Markdown inherently requires DeepAgents.

---

### Task 1: Canonical persistence and API compatibility

**Files:** `src/hostpilot/persistence/skill_execution.py`, `src/hostpilot/api/app.py`, related persistence/API tests.

- [x] Add tests for old database schema/data migration, canonical `markdown` persistence, legacy API input alias, and canonical API output.
- [x] Run failing tests, then update SQLite schema with a transactional migration and normalize legacy input.
- [x] Run persistence and API test subsets.

### Task 2: Route by Skill format and inject the selected executor

**Files:** `src/hostpilot/routing/skill_selector.py`, `src/hostpilot/orchestration/skill_runtime.py`, `src/hostpilot/orchestration/runtime.py`, `src/hostpilot/skills/deepagents_adapter.py`, related routing/orchestration tests.

- [x] Add tests proving the selector emits `markdown`, recognizes `markdown_skill`, and the coordinator dispatches via the `markdown` executor key.
- [x] Rename format-level DeepAgents terminology to Markdown while leaving true framework adapter/plugin names intact.
- [x] Run routing and Skill Runtime test subsets.

### Task 3: Update the Skill settings UI and architecture records

**Files:** `frontend/src/api/skills.ts`, `frontend/src/SkillsPanel.vue`, relevant frontend tests, ADR-0013, roadmap, test report and metrics.

- [x] Update types, option values, labels, and fallbacks to `markdown`; test visible behavior and API payload.
- [x] Record compatibility and migration behavior in architecture and test documentation.
- [x] Run frontend tests and full backend/frontend verification.

