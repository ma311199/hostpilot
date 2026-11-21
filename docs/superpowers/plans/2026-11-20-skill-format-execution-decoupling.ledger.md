# Skill format / execution decoupling ledger

- Execution: inline in the current shared workspace; preserve unrelated dirty work and do not change branches.
- Existing ADR-0013 is the design authority: canonical preferences are `yaml`, `markdown`, and `both`; `deepagents` is a legacy backend name.
- Ruling: accept `deepagents` as a legacy API input alias, normalize it to `markdown`, and transactionally migrate persisted rows. This preserves existing deployments while making all newly returned and stored values format-based; cost if wrong is an extra compatibility path to maintain.
- Task 1: SQLite/API format persistence migration and alias support completed; storage/API subset passed (5 tests before the later concurrency regression was added).
- Task 2: routing and Runtime format vocabulary renamed to Markdown; classifier, adapter dispatch, result assessment, and fallback subset passed (70 tests across routing, orchestration, archive, persistence/API cases).
- Task 3: frontend values/labels and ADR, roadmap, requirements, guide, testing strategy, metrics, and report synchronized.
- Debug record: first full-suite run had 10 failures and mypy import errors because a bulk format rename changed `deepagents.backends` imports to `markdown.backends`. Restored the actual framework imports while keeping format-related names as Markdown; the previously failing adapter/API group then passed 10 tests.
- TDD: a concurrent initialization test failed because two SQLite connections both observed the table as absent; moved table inspection and migration under `BEGIN IMMEDIATE`, then the persistence suite passed 4 tests.
- Final verification: backend pytest 553 passed with one existing FastMCP/Authlib deprecation warning; frontend Vitest 78 passed; ESLint, TypeScript check/build, Ruff lint/format, mypy (123 source files), and `git diff --check` passed.
- Final review: self-review (no subagent tool/delegation); inspected SQLite transaction and data copy, API normalization, format-only selector names, executor mapping, frontend payload, and compatibility docs. No unresolved correctness findings.
