# ADR-0024：Audit、Cancellation 与 Trace Store Port

## 状态

已采纳

## 背景

Audit、Cancellation 和 Trace 分别保存安全决策、执行取消摘要与运行观测事件。它们虽然默认共用 HostPilot SQLite 数据库，但生命周期、写入模型、查询接口和敏感信息约束不同。此前 `create_app` 直接创建三个 SQLite 实现，应用集成测试或其他受信任部署无法在不改应用代码的情况下替换它们。

## 决定

- 定义独立的 `AuditStore`、`CancellationStore`、`TraceStore` Protocol，并由独立 Factory 创建。
- `create_app` 分别接受 `audit_store_factory`、`cancellation_store_factory` 和 `trace_store_factory`；默认保持原有 SQLite 实现、数据库表和文件路径。
- Audit 与 Trace 的 `record` 保持同步，避免改变现有事件 Sink 契约；取消快照写入保持异步。
- Port 按各自数据域提供初始化、写入和既有查询方法，不引入通用 Event Store，也不提供插件注册表或 UI 后端选择。
- 所有替换实现必须保持原有安全约束：Audit 不扩展已声明字段，Trace 仅保存属性白名单，Cancellation 仅持久化脱敏摘要；列表查询需保留相应的输入边界。

## 影响

- 三类运行记录可以独立注入测试替身或受信任的自定义实现。
- 默认安装和现有数据无需迁移，SQLite 表结构及数据格式不变。
- Protocol 本身不自动实施脱敏；实现方需要遵守并验证数据域约束。

## 复核条件

引入新的持久化后端或分进程写入时，需重新评估事务一致性、事件投递失败行为、并发语义、保留期限和数据迁移。
