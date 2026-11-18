# 2026-11-14：Plan 任务生命周期 Trace

## 范围

Plan Executor 为每个实际执行任务记录安全的生命周期事件，状态包括 `started`、`completed`、`failed`、`interrupted` 和 `reused`。事件沿用当前请求 Trace 的父子关系，并写入现有 SQLite Trace Store，不新增数据库或 API。

事件仅记录任务 ID、执行类型、状态、耗时、受限原因码、工具调用数、副作用数、复用来源和取消时结果是否不确定。任务描述、输入/输出、依赖结果、工具参数、幂等键和补偿句柄均不写入 Trace。写入失败不会改变 Plan 的执行结果。

该能力用于诊断步骤进度，不提供从进程重启后恢复 Blackboard、重试任务或自动补偿。架构决策见 [ADR-0033](../decisions/0033-plan-task-lifecycle-trace.md)。

## 验证结果

- Plan 编排、Planner 适配、Trace Store 和上下文聊天集成相关测试：**35 项通过**。
- 覆盖任务开始/完成事件使用同一子 Span、步骤状态挂接请求 Trace、任务正文与输出不进入事件、并行任务取消时记录中断和外部结果不确定状态，以及 Trace SQLite 属性白名单丢弃任务 payload。
- Ruff 检查与格式检查通过；Mypy 检查 17 个相关后端源码文件通过。
- 集成测试出现一条已知 Authlib `authlib.jose` 弃用警告，不影响通过结果。

## 后续

如需支持故障后恢复 Plan，需另行定义持久化 Blackboard 的 payload 隐私边界、保留期限、任务幂等策略、并发任务中断判定与防止重复副作用的恢复协议。本次 Trace 不是恢复快照。
