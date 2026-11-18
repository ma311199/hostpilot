# ADR-0016：增加本地 STDIO MCP Server

- 状态：已采纳并实现
- 日期：2026-11-07
- 替代：[ADR-0008](0008-remote-mcp-servers.md) 中“仅 Streamable HTTP”的范围；继续保留该 ADR 的工具命名、审批和治理规则。

## 背景

常见 MCP 包通过本机命令启动，并使用 STDIO 传输，例如 `npx -y @modelcontextprotocol/server-filesystem ...`。现有连接管理器只支持 Streamable HTTP，不能接入这类本地 Server。

## 决定

- 外部 MCP Server 支持 Streamable HTTP 和本地 STDIO 两种传输；内置 FastMCP Server 继续使用进程内 Client/Server 会话。
- STDIO Server 配置保存 transport、command、args、cwd、超时和密钥环境变量名等非秘密值。`command` 与每个 `args` 项作为独立参数传给子进程，不经过 Shell。
- STDIO 执行程序须匹配受支持的可执行程序 allowlist（`npx`、`node`、`uvx`、Python、`bun`、`deno`）。扩充 allowlist 应更新此 ADR 并测试。
- 子进程仅继承 PATH、HOME、临时目录、locale 和 npm cache/prefix 等运行所需环境；HostPilot 的模型、RAG 和其他 MCP 密钥不继承。显式配置的 MCP Key 只注入对应的命名环境变量，并只在后端进程内存持有。
- 子进程 stderr 丢弃到系统空设备，避免第三方 Server 将环境秘密写入 HostPilot 日志；连接错误仅公开错误类型。
- 关闭、停用、删除配置或替换 Provider 时关闭对应子进程。单个 Server 故障不阻止其他 Server 连接。
- 内置工具保持进程内连接；当前结构已可由 Harness 直接管理，迁移到单独子进程不会带来必要收益。

## 安全与运行限制

- STDIO Server 与 HostPilot 以同一操作系统用户权限运行。该机制不是沙箱；`npx` 可从 npm 下载并执行配置指定的包。用户只应配置可信命令、包和参数。
- Filesystem MCP 的可访问目录由用户配置的 args 决定。HostPilot 不会额外扩大该 Server 的文件访问范围。
- 不支持 Shell 表达式、管道、命令替换或 `sh -c`。需要多个参数时，应在 UI 中逐行填写，或在 JSON 配置中写入 `args` 数组。
- MCP 管理页可导入含 `mcpServers` 对象的 JSON：STDIO 使用 `command`/`args`/`cwd`，HTTP 使用 `url`。导入含非空 `env` 或 `headers` 的项时整体拒绝，避免凭证写入持久配置；凭证须在管理表单单独提交。
- API Key 不写入 SQLite、日志、Trace、浏览器存储或响应。应用重启后，需要密钥的 STDIO Server 等待用户重新输入。

## 验证

- 覆盖真实本地 FastMCP STDIO 子进程的启动、工具发现和退出。
- 覆盖不继承 HostPilot 环境密钥、可执行程序 allowlist、配置跨重启恢复和 API Key 不落盘。
- HTTP Server 的现有行为与 API 配置保持兼容。
