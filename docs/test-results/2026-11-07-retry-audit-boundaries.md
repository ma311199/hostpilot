# Stage F：有限重规划与 Audit 边界验证

## 范围

核对 Plan fallback 的有限重规划和失败任务去重，并明确 MCP 工具调用没有自动重试。补齐 ToolExecutor Audit 事件与当前请求 Trace 的关联，验证审计字段不包含原始参数。

## 实现与安全检查点

- `OrchestratorPlanExecutor` 默认最多重规划 2 次；已完成的等价任务复用结果，相同签名的失败任务在没有新信息时不重复执行。
- MCP 工具调用直接经过 ToolExecutor；当前没有调用层自动重试。超时或 transport 错误可能意味着远端副作用状态不确定，因此不自动重放。
- ToolExecutor 在活动 Trace 上生成 AuditEvent 时写入其 `trace_id`；无 Trace 上下文时该字段为空。
- AuditEvent 只包含 action、结果、资源类型/标识、原因码和 trace ID；Trace 事件通过现有白名单保存工具 ID、状态与原因码，不保存参数或内容。

## 验证

- 定向测试：`.venv/bin/pytest -q tests/unit/tools/test_executor.py tests/unit/tools/test_mcp.py tests/unit/skills/test_orchestration.py`，47 项通过。
- 后端全量测试：`.venv/bin/pytest -q tests`，298 项通过，0 项失败；有 1 条第三方 Authlib 弃用警告。
- Ruff 检查和格式检查通过；`.venv/bin/mypy src/hostpilot` 检查 80 个源文件通过。
- 前端测试：`pnpm test`，13 个测试文件、52 项通过；`pnpm lint` 与 `pnpm build` 通过。
- 回归验证同一失败 Plan 任务不会再次执行；超时 MCP transport 只调用一次并收到取消；Audit trace ID 与请求相同，私有参数不出现在 Audit/Trace 数据中。

## 仍需完善

Audit 当前通过结构化日志输出；工具 Audit 元数据也进入 SQLite Trace 白名单，但尚无独立 Audit 查询 API、保留策略和导出流程。远端 Server 对取消的响应以及失败后的副作用状态也需要真实 Server 集成验证。
