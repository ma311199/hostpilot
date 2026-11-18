# MCP 动态工具路由表记录

## 实现

- 新增 `RemoteToolRouteTable` Protocol 和 `MCPToolRoutingTable` 默认实现，并允许注入 `MCPToolProvider`。
- 将远程 MCP 暴露名称映射、路由解析及调用来源校验从 Provider 内部字典抽出。
- 保留 discovery 批次冲突检查；冲突、保留名称覆盖或无效目标不产生部分路由。
- Tool Executor 仍拥有参数校验、Policy、预算、审批和执行结果验证。
- 更新新架构、模块职责、开发指南、Stage D 路线图及 ADR-0026。

## 验证

- 定向 MCP Provider 与路由表测试：27 passed，1 条第三方 Authlib 弃用警告。
- 全量后端测试：334 passed，1 条第三方 Authlib 弃用警告。
- 前端 Vitest：66 passed。
- Ruff 检查通过；MyPy 检查全部 89 个后端源文件通过；`git diff --check` 通过。

## 限制

- 当前没有路由热移除或运行时重载接口；应用仍通过重建 Provider 应用配置变更。
