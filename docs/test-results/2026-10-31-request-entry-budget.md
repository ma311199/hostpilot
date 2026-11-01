# 请求入口与预算首批验证记录

- 日期：2026-10-31
- 范围：可配置请求字符上限、请求超时、BudgetContext 传递、RequestBudget 计量契约及保守 direct Router

## 实现结果

- `ChatRequest` 接受上限由应用配置控制，超限返回 HTTP 413。
- 单请求超时覆盖上下文装配和 Agent 调用，超时返回 HTTP 504。
- `ContextSnapshot` 携带请求预算上限；工具调用和检索调用由 `RequestBudget` 在执行前预留额度，现已由 Tool Executor 使用。
- 默认 Router 仅选择 direct；尚未接入的 tool_call / plan_execute 路由会明确返回 HTTP 501。
- 注入的 clarify 决策会将用户输入和澄清回复追加到 LangGraph 会话检查点，不调用模型。
- 新增设置均使用 `HOSTPILOT_` 环境变量前缀；未增加依赖。

## 验证结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 完整测试 | `.venv/bin/pytest -q` | 67 passed |
| Ruff lint | `.venv/bin/ruff check src/hostpilot tests` | 通过 |
| Ruff 格式 | `.venv/bin/ruff format --check src/hostpilot tests` | 56 个文件格式正确 |
| 类型检查 | `.venv/bin/mypy src/hostpilot` | 40 个源文件无问题 |
| 锁文件一致性 | `uv lock --check` | 解析 117 个包 |

本次未采集覆盖率，因此不报告覆盖率百分比。

## 后续缺口

- Agent Runtime 尚未回报模型 Token 实际用量；预算上下文已传入，但模型 Token 的逐步计量仍需接入运行时。
- 模型 Token 的实际计量仍需接入运行时。
- Origin 校验目前允许无 Origin 的本机写请求；工具副作用 API 接入前需结合策略与确认流程复核该边界。
