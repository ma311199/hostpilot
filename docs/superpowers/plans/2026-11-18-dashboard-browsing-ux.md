# Dashboard Browsing UX Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make growing HostPilot lists and configuration panels easier to browse on desktop and mobile while keeping current local API semantics and visual identity.

**Architecture:** Keep current page components and API boundaries. Add one small reusable Vue pagination control for bounded card/list pages; use computed filtering and pagination in the client, bounded scrolling for the chat sidebar, and collapsible native details groups for Harness settings. Keep the current MCP API contract unchanged; defer MCP Server purpose descriptions until the backend interface supports them.

**Tech Stack:** Vue 3 Composition API, TypeScript, scoped CSS, native CSS, FastAPI/Pydantic, SQLite, Vitest/Vue Test Utils, pytest.

**Spec:** `docs/superpowers/specs/2026-11-18-dashboard-browsing-ux-design.md`

## Global Constraints

- Do not add interface, routing, state-management, or API dependencies.
- Keep existing Vue 3, TypeScript, Fetch, CSS, and FastAPI API conventions.
- Keep security, approval, workspace, and runtime behavior unchanged.
- Preserve full tool and Skill descriptions; limit only the collapsed visual presentation.
- Do not modify backend files or assume MCP Server description fields exist in the current API.
- Use 12 results per page for tools, Skills, and Trace summaries; clamp card descriptions to 3 lines.
- Keep responsive content within the viewport; management page maximum width is 1240–1320px, with readable text line lengths.

## Review Focus

- Existing SQLite databases have no MCP description column: migration must add an empty default without losing saved server settings. Test in Task 1.
- Older MCP clients omit `description`: save must remain compatible and the response must provide a string. Test in Task 1.
- Search, category, and status filters can shrink a result set below the current page: page must reset or clamp and never show an empty stale page. Test in Tasks 3–4 and 7.
- Deleting the selected Skill or Trace, or changing pages, must not leave stale detail content. Test in Tasks 4 and 7.
- Long labels and narrow viewports must not cause horizontal overflow; full descriptions remain available to keyboard and touch users. Test relevant component states in Tasks 3–8.

## File Map

- `frontend/src/api/mcp.ts`: use existing MCP request/response types unchanged.
- `frontend/src/PaginationControls.vue`: reusable accessible page controls.
- `frontend/src/ChatPanel.vue`: conversation search/grouping and workspace location presentation.
- `frontend/src/ToolsPanel.vue`: tool search, filters, paging, and full-description disclosure.
- `frontend/src/SkillsPanel.vue`: directory action toolbar, search/filters/paging, and description disclosure.
- `frontend/src/McpConnectionsPanel.vue`: deferred editor, configuration mode actions, and server descriptions.
- `frontend/src/TracesPanel.vue`: trace search/filter/paging and bounded list/detail regions.
- `frontend/src/HarnessSettingsPanel.vue`: collapsible configuration sections.
- `frontend/src/style.css`: shared responsive page widths and chat workspace styling.
- Existing unit/component tests beside each changed module; add focused tests for new behavior.

## Tasks

### Task 1: Use current MCP API contract

**Ruling:** Defer MCP Server descriptions and make no backend changes because another program owns backend work; frontend MCP improvements must work with the existing interface.

- [x] Confirm the current MCP connection/configuration endpoints and fields before editing the UI.
- [x] Remove backend MCP description implementation from this plan's scope.

### Task 2: Add shared pagination controls

**Files:** Create `frontend/src/PaginationControls.vue`, create `frontend/src/PaginationControls.test.ts`.

**Interfaces:** Props `page: number`, `pageSize: number`, `total: number`; emit `update:page` with a one-based page number. Render result range, previous/next controls, and `aria-current="page"` for the active page; render nothing for zero or one page.

- [ ] Write tests for zero/one page, active page semantics, previous/next disabled states, and page-change events.
- [ ] Run the focused Vitest file and confirm failure before implementation.
- [ ] Implement the Vue component with 44px touch targets and responsive control wrapping.
- [ ] Run the focused Vitest file and confirm all pagination control cases pass.

### Task 3: Browse the tool directory

**Files:** `frontend/src/ToolsPanel.vue`, `frontend/src/ToolsPanel.test.ts`, `frontend/src/PaginationControls.vue`.

**Interfaces:** Derived visible tools use case-insensitive name/description search, tool type (`all`, `retrieval`, `action`, `code`), configured/runtime state, and 12-item pages. Description disclosure preserves the original text and has collapsed/expanded state.

- [ ] Add component tests for search, type/status filtering, page changes, filter reset to page 1, empty search results, and long-description disclosure.
- [ ] Run focused ToolsPanel tests and confirm the new behavior assertions fail.
- [ ] Implement the filter toolbar, computed result set, pagination, three-line description clamp, hover title, and touch/keyboard expand control.
- [ ] Run focused ToolsPanel tests and confirm all cases pass.

### Task 4: Browse and manage the Skill library

**Files:** `frontend/src/SkillsPanel.vue`, `frontend/src/SkillsPanel.test.ts`, `frontend/src/PaginationControls.vue`.

**Interfaces:** Derived Skill results support case-insensitive name/description/ID search, source (`all`, `bundled`, `user`), enabled state, and 12-item pages. Existing details/actions continue to use `skill_id` identity.

- [ ] Add component tests for search/source/enabled filters, page reset, empty results, details after page changes, and description expansion.
- [ ] Run focused SkillsPanel tests and confirm the new assertions fail.
- [ ] Implement the aligned refresh/import toolbar, filters, computed pagination, and full-description disclosure.
- [ ] Ensure a selected Skill detail closes when filtering or pagination removes that Skill from the visible set.
- [ ] Run focused SkillsPanel tests and confirm all cases pass.

### Task 5: Make the chat workspace browsable

**Files:** `frontend/src/ChatPanel.vue`, `frontend/src/ChatPanel.test.ts`, `frontend/src/style.css`.

**Interfaces:** Conversation search filters title; date groups are `today`, `yesterday`, and `earlier`; list rendering remains keyed by `conversation_id`. Workspace path and read-only state are a distinct status block with a “更换目录” action.

- [ ] Add tests for title search, all three date groups, no matches, and current workspace/read-only feedback.
- [ ] Run focused ChatPanel tests and confirm new assertions fail.
- [ ] Implement searchable grouped sessions in a bounded desktop list and bounded mobile vertical list.
- [ ] Reorganize workspace selection into a path/status block; retain native directory picker and current success/error behavior.
- [ ] Run focused ChatPanel tests and confirm all cases pass.

### Task 6: Clarify MCP configuration

**Files:** `frontend/src/api/mcp.ts`, `frontend/src/McpConnectionsPanel.vue`, `frontend/src/McpConnectionsPanel.test.ts`.

**Interfaces:** Keep the existing request and response types. The editor opens from “添加 Server” or “编辑”, offers form/JSON modes, and closes after successful save/import. Built-in and environment-managed rows show a concise frontend explanation without claiming a specific server purpose.

- [ ] Add component/API tests for hidden default editor, add/edit entry points, import mode, cancel behavior, and system-managed explanations.
- [ ] Run focused MCP tests and confirm new assertions fail.
- [ ] Implement deferred editor and mode choices inside the editor; preserve form draft when switching modes and confirm before discarding unsaved edits.
- [ ] Close editor after successful save/import; display concise connection metadata and frontend-managed explanations for built-in/environment entries.
- [ ] Run focused MCP component and API tests and confirm all cases pass.

### Task 7: Browse Trace history without page-length growth

**Files:** `frontend/src/TracesPanel.vue`, `frontend/src/TracesPanel.test.ts`, `frontend/src/PaginationControls.vue`.

**Interfaces:** Search matches trace ID, Skill ID, last event, and status; status filter includes all/available statuses; page size is 12 within the existing recent-record response. Detail selection remains by `trace_id`.

- [ ] Add tests for query/status filters, page reset, empty results, selection retention/clearing, and list/detail loading/error states.
- [ ] Run focused TracesPanel tests and confirm new assertions fail.
- [ ] Implement computed search/filter/pagination and bounded list/detail scrolling on desktop; use natural single-column flow on narrow screens.
- [ ] Run focused TracesPanel tests and confirm all cases pass.

### Task 8: Group Harness settings and widen responsive panels

**Files:** `frontend/src/HarnessSettingsPanel.vue`, `frontend/src/HarnessSettingsPanel.test.ts`, `frontend/src/style.css`.

**Interfaces:** Existing configuration groups become native disclosure sections; the first section is initially open and each section retains all current field bindings. Shared page widths use one responsive maximum in the 1240–1320px range.

- [ ] Add component tests for initial open state and access to each existing configuration group/field.
- [ ] Run focused HarnessSettingsPanel tests and confirm new assertions fail.
- [ ] Convert groups to accessible disclosure sections without changing save payload or validation behavior.
- [ ] Expand management page containers responsively; preserve constrained reading width for paragraphs and single-column layouts below existing mobile breakpoints.
- [ ] Run focused HarnessSettingsPanel tests and check CSS layout rules at desktop, tablet, and mobile breakpoints.

### Task 9: Full verification and documentation

**Files:** changed frontend/backend modules and `docs/frontend-architecture.md` if the MCP metadata contract requires documenting its new field.

- [ ] Do not run or change backend code for this frontend-only task.
- [ ] Run all frontend Vitest tests, frontend lint, and frontend production build/typecheck.
- [ ] Run the project quality command or the relevant backend lint/type checks, without altering unrelated warnings.
- [ ] Review mobile and desktop behavior at 390px, 768px, 1280px, and 1440px; confirm no page-level horizontal overflow and the list controls remain operable.
- [ ] Update the frontend architecture/API documentation with the new browse patterns and note MCP purpose descriptions are deferred until API support exists.
- [ ] Review the final diff for unrelated changes and ensure every spec acceptance result is covered.
