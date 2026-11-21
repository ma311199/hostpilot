# Persistent Turn Lifecycle Validation

## Scope

Persist Turn lifecycle metadata through an injectable `TurnStore`, use SQLite in the application Runtime Host, and recover only Turn records owned by an expired Host lease. The implementation does not restore Agent/Plan cursors, replay tools, or persist messages, model output, tool arguments, credentials, or exception text.

Each ASGI worker gets a unique `runtime_host_id`. The `builtin.state-services` plugin registers the Host lease before readiness and manages the `hostpilot.turn-store` service. FastAPI starts its 5-second heartbeat through `RuntimeHost.create_task`; the lease expires after 30 seconds. Host shutdown cancels active Turns, persists their terminal states, cancels the heartbeat, then releases the lease.

## Coverage

- SQLite schema and lifecycle metadata survive store/process reopen.
- `running` transitions to `completed`, `failed`, or `cancelled`; a failed start write prevents ChatService execution.
- Terminal persistence errors clean the in-memory active index and remain visible during normal execution and Host shutdown; Host plugins still close in order.
- Registering a new Host marks only expired owners' `running` Turns `interrupted`; a live Host's Turn remains `running` after heartbeat renewal.
- Host shutdown persists cancellation before releasing the lease.
- A Chat API request produces a persisted terminal Turn record that remains readable after a second FastAPI application lifecycle starts.
- SQLite rows contain only Turn ID, Session ID, Host ID, state, and timestamps. No model or external tool calls are used by the tests.

## Verification

- Baseline before changes: `pytest -q` — **570 passed**, 1 existing Authlib deprecation warning.
- Focused lifecycle/Host/State plugin/API persistence suite: **20 passed**, including the Chat API restart integration test; 1 existing Authlib deprecation warning.
- Final full backend suite: `pytest -q` — **585 passed**, 1 existing Authlib deprecation warning.
- Full Ruff lint: `ruff check src tests` — passed.
- Full mypy: `mypy src/hostpilot` — **124 source files**, no issues.
- Ruff format check on all **14 changed Python files** — passed.
- `git diff --check` — passed.
- Full-repository `ruff format --check src tests` reports one pre-existing formatting difference in untouched `src/hostpilot/persistence/skill_execution.py:37`; no unrelated formatting change was made.

## Development findings

- The persistence tests first failed collection because `SQLiteTurnStore` was not exported yet; after implementation, all SQLite cases passed.
- A Host identity regression test first failed because `RuntimeContext` had no Host ID; the added UUID identity fixed it.
- Failure injection showed `TurnLifecycleManager.cancel_all()` swallowed terminal Store errors. It now propagates the first persistence error, while `RuntimeHost.close()` still stops plugin tasks and releases resources in its `finally` path.
- Turn timestamps are stored in UTC ISO format. Host leases and Turn updates use SQLite write transactions for stale-owner recovery. There is currently no Turn metadata retention or cleanup policy.

## Result

Turn lifecycle metadata is durable across process restart. A process restart does not resume an interrupted model or tool operation; an expired Host's still-running Turn is recorded as `interrupted` for later diagnosis. No automatic tool replay is introduced.
