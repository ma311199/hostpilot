# Runtime Plan 持久恢复与工具幂等实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 用户确认后，从 SQLite 恢复被 Host/进程中断的 Plan，并依据工具副作用分类、稳定幂等键和调用日志安全地继续执行。

**Architecture:** Runtime 统一持久化 `PlanRun` 快照和逐次工具调用记录。每个工具调用先写入带稳定键与参数指纹的日志，再通过现有 ToolFacade/Provider 治理链派发；Plan 重启后只有在用户确认、状态可恢复且工具策略允许时才继续。对支持幂等能力的 Provider 使用同键同请求重放，不支持或状态不确定的写调用等待用户核实。

**Tech Stack:** Python 3.11、Pydantic、SQLite/aiosqlite、FastAPI、Vue 3、TypeScript、pytest、Vitest。

**Spec:** `docs/superpowers/specs/2026-11-22-runtime-plan-resume-idempotency-design.md`

## Global Constraints

- 恢复入口必须由用户明确确认；Host 启动只标记中断，不派发恢复任务。
- 只有 `interrupted` 或等待人工核实的 PlanRun 可恢复；正常结束为 `failed` 的 PlanRun 保持终态。
- 每次工具调用在派发前必须成功持久化逻辑调用记录；记录失败时不得派发。
- 幂等键由 `plan_run_id`、任务 ID、任务内逻辑调用序号、工具 ID 和可用业务标识稳定生成；不得依赖新 Turn ID 或随机调用 ID。
- 重放必须匹配原请求指纹并处于工具声明的幂等有效期内；未知/未声明能力不得自动重放。
- 纯读可安全重试；非幂等和未知写在没有有效工具端幂等保证时必须人工核实。
- 恢复使用新的 RequestContext、预算和当前 Policy/授权，已完成任务不重复执行。
- 重放所需快照和工具参数只存本地 SQLite，沿用数据库权限、随 Session 删除；不得写入 Trace/Audit；不宣称静态加密。
- 不改变 HTTP/WebSocket 已有消息兼容性、精确成功结果缓存审批、模型凭证管理或工具副作用确认边界。
- 保留工作区已有未提交 Harness 设置改动；本计划不提交、不推送。
- 不运行真实模型评测；需要密钥的模型质量测试由用户手动运行。

## Review Focus

- 多 ASGI worker 同时恢复同一 PlanRun 时只能有一个获得有效租约并派发。
- 故障发生在预写入之前与预写入之后必须有不同恢复结论；记录存在不能被误当成调用从未发出。
- 相同幂等键对应的请求参数必须一致；键过期、参数指纹变化、能力声明缺失或未知时禁止自动派发。
- 并行 Plan wave 中部分任务已完成、部分任务运行中断时，只恢复未完成且可安全执行的任务，保持依赖结果和完成输出。
- 人工“已执行”核实输出标为 `user_reported`，不伪称由工具验证；无输出不能标记成功。
- Session 删除必须删除 Plan 快照及待恢复调用数据；Trace/Audit 不包含参数、键或结果正文。

---

### Task 1: PlanRun 与工具调用的持久化契约

**Files:** `src/hostpilot/persistence/ports.py`, `src/hostpilot/persistence/tasks.py`, `src/hostpilot/persistence/__init__.py`, `src/hostpilot/task_results.py`, `src/hostpilot/runtime/lifecycle.py`; tests in `tests/unit/persistence/test_tasks.py` and `tests/unit/runtime/test_task_lifecycle.py`.

- [x] 先为 PlanRun 创建/更新/读取、调用记录状态变化、SQLite 重开、Session 隔离、参数指纹和原子恢复认领编写失败测试。
- [x] 新增 framework-neutral `PlanRunSnapshot`、`ToolInvocationRecord` 和相应 Store Port；明确字段校验、状态版本及 allowed transitions。
- [x] 扩展 SQLite TaskStore（或同一 state store）创建 PlanRun、保存版本化 DAG/Blackboard 快照、追加 prepared 调用、完成调用及原子认领恢复者。
- [x] 新旧 schema 均可启动；旧 TaskRun 不伪造 PlanRun，可通过现有旧路径展示但不可恢复。
- [x] 限制快照和待执行参数仅按 Session 保存，并在 Session 清理时一致删除；确认 Trace/Audit 序列化不读取这些字段。
- [x] 运行 TaskStore 与 TaskLifecycleManager 定向测试。

### Task 2: 工具重试分类、幂等键与派发日志

**Files:** `src/hostpilot/contracts/models.py`, `src/hostpilot/runtime/execution.py`, `src/hostpilot/runtime/adapters.py`, `src/hostpilot/runtime/lifecycle.py`, `src/hostpilot/tools/facade.py`, `src/hostpilot/tools/governance.py`, `src/hostpilot/tools/mcp.py`, `src/hostpilot/persistence/tool_outcomes.py`, `src/hostpilot/persistence/ports.py`; tests in `tests/unit/runtime/test_execution_loop.py`, `tests/unit/runtime/test_tool_outcome_lifecycle.py`, `tests/unit/tools/test_facade.py`, `tests/unit/tools/test_mcp.py`.

- [x] 先为纯读重试、幂等写同键/同指纹重放、同键不同指纹拒绝、期限过期、能力未声明转人工、日志写入失败时零派发编写失败测试。
- [x] 在工具契约中独立表示副作用/重试类别和 Provider 幂等能力，不将可逆性或高影响等级错误映射成重试安全性。
- [x] 实现稳定逻辑调用 ID/幂等键与规范 JSON 参数指纹；从 PlanRun ID、Task ID、任务内序号、工具 ID 和工具声明业务字段生成，恢复时读取并重用既有记录。
- [x] 扩展 Runtime Tool Invocation Port，在工具调用进入 ToolFacade/Provider 前写入 `prepared`；持久化成功后才允许派发，并在结果持久化后更新终态。
- [x] 让当前 Runtime Chat 与 Plan 工具调用路径都使用同一调用账本；通用 MCP/STDIO 默认不声明幂等支持，只有显式适配的 Provider 才能接收重放键。
- [x] 按当前 Policy、预算和用户确认重新治理恢复调用；幂等键和原始参数不得进入模型可见描述、Trace 或 Audit。
- [x] 运行 Runtime、ToolFacade 与 MCP 定向测试。

### Task 3: OrchestratorPlanExecutor 快照与恢复执行

**Files:** `src/hostpilot/skills/orchestration.py`, `src/hostpilot/skills/model_plan.py`, `src/hostpilot/runtime/context.py`, `src/hostpilot/runtime/lifecycle.py`; tests in `tests/unit/skills/test_orchestration.py`, `tests/unit/skills/test_model_plan.py`, and `tests/unit/runtime/test_task_lifecycle.py`.

- [x] 先为计划验证后存快照、波次/任务状态持久化、已完成任务不重跑、部分并行波次恢复、待决调用按分类重试/暂停编写失败测试。
- [x] Plan 创建时生成稳定 `plan_run_id`，在执行前持久化已验证 DAG、原始目标、稳定任务 ID、依赖和版本化初始 Blackboard。
- [x] 每个 wave/task 状态变更和结果先持久化再推进依赖；保存每个任务内逻辑调用序号及可供恢复的精确调用数据。
- [x] 为恢复创建入口加载 PlanRun 快照，校验版本、Session、DAG/输入指纹、当前授权及当前 Harness 限制。
- [x] 恢复时保留 `plan_run_id`，创建新的 Turn/RequestContext；跳过已完成状态，从最早未完成安全边界继续，并按 Task 2 调用日志处理未决工具。
- [x] 失败终态不恢复；未知调用保持 PlanRun 暂停，未解决前不得调度依赖步骤或 Planner 自动重规划。
- [x] 运行 Plan/Orchestrator/TaskLifecycle 定向测试。

### Task 4: 用户确认恢复与人工核实 API/UI

**Files:** `src/hostpilot/api/app.py`, `frontend/src/api/chat.ts`, `frontend/src/ChatPanel.vue`, `frontend/src/ChatPanel.test.ts`, `frontend/src/api/chat.test.ts`, `frontend/src/api/chatSocket.ts`, `frontend/src/api/chatSocket.test.ts`, `frontend/src/style.css`; tests in `tests/unit/api/test_app.py`.

- [x] 先为可恢复 Plan 查询、用户确认前零派发、并发认领拒绝、仅允许当前 Session 恢复和人工核实 enum/result 验证编写失败测试。
- [x] 提供按 Session 查询可恢复 PlanRun 的只读 API，以及明确恢复 API；恢复响应可区分成功认领、已完成、已失败、已被其他 Host 认领和需人工核实。
- [x] 在 ChatPanel 显示被中断 Plan 的目标、任务状态、已完成结果和未决调用；仅由明确按钮/确认动作调用恢复 API。
- [x] 人工核实支持“已执行并提供结果”与“未执行”；拒绝空成功结果，存储 `user_reported` 来源，未执行时仍需当前工具 Policy/确认门。
- [x] 恢复/核实失败、过期键、请求指纹变化和能力不支持均提供明确暂停原因，不显示密钥或将参数/结果写入 Trace。
- [x] 运行 API/WebSocket/ChatPanel 前端定向测试。

### Task 5: 迁移文档、测试报告与整体验证

**Files:** `docs/新架构文档.md`, `docs/roadmap.md`, `docs/decisions/0034-runtime-core-and-plugin-lifecycle.md`, `docs/testing-catalog.md`, `docs/testing-metrics.md`, dated `docs/test-results/` report.

- [x] 记录 PlanRun 恢复语义、幂等能力声明、失败关闭边界、旧记录兼容和 SQLite 数据留存/删除策略。
- [x] 运行 Python 全量测试、Ruff、mypy、前端全量测试、ESLint、TypeScript 检查、生产构建及 `git diff --check`。
- [x] 运行无需真实模型密钥的 Plan/Provider 故障注入集成测试，覆盖派发前后崩溃窗口、幂等重放和人工核实；不运行真实模型评测。
- [x] 汇总验证结果、限制、发现的问题和修复记录；不提交、不推送。

## 验收标准

- Plan 可从 SQLite 快照跨 Host 重启恢复，必须由用户确认后才派发。
- 写前日志缺失的逻辑调用可在确认后继续；有记录但结果缺失时，只有纯读或具备有效工具端幂等保证的写入能安全重试。
- 同一操作重放使用相同幂等键与请求指纹；任何不确定情形均阻断并等待用户核实。
- 已完成任务和结果不会重复执行；未决任务不能提前满足依赖或进入 Verifier。
- 当前用户核实、预算、授权、Policy、审批、Session 隔离、删除清理和敏感字段边界均通过回归测试。
