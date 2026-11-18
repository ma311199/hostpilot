# Stage F：远程 MCP 参数 Schema 验证

## 范围

远程 MCP Server 返回的工具 `inputSchema` 作为不可信数据处理。HostPilot 在注册远程工具前检查 schema，并在每次调用中由 Tool Executor 校验参数；远程工具与 YAML Skill action、Plan tool task 共用同一调用治理链。

## 实现与安全检查点

- 使用 JSON Schema Draft 2020-12 校验 Server 提供的 schema。
- schema 最多 512 个节点、嵌套深度最多 16 层；只允许已解析的本地 JSON Pointer 引用，拒绝外部引用和无效指针。
- 参数对象必须有字符串键并满足 schema；校验在 Policy Engine、用户审批和远端 handler 之前执行。
- 同一 Server 的工具先完整发现、schema 编译及 LangChain 工具装载，再一起注册。单个工具 schema 无效时，Server 不留下部分工具注册。
- 内置工具继续使用各自的强类型参数验证器，不依赖 Server 提供的 schema。

## 验证

- MCP 定向测试：`.venv/bin/pytest -q tests/unit/tools/test_mcp.py`，19 项通过。
- 后端全量测试：`.venv/bin/pytest -q tests`，291 项通过，0 项失败；有 1 条第三方 Authlib 弃用警告。
- 静态检查：`.venv/bin/ruff check src/hostpilot tests`，通过；相关文件格式检查通过。
- 类型检查：`.venv/bin/mypy src/hostpilot`，80 个源文件通过。
- 前端质量构建：`pnpm build`（含 `vue-tsc` 类型检查与 Vite 生产构建），通过。
- 覆盖了缺失/错误/额外参数、外部引用、未解析引用、超限 schema，以及一个 Server 多工具 schema 校验失败时原子拒绝。
- 未连接真实远程 MCP Server；调用集成使用内存 Server 和 fake Server。

## 后续

继续完善 YAML/资源校验、凭证隔离，以及超时、取消、重试和审计边界。远端 schema 是参数结构约束，不会赋予工具权限；所有调用仍受 Tool Executor 和 Policy Engine 控制。
