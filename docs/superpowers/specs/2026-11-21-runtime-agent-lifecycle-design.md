# Runtime Agent Lifecycle Design

## Intent

Make the Runtime own the lifecycle of the active Chat Agent instance. A chat Turn must use one Agent instance from context loading through response persistence, and a replaced Agent must stay alive until all Turns using it finish.

## Current state

- `ChatAgentRuntimeHostPlugin` stores one runtime, swaps it during configuration updates, and immediately closes the previous instance.
- `ChatService.send()` calls `ContextBuilder`, `respond()`, and `record_turn()` through separate references without holding an Agent lease across the Turn.
- A configuration refresh can therefore close an Agent while an in-flight Turn is still using it, or mix one Turn across old and new Agent instances.

## Goals

- Add a Runtime-owned lifecycle manager for the active Chat Agent instance.
- Lease one Agent for the full Turn, including context history loading and final message recording.
- Publish a replacement atomically so new Turns use it immediately; close the previous Agent only after its existing leases drain.
- Reject new leases after Host shutdown begins and close the current Agent after active Turns have been cancelled and released.
- Keep Agent framework construction and provider-specific behavior inside existing factories/plugins.

## Non-goals

- Per-session Agent instances or persisted Agent state.
- Changes to Session/Conversation/Turn schemas, API payloads, HTTP/WebSocket contracts, model/tool behavior, or framework selection.
- Cross-process Agent sharing; each ASGI worker owns its own Host and Agent instances.

## Approach

Use an `AgentLifecycleManager` in `hostpilot.runtime` that holds a current Agent and an active-use count. Acquiring a lease increments the selected generation's count. Replacement publishes a new generation first, then waits for the old generation's count to reach zero before closing it. Host close prevents new leases, waits for the current generation to drain, then closes it. A replacement lock serializes replace/close operations while the lease condition remains available to request completion.

This preserves the existing plugin/factory boundary and makes the lifecycle guarantee apply to the entire ChatService Turn. Immediate close would break in-flight requests; constructing one Agent per Turn would duplicate expensive model/graph resources and bypass current Host-level provider reuse.

## Acceptance

- A Turn holds one Agent generation for context loading, response generation, and turn recording.
- Replacing an Agent immediately routes new Turns to the replacement while the old Agent remains open for existing Turns.
- The old Agent closes exactly once after its last lease releases.
- Host shutdown rejects new leases and closes the active Agent after active leases release.
- Existing raw `ChatAgentRuntime` injection for tests and standalone ChatService callers remains supported.
