# Runtime Tool Executor Factory Implementation Plan

> Execute inline in the current shared checkout under TDD. Preserve existing workspace changes and do not commit.

**Goal:** Move PolicyEngine and ToolExecutor construction behind a Runtime-registered governance factory while keeping provider-specific registries, transports, and resource ownership in MCP Provider.

**Architecture:** `builtin.tool-governance` registers a factory service. `MCPToolProvider` receives that factory and creates primary and compensation executors over its own registries. FastAPI resolves and injects the Runtime service into initial and candidate provider assembly. Direct builders retain a default factory.

**Spec:** `docs/superpowers/specs/2026-11-18-runtime-tool-executor-factory-design.md`

## Global constraints

- Preserve authorization, confirmation, budget, timeout, Audit/Trace, result verification, and compensation behavior.
- Keep provider registries, MCP sessions, handlers, and resources owned by `MCPToolProvider`/`MCPProviderHostPlugin`.
- Use existing request locking for candidate Runtime/Provider commits.
- Do not implement full execution dispatch migration or Sandbox lifecycle migration in this slice.

## Task 1: Runtime-owned ToolExecutor construction seam

**Files:**
- Create: `src/hostpilot/tools/governance.py`
- Modify: `src/hostpilot/orchestration/tool_governance_host_plugin.py`
- Modify: `src/hostpilot/tools/mcp.py`
- Modify: `src/hostpilot/api/app.py`
- Test: `tests/unit/tools/test_governance.py`
- Test: `tests/unit/tools/test_mcp.py`
- Test: `tests/unit/orchestration/test_tool_governance_host_plugin.py`
- Test: `tests/unit/api/test_app.py`
- Update: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Update: `docs/新架构文档.md`
- Update: `docs/roadmap.md`
- Update: `docs/test-results/2026-11-18-runtime-turn-lifecycle.md`
- Update: `docs/testing-catalog.md`
- Update: `docs/testing-metrics.md`

Steps:

1. Write unit tests for default and Runtime factory executor construction; assert registry, sandbox policy, audit and trace wiring.
2. Run tests and confirm they fail because the factory contract/service is missing.
3. Implement `ToolExecutorFactory` and its default implementation; expose it as `hostpilot.tool-governance` from `ToolGovernanceHostPlugin`.
4. Inject the factory into `MCPToolProvider`, preserving separate primary and compensation registries and current policy settings; keep the builder's direct-call default.
5. Add tests for provider use of injected executors across local, remote, and compensation paths; verify RED before implementation.
6. Inject the Runtime service in FastAPI initial and candidate Provider builders. Verify through API tests that the registered service reaches each committed provider while the facade remains stable.
7. Update architecture and test metrics/report.
8. Run targeted Ruff, mypy, focused pytest, full pytest, and `git diff --check`; record any known failures with their existing diagnosis.

## Review focus

- Compensation registry includes only the restore tool and cannot be confused with the public registry.
- Runtime and direct provider paths use the same policy/executor configuration.
- Candidate provider creation cannot replace active Runtime service state before commit.
- No provider resource is closed by the Runtime executor factory.
