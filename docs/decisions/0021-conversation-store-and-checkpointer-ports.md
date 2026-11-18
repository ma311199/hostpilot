# ADR-0021：会话 Store 与 Checkpointer Port

## 状态

已采纳

## 背景

会话元数据和 LangGraph 对话状态当前都由 SQLite 提供，但它们是两种不同契约：元数据管理会话 ID、标题、更新时间和沙箱级别；Checkpointer 保存 Agent 图状态并按 thread ID 删除。把两者绑定为一个 Store 会阻碍独立替换和测试。

## 决定

- ContextBuilder、ChatService 与 API 对话端点依赖 `ConversationStore` Protocol，不依赖 `SQLiteConversationStore` 实现。
- API 通过 `ConversationStoreFactory` 构造会话元数据 Store；默认 factory 为 `SQLiteConversationStore`。
- LangGraph Checkpointer 通过 `CheckpointerFactory` 异步上下文管理器单独注入；默认由 `open_sqlite_checkpointer` 创建。
- conversation ID 到 LangGraph thread config 的映射属于共享契约，提供 `conversation_thread_config` helper；SQLite Store 的旧静态方法保留为兼容入口。
- 默认仍由两个 SQLite 实现共用数据库文件。自定义元数据 Store 不会自动替换 Checkpointer，且须保持 UUID 会话 ID 和 `ConversationNotFoundError` / 返回值约定。
- 其他模型、MCP、Harness、审计、取消、Workspace、Trace 和 Skill 执行配置 Store 仍使用当前 SQLite 实现；本次不建立通用 Store 插件注册表。

## 影响

- 应用可以分别替换会话元数据和对话状态存储；测试可以注入轻量 Store 或 Checkpointer。
- 自定义后端需要自己保证并发、安全、原子沙箱升级和生命周期行为；Port 只固定应用依赖的方法签名，不提供存储迁移或事务能力。

## 复核条件

替换其他 SQLite Store 或添加 UI/环境选择后端时，先定义每个存储域的数据迁移、事务和凭证策略，再考虑是否需要统一 Registry。
