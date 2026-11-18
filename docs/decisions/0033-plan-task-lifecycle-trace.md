# ADR-0033：Plan 任务生命周期 Trace

- 状态：已采纳
- 日期：2026-11-14
- 关联：[ADR-0017 本地 Trace 元数据](0017-local-trace-storage-and-query.md)、[ADR-0024 Trace Store Port](0024-audit-cancellation-trace-store-ports.md)

## 背景

Plan 的 `PlanTaskState` 和 Blackboard 仅在执行过程中保存在内存。现有 Trace 能关联请求、Skill、工具与审批，但不能显示 Plan 中每个任务是否开始、完成、失败、复用或因取消中断，排查并发执行和部分失败时缺少步骤级证据。

## 决定

- Plan Executor 为每个实际执行任务记录 `plan.task` 生命周期事件；任务开始、完成、失败和取消中断使用相同 span，复用的已完成任务记录 `reused` 状态。
- 事件只包含任务 ID、执行类型、状态、耗时、格式受限的原因码、工具调用数、副作用数、复用来源任务 ID，以及取消时外部结果是否不确定。
- 事件通过现有 `TraceSink` 和 SQLite Trace Store 写入，继续遵循当前保留期限和属性白名单；Trace 写入失败不得改变 Plan 执行结果。
- 任务描述、输入、输出、依赖结果、工具参数、幂等键、补偿句柄和补偿正文不得进入 Trace。
- 生命周期 Trace 用于诊断，不是可恢复执行日志，不授权自动重试，也不改变取消或补偿策略。标记 `interrupted` 不代表外部工具已停止；`outcome_uncertain` 表示请求取消时该任务仍在执行。

## 影响

- Trace 页面可通过请求详情查看 Plan 的步骤状态与耗时，同时复用现有本地保留和访问边界。
- 当前可持久查看步骤元数据，但不能在进程重启后重建 Blackboard 或恢复 Plan。持久状态快照和恢复需要另行定义 payload 数据处理、保留、幂等与重复副作用规则。

## 验证

- 单元测试覆盖开始/完成事件关联、任务正文和结果不入 Trace、取消时并发任务的中断及不确定状态。
- SQLite Trace 测试覆盖新字段白名单，并确认任务输入输出和补偿句柄会被丢弃。
