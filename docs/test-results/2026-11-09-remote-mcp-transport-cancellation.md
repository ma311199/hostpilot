# 真实 Streamable HTTP MCP 超时与远端执行状态

## 范围

验证 HostPilot 对远程 MCP 工具调用施加 Server 超时后，真实 HTTP transport 的断开行为是否会终止远端 FastMCP 工具任务。

## 测试方式

- 集成测试启动真实 Uvicorn 和 FastMCP Streamable HTTP Server，使用本机临时 TCP 端口，不访问外网。
- HostPilot 通过真实 `MCPToolProvider` 连接 Server，发现工具后由 `ToolExecutor` 执行，审批测试 Provider 明确批准调用。
- Server 的工具等待测试释放事件；HostPilot 的 Server 调用超时设为 0.4 秒，断言调用方收到超时并停止等待。
- 超时后检查服务端任务状态，再释放它以保证测试清理完整。

## 结果

- `.venv/bin/pytest -q tests/integration/test_mcp_transport_cancellation.py`：1 项通过。
- `.venv/bin/ruff check tests/integration/test_mcp_transport_cancellation.py`：通过。
- `.venv/bin/ruff format --check tests/integration/test_mcp_transport_cancellation.py`：通过。
- 完整后端测试 `.venv/bin/pytest -q tests`：353 项通过，0 项失败；1 条第三方 Authlib 弃用警告。
- 前端 Vitest：17 个文件、67 项通过；`pnpm typecheck` 和 `pnpm lint` 通过。
- `.venv/bin/mypy src/hostpilot`：92 个源文件通过；`git diff --check` 通过。
- 本次验证中，超时后服务端工具仍在运行，未收到 `CancelledError`；它在测试显式释放后才结束。HostPilot 端记录 `tool_timeout`，MCP Server 连接仍保持可用。
- 远程工具超时现在通过专用错误类型映射为 `remote_tool_outcome_unknown`，聊天 UI 会提示先检查目标系统状态再决定是否重试；不会把远端操作描述成已取消。
- WebSocket 错误码映射回归、远程 Provider 集成测试与聊天错误展示测试均已通过定向质量检查。

## 结论与边界

Streamable HTTP 请求超时能停止 HostPilot 的等待，但不能作为远程 MCP 操作已停止或没有副作用的证据。结果应视为未知；不得因超时自动重试或声称操作已回滚。远端 Server 可能采用不同的断开处理方式，本测试只覆盖当前 FastMCP 2.14.7 Server/SDK 组合，不代表所有 MCP 实现。
