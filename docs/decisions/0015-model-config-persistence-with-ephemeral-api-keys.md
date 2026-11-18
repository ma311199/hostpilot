# ADR-0015：模型配置持久化与按角色管理 API Key

- 状态：已采纳
- 日期：2026-11-07；2026-11-13 修订 Embedding 与 Reranker Key 生命周期
- 关联：[需求基线](../requirements.md)、[新架构文档](../新架构文档.md)、[前端架构](../frontend-architecture.md)、[ADR-0010 Harness 服务契约](0010-harness-contracts-and-configuration-boundary.md)、[ADR-0014 MCP 配置与 API Key 生命周期](0014-mcp-config-persistence-with-ephemeral-api-keys.md)

## 背景

模型配置面板管理主 LLM、轻量 LLM、Embedding 和 Reranker，各模型角色可以使用独立的模型名、Base URL 和 API Key。主 LLM、轻量 LLM 与 MCP 凭证继续采用进程内存生命周期。Embedding 与 Reranker 通常连接用户本机运行的模型服务；用户要求这两种 Key 在应用重启后保留。

## 决定

- 模型名称、Base URL、流式选项等非敏感配置存入本地 SQLite，并在启动时恢复。
- Embedding 和 Reranker 的 API Key 与对应角色配置一起保存在应用本地 SQLite，以支持本机模型服务在 HostPilot 重启后继续连接。
- 主 LLM、轻量 LLM 和 MCP 的 API Key 仅由后端进程内存持有，重启后需要重新提交；本决定不改变它们的凭证生命周期。
- 配置读取接口只返回各角色 Key 是否已配置，不返回 Key 值或可逆表示；Key 不得进入浏览器存储、日志、Trace、模型上下文、Skill 包或工具 schema。
- 页面提交配置时未提供新的 Embedding/Reranker Key，后端保留已保存的 Key；清空相应模型角色时，同时清除该角色的 Key。
- SQLite 数据库文件权限设置为仅当前账户可读写（`0600`），凭证不额外加密。此选择适用于用户本机模型端点的风险假设；如果后续支持远端或共享部署，应重新评估密钥加密和访问控制。
- 第三方 Provider 的 OAuth、客户端密钥或其他秘密类型需另行确定生命周期，不得默认持久化。

## 影响

- `routing_model_configuration` 表包含 Embedding 与 Reranker Key 列；旧数据库通过启动迁移增加列，旧配置仍可正常读取。
- 应用启动时从 SQLite 读取已保存的两种 Key 并重建可选模型客户端；主 LLM、轻量 LLM、MCP 仍按既有流程重新提交密钥。
- 前端清空密码输入框且不提交新值时不会覆盖已保存的 Embedding/Reranker Key；状态接口仅用于显示 Key 已配置。
- 测试覆盖新配置写入、旧表迁移、重启恢复、清除角色凭证和配置 API 不回传密钥。

## 复核条件

若模型服务端点不再限于用户本机，或应用开始支持多用户/远端部署，应重新评估本地数据库中的明文凭证、密钥加密、操作系统密钥环及访问控制。
