# ADR-0014：持久化 MCP 连接配置但不持久化 API Key

- 状态：已采纳，已实现
- 日期：2026-11-07
- 关联：[需求基线](../requirements.md)、[新架构文档](../新架构文档.md)、[Stage E 路线](../roadmap.md)、[Harness 服务契约](0010-harness-contracts-and-configuration-boundary.md)

## 背景

当前远程 MCP Server 通过 `HOSTPILOT_MCP_SERVERS` 环境变量配置。HostPilot 尚未提供 UI 配置 API 或持久化 Server 管理。目标管理页面需要在重启后保留 Server 连接信息，同时避免把 API Key 写入本地持久配置。

## 决定

- 持久化非敏感 MCP 连接元数据，包括稳定 Server ID、名称、endpoint URL、Transport、连接超时、启用状态、认证方式及必要的请求头名称。
- API Key 作为独立秘密输入，只在 HostPilot 运行期间保存在后端进程内存；不得写入 SQLite、配置文件、浏览器存储、日志、Trace、Skill 包或工具 schema。
- 配置读取可以返回 `api_key_configured` 布尔状态，但不得返回 Key 值或其可逆表示。
- 应用启动时恢复非敏感连接元数据。无需 API Key 的 Server 可按启用状态连接；需要 API Key 的 Server 保留配置并标记等待密钥，在用户重新提交 Key 前不发起认证连接。
- 用户重新提交 API Key 后，HostPilot 使用已持久化的 Server 元数据建立或重建连接。连接超时、启用状态及 Server 名称无需随重启再次配置。
- endpoint URL 不得承载 API Key 或其他秘密。认证方式标识、Header 名称等可作为非敏感元数据保存，秘密值只进入运行时注入路径。
- 当前 `HOSTPILOT_MCP_SERVERS` 环境配置行为保持不变，直到目标配置存储、写入/轮换 API 与 UI 实现并迁移。
- 非 `api_key` 的 OAuth、客户端密钥等认证类型不由本 ADR 预设；接入前须定义同等明确的秘密生命周期和重启行为。

## 影响

- 非敏感 Server 配置保存在应用 SQLite 数据库的独立表中；数据库沿用应用数据目录的本地权限策略。
- MCP 状态 API 需要区分已连接、未连接、失败及等待重新输入 API Key；UI 不得把“元数据已保存”显示成“连接已认证”。
- 写入、轮换或清除 API Key 后，需要安全地更新内存配置并重建对应的 MCP Client；读接口仅暴露是否配置。
- 测试需覆盖进程重启后的元数据恢复、密钥缺失时不连接、重新输入后可连接、Key 不出现在任何持久文件/日志/响应，以及多 Server Key 隔离。
- 已交付本地配置 CRUD、启停、密钥状态、缺少 Key 时等待、提交 Key 后重建远程 MCP Provider，以及连接失败时保留其他可用 Server 的行为。API 验证错误不回显提交值。

## 复核条件

如果部署安全模型、操作系统密钥托管能力或 MCP Server 的认证协议要求改变 API Key 生命周期，应新增决策记录复核本 ADR，不得默认把 Key 添加到普通配置持久化中。
