# Trace 持久化与观测页面验证

## 实现范围

- 新增 SQLite Trace Store，按请求 `trace_id` 关联事件，最多保存 30 天或 100,000 个事件。
- 入库时只保留状态、原因类型、计数、耗时、路由、Skill ID、工具 ID、执行入口和 Token 数等字段；对话内容和其他未列入白名单的属性不会持久化。
- 聊天完成时写入路由、耗时和实际 Token 数；Memory Controller 的召回事件写入状态、耗时和片段统计。
- 新增本地 Trace 列表与详情 API，以及 Vue 导航中的 Trace 页面。
- 本记录验证首版聊天与记忆事件。Skill、工具和审批 Span 后续补齐，见 [Span 补充验证](2026-11-07-trace-skill-tool-approval-spans.md)。不持久化独立 Cost/Audit 流。

## 验证结果

| 验证 | 结果 |
| --- | --- |
| 后端全量测试 | 264 passed |
| 后端 Ruff 检查与格式检查 | 通过，121 个文件 |
| 后端 mypy | 通过，76 个源文件 |
| 前端 Vitest | 44 passed，12 个测试文件 |
| 前端 ESLint | 通过 |
| 前端类型检查 | 通过 |
| 前端生产构建 | 通过 |

后端测试期间出现锁定 FastMCP 依赖链的 Authlib `authlib.jose` 弃用警告，不影响结果。未采集覆盖率。
