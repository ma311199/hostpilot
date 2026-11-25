# Runtime Chat Turn 跨 Host 恢复设计

> 状态：用户已批准。用户已选择“恢复原执行”；本文确认实现边界和安全语义。

## 意图

进程或 Runtime Host 意外中断时，用户可以在同一 Session 中明确恢复受 Runtime-native Chat 执行的原 Turn，从最近一次可证明已持久化的消息边界继续。恢复继续受当前预算、授权、Policy 和工具审批约束；Host 启动本身不派发模型或工具调用。

恢复的是**同一逻辑 Turn**，不是重新提交一条相同文本的新消息。恢复过程中如果进程在模型请求完成、响应尚未写入 checkpoint 时中断，HostPilot 无法恢复远端模型请求本身；用户确认恢复后会依据最近的持久化上下文重新请求模型。模型请求可能因此重复计费，但工具副作用按调用日志、幂等键和人工核实规则保护。

## 当前实现

- `TurnStore` 保存 Turn ID、Session ID、Runtime Host、生命周期状态和时间戳；Host 发现过期租约时把残留 `running` Turn 标为 `interrupted`。Store 目前没有按 Session 查询中断 Turn 或原子认领恢复的契约。
- LangGraph SQLite Checkpointer 按 Session 保存 Runtime Chat 的消息增量；消息包含用户输入、模型工具提案和工具结果。消息目前没有稳定的 Turn 关联标记。
- Runtime-native Chat 可在工具派发前记录工具调用，并在结果写入 checkpoint 后完成日志；结果未知的副作用调用会跨请求阻断，现有人工核实可将观察结果写回原工具调用。
- 新 Chat 请求总会创建新 RequestContext 和新 Turn，并向 Session checkpoint 追加一条新的用户消息。没有恢复旧 Turn 的 API 或 WebSocket 事件。
- 逐工具审批已通过 `/ws/chat` 工作；该连接同时支持普通聊天及 Plan 恢复。REST 请求不支持交互式审批往返。

## 目标

- 查询某个 Session 中仍可恢复的 Runtime Chat Turn，并提供足够的时间/状态信息让用户识别它。
- 只有用户明确选择恢复才认领并继续；同一个 Turn 在多 Host 并发恢复时只能有一个成功。
- 恢复时使用原逻辑 Turn ID 和原 checkpoint 关联，以便定位原用户输入、未完成消息和已有工具调用日志，并稳定重用原幂等键。
- 从最新的持久化消息边界继续，不重复写入原用户消息；已持久化工具结果不重复派发。
- 纯读操作按安全策略重试；幂等写只有在原幂等键、请求指纹、Provider 能力及期限均有效时才重放；其他结果未知的写调用在人工核实前保持阻断。
- 恢复创建新的 RequestContext 和预算，并重新执行当前授权、Policy、工具确认与输出校验。
- 提供前端明确恢复入口，恢复期间复用 WebSocket 的逐工具审批协议。

## 非目标

- Host 启动时自动调用模型、恢复 Turn 或派发工具。
- 将普通 `failed`、用户取消的 `cancelled` 或已经完成的 Turn 当作可恢复状态。
- 精确续接已在供应商侧运行、但尚未持久化响应的模型请求；此类请求在用户批准恢复后可能重发。
- 在此阶段恢复显式选择的 `builtin.deepagents` 兼容执行器内部、无法由 Runtime 检查点表达的私有图状态。该路径应明确返回不支持恢复，不得降级成新执行。
- 为 REST-only 客户端另建异步审批协议。REST 可以查询可恢复 Turn；ChatPanel 通过 WebSocket 发起恢复并处理审批。若后续出现 REST-only 客户端需求，再设计启动/查询/决策接口。
- 改变 PlanRun 已有的恢复、工具幂等和人工核实语义。

## 实现方案

### 状态与认领

- 扩展 `TurnStore` Port 和 SQLite 实现，支持按 Session 列出 `interrupted` Turn，以及原子认领：验证仍为 `interrupted`、验证原 Host 已失效、转交给当前 Host 并置为运行中。
- `TurnLifecycleManager` 提供独立的恢复跟踪入口；完成、失败、取消仍记录在原逻辑 Turn 上。恢复后再次发生 Host 中断时，旧 Host 记录仍可被标为 `interrupted`。
- 必须有稳定的 Turn-to-checkpoint 关联。恢复只能选择 checkpoint 尾部仍对应的、没有被后续 Turn 覆盖的中断 Turn；若存在更新的已完成/已开始 Turn，旧记录标为不可恢复并返回安全原因，不能从模糊位置猜测。
- TurnStore 只保存生命周期和关联元数据，不复制聊天正文、工具参数或工具结果。消息继续由现有 Checkpointer 保存。

### 工具调用日志跨 Host 交接

- Turn 被当前 Host 原子认领后，Runtime 从内部 TaskStore 读取该 Session/Turn 的工具调用日志，并把未决记录所有权交给当前 Host；没有完成这一步不得派发工具。
- 已持久化工具提案用原 Turn ID、原逻辑调用序号和原参数重算调用 ID/请求指纹。相同记录保持 `prepared` 并可继续；身份、参数或状态不匹配时停止恢复。
- 对纯读调用允许重试；对幂等写调用，只有原幂等键、参数指纹、当前 Provider 显式能力和期限全部有效时，才使用原键和原参数重放。恢复时不新建随机调用 ID，也不改变键。
- 可能产生副作用的调用在结果 checkpoint 落盘前继续保留 `ToolOutcomeStore` 门禁。安全重放由当前 Host 认领该门禁；结果成功写入原 ToolMessage 后，才清除门禁并完成调用日志。未通过自动恢复判定的写调用仍通过现有人工核实路径解决。
- 恢复上下文的 `snapshot.request_id` 保持原 Turn ID，以复用现有 Runtime Chat 稳定调用身份算法；预算、授权快照、审批提供器和 Runtime Context 仍是本次恢复新建的。

### 恢复执行

1. 前端通过只读 API 查询当前 Session 的可恢复 Turn；API 不返回工具参数、幂等键或结果正文。
2. 用户点击恢复后，ChatPanel 通过 `/ws/chat` 发送 `turn.resume`，包括 `session_id` 和 `turn_id`。
3. 服务端获取 Session 操作租约，校验 Turn 属于该 Session、状态为 `interrupted`、执行器支持恢复，并原子认领 Turn。
4. Runtime 从与原 Turn 关联的 checkpoint 读取消息，构造新的 RequestContext/预算，并以原 Turn ID 作为逻辑调用身份。恢复路径不得再次追加原用户消息。
5. Runtime 从持久化消息增量及工具日志重建最近安全边界。已完成调用使用 checkpoint 结果；没有预写调用日志的操作可在恢复确认后继续；纯读可安全重试；写调用按现有幂等契约重放或等待用户核实。
6. 需要工具审批时，通过当前 WebSocket 发送既有 `tool.confirmation.request` 并等待用户决定；批准仍须通过当前 Policy 和 ToolFacade。
7. 完成/失败响应沿用当前 Chat WebSocket 事件族，包含稳定 `turn_id` 和结果状态，不把 checkpoint 正文或工具参数写入 Trace/Audit。
8. 恢复 WebSocket 在 Turn 完成前断开时，将本次恢复尝试重新记为 `interrupted`，不继续派发后续调用；下次仍须用户明确触发。用户明确拒绝/取消某项工具审批则按用户取消终止该 Turn，不会在下次恢复时再次询问同一调用。

### 失败关闭

- 缺少或无法验证 Turn-to-checkpoint 关联、checkpoint 尾部已经推进、Session/Host 所属关系不符、Turn 已被认领、执行器不支持恢复、请求指纹变化、Provider 幂等能力缺失或幂等期限过期时，不派发相关写工具。
- 结果未知且没有可验证的工具端幂等保证时，继续复用现有人工核实入口；核实前保持 Session 的副作用门禁。
- WebSocket 断开时，恢复 Turn 回到 `interrupted`，下次仍需用户显式触发；工具审批明确拒绝/取消则按用户取消保持终态。

### 文档

- 更新 `docs/roadmap.md` 中仍称 Plan 快照/恢复尚未设计的历史描述，使其与已交付的 PlanRun 恢复及本阶段 Agent Turn 恢复状态一致。
- 更新 ADR-0034 的实施进度，说明 Runtime-native Chat Turn 的恢复范围和兼容 `builtin.deepagents` 的限制。
- 增加测试报告和测试指标记录，明确模型供应商在途请求无法精确续接这一限制。

## 替代方案

1. **恢复原逻辑 Turn（选定）**：从可验证的 Session checkpoint 边界继续，保留 Turn 身份与幂等键；用户需要显式操作，且模型在途请求可能重发。符合用户已确认的“恢复原执行”，与 PlanRun 的恢复确认和当前工具账本一致。
2. **把中断消息作为历史，由新 Turn 继续**：实现简单，但会新建 Turn、重建上下文并可能重新执行任务，不能保证延续原执行身份或复用原工具幂等身份，不满足本次目标。
3. **Host 启动自动恢复**：启动时可能产生模型费用或触发工具副作用，也无法确认用户仍希望原请求继续；与既有用户确认约束冲突，不采用。

## 验收标准

- SQLite 重开/新 Host 启动后，中断 Turn 可被查询，但 Host 启动和查询均不触发模型或工具调用。
- 显式恢复能从原 Turn 的持久化 checkpoint 继续，不重复追加用户输入；完成的工具调用不会重复派发。
- 两个 Host 同时恢复时最多一个能认领并派发。
- 工具结果日志与 checkpoint 的各故障窗口都按纯读、幂等写和非幂等/未知写分类恢复；没有有效幂等保证的未知写保持人工核实门禁。
- WebSocket 逐工具审批在恢复中继续生效；批准、拒绝、断连均有回归覆盖。
- 普通新 Turn、PlanRun 恢复、Session 隔离和 Session 删除语义维持兼容。
- Python 全量测试、Ruff、mypy、前端测试/lint/typecheck/build 和 `git diff --check` 通过；不运行需要真实模型密钥的评测。

## 待批准的边界

- 第一阶段仅为默认 `builtin.runtime-chat` Runtime-native 执行路径提供可恢复 Turn；显式选择 `builtin.deepagents` 的旧兼容路径对中断 Turn 明确提示不支持恢复。若需要兼容路径也能恢复，应另行定义其框架 checkpoint 的恢复契约。
- 恢复 UI 和人工逐工具审批使用现有 WebSocket；REST 只负责查询可恢复 Turn。此阶段不新增 REST-only 恢复审批协议。
