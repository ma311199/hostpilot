# Session ID 与 Runtime 命名统一设计

## 目标

让同一个会话标识在后端、持久化、HTTP/WebSocket 和前端统一称为 `session_id`，并让含 `Runtime` 的类型清楚表达自己负责进程宿主、聊天 Agent、Skill 执行还是模型组件。

## 当前情况

项目用 `conversation_id` 表示聊天会话的 UUID；它贯穿 API、前端、SQLite、检查点映射、记忆和测试。`conversation` 仍是用户看到并管理的资源名称。进程级 `RuntimeHost` 与聊天 `AgentRuntime`、`ChatRuntimeRegistry`、`SkillRuntime`、`ModelRuntime` 等领域类型并存，职责不同但词面接近。

## 方案

采用全链路会话标识改名，并按职责重命名领域类型。保留 Conversation 资源及 `/conversations` 路径；路径参数和返回字段统一使用 `session_id`。SQLite `conversations` 表改用 `session_id` 列，通过 schema version 3 将既有 `conversation_id` 列原位重命名，保留现有记录及 UUID，因此既有 LangGraph checkpoint thread ID 不变。此选择会改变 API JSON 字段和 WebSocket 字段，调用旧字段的外部客户端需要同步更新；不提供双字段兼容期。

保留 `RuntimeHost`、`RuntimeContext`、`RuntimePlugin`、`RuntimeServiceRegistry` 等专指进程级 Runtime Host 的名称。聊天执行契约命名为 `ChatAgentRuntime`，其 LangGraph 实现、注册表上下文、工厂、注册表、异常和 Host 插件使用 `ChatAgentRuntime` 领域前缀。`SkillRuntime` 改名为 `SkillEngine`。模型工厂输出的 Provider 与 Agent 模型组合改为 `ModelRuntimeComponents`，其注册表改为 `ModelProviderRegistry`；路由侧依赖组合改为 `RoutingModelComponents`。Harness 配置模型使用 `HarnessConfiguration`。其他 `Runtime*` 名称仅在确属进程宿主职责时保留。

比较过的替代方案：仅改 Python 内部名称能降低客户端迁移量，但会让外部契约与内部术语分叉；只在文档解释旧名称不会解决代码阅读时的歧义。全链路命名提供一致语义，代价是 API 字段变更和数据库 schema migration，因此须覆盖迁移和客户端测试。

## 范围与边界

- 将代码、测试、示例、架构文档和当前有效台账中会话 ID 标识统一为 `session_id`；自然语言中的 conversation/conversation resource、类名 `Conversation*`、路由 `/conversations` 和表名 `conversations` 保留。
- HTTP 路径保持 `/conversations/{...}`，路径参数名改为 `session_id`；请求/响应 JSON 中对应标识字段改为 `session_id`。
- SQLite 迁移必须幂等、安全保留旧数据，并提高 `PRAGMA user_version`；不得删除或重建用户数据表。
- 保持检查点 thread ID 的 UUID 值和格式不变。
- 不改变 Runtime 生命周期行为、插件加载顺序、服务 ID 语义、模型选择行为或 Skill 执行行为。
- 不提供 `conversation_id` 的 API 别名，不改名已持久化配置项或插件实现 ID，除非它们是 Python API 类型/符号名的一部分。

## 验证

- SQLite 旧 schema v2 的迁移保留行、UUID、元数据并迁移到 v3；重复初始化保持成功。
- API、WebSocket、前端客户端和持久化契约只读写 `session_id`。
- Runtime 名称重命名后，Host 装配、Registry 工厂和调用链行为不变。
- 全量 Python 与前端测试、lint、类型检查及文档/代码残留搜索通过。
