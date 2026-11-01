# ADR-0008：远程 MCP Server 与 Streamable HTTP

- 状态：已采纳
- 日期：2026-11-01
- 关联：[架构设计](../architecture.md)、[需求基线](../requirements.md)、[ADR-0007](0007-in-process-fastmcp-tools.md)

## 背景

HostPilot 需要接入由独立服务部署的 MCP 工具，同时继续通过自身工具注册和策略执行链治理调用。当前内置 FastMCP Server 使用进程内连接；外部 RAG 当前提供普通 HTTP API，不是 MCP Server。

## 决定

通过 `HOSTPILOT_MCP_SERVERS` JSON 数组配置一个或多个远程 MCP Server。每项包含唯一 Server 名称、Streamable HTTP MCP URL、可选认证请求头和请求超时。HostPilot 为每个远程 Server 建立独立 FastMCP Client 与会话。

发现的远程工具用 `{server_name}_{tool_name}` 命名，登记到同一个 Tool Registry。模型调用和路由器直接调用都经过 ToolExecutor。远程工具按第三方服务上的高影响操作处理，默认需要显式用户审批；远程 Server 的注解不构成 HostPilot 授权。

## 理由

- 每个远程端点有独立连接和生命周期，故障来源及配置更容易定位。
- Streamable HTTP 适用于独立进程或远程部署的 MCP Server。
- HostPilot 保留统一策略、预算、审计和审批边界，不把授权交给远程 Server。
- 工具名前缀避免多个 Server 暴露同名工具时发生路由歧义。

## 影响

- 远程 Server 必须实现 MCP Streamable HTTP；普通 REST 服务不能直接使用该传输。
- 凭证只从后端环境配置读取并保存在 SecretStr 中，不进入 Agent 工具 schema 或工具目录响应。
- 首版通过环境 JSON 配置，不提供前端增删或连接状态管理；配置变更需重启 HostPilot。
- 远程工具默认逐次审批，降低未知 Server 工具副作用风险，但会增加交互步骤。
- 客户端连接成功后才能发现远程工具；远程 Server 不可用时，已配置模型的工具初始化会失败并关闭候选 Provider。

## 复核条件

当远程工具有可靠的细粒度权限声明、工具级策略配置或需要无需逐次审批的只读调用时，重新评估默认审批规则。若需管理大量 Server，再评估持久化配置界面、连接健康状态和单 Server 故障降级。
