# Trace Skill、工具与审批 Span 补充验证

## 实现范围

- ChatService 为运行中的请求建立根 Trace Context；异步调用链通过 ContextVar 继承父 Span。
- `SkillRuntimeAgentAdapter` 记录 `skill.execute` Span，包含 Skill ID、执行入口、状态、耗时和失败类型。
- `ToolExecutor` 记录 `tool.execute` 状态事件与 `tool.span` 完成 Span；批准、拒绝、取消和超时记录为 `tool.confirmation` 子 Span。
- Span 层级为请求→Skill→工具→审批；顶层显式工具请求则为请求→工具→审批。
- Trace 持久化白名单新增 `tool_id` 和 `execution_mode`。对话内容、工具参数、审批摘要和凭证仍不会写入 Trace。
- Trace 页面显示事件 Span ID 与父 Span ID；请求摘要保留本次 Trace 中最近的 Skill ID。

## 验证结果

- 后端全量测试：275 passed。
- 后端全量语句覆盖率：83%（6,180 条语句，1,051 条未覆盖）。本轮关键模块覆盖率：Trace Context 100%、ChatService 96%、Trace Store 95%、ToolExecutor 80%、Skill Runtime 适配器 78%。
- Ruff 检查与格式检查：通过，124 个文件。
- Mypy：通过，78 个源文件无问题。
- 前端 Vitest：47 项通过；ESLint、Vue 类型检查与 Vite 生产构建通过。
- 验证覆盖工具审批与工具执行的父子 Span、Skill Span、SQLite 字段白名单、Skill 摘要提取，以及页面对父 Span ID 和工具/审批事件的呈现。
- 未启动外部 MCP Server 或真实模型。剩余未覆盖路径主要在失败/取消分支及运行时适配边界；本轮新增测试用替身验证 Trace 关联与脱敏契约。
