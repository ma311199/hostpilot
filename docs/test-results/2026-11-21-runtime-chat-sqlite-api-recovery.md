# Runtime Chat SQLite + API 不确定工具结果恢复集成测试

- 日期：2026-11-21
- 范围：通过 Chat API 和真实 SQLite checkpointer 验证工具结果 checkpoint 失败后的重启恢复。
- 外部服务调用/真实外部副作用：0；模型与工具均为确定性测试替身，工具副作用仅由内存计数器表示。

## 场景与结果

测试使用三个独立 FastAPI 应用生命周期和同一个 SQLite 数据文件：创建会话、执行故障请求、重启后重试。故障注入发生在工具 handler 已完成、Runtime 保存 Tool 结果消息之前。

- 故障请求返回 HTTP 500；SQLite 中已保留 User 请求和 Assistant 工具提案。
- 重启后，新 Runtime 从 SQLite 读取未完成的工具提案，并通过 Chat API 返回 `clarify`，告知用户工具结果不确定、应先核实。
- 确定性模型只调用 1 次，内存工具只执行 1 次；恢复请求没有再次调用模型或工具。
- 新增集成测试：**1 passed**。
- Ruff：**通过**；mypy（新增测试文件）：**通过**；`git diff --check`：**通过**。
- 全量后端 pytest：**570 passed**，耗时 36.79 秒；有 1 条既有 Authlib 弃用警告。

## 评测边界

该测试覆盖真实 SQLite checkpoint 与 Chat API 边界，但模型和工具均为本地确定性替身。它验证 checkpoint 中存在工具提案、缺少结果时的安全恢复，不覆盖 SQLite 文件损坏、磁盘故障或真实外部工具的事务语义。
