# Runtime Tool Execution Scope Implementation Plan

> Execute inline in the current shared checkout under TDD. Preserve existing workspace changes and do not commit.

**Goal:** Make MCP Provider use Runtime-created execution scopes rather than concrete ToolExecutor instances.

**Architecture:** Change `ToolExecutorFactory` to create `ToolExecutionScope` objects. The default scope wraps current `ToolExecutor`; provider-specific registry and transport behavior remain in MCP Provider.

**Spec:** `docs/superpowers/specs/2026-11-18-runtime-tool-execution-scope-design.md`

## Global constraints

- No behavior changes to authorization, confirmation, budgets, Audit/Trace, verification, remote timeouts, or compensation.
- Keep distinct per-provider primary and compensation registries.
- Candidate scope creation stays isolated until the existing provider commit lock.
- Direct builders continue to use the default factory.

## Task 1: Provider-scoped Runtime execution contract

**Files:**
- Modify: `src/hostpilot/tools/governance.py`
- Modify: `src/hostpilot/tools/mcp.py`
- Modify: `src/hostpilot/tools/__init__.py`
- Modify: `tests/unit/tools/test_governance.py`
- Modify: `tests/unit/tools/test_mcp.py`
- Modify: `tests/unit/api/test_app.py`
- Update: `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`
- Update: `docs/新架构文档.md`
- Update: `docs/roadmap.md`
- Update: `docs/test-results/2026-11-18-runtime-turn-lifecycle.md`
- Update: `docs/testing-catalog.md`
- Update: `docs/testing-metrics.md`

Steps:

1. Add failing tests for the scope contract and injected-scope use by local/intercepted/remote/compensation provider paths.
2. Run them and verify the expected missing-scope failures.
3. Implement `ToolExecutionScope` and change the factory to return scopes wrapping `ToolExecutor`.
4. Replace concrete executor references in `MCPToolProvider` with primary and compensation scopes; maintain a provider-owned compensation registry.
5. Verify all local, remote, intercepted, and compensation call paths with recording scopes and current integration tests.
6. Update API integration assertions so initial and candidate providers receive Runtime-created scopes while the facade remains stable.
7. Update ADR, architecture, roadmap, test catalog, metrics, and validation report.
8. Run targeted Ruff/mypy, focused tests, full pytest, and `git diff --check`; record the known sandbox failure if it remains.

## Review focus

- No production path accesses or exposes the wrapped concrete `ToolExecutor`.
- Compensation registration targets only the compensation registry.
- Remote timeout translation still surrounds execution through the scope.
- Custom scopes can replace execution without changing MCP Provider transport routing.
