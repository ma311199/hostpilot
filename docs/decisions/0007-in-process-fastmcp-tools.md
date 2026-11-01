# ADR-0007：进程内 FastMCP 内置工具

- 状态：已采纳
- 日期：2026-11-01
- 关联：[架构设计](../architecture.md)、[依赖管理](../dependency-management.md)

## 背景

HostPilot 需要向 Agent 提供本地文件操作和知识检索等工具，并确保每次模型发起的调用都经过统一的注册、策略检查、预算与执行治理。MCP 是工具适配协议；它本身不要求 Client 和 Server 必须运行在不同进程。

## 决定

当前内置工具由 HostPilot 进程内的 FastMCP Server 注册。HostPilot 创建 FastMCP Client 并通过内存会话连接该 Server，再将发现的工具适配给 Agent。模型发起的调用由拦截器转交给 HostPilot ToolExecutor，遵循 Tool Registry、Policy Engine 和执行后验证链路。

`retrieve_knowledge` 也是该内置 MCP Server 上的工具，但其实现通过 HTTP 调用独立的 Agentic RAG 服务。RAG 服务不是另一个由 HostPilot MCP Client 连接的 MCP Server；用户和密钥由后端配置并注入请求，Agent 只提供 query。

## 理由

- 内置工具不需要启动和管理本地子进程，也不需要配置 stdio 生命周期与通道。
- 仍保留 MCP 工具发现和调用契约，并能复用统一的 MCP 到 Agent 适配和策略拦截链。
- RAG 服务保持独立部署边界，其 HTTP 接口和服务生命周期不与 HostPilot 的本地工具宿主混为一体。

## 影响

- FastMCP Client 与内置 Server 共享 HostPilot 的进程生命周期和故障域；HostPilot 关闭时，本地 MCP 会话也会关闭。
- 当前集成覆盖进程内内存传输；独立 MCP 子进程、stdio 和远程 MCP 连接行为尚未由此实现或验证。
- RAG HTTP 调用具有独立服务的网络故障和超时边界，应由其客户端配置与错误处理负责。
- 这是简化当前本地工具宿主的架构选择，不应在缺少基准数据时描述为已经验证的性能优化。

## 复核条件

当需要让工具独立部署、跨进程隔离、被多个客户端共享，或要接入第三方 MCP Server 时，重新评估 stdio 或远程 MCP 传输，并补充相应生命周期、安全边界和集成验证。
