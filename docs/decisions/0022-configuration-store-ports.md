# ADR-0022：模型与 MCP 配置 Store Port

## 状态

已采纳

## 背景

模型配置和 MCP Server 连接项跨重启保存，但目前 API 直接实例化 SQLite Store。不同配置域使用独立数据契约。主 LLM、轻量 LLM 与 MCP 凭证只驻留进程内存；按 ADR-0015，路由配置 Store 额外持久化本地 Embedding 和 Reranker API Key。

## 决定

- 模型、路由模型和 MCP 配置分别依赖 `ModelConfigurationStore`、`RoutingModelConfigurationStore`、`MCPConfigurationStore` Protocol，通过独立 factory 注入 `create_app`。
- 默认实现分别为 `SQLiteModelConfigurationStore`、`SQLiteRoutingModelConfigurationStore` 和 `SQLiteMCPConfigurationStore`，使用 HostPilot 本地 SQLite 文件。
- 主模型 Store 只接收模型名、Base URL、streaming；MCP Store 的 TypedDict 只保存端点/进程参数、凭证字段名称和连接状态元数据。路由模型 Store 保存模型角色配置，以及 Embedding/Reranker API Key；该例外及本地明文存储范围见 ADR-0015。
- Factory 注入用于应用集成和测试，当前不增加运行时插件 ID、配置 UI 或存储后端热切换。
- Store 实现必须保留现有原子保存、稳定排序、迁移兼容和密钥排除行为。

## 影响

- 应用可以替换各配置域持久化实现而不改动 API 业务逻辑。
- 插件化仅覆盖这两个配置域；其他 SQLite Store 仍使用各自的具体实现。

## 复核条件

增加非 SQLite 后端、跨进程并发写入或配置迁移时，验证唯一性约束、原子更新、历史数据迁移及敏感字段排除。
