# Stage D：MCP 连接状态 API 验证

- 日期：2026-11-07
- 范围：内置 MCP 与远程 Streamable HTTP Server 的生命周期状态和只读 API
- 新增运行依赖：无

## 实现

- 增加 `ToolConnectionStatus` 合约，返回连接 ID、transport、生命周期状态、发现工具数和错误码。
- MCP Provider 分别跟踪内置进程内连接及每个远程 Server；连接和工具发现成功后报告 `connected`，初始化错误报告 `failed`，正常关闭报告 `disconnected`。
- 新增 `GET /api/v1/mcp/connections`。响应不包含 endpoint URL、HTTP 请求头、凭证或底层异常文本。错误只公开异常类别名。
- Model 配置候选启动失败后，保留该候选的安全连接状态，便于调用方读取失败 Server 的诊断。
- 连接 ID `builtin/local` 用于内置 Server，远程 ID 使用配置名；这样远程 Server 名称即使为 `builtin` 也不会和内置连接冲突。

## 验证

- 后端全量测试：**230 passed**。
- MCP/API 定向测试：**40 passed**。
- Ruff lint：通过。
- Mypy（`src/hostpilot`）：通过，检查 71 个源文件。
- 前端 Vitest：**15 passed**；ESLint：通过；`vue-tsc` 类型检查和 Vite production build：通过。
- `git diff --check`：通过。
- 测试期间出现一个来自锁定 FastMCP 依赖的 Authlib 弃用警告；未影响测试结果。未采集覆盖率。

## 当前边界

- `connected` 表示最近一次连接与工具发现成功，不代表有周期性健康探测；运行中断线需在后续连接管理迭代中加入探测或调用失败状态更新。
- 本轮仅交付后端只读 API，前端 MCP 连接管理页仍在 Stage E 路线中。
