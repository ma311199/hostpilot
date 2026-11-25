# Runtime Chat Turn Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Let a user explicitly resume an interrupted Runtime-native Chat Turn across Runtime Host restarts from its latest durable checkpoint without changing its logical Turn or tool idempotency identity.

**Architecture:** Extend the durable Turn lifecycle Store with Session-scoped recoverable queries and atomic claims. Tag checkpointed Runtime Chat messages with their logical Turn ID, then add a Runtime resume entry that reuses the original user message and existing tool journal under a fresh RequestContext. Expose recoverable Turns through REST and resume through the approval-capable chat WebSocket; the UI requires an explicit user action.

**Tech Stack:** Python 3.11, Pydantic, SQLite/aiosqlite, FastAPI, Vue 3, TypeScript, pytest, Vitest.

**Spec:** `docs/superpowers/specs/2026-11-23-runtime-chat-turn-recovery-design.md`

## Global Constraints

- Recovery is initiated only by an explicit user action; Host startup marks abandoned running Turns interrupted but never dispatches recovery.
- Only interrupted Runtime-native Chat Turns are recoverable; completed, failed, and user-cancelled Turns remain terminal.
- Recovery uses the original logical Turn ID and checkpoint association, while constructing a fresh RequestContext, budget, authorization, and Policy decision.
- A model request whose response was not checkpointed may be sent again after user confirmation; never claim exact continuation of an in-flight provider request.
- A durable tool result is reused; pure reads may be retried; writes require matching arguments, stable idempotency key, declared Provider support, and a valid window, otherwise wait for user reconciliation.
- A Turn may be claimed by only one active Runtime Host; claim and ownership transfer are atomic.
- Checkpoint message content remains in the existing Checkpointer; Turn metadata stores no user prompt, tool parameters, credentials, or result bodies.
- Recovery-time per-tool approvals use the existing `/ws/chat` WebSocket protocol. REST lists recoverable Turns only; this plan does not add a REST approval workflow.
- The first implementation supports `builtin.runtime-chat`; `builtin.deepagents` compatibility Turns fail closed with a clear unsupported-recovery reason.
- Preserve existing uncommitted Harness history configuration changes; do not commit or push; do not run model evaluations requiring real credentials.

## Review Focus

- Two Hosts race to resume the same interrupted Turn: exactly one atomic claim succeeds and only that Host can dispatch.
- A checkpoint contains multiple Turns or later messages: recovery must identify the original Turn boundary or reject as stale; it must not infer a boundary from message order alone.
- A crash occurs after a model response but before its checkpoint write: recovery may request the model again, but cannot duplicate a tool side effect.
- A crash occurs between tool dispatch and result checkpoint: same-key replay is allowed only with a matching fingerprint, declared Provider support, and unexpired idempotency window; otherwise require reconciliation.
- A new Turn is attempted while an eligible interrupted Turn exists: the UI and service must avoid silently superseding or mixing the recoverable execution.

---

### Task 1: Durable Turn Recovery Query, Correlation, and Claim

**Files:**
- Modify: `src/hostpilot/persistence/ports.py`
- Modify: `src/hostpilot/persistence/turns.py`
- Modify: `src/hostpilot/persistence/__init__.py`
- Modify: `src/hostpilot/runtime/lifecycle.py`
- Test: `tests/unit/persistence/test_turns.py`
- Test: `tests/unit/runtime/test_lifecycle.py`

**Interfaces:**
- Produces `TurnStore.list_interrupted_turns(session_id: str) -> tuple[StoredTurn, ...]`.
- Produces `TurnStore.claim_interrupted_turn(turn_id: str, session_id: str, runtime_host_id: str, *, claimed_at: datetime) -> bool`; it succeeds only for a matching interrupted Turn and a currently active claiming Host.
- Defines `StoredTurn` as a persistence DTO in `ports.py`, exports it from `hostpilot.persistence`, and has SQLite Store return it.
- Produces `TurnLifecycleManager.list_recoverable(session_id: str) -> tuple[StoredTurn, ...]`, `claim_recovery(turn_id: str, session_id: str) -> bool`, and `track_recovery(turn_id: str, session_id: str)` using its configured Store and Host identity.
- Preserve existing `runtime_turns` schema fields and existing `StoredTurn` values; do not add request payload columns.

- [x] **Step 1: Write failing tests** `test_list_interrupted_turns_is_session_scoped`, `test_claim_interrupted_turn_transfers_owner_once`, `test_claim_requires_active_host_and_matching_session`, `test_recovery_track_finishes_claimed_turn_on_new_host`, and `test_cancelled_recovery_returns_turn_to_interrupted`; assert one winner for concurrent Host claims.
- [x] **Step 2: Run the Store tests and confirm they fail** because the Port and SQLite Store lack recovery query/claim operations.
- [x] **Step 3: Implement the Port, SQLite transaction, and manager wrappers**. Use one `BEGIN IMMEDIATE` transaction for state/Session validation, active Host lease validation, and `interrupted` → `running` ownership transfer.
- [x] **Step 4: Run** `.venv/bin/pytest -q tests/unit/persistence/test_turns.py tests/unit/runtime/test_lifecycle.py`.
  **Expected:** all tests pass, including the two-Host atomic claim race.

### Task 2: Tool Invocation Journal Ownership Handoff

**Files:**
- Modify: `src/hostpilot/persistence/ports.py`
- Modify: `src/hostpilot/persistence/tasks.py`
- Modify: `src/hostpilot/runtime/lifecycle.py`
- Test: `tests/unit/persistence/test_tasks.py`
- Test: `tests/unit/runtime/test_task_lifecycle.py`
- Test: `tests/unit/runtime/test_tool_outcome_lifecycle.py`

**Interfaces:**
- Consumes Task 1 Turn claim semantics; journal handoff is called only after the Turn belongs to the current Host.
- Produces `TaskLifecycleManager.list_runtime_tool_invocations(session_id: str, turn_id: str) -> tuple[ToolInvocationRecord, ...]`.
- Produces `TaskLifecycleManager.claim_runtime_invocations(session_id: str, turn_id: str) -> bool`; only prepared records for a Turn currently running under this Host may transfer ownership.
- A repeated `record_runtime_tool_invocation()` for the same recovered invocation is accepted only when the existing prepared record has the exact same Session, Turn, call identity, request fingerprint, arguments, retry class, and idempotency data.
- The existing `ToolOutcomeLifecycleManager.claim_resolution()` claims a pending side-effect gate before a safe keyed replay; the gate clears only after the recovered ToolMessage is checkpointed.

- [x] **Step 1: Write failing tests** `test_list_runtime_tool_invocations_is_turn_scoped`, `test_claim_runtime_invocations_requires_current_turn_owner`, `test_record_runtime_tool_invocation_reuses_exact_prepared_record`, and `test_record_runtime_tool_invocation_rejects_changed_fingerprint`; verify a prior pending outcome can be claimed and cleared by the recovering Host after checkpoint.
- [x] **Step 2: Run the named tests and confirm** runtime invocation transfer/reuse is missing and duplicate prepare currently rejects the existing row.
- [x] **Step 3: Implement Turn-scoped journal lookup and guarded ownership transfer** in TaskStore/TaskLifecycleManager, plus exact-match re-prepare behavior. Never return keys, arguments, or result bodies through user-facing APIs.
- [x] **Step 4: Run** `.venv/bin/pytest -q tests/unit/persistence/test_tasks.py tests/unit/runtime/test_task_lifecycle.py tests/unit/runtime/test_tool_outcome_lifecycle.py`.
  **Expected:** exact prepared records transfer and reuse; mismatched records, wrong Host, and wrong Turn fail without changing state.

### Task 3: Checkpoint Turn Association and Runtime Resume

**Files:**
- Modify: `src/hostpilot/models/contracts.py`
- Modify: `src/hostpilot/orchestration/runtime_chat_state.py`
- Modify: `src/hostpilot/orchestration/runtime_chat.py`
- Modify: `src/hostpilot/orchestration/chat.py`
- Test: `tests/unit/orchestration/test_runtime_chat.py`
- Test: `tests/unit/orchestration/test_runtime_chat_state.py`
- Test: `tests/unit/runtime/test_execution_loop.py`
- Create: `tests/integration/test_runtime_chat_turn_recovery.py`

**Interfaces:**
- Consumes Task 1 `TurnLifecycleManager.list_recoverable()`, `claim_recovery()`, and `track_recovery()`.
- Consumes Task 2 `TaskLifecycleManager.list_runtime_tool_invocations()` and `claim_runtime_invocations()`.
- Produces `RecoverableChatTurn(turn_id: str, started_at: datetime, recoverable: bool, reason_code: str | None)` in `hostpilot.orchestration.chat`.
- Produces `ChatService.list_recoverable_turns(session_id: str) -> tuple[RecoverableChatTurn, ...]`.
- Produces `ChatService.resume_turn(session_id: str, turn_id: str, *, confirmation_provider: AsyncConfirmationProvider | None = None) -> ChatTurnResult`.
- Produces `RuntimeChatAgentRuntime.resume_turn(turn_id: str, request_context: RequestContext) -> ChatAgentResponse`.
- Adds optional `turn_id: str | None` metadata to normalized `ModelMessage`; model-provider serialization ignores this HostPilot-only marker, while `LangGraphRuntimeSessionStateAdapter` persists and restores it with messages.

- [x] **Step 1: Write failing tests** `test_runtime_chat_messages_carry_turn_id_through_checkpoint`, `test_resume_turn_uses_original_user_message_without_duplicate`, `test_resume_turn_preserves_turn_id_for_idempotency_identity`, `test_resume_reuses_checkpointed_tool_result`, and `test_resume_rejects_turn_when_checkpoint_has_newer_turn`.
- [x] **Step 2: Write failing tests** for pure-read retry, same-key/same-fingerprint supported write replay with a claimed pending outcome, reconciliation-only behavior for unsupported/expired/mismatched writes, and a fresh RequestContext/budget on resume.
- [x] **Step 3: Run the named Runtime tests and confirm the new resume entry and metadata are absent.**
- [x] **Step 4: Implement message association and `RuntimeChatAgentRuntime.resume_turn()`**. Build the model prompt from checkpoint messages tagged with the original Turn, do not append the original user message again, rebuild call indexes from its journal, reuse completed results, and use the original Turn ID for stable tool keys.
- [x] **Step 5: Implement `ChatService.list_recoverable_turns()` and `resume_turn()`**. Under a Session lease, validate Session, Turn state, checkpoint boundary, and Runtime capability; atomically claim the Turn and transfer its invocation ownership before dispatch; create fresh budget/authorization context; track completion on the original Turn row.
- [x] **Step 6: Apply `decide_tool_invocation_recovery()` to every prepared write**. Replay only with the same key/fingerprint, current Provider support, valid window, and claimed outcome gate; return reconciliation-required without dispatch for every other uncertain write.
- [x] **Step 7: Run** `.venv/bin/pytest -q tests/unit/orchestration/test_runtime_chat.py tests/unit/orchestration/test_runtime_chat_state.py tests/unit/runtime/test_execution_loop.py tests/integration/test_runtime_chat_turn_recovery.py`.
  **Expected:** recovery tests pass; no dispatch happens before claim or user action; an in-flight model response may be requested again, as documented.

### Task 4: Recovery API, WebSocket Event, and Chat UI

**Files:**
- Modify: `src/hostpilot/api/app.py`
- Modify: `frontend/src/api/chat.ts`
- Modify: `frontend/src/api/chatSocket.ts`
- Modify: `frontend/src/ChatPanel.vue`
- Modify: `frontend/src/ChatPanel.test.ts`
- Modify: `frontend/src/api/chat.test.ts`
- Modify: `frontend/src/api/chatSocket.test.ts`
- Modify: `frontend/src/style.css`
- Test: `tests/unit/api/test_app.py`

**Interfaces:**
- REST `GET /api/v1/conversations/{session_id}/turns/recoverable` returns Session-scoped Turn ID, start time, recoverable flag, and safe reason code; it never returns checkpoint message bodies or tool data.
- WebSocket accepts `{ type: "turn.resume", session_id, turn_id }`; response is `{ type: "turn.resume.response", data: { turn_id, status, assistant_message?, reason_code? } }`.
- Recovery uses `WebSocketConfirmationProvider`, the existing `tool.confirmation.request` and `tool.confirmation.decision` messages, and current ChatPanel approval handling.

- [x] **Step 1: Write failing API/WebSocket tests** proving list isolation, zero dispatch on query, explicit resume requirement, error for wrong Session/stale Turn, tool approval request/decision during recovery, WebSocket disconnect returning the Turn to interrupted without further dispatch, and explicit approval rejection leaving it terminal.
- [x] **Step 2: Run the API tests and confirm** the recoverable Turn endpoint and `turn.resume` event are not recognized.
- [x] **Step 3: Implement the REST query and WebSocket resume request/response** using Task 3 ChatService contracts; map conflicts to stable error/reason codes and keep REST resume/approval endpoints out of scope.
- [x] **Step 4: Write failing frontend tests** `showsRecoverableTurnAndResumesOnlyAfterClick`, `refreshesRecoverableTurnsAfterResume`, and socket tests for matching resume response, error, and approval callbacks.
- [x] **Step 5: Implement ChatSocketClient and ChatPanel support**. Refresh the recoverable Turn list on Session selection and after recovery; disable duplicate clicks while the request is active; never resume automatically.
- [x] **Step 6: Run** `.venv/bin/pytest -q tests/unit/api/test_app.py` from the repository root and `pnpm test` from `frontend/`.
  **Expected:** all API and frontend tests pass; only an explicit user action emits `turn.resume`.

### Task 5: Migration Documentation and Full Verification

**Files:**
- Modify: `docs/roadmap.md`
- Modify: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Modify: `docs/新架构文档.md`
- Modify: `docs/testing-catalog.md`
- Modify: `docs/testing-metrics.md`
- Create: `docs/test-results/2026-11-23-runtime-chat-turn-recovery.md`

- [x] **Step 1: Update stale roadmap statements** that still say Plan snapshots/recovery are unimplemented; state that PlanRun recovery is delivered and Runtime-native Chat Turn recovery is now covered, while explicit `builtin.deepagents` recovery remains unsupported.
- [x] **Step 2: Document the recovery contract and limitations**: explicit user action, durable message boundary, new RequestContext/current authorization and budget, possible provider request replay, stable tool idempotency, manual reconciliation, and no Host-startup auto-resume.
- [x] **Step 3: Run full Python quality checks** with `bash scripts/check_quality.sh`.
  **Expected:** Ruff format/lint, mypy, Python tests, and `uv lock --check` pass.
- [x] **Step 4: Run full frontend checks** from `frontend/`: `pnpm test`, `pnpm lint`, and `pnpm build`.
  **Expected:** Vitest, ESLint, TypeScript, and production build pass.
- [x] **Step 5: Run** `git diff --check` and record exact test counts, warnings, limitations, and whether coverage was measured in the dated report and testing metrics.
  **Expected:** no whitespace errors; no model evaluation requiring real credentials is run.

## Acceptance Criteria

- An interrupted Runtime-native Chat Turn is queryable after Host restart and is never resumed automatically.
- Explicit user recovery resumes the same logical Turn from its tagged durable checkpoint without duplicating its original user message.
- The original Turn ID preserves the existing tool invocation identity and valid idempotency key across Host restart.
- Two Hosts cannot concurrently claim and dispatch one recovery.
- Completed tool results remain completed; unresolved writes replay only under the declared same-key contract or wait for user reconciliation.
- The Chat UI shows a recoverable Turn and resumes only after a user click; interactive tool confirmations continue over WebSocket.
- Compatibility `builtin.deepagents` recovery fails closed with an explicit unsupported reason.
- Existing Plan recovery, normal Chat Turns, Session isolation/deletion, and approval boundaries pass regression tests.
- All planned quality checks pass; work remains uncommitted and unpushed.
