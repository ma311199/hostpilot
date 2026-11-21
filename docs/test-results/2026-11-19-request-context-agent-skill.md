# RequestContext Agent 与 Skill 传递验证

- 日期：2026-11-19
- 范围：将同一个 Runtime `RequestContext` 从 ChatService 传递至 ChatAgentRuntime、Skill Selector、SkillEngine、Plan fallback 和 DeepAgents 结果评估；同步 Chat Runtime 插件 API 与架构文档。
- 代码版本：当前工作区未提交改动；没有独立提交号。
- 环境：Python 3.11、pytest、Ruff、mypy。
- 数据集：不适用；本轮为接口身份、执行链和兼容边界自动化测试。

## 改动和覆盖边界

- Chat Runtime `respond()` 与 DeepAgents 入口接收 `RequestContext`；插件 API 从 1.0 升到 2.0，注册表拒绝旧版本。
- Skill Selector、DeepAgents 结果评估和 `SkillExecutionRequest` 使用同一个请求对象；Skill fallback 到 Plan 时继续沿用对象身份。
- LangGraph 将请求对象适配成既有工具 Mapping 键 `snapshot`、`budget`、`confirmation_provider`；审批 Provider 缺失时保留 `None`。
- 模型与工具 Port 仍使用已有参数形式，在边界读取 RequestContext 字段；HTTP/WebSocket、预算和审批行为未改变。

## 失败原因与修复

- 首轮全量 pytest：**505 passed、3 failed**。失败均位于 `tests/integration/test_contextual_reference_resolution.py`，该文件仍以旧形式 `selector.select(message, context, budget=...)` 调用 Selector；新接口要求 `RequestContext`。栈追踪和同模块新用例确认生产代码路径正常，根因是集成测试没有随接口迁移更新。
- 将三个集成测试改为传入 `RequestContext(context, request_budget())` 后，该文件 **3 passed**，最终全量回归通过。
- 首轮 Ruff 发现相关改动文件的导入排序、格式和尾随空格问题；已整理相关 Python 文件，并清理其余已迁移测试文件的 Ruff 导入问题。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| RequestContext/Agent/Skill 定向 pytest | 185 passed，1 条 FastMCP/Authlib 弃用警告 |
| `.venv/bin/pytest -q`（最终运行） | 508 passed，1 条 FastMCP/Authlib 弃用警告 |
| `.venv/bin/mypy src/hostpilot` | Success，116 个源码文件无问题 |
| `.venv/bin/ruff check src tests scripts` | All checks passed |
| `.venv/bin/ruff format --check src tests scripts` | 205 files already formatted |
| `git diff --check` | 通过 |

## 指标与限制

- 功能测试通过率：最终 Python 测试 508/508；通过数不代表模型质量指标。
- 本轮未测模型质量、覆盖率、运行性能或生产插件兼容性。
- 插件 API 1.0 现被明确拒绝；自定义 Chat Runtime 插件需按 2.0 实现 `respond(message, request_context)`。
- Agent/Session 持久化恢复和通用 `Context → Model → Tool/Skill` 执行循环仍未迁入 Runtime Core。
