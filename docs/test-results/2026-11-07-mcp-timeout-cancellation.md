# Stage F：MCP 超时与取消清理验证

## 范围

为 MCP Provider 的工具发现、Server 启动和连接清理补上有界等待，并验证请求超时时 transport 等待会收到取消。既有 Tool Executor 继续使用 Server timeout 与请求剩余预算中较小者作为工具调用时限。

## 实现与安全检查点

- 内置工具 schema/适配发现最多等待 15 秒；超时后启动失败并关闭本地 MCP 会话。
- 远程 Server 建连、`list_tools` 和 LangChain 工具适配均受该 Server 的 `timeout_seconds` 限制；超时仅将对应 Server 置为失败，不阻断其他 Server。
- 远程工具适配未完成或启动被取消时关闭当前 Server；顶层启动取消还会关闭之前已经连接的 Server。
- 本地和远程 MCP 会话关闭最多等待 5 秒；远程关闭失败会记录脱敏错误类型并继续清理其他连接。
- 工具执行超时会取消 HostPilot 等待的 transport 调用；工具调用审计记录 `tool_timeout`，显式请求取消记录 `execution_cancelled`。

## 验证

- MCP 与 Tool Executor 定向测试：`.venv/bin/pytest -q tests/unit/tools/test_mcp.py tests/unit/tools/test_executor.py`，34 项通过。
- 后端全量测试：`.venv/bin/pytest -q tests`，298 项通过，0 项失败；有 1 条第三方 Authlib 弃用警告。
- Ruff 检查和相关文件格式检查通过；`.venv/bin/mypy src/hostpilot` 检查 80 个源文件通过。
- 前端测试：`pnpm test`，13 个测试文件、52 项通过；`pnpm lint` 与 `pnpm build` 通过。
- 覆盖本地发现超时、远端工具适配超时、启动中取消并清理多个 Server、有界关闭和工具调用超时后的 transport 取消。
- 使用内存/Fake Server 验证，未连接真实远程 MCP Server。

## 限制

客户端取消请求只代表 HostPilot 停止等待并向 transport 传递取消。MCP 协议和远端 Server 未必能停止已开始的操作，副作用结果可能不确定；当前不会自动重试或补偿远端操作。
