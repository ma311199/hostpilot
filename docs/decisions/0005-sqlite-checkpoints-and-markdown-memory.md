# ADR-0005：SQLite 会话检查点与 Markdown 记忆

- 状态：已接受
- 日期：2026-10-29

## 背景

HostPilot 当前部署在用户终端，面向单个本地用户且免登录，同时需要支持多个相互隔离的会话。会话在进程重启后应能够恢复；长期记忆应便于检查和编辑。

## 决定

- 使用本地 SQLite 保存会话索引和 LangGraph 检查点。HostPilot 的 conversation_id 映射为 LangGraph thread_id；同一会话的调用复用同一个 thread_id。
- 使用 Markdown 保存会话摘要和跨会话长期记忆。按需将与当前会话相关的记忆装入 ContextSnapshot，不默认读取所有记忆文件。
- SQLite 检查点负责恢复图状态和会话消息；Markdown 是摘要及长期记忆，不作为工具授权、确认结果或图执行检查点的权威来源。
- MemoryWriteProposal 经 HostPilot 策略处理后才能写入 Markdown；记忆内容按不可信输入处理。
- 会话数据、检查点和 Markdown 文件存放于本机用户数据目录。具体路径、权限、保留期限、备份、迁移和文件格式在实现时确定。

## 后果

- Context Builder 按 conversation_id 加载 SQLite 检查点和相关 Markdown 记忆，并保证会话隔离。
- DeepAgents 集成必须配置持久化 LangGraph checkpointer；当前仅使用 StateBackend 的 Skill 适配器不提供会话持久化。
- 已在锁定依赖中验证并引入 langgraph-checkpoint-sqlite 3.1.2；核心契约不依赖该实现专有类型。
- 删除会话时需同时处理对应 SQLite 检查点及会话级 Markdown 摘要；共享长期记忆不随单会话删除而自动清除。
