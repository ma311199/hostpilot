# Runtime Sandbox 请求级生命周期实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将会话 Sandbox 模式绑定、传播和清理迁入 ChatService 与 Runtime 管理的 ToolFacade 作用域，移除 FastAPI 对 Runtime/MCP 对象图的递归修改。

**Architecture:** Conversation 的持久 `sandbox_mode` 在 Session 操作租约中读取，并放进新的 RequestContext。ChatService 对整个 Chat/恢复操作进入 `ToolFacade.sandbox_mode_scope(mode)`；该作用域通过专用 `asyncio.Lock` 序列化共享 Provider 模式变更，设置 ContextVar 和 MCP Provider 模式，并在所有退出路径中恢复。API 现有 `model_components_lock` 继续保护运行中 Provider 与配置替换。

**Tech Stack:** Python 3.11, FastAPI, asyncio, ContextVar, pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-runtime-sandbox-request-lifecycle-design.md`

## Global Constraints

- Sandbox 模式是请求级执行授权数据，不进入模型提示词或进程级共享 RuntimeContext。
- `read-only`、`workspace-write`、`danger-full-access` 的已有授权、审批和 OS Sandbox 行为保持不变。
- 持久 MCP Server task 继续通过 Workspace 的显式模式绑定读取模式；ContextVar 负责当前请求协程中的本地调用。
- Chat、Plan/Skill 工具、PlanRun 恢复和 Chat Turn 恢复都使用同一 Session 持久模式。
- 保留 API `model_components_lock`；Sandbox 作用域另行保护独立 ChatService 请求之间的 Provider 模式读写。
- 不改变 API/WebSocket 协议，不重新设计逐工具显式传 Sandbox 参数，不运行需要真实模型密钥的评测。
- 保留现有未提交改动；不提交、不推送。

## Review Focus

- **只读 Session**：REST/WebSocket Chat 与恢复仍拒绝文件写入；Task 2 和 Task 3 分别覆盖 ChatService 与 API 集成行为。
- **可写/危险 Session**：ToolFacade 在作用域中绑定正确模式，既有审批和危险模式授权边界保持；Task 2 和 Task 3 覆盖。
- **异常、取消、审批拒绝和 WebSocket 断开**：ContextVar、Provider 模式和锁必须清理；Task 1 测作用域异常，Task 3 测 WebSocket 断开后新 Session 不继承旧模式。
- **并发不同 Session**：Provider 模式不能交叉；Task 1 测试两个重叠请求按作用域锁隔离。
- **未注入 ToolFacade 的 ChatService**：ContextVar 仍按默认 `read-only` 或显式 RequestContext 模式工作；Task 2 覆盖无 Provider 回退。

---

### Task 1: RequestContext 模式字段与 ToolFacade 作用域

**Files:**
- Modify: `src/hostpilot/runtime/context.py`
- Modify: `src/hostpilot/tools/facade.py`
- Test: `tests/unit/runtime/test_context.py`
- Test: `tests/unit/tools/test_facade.py`

**Interfaces:**
- Produces `RequestContext.sandbox_mode: SandboxMode`, defaulting to `SandboxMode.READ_ONLY`; it remains separate from `ContextSnapshot`.
- Produces `ToolFacade.sandbox_mode_scope(mode: SandboxMode) -> AsyncContextManager[None]`; it serializes scopes, binds the current provider with `set_sandbox_mode(mode)`, binds the existing Sandbox ContextVar, and resets both in `finally`.
- Existing `ToolFacade.set_sandbox_mode()` remains the explicit binding operation used by the scope.

- [ ] **Step 1: Write failing context tests** `test_request_context_carries_sandbox_mode_outside_snapshot` for default and explicit modes; assert `ContextSnapshot` has no Sandbox field.
- [ ] **Step 2: Run the context test and confirm** `RequestContext` does not yet accept or expose `sandbox_mode`.
- [ ] **Step 3: Implement the RequestContext field** using the existing `SandboxMode` enum and default `READ_ONLY`.
- [ ] **Step 4: Run the context test and verify** the default and explicit values pass without changing the snapshot.
- [ ] **Step 5: Write failing ToolFacade scope tests** `test_sandbox_mode_scope_binds_and_resets_mode`, `test_sandbox_mode_scope_resets_after_exception`, and `test_sandbox_mode_scope_serializes_overlapping_requests`; assert the provider sees one mode at a time, reset receives `None`, and ContextVar returns to its prior token value.
- [ ] **Step 6: Run the named facade tests and confirm** the scope API is absent.
- [ ] **Step 7: Implement `ToolFacade.sandbox_mode_scope()`** as an async context manager using a per-facade `asyncio.Lock`, the explicit provider setter, and `sandbox_mode_scope()`; place the reset in `finally` while allowing the lock context to release on cancellation.
- [ ] **Step 8: Run** `.venv/bin/pytest -q tests/unit/runtime/test_context.py tests/unit/tools/test_facade.py`.
  **Expected:** all RequestContext and scope lifecycle/isolation tests pass.

### Task 2: ChatService loads Session mode and owns the full execution scope

**Files:**
- Modify: `src/hostpilot/orchestration/chat.py`
- Test: `tests/unit/orchestration/test_chat_routing.py`
- Test: `tests/integration/test_runtime_chat_turn_recovery.py`

**Interfaces:**
- `ChatService.send()` loads the existing Conversation under its Session lease and passes `conversation.sandbox_mode` to `_send_leased()`.
- `resume_plan_run()` and `resume_turn()` reuse the Conversation they already load.
- Each execution path enters `ToolFacade.sandbox_mode_scope(mode)` when available; without a facade, use the Sandbox ContextVar scope. The same `mode` is stored in the created `RequestContext`.
- `select_route_only()` keeps the default `READ_ONLY` mode and does not start a tool scope.

- [ ] **Step 1: Extend ChatService test doubles** with Session-scoped `ConversationSummary` lookup and a recording async sandbox scope.
- [ ] **Step 2: Write failing ChatService tests** `test_send_uses_persisted_sandbox_mode_in_request_context`, `test_resume_plan_run_uses_persisted_sandbox_mode`, `test_resume_turn_uses_persisted_sandbox_mode`, and `test_send_uses_read_only_contextvar_without_tool_facade`; assert scope begins before runtime/tool work and exits on completion/error.
- [ ] **Step 3: Run the named tests and confirm** ChatService neither reads Session Sandbox mode nor enters a mode scope.
- [ ] **Step 4: Add an internal async `_sandbox_mode_scope(mode)` helper** that delegates to an injected ToolFacade scope when present and otherwise binds the existing ContextVar.
- [ ] **Step 5: Update `send()`, `resume_plan_run()`, and `resume_turn()`** to read/preserve the Session mode, set `RequestContext.sandbox_mode`, and wrap the full execution operation in the scope. Do not include Sandbox mode in ContextSnapshot or prompt data.
- [ ] **Step 6: Run** `.venv/bin/pytest -q tests/unit/orchestration/test_chat_routing.py tests/integration/test_runtime_chat_turn_recovery.py`.
  **Expected:** Chat and both recovery paths use the persisted mode; scope cleanup occurs on normal and exceptional exits.

### Task 3: Remove API object-graph mode mutation and verify HTTP/WebSocket behavior

**Files:**
- Modify: `src/hostpilot/api/app.py`
- Modify: `src/hostpilot/orchestration/runtime_host_plugin.py`
- Test: `tests/unit/api/test_app.py`
- Test: `tests/unit/orchestration/test_runtime_plugins.py`

**Interfaces:**
- FastAPI keeps `model_components_lock` but delegates Session mode lookup, binding and cleanup to ChatService/ToolFacade.
- `_set_runtime_sandbox_mode()` and the API-level `sandbox_mode_scope()` wrappers are removed.
- `ChatAgentRuntimeHostPlugin` no longer recursively discovers nested `provider`/`runtime` attributes to set Sandbox mode.

- [ ] **Step 1: Add an API regression test** that disconnects while a writable WebSocket Turn is waiting for approval, then sends a read-only Session request and verifies it remains read-only and the previous mode was cleared.
- [ ] **Step 2: Run** `.venv/bin/pytest -q tests/unit/api/test_app.py::test_chat_sandbox_mode_is_isolated_between_conversations` and the new disconnect regression; confirm the new cleanup behavior fails before integration.
- [ ] **Step 3: Remove `_set_runtime_sandbox_mode()` and API `sandbox_mode_scope()` calls** from REST Chat and WebSocket Chat/Plan/Turn recovery handlers; let ChatService validate/load the Conversation and establish the scope.
- [ ] **Step 4: Remove recursive `ChatAgentRuntimeHostPlugin.set_sandbox_mode()`** and update its tests to assert no runtime-object graph traversal is exposed; keep stable ToolFacade provider binding.
- [ ] **Step 5: Run** `.venv/bin/pytest -q tests/unit/api/test_app.py tests/unit/orchestration/test_runtime_plugins.py tests/unit/orchestration/test_tool_governance_host_plugin.py tests/unit/tools/test_mcp.py`.
  **Expected:** REST and WebSocket mode isolation, approvals, cancellation, provider replacement and persistent MCP handler behavior pass.

### Task 4: Synchronize Sandbox migration documentation and verify

**Files:**
- Modify: `docs/roadmap.md`
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/新架构文档.md`
- Modify: `docs/testing-catalog.md`
- Modify: `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-23-runtime-sandbox-request-lifecycle.md`

- [ ] **Step 1: Update Sandbox migration status** to say the Runtime Chat/ToolFacade scope owns Session mode binding and cleanup; distinguish this request lifecycle from the already Runtime-owned process backend set.
- [ ] **Step 2: Correct directly touched architecture text** so it no longer says API owns mode binding or that persisted Turn lifecycle metadata is process-only.
- [ ] **Step 3: Run** `bash scripts/check_quality.sh`.
  **Expected:** Ruff format/lint, mypy, full Python tests and `uv lock --check` pass.
- [ ] **Step 4: Run** `pnpm test`, `pnpm lint`, and `pnpm build` from `frontend/`.
  **Expected:** existing Vitest, ESLint, TypeScript and production build checks pass.
- [ ] **Step 5: Run** `git diff --check`, record exact counts/warnings and coverage status in the report and metrics ledger. Do not run model evaluation or commit/push.

## Acceptance Criteria

- FastAPI no longer traverses Runtime/Provider object graphs or binds Sandbox mode around Chat calls.
- RequestContext holds the Session's Sandbox mode outside the model-visible ContextSnapshot.
- ToolFacade scopes serialize shared provider binding and restore both ContextVar and Provider mode for success, exception and cancellation.
- REST/WebSocket Chat, PlanRun resume and Chat Turn resume use the persisted Session mode.
- Existing workspace-write approval, read-only rejection, dangerous-mode upgrade, Provider refresh and MCP persistent-task behavior pass regression tests.
- Full Python quality checks, frontend checks and `git diff --check` pass; work remains uncommitted and unpushed.
