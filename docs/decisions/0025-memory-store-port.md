# ADR-0025：Memory Store Port 与记忆治理边界

## 状态

已采纳

## 背景

MemoryController 和 MemoryWriteService 直接依赖 `MarkdownMemoryStore`，API 删除会话时也直接使用该具体类。替换记忆文件格式或为测试提供替身，会让记忆治理层和应用层绑定本地 Markdown 实现。记忆内容同时是非可信上下文；替换后端不应改变审批和会话范围边界。

## 决定

- 定义面向数据域的 `MemoryStore` Protocol，包含会话摘要与共享长期记忆的读写、会话摘要删除和单文件大小限制。
- `create_app` 接受 `memory_store_factory`，factory 接收配置的 memory 根目录；默认仍创建 `MarkdownMemoryStore`，现有文件路径和格式保持不变。
- MemoryController 与 MemoryWriteService 依赖 Protocol，而非具体 Markdown 类。MemoryController 统一提供受范围约束的记忆召回；MemoryWriteService 负责审批、会话存在校验和写入大小校验。
- Conversation API 删除会话时只调用 Store 删除该会话摘要，不触及共享长期记忆。
- 不将 `MemoryStore` 暴露给 Skill/插件作为可直接写入的能力；任何替代实现必须保持会话隔离、文件大小约束和原子/完整替换语义。
- 本决定不引入语义检索、向量索引或新的归档策略；现有 Markdown 后端仍返回当前会话摘要与共享记忆。

## 影响

- 记忆后端可独立替换和测试，治理层继续控制记忆访问与审批写入。
- 默认记忆文件无需迁移；Markdown 实现继续拒绝符号链接和越界路径，并以原子替换方式写入。
- Protocol 不能自动确保自定义实现的磁盘权限和原子性，替代实现须自行遵守这些约束。

## 复核条件

增加语义召回、向量索引、并发多进程写入或远程记忆服务时，重新评估查询接口、用户隔离、索引一致性、事务和冲突解决策略。
