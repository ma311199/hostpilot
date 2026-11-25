# Stage C Runtime 集成验证实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add deterministic Chat/API integration coverage for the Runtime Chat Skill-to-Plan pipeline and document the separate manual live-model evaluation step.

**Architecture:** Use `create_app` with an injected `SkillEngineAgentAdapter`, the real YAML `SkillEngine` and temporary Skill registry, plus scripted selectors/assessors and in-memory tool providers. The tests exercise the application request path without network, model credentials, real files, or external side effects; existing live evaluation scripts remain manual.

**Tech Stack:** Python 3.11+, FastAPI `TestClient`, pytest, Runtime `RequestContext`, SkillEngine, Plan task executor, ToolFacade.

**Spec:** `docs/superpowers/specs/2026-11-24-stage-c-runtime-integration-validation-design.md`; evaluation policy: `docs/stage-c-model-evaluation.md`.

## Global Constraints

- “不改变 Runtime、SkillEngine、Plan、Policy Engine 或 Tool Executor 的生产契约，除非新增集成测试揭示具体缺陷。”
- “不调用外部模型、不读取或记录 API Key、不执行真实副作用。”
- “不调整路由阈值、评测期望标签或生产工具权限。”
- Use temporary SQLite/data/Skill directories and memory-only tool handlers; never attach real external tools.
- Keep manual model-evaluation instructions separate from deterministic test results; do not report live-model quality without user-run output.
- Preserve all existing uncommitted Runtime/Sandbox/Turn Recovery work; use the existing shared workspace and do not commit or push.

## Review Focus

- Request context or budget is replaced between ChatService, selector, SkillEngine, Plan fallback, and tool execution: assert the same request context/budget reaches each boundary.
- A selected YAML Skill failure is rendered as a normal success or loses its failure reason/candidate IDs: assert Plan handoff and final Chat route explicitly.
- A task tool outside the authorized descriptors reaches the tool provider: assert retrieval and execution remain constrained by the declared allowlist.
- A tool invocation bypasses the ToolFacade/confirmation capability or triggers a real side effect: use only an in-memory governed provider and assert the handler/confirmation boundary.
- A fixture accidentally depends on credentials, network, or installed model configuration: run it with an empty model configuration and assert all calls are scripted/local.

---

### Task 1: Cover a selected YAML Skill through the Chat API

**Files:**
- Create: `tests/integration/test_runtime_chat_skill_plan_pipeline.py`
- Reference: `tests/integration/test_contextual_reference_conversation.py`
- Reference: `tests/unit/skills/test_skill_runtime.py`

**Interfaces:**
- Consumes `create_app(settings, chat_agent_runtime_factory=...)`, `SkillEngineAgentAdapter.respond(message, request_context)`, `SkillEngine.execute(SkillExecutionRequest)`, and the existing test fixture Skill builder.
- Produces deterministic integration tests proving a selected YAML Skill uses the real YAML registry/engine, a selected Markdown Skill uses only the Markdown adapter, both receive the ChatService request context, and the API returns/persists the expected response/history.

- [x] **Step 1: Add `test_selected_yaml_skill_runs_through_chat_api_and_persists_context` first** using a temporary YAML Skill, scripted selector, real SkillEngine, and local action/assessor. Assert API status/route/response, persisted history, selected Skill ID, and identity of RequestContext passed from selector to SkillEngine/action.
- [x] **Step 2: Add `test_selected_markdown_skill_uses_only_markdown_adapter_through_chat_api`** using the same API fixture and a Chat Runtime fake with a Markdown Skill hook. Assert the Markdown hook receives the same RequestContext, the YAML SkillEngine is not invoked, and the response is persisted.
- [x] **Step 3: Run the two single tests** with `.venv/bin/pytest -q tests/integration/test_runtime_chat_skill_plan_pipeline.py -k 'selected_yaml_skill or selected_markdown_skill'`; expect the real application request path to pass with no network or model configuration. This is coverage-only work: if existing behavior passes immediately, record that fact and do not mutate production code to manufacture a failure.
- [x] **Step 4: Fix only test fixture/setup defects** if tests fail; change production behavior only if an assertion demonstrates a concrete defect.
- [x] **Step 5: Run** `.venv/bin/pytest -q tests/integration/test_runtime_chat_skill_plan_pipeline.py -k 'selected_yaml_skill or selected_markdown_skill'`; expect both format journeys to pass without network access or model configuration.

### Task 2: Cover failure-to-Plan handoff and governed task tools

**Files:**
- Modify: `tests/integration/test_runtime_chat_skill_plan_pipeline.py`
- Reference: `tests/unit/orchestration/test_skill_runtime_adapter.py`
- Reference: `tests/unit/skills/test_model_plan.py`
- Reference: `tests/unit/tools/test_facade.py`

**Interfaces:**
- Consumes the Task 1 Chat/API fixture, existing Skill fallback protocol, task-level tool candidate retrieval, and `ToolFacade` provider contract.
- Produces integration coverage that a failed selected Skill enters Plan with the same RequestContext, original failure reason, and candidate Skill IDs; a tool task selects only a descriptor within its authorized candidate set; invocation reaches the in-memory governed ToolFacade provider with the current budget and confirmation provider.

- [x] **Step 1: Add `test_skill_failure_falls_back_to_governed_plan_tool_through_chat_api`** with scripted Skill failure, deterministic provider responses for the real Plan planner/task executor/verifier, and an in-memory provider backed by ToolFacade/ToolExecutor. Assert failure reason and candidate IDs reach Plan, candidate selection stays within authorized descriptors, ToolExecutor receives the current budget/confirmation capability, and no real handler runs.
- [x] **Step 2: Run the single test** with `.venv/bin/pytest -q tests/integration/test_runtime_chat_skill_plan_pipeline.py::test_skill_failure_falls_back_to_governed_plan_tool_through_chat_api`; expect all model responses and tool results to come from local scripted/in-memory fixtures. If existing behavior passes immediately, record coverage-only evidence without production mutation.
- [x] **Step 3: Fix only test fixture/setup defects** if the test fails; change production behavior only if an assertion demonstrates a concrete defect.
- [x] **Step 4: Run** `.venv/bin/pytest -q tests/integration/test_runtime_chat_skill_plan_pipeline.py`; expect both integration journeys to pass with no network/model credentials and no real side effects.

### Task 3: Record verification and manual model evaluation handoff

**Files:**
- Modify: `docs/testing-catalog.md`
- Modify: `docs/testing-metrics.md`
- Modify: `docs/roadmap.md`
- Create: `docs/test-results/2026-11-25-stage-c-runtime-integration-validation.md`

**Interfaces:**
- Consumes test outcomes from Tasks 1–2 and the existing command/options in `docs/stage-c-model-evaluation.md` / `scripts/evaluate_stage_c_runtime.py`.
- Produces an accurate verification record separating deterministic Chat/API contract coverage from live model behavior, plus a safe one-case command for the user to run locally.

- [x] **Step 1: Update the result report and metrics/catalog** with exact automated test and full quality-script counts; record real-model execution as pending until the user provides local output.
- [x] **Step 2: Include the existing manual command** for one `plan-001` scenario with output directed to `/tmp`: `.venv/bin/python scripts/evaluate_stage_c_runtime.py --case-id plan-001 --show-response --output /tmp/hostpilot-stage-c-plan-001-2026-11-25.json`; do not run it or request secrets through chat.
- [x] **Step 3: Run** `bash scripts/check_quality.sh` and `git diff --check`; expect all automated checks to pass and no model-evaluation network request to occur.

## Acceptance Criteria

- Chat/API integration tests pass using only temporary state and scripted/in-memory dependencies.
- Tests assert meaningful route, history, context/budget, failure handoff, candidate authorization, and ToolFacade boundary behavior.
- The full Python quality script and `git diff --check` pass.
- Reports distinguish deterministic Runtime integration from live model quality; the user receives a one-case command and no model-quality claim is made before their run.
- No commit or push is created; existing shared-workspace modifications remain intact.
