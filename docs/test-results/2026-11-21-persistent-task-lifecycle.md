# Persistent Task Lifecycle Validation

## Scope

Persist metadata for each actual Plan task execution through a replaceable `TaskStore` Port backed by SQLite. The Runtime-owned `TaskLifecycleManager` records start, completion, failure, and cancellation. Startup recovery marks residual `running` TaskRuns owned by expired or stopped Runtime Hosts as `interrupted`. It does not resume tasks, retry them, or replay tools.

TaskRun rows contain only TaskRun ID, Plan-local task ID, Turn ID, Session ID, Runtime Host ID, state, and timestamps. Task bodies, inputs, dependencies, outputs, tool arguments, credentials, side-effect handles, and raw exceptions are excluded.

## Coverage

- SQLite TaskRun records remain readable after reopening the database.
- Task lifecycle start writes fail closed; terminal states are persisted, and terminal persistence failures remain visible.
- Concurrent/replanned work receives distinct TaskRun IDs even when Plan-local task IDs repeat.
- Live Host TaskRuns remain `running` when another Host starts; expired and stopped Host residual TaskRuns become `interrupted`.
- Chat API integration uses deterministic fake execution and verifies TaskRun state after application restart.
- No test calls a real model or external tool.

## Findings and repair

- Initial focused baseline: **31 passed**, with one existing Authlib deprecation warning.
- Added a regression test for a stopped Host with a residual `running` TaskRun. It failed as expected because the recovery query selected only `expired` Hosts (**expected 1 interruption, observed 0**).
- Updated the TaskStore Port and SQLite implementation to recover TaskRuns owned by `expired` or `stopped` Hosts, and renamed the operation to `interrupt_inactive_host_tasks`. The regression test and expired/live Host isolation test then passed (**2 passed**).
- A failed terminal Store write during shutdown can leave a row under a normally stopped Host. Such a Host cannot own live work, so recovery marks the row `interrupted`; it never retries or replays the operation because an external side effect may already have completed.

## Verification

- Focused Task/Turn lifecycle, Host plugin, Chat routing, Plan orchestration, and persistence integration suite: **58 passed**, one existing Authlib deprecation warning.
- Full backend suite: **596 passed**, one existing Authlib deprecation warning (**46.86 seconds**).
- Ruff lint: `ruff check src tests` — passed.
- mypy: `mypy src/hostpilot` — passed, **125 source files**.
- Ruff format check on **22 changed Python files** — passed.
- `git diff --check` — passed.
- Full-repository Ruff format check reports one unchanged pre-existing formatting difference in `src/hostpilot/persistence/skill_execution.py:37`; **219 files are formatted**. That unrelated file was left unchanged.

## Result and limitations

Plan TaskRun lifecycle metadata now survives process restart and exposes interrupted work without implying resumability. TaskRun retention/cleanup is not configured. This metadata cannot establish whether an external side effect completed if a process stopped during a tool call; users must inspect the external system before retrying such work.
