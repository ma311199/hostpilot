# Memory Store Port 记录

## 实现

- 新增 `MemoryStore` Protocol 和 `memory_store_factory`，默认仍构造原有 `MarkdownMemoryStore`。
- MemoryController、MemoryWriteService 和删除会话 API 依赖 Store Port；记忆审批、会话存在检查、大小限制和共享长期记忆隔离继续由现有治理逻辑执行。
- 更新新架构、模块职责、开发指南和 ADR-0025。
- 没有改变记忆文件格式，也没有增加向量或语义召回。

## 验证

- 定向 API、MemoryController、Markdown Store 测试：64 passed，1 条第三方 Authlib 弃用警告。
- 全量后端测试：331 passed，1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查全部 88 个后端源文件通过；`git diff --check` 通过。

## 限制

- 尚未接入替代记忆后端；自定义实现必须保持会话隔离、大小上限和完整替换语义。
- 测试未采集覆盖率。
