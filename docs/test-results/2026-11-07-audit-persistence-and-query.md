# Audit 本地留存与查询

## 实现范围

- 增加 `SQLiteAuditStore`，和 Trace 共用 HostPilot SQLite 数据库；默认保留 90 天、最多 100,000 条，数据库目录和文件权限分别设为 `0700` 与 `0600`。
- Audit 事件继续经结构化日志输出，并由组合 Sink 同步写入 SQLite。MCP 工具执行和记忆提案审批使用同一 Sink。
- 新增只读 `GET /api/v1/observability/audit`，支持 `limit`、`trace_id`、`since`、`until` 参数，按时间倒序返回声明过的 Audit 字段。
- 查询可用 Trace ID 关联工具动作；没有 Trace 上下文的事件仍可按时间查询。

## 安全和边界

- 持久化只写 AuditEvent 的有限字段，不接受事件载荷、工具参数或凭证字段。
- 时间过滤必须包含时区；反向时间区间返回 422。每页最多查询 1,000 条。
- 当前没有前端 Audit 页面和导出功能；需要时可在既有 Audit API 上增加独立交互与导出格式。

## 验证

- 定向 API 与持久化测试：50 项通过。
- 后端全量测试：302 项通过，0 项失败；1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查 81 个源文件通过。

## 后续

- 结合实际运维场景确定 Audit 页面与导出需求。
- 对 SQLite 并发写入和长期数据保留策略进行运行期观察。
