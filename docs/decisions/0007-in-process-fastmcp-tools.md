# ADR-0007：进程内 FastMCP 内置工具

- 状态：已采纳（内置 MCP 连接方式仍有效；外部 MCP Transport 范围由 ADR-0016 扩展）
- 日期：2026-11-01
- 关联：[新架构文档](../新架构文档.md)、[依赖管理](../dependency-management.md)

> 2026-11-02 确认：内置 MCP Server/Client 继续在 HostPilot 进程内连接。此前“不采用本地 stdio 子进程”的范围限制已由 [ADR-0016](0016-local-stdio-mcp-servers.md) 更新；外部 Server 现在支持 STDIO 与 Streamable HTTP。

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
- 内置工具沿用进程内连接；外部 MCP Server 可使用 ADR-0016 定义的 STDIO 子进程，或 ADR-0008 定义的独立 Streamable HTTP Client。
- 真实远程网络、TLS 和服务端认证仍需使用实际端点做联调。
- RAG HTTP 调用具有独立服务的网络故障和超时边界，应由其客户端配置与错误处理负责。
- 本地 MCP 连接不提供操作系统级沙箱隔离；工具权限仍由 HostPilot 策略与工具实现约束。

## 复核条件

当内置工具需要独立部署、跨进程隔离或被多个客户端共享时，再评估额外的进程/沙箱边界。
