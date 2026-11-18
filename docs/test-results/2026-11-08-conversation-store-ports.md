# 会话 Store 与 Checkpointer Port 实现记录

## 实现

- 新增 `ConversationStore` Protocol，ContextBuilder、ChatService、API 会话端点依赖该契约。
- `create_app` 支持注入 `conversation_store_factory`，默认仍创建 `SQLiteConversationStore`。
- 新增独立的 `CheckpointerFactory` 和最小 `ConversationCheckpointer` Port；默认仍通过 SQLite Checkpointer，且与会话元数据 Store 共用数据库文件。
- LangGraph thread ID 映射抽取为 `conversation_thread_config`；SQLite 类原静态方法保留兼容。
- 本次未给其他 SQLite 配置/Trace/Audit Store 增加注册表。

## 验证

- 定向测试：会话持久化、Checkpointer、上下文、Chat 路由和 API App 测试：63 passed，1 条第三方 Authlib 弃用警告。
- 全量后端测试：331 passed，1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查全部 88 个后端源文件通过。

## 后续

- 其他 SQLite Store 仍为具体实现；按使用边界定义 Port，确认确实存在替换需求后再做注册或后端选择。
