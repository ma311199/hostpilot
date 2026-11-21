# Persistent Task Lifecycle Design

## Intent

Persist Runtime Plan subtask lifecycle metadata so a process restart does not erase which subtasks were running, completed, failed, or cancelled. The state will support diagnosis and a safe recovery boundary; it will not restore Plan execution or replay tools.

## Current state

- Session metadata already persists in `ConversationStore`; normalized messages and execution checkpoints persist through the LangGraph checkpointer. This design does not add a duplicate Session store or Session status table.
- `OrchestratorPlanExecutor` currently keeps `PlanTaskState` in memory for one execution. That state can contain task outputs, error information, side effects, idempotency keys, and invoked tool IDs.
- Runtime Turn metadata is already persisted through `TurnStore`, with one Host lease per ASGI worker. A Host startup marks expired owners' running Turns as interrupted.

## Goals

- Persist one metadata record for each actual Plan subtask execution attempt.
- Associate each TaskRun with its `session_id`, parent `turn_id`, owning `runtime_host_id`, and the Plan-local `task_id`.
- Record `running`, `completed`, `failed`, `cancelled`, and `interrupted` states with UTC start/end timestamps.
- Mark TaskRuns owned by an inactive Host (`expired` or `stopped`) as `interrupted` before the new Host serves requests.
- Inject the persistence contract as a replaceable `TaskStore` Port; use SQLite in the current application.
- If the start write fails, do not invoke the TaskExecutor. If a terminal write fails, propagate the persistence error and remove local active tracking.

## Recovery boundary

- A restart records an active TaskRun as `interrupted`; it does not resume it.
- Existing TaskRun rows contain metadata only. They do not contain task descriptions, inputs, dependencies, outputs, model text, tool arguments, credentials, side-effect handles, or raw exceptions.
- A user retry begins a new Turn and creates new TaskRun IDs. No existing `running`, `failed`, `cancelled`, or `interrupted` TaskRun is retried in place.
- Existing Runtime Chat checkpoint behavior remains responsible for message-level recovery and its known tool-result replay protection. This TaskStore does not change that behavior.

## Data model

Each TaskRun has a generated `task_run_id` primary key, `task_id` (local to its Plan), `turn_id`, `session_id`, `runtime_host_id`, constrained status, `started_at`, and optional `finished_at`. Task records share the Host lease table used by Turn persistence but are owned by a separate replaceable `TaskStore` Port. Recovery queries Hosts marked expired by TurnStore registration or stopped during normal shutdown, making recovery safe if shutdown failed to persist a terminal Task state.

## Approaches considered

1. **Persist metadata only (selected).** Stores status and ownership without task payloads. It supports diagnosis and interruption accounting without making unsafe resume promises.
2. **Persist Plan snapshots and resume completed steps.** Could avoid repeating completed work, but outputs and dependency values can contain sensitive data; in-flight tool outcomes need idempotency and external side-effect verification. Defer until a separate resume protocol exists.
3. **Treat LangGraph checkpoints as Task state.** This would couple Runtime task semantics to the current Chat framework, while Plan task state is produced by the HostPilot orchestrator. Keep the Store Port framework-neutral.

## Non-goals

- Persisting additional Session metadata already covered by ConversationStore and the checkpointer.
- Automatic Plan resumption, Task retry, tool replay, or restoration of dependency outputs.
- New API/UI endpoints for browsing or resuming Tasks.
- Task metadata retention/cleanup policy; records remain available until a separate retention policy is designed.

## Implementation

- Add `TaskStore` and `SQLiteTaskStore` with atomic state updates and recovery of inactive Host TaskRuns.
- Add `TaskLifecycleManager` to `RuntimeContext`; `StateServicesHostPlugin` creates, initializes, registers, configures, and closes its store alongside `TurnStore`, and interrupts running TaskRuns for expired or stopped Hosts before readiness.
- Add an optional TaskLifecycleManager reference to per-request `RequestContext`; ChatService supplies the Runtime Host manager.
- Wrap actual `OrchestratorPlanExecutor.execute_one` invocations in Task lifecycle tracking. A successful `PlanTaskResult` maps to `completed`; an unsuccessful result or caught executor error maps to `failed`; cancellation maps to `cancelled`.
- Keep failed or uncertain side-effect details in the existing governed cancellation/audit/checkpoint paths; do not duplicate them into TaskStore.

## Acceptance

- SQLite TaskRun metadata survives reopening the application database.
- Successful, failed, and cancelled Plan subtask attempts persist their terminal states; crash recovery changes only expired Host TaskRuns from running to interrupted.
- A live Host's TaskRuns are not altered by another Host's startup; a stopped Host's residual `running` TaskRuns are marked `interrupted`.
- Store failures fail closed before task execution and remain visible on terminal writes.
- Existing Session persistence, Chat checkpoint recovery, Plan behavior, and no-tool-replay safety tests pass unchanged.
