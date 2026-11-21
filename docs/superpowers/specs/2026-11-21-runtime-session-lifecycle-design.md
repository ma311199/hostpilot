# Runtime Session 生命周期设计

## 意图

让 Runtime 统一管理 Session 的接入、使用租约和关闭；现有 ConversationStore、Checkpoint 与 Memory 实现继续作为可替换的持久化服务。主要目标是让 Session 删除和运行中的聊天/历史读取协调起来，并支持同一 SQLite 数据库由多个 ASGI worker 共享。

## 当前状态

- Session 对外由 `/conversations` API 表示，元数据由 `ConversationStore` 保存；LangGraph checkpoint 和 Session 摘要分别由 Checkpointer、Memory Store 管理。
- ChatService 在 Context 构建完成后记录 Turn，当前没有一个覆盖完整 Context/Model/Tool 操作的 Session 租约。
- 删除 API 依次删除 checkpoint、ConversationStore 记录、Session 摘要和待审提案；它不阻止新操作，也不等待已有操作完成。
- Runtime Host 与 Turn 元数据已有共享 SQLite Host 租约，默认每 5 秒心跳、30 秒失效，可供跨 worker 判断租约归属是否仍有效。

## 目标

- 在 `hostpilot.runtime` 提供 Session 生命周期协调器；API 与 ChatService 通过该协调器执行 Session 创建、访问及关闭/删除流程。
- 每个 Session 操作在开始前取得可持久协调的租约，并在完整操作结束后释放。
- 关闭 Session 时原子地停止接受新租约；已持有租约的操作可完成并释放，关闭流程等待其排空后再清理 checkpoint、memory 和会话元数据。
- 使用 Runtime Host 租约判断跨 worker 的 Session 使用者是否仍活跃；失效 Host 的租约不应永久阻塞删除。
- 保持现有 HTTP/WebSocket 请求与响应契约，并使具体生命周期状态存储保持可替换。

## 非目标

- 不取消正在执行的 Turn，不远程中断另一 worker 中的模型或工具调用。
- 不更改 Turn/TaskRun 的状态定义、请求正文持久化方式或工具结果恢复策略。
- 不更换 SQLite、LangGraph Checkpointer、Memory Store 等当前存储产品。
- 不增加可见的 Session 暂停、归档或恢复 API。

## 方案比较

1. **仅在单个 Runtime Host 内存中协调。** 实现最少，但 Session 请求可落在不同 ASGI worker；一个 worker 的关闭流程看不到另一个 worker 的活动操作，因此不满足当前共享数据库部署边界。
2. **仅将 API 逻辑搬进 Session 服务。** 职责归 Runtime，但如果服务只用进程内锁，跨 worker 删除竞态仍存在。
3. **Runtime 生命周期协调器 + 可替换的持久 Session 状态/租约 Port（选用）。** 每个 worker 使用同一持久 Port 原子获取/释放租约与关闭 Session；SQLite 是当前适配器，Host 租约用于排除崩溃 worker。实现范围更大，但符合每个 worker 一个 Runtime Host、多个 Host 共用持久化状态的架构。

## 行为约定

- 创建 Session 后即处于 `active` 状态。
- 读取历史、路由评估和聊天 Turn 等使用 Session 的请求先获取 Session 租约；关闭开始后新请求得到确定的 Session 不可用结果。
- 删除请求把 Session 转为 `closing`，等待所有仍有效的租约释放，再按现有依赖顺序清理 Checkpoint、Memory 和会话数据。清理失败时保留可重试的 closing 状态；清理步骤必须可重复执行。
- Session 租约归属于当前 Runtime Host。Host 正常停止时，其租约失效；Host 崩溃时，超过 Host 租约有效期的租约不再阻塞关闭。
- 删除请求等待活动租约自然排空并在清理完成后返回现有 `204`；不引入独立的删除超时、后台清理任务或异步 API。聊天 Turn 仍遵守现有 Harness 请求超时。
- Chat、历史读取、route-only 路由评估、单 Session 元数据读取及 SandboxMode 升级均获取 Session 租约。全局 Session 列表读取不锁定单个 Session；删除完成前列表可暂时仍显示 closing Session。
- Runtime Host 停止接收请求时先拒绝新 Session 租约，再取消活动 Turn/Task，并等待本 worker 已取得的 Session 租约释放；State Services 最后释放 Host 租约。因此其他 worker 不会把仍在使用的本地 Session 租约当作 Host 已失效。

## 架构边界

- `SessionLifecycleManager` 位于 Runtime，负责操作次序、关闭/排空协调和生命周期状态语义。
- `SessionLifecycleStore` 是持久化 Port，负责跨 worker 的状态转换及租约原子操作；SQLite 实现由 State Services 插件创建和注入。
- `ConversationStore`、Checkpointer、Memory Store 和 MemoryProposalReview 继续提供各自数据，不由 Runtime 核心决定存储格式。
- Runtime Host 向 Session 生命周期组件提供 `runtime_host_id`；ChatService 使用一个 Session 租约覆盖整个请求操作。

## 约束与待验证项

- 关闭与获取租约必须由 Store 在可串行化写事务中仲裁，不能依赖 Python 锁。
- 活跃 Host 的 Session 租约不得因固定 Session TTL 过期；租约有效性应跟随 Host 心跳。
- closing 状态下的清理重试不得恢复 Session 接入，也不得重复产生外部副作用。
- 自定义 ConversationStore/SessionLifecycleStore 注入仍需支持；不支持持久 Session 租约的 Store 应在启动或配置时明确失败，不能静默降级为单进程锁。
- 多个 worker 同时关闭同一 Session 时仅一个调用者执行清理；其他调用者等待状态完成，并在删除后返回一致的 not-found/204 语义。
