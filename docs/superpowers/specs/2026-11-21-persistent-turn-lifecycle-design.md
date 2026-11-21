# 持久化 Turn 生命周期设计

## 意图

让 Runtime 在进程重启后仍能区分已完成、失败、取消和中断的 Turn，同时保留 SQLite/其他存储可替换的边界。第一阶段只持久化 Turn 生命周期元数据；不会尝试恢复模型调用游标或自动重放工具。

## 当前状态

`TurnLifecycleManager` 当前将活动 Turn 和最近终态保存在 `RuntimeContext` 的进程内内存。ConversationStore 保存会话元数据，LangGraph Checkpointer 保存消息与执行 checkpoint。每个 ASGI worker 都有自己的 Runtime Host，因此多个 worker 可能同时写同一个数据库。

## 目标与非目标

- 持久化 `turn_id`、`session_id`、拥有它的 `runtime_host_id`、状态、开始时间和结束时间。
- 保存 `running` 到 `completed`、`failed`、`cancelled` 的状态转换。
- Runtime Host 崩溃或重启后，将租约已过期的 Host 所拥有的 `running` Turn 标为 `interrupted`。
- Host 启动时只回收租约已过期的其他 Host，不能误标记仍在运行的 ASGI worker 的 Turn。
- SQLite 是当前实现；持久化协议通过 TurnStore Port 注入，以便替换存储介质。
- 不持久化用户消息、模型正文、工具参数、凭证或异常原文。
- 不自动恢复 Agent/Plan 执行，不重放工具，不新增前端/API 诊断界面；这些属于后续独立阶段。

## 实现方案

在 `persistence.ports` 增加 `TurnStore`，并提供 `SQLiteTurnStore`。Runtime Host 为每个 worker 创建唯一 `runtime_host_id`。SQLite 记录 Host 心跳租约；活跃 Host 定期续约，正常关闭时标记停止。新 Host 启动时，在同一事务中找出过期租约、将其名下仍为 `running` 的 Turn 改为 `interrupted`，再注册自己的 Host 记录。默认心跳周期 5 秒、租约过期时间 30 秒，常量保持可在测试中注入。

`TurnLifecycleManager` 继续负责当前进程的活动任务索引和取消，但通过注入的 `TurnStore` 持久化生命周期转换。ChatService 使用异步 Turn 跟踪上下文，确保 Store 写入失败时请求失败且不会谎报持久化完成。StateServicesHostPlugin 创建、初始化、注册和关闭 Store；RuntimeHost 管理心跳后台任务，并在关闭顺序中先停止心跳，再结束 Host 租约。

## 考虑过的方案

1. **启动时把所有 `running` Turn 改为 `interrupted`。**实现简单，但多 worker 共用 SQLite 时，一个 worker 启动会误标记其他 worker 的活跃请求，不能满足每个 ASGI worker 一个 Runtime Host 的架构。
2. **Turn 记录与 Host 租约一起持久化（采用）。**增加一张 Host 表和心跳任务，但能安全区分崩溃的 Host 与仍在服务的 Host；租约超时可能延迟中断识别，心跳周期应明显短于租约期限。
3. **立即持久化完整 Agent/Session/Task 快照。**恢复能力更广，但会把框架内部执行游标和版本兼容引入 Runtime 状态契约，也会放大工具副作用重放风险；先不纳入此阶段。

## 验收

- 同一进程中 Turn 的成功、失败和取消均写入持久 Store。
- 模拟 Host 崩溃后，新 Host 将过期 Host 持有的 `running` Turn 标为 `interrupted`。
- 两个仍持有有效租约的 Host 并行运行时，启动其中一个不会改变另一个 Host 的活跃 Turn。
- Host 关闭时停止心跳并释放租约；重复启动/关闭不会产生重复记录或破坏状态。
- SQLite Store 重启读取状态；TurnStore Port 可由测试替身替换。
- 现有 Chat API、取消行为和工具结果恢复测试保持通过；无工具自动重放。
