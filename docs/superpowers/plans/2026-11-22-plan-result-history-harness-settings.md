# Plan 历史结果 Harness 配置实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Harness 页面配置新 Plan 可读取的历史成功子任务条数和总字符数，并将有界结果实际传给 Planner。

**Architecture:** 将两个限制加入统一的 `HarnessConfiguration`，复用现有 API、SQLite Store 与即时配置回调。Chat Service 从 `TaskLifecycleManager` 加载同 Session、当前 Turn 之前的有界成功结果，单独放入 `BlackboardSnapshot` 并只提供给 Planner。

**Tech Stack:** Python 3.11、Pydantic、SQLite/aiosqlite、FastAPI、Vue 3、TypeScript、pytest、Vitest。

**Spec:** `docs/superpowers/specs/2026-11-22-plan-result-history-harness-settings-design.md`

## Global Constraints

- 默认历史结果窗口为 32 条、65,536 字符；配置范围分别为 1–128 条、1–262,144 字符。
- 配置通过现有 Harness SQLite Store 持久化，并即时应用于后续请求。
- Session 隔离且排除当前 Turn；只加载成功完成并存在结果行的 TaskRun。
- 历史结果只供 Planner 参考，不进入当前 Turn 完成结果或 Task Executor 输入。
- SQLite 旧记录不因本配置删除；每条结果现有 65,536 UTF-8 字节写入上限不变。
- 不改变精确指纹缓存审批、工具治理和副作用确认。

## Review Focus

- 旧 Harness 配置缺少新字段时加载默认值，且升级后可保存、重启再读取。
- 零值和超范围配置被拒绝，运行中更新即时生效。
- 字符预算对 Unicode 正确计量，条数/字符数限制都遵守，过旧记录按时间顺序截断。
- 当前 Turn、其他 Session、失败/取消/中断及缺结果记录不进入 Planner 上下文。
- Planner 能看到历史参考，但 Blackboard 当前结果和 Task Executor 不会把它们视作已完成。

---

### Task 1: Harness 配置契约、持久化与运行时注入

**Files:** `src/hostpilot/persistence/harness_configuration.py`, `src/hostpilot/config/settings.py`, `src/hostpilot/api/app.py`; tests in `tests/unit/persistence/` and `tests/unit/api/test_app.py`.

- [x] 为默认配置、校验边界、旧配置加载及 SQLite 保存/重载编写失败测试。
- [x] 将 `plan_result_history_limit` 和 `plan_result_history_max_chars` 加入 Pydantic 配置，默认值分别为 32、65,536，边界分别为 1–128、1–262,144。
- [x] 在 API Harness 更新回调中将值应用到 `TaskLifecycleManager`，并验证下一请求读取更新后的限制。
- [x] 运行配置 Store 和 Harness API 定向测试。

### Task 2: TaskStore 字符预算和历史查询边界

**Files:** `src/hostpilot/persistence/tasks.py`, `src/hostpilot/persistence/ports.py`, `src/hostpilot/runtime/lifecycle.py`; tests in `tests/unit/persistence/test_tasks.py` and `tests/unit/runtime/test_task_lifecycle.py`.

- [x] 添加失败测试：查询遵守可配置条数和字符数；UTF-8 多字节文本按字符计量；Session/Turn/终态筛选继续生效。
- [x] 将可配置字符预算传入 TaskStore 查询，移除历史查询对固定 32 条和聚合字节数的钳制；保留单条写入的 65,536 字节限制。
- [x] 在 `TaskLifecycleManager` 保存并更新两个查询限制，向 Store 传递当前配置。
- [x] 验证候选历史按最近记录选取、返回顺序为时间正序，越过任一限制时停止追加。

### Task 3: 历史结果接入 Planner 的独立上下文

**Files:** `src/hostpilot/skills/orchestration.py`, `src/hostpilot/skills/model_plan.py`; tests in `tests/unit/skills/test_orchestration.py` and `tests/unit/skills/test_model_plan.py`.

- [x] 添加失败测试：新 Plan 收到同 Session 先前 Turn 的历史结果；历史不出现在当前 Turn 的 `results` 或 Executor 输入中。
- [x] 在新 Plan 创建时按当前 Harness 限制加载历史成功结果，并将它们作为独立字段传入 `BlackboardSnapshot`。
- [x] 更新模型 Planner 输入，明确历史结果仅供参考，不代表当前 Plan 已执行或完成。
- [x] 验证缓存审批的精确指纹查找仍独立工作，历史上下文不可用时遵循现有失败处理策略。

### Task 4: Harness 页面设置与前端回归

**Files:** `frontend/src/api/harness.ts`, `frontend/src/HarnessSettingsPanel.vue`, `frontend/src/HarnessSettingsPanel.test.ts`, `frontend/src/App.test.ts`.

- [x] 为配置类型、表单默认值、初始加载与保存请求编写失败测试。
- [x] 在“Plan 子 Agent”分组新增最近历史成功结果条数和总字符数输入，表明它们控制 Planner 可见的会话历史。
- [x] 设置数值范围、可访问标签和字段帮助说明，并保留现有完整配置保存行为。
- [x] 运行 Harness 面板与相关 App 前端测试。

### Task 5: 文档与验证

**Files:** `docs/新架构文档.md`, `docs/roadmap.md`, `docs/testing-catalog.md`, `docs/testing-metrics.md`, dated `docs/test-results/` report.

- [x] 记录配置默认值、持久化行为、Planner 历史上下文语义及 SQLite 留存边界。
- [x] 运行 Python 全量测试、Ruff、mypy、前端全量测试、ESLint、TypeScript 检查、生产构建及 `git diff --check`。
- [x] 汇总验证结果、限制与失败/修复记录；不提交、不推送。
