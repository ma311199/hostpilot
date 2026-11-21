# Runtime 工具结果核实与跨请求恢复设计

## 意图

当工具可能已经产生外部影响，但结果没有写入 checkpoint 时，Runtime 必须持续阻止同一 Session 的后续执行，直到用户核实结果。核实出的结果要能进入 checkpoint，让后续新 Turn 可依赖它；Runtime 不自动重放工具，也不恢复原 Turn 的执行游标。

## 当前行为

Runtime Chat 在工具调用前把 Assistant 工具提案写入 checkpoint。工具结果写入 checkpoint 失败后，下一次请求能识别缺失结果并澄清一次，但该澄清作为普通 Assistant 消息保存后，之后的新请求可能再次进入模型。系统没有持久的未决调用记录，也没有提交人工核实结果的入口。此前的故障注入与边界记录见 `docs/test-results/2026-11-21-unknown-tool-outcome-recovery.md`。

## 目标与非目标

- 对可能产生副作用的工具，在执行前持久登记未决调用；登记失败时不得调用工具。只读工具不登记，可在结果未保存时安全重试。
- 未决调用存在时，后续 Chat Turn 在模型、Skill 和工具执行之前被确定性阻断。
- 提供本地 Web 界面，让用户选择“确认已执行并填写结果”或“确认未执行”；尚无法确认时保持阻断。
- 把人工核实结果作为与原工具调用关联的 ToolMessage 写入 checkpoint；checkpoint 成功后才能清除未决状态。
- 未决记录只覆盖“工具已获准派发，但结果尚未可靠落入 checkpoint”的窗口；结果可靠保存后清除该记录，不把它作为永久调用历史。
- 工具副作用由 `ToolDescriptor` 的显式字段分类；副作用未知时默认按“可能产生副作用”处理。现有文件读取、目录列表、文件名搜索和知识检索工具明确标为只读。
- 保留现有工具策略、预算、审批、审计和 Session 租约约束。
- 不自动重放工具，不自动恢复 Plan/Agent 执行游标，不声称人工输入已被外部系统独立验证。

## 方案

1. **只在普通聊天提示一次。** 复用现有 checkpoint 消息检测，改动较少；但新的普通消息仍会消除检测边界，无法跨请求持久阻断，也没有可靠的结果交接，不能满足目标。
2. **持久化未决调用，并提供明确核实入口（选用）。** Runtime 在调用工具前写入持久记录；ChatService 对该 Session 的后续 Turn 先检查记录；用户通过本地 UI 提交已核实结果或确认未执行。核实结果以原调用 ID 幂等写入 checkpoint，随后才解除阻断。多 worker 通过持久认领避免同时解决同一调用。
3. **依靠模型判断用户回复是否为核实结果。** 可以复用自由文本聊天，但不能作为安全门：模型可能误读普通消息，也无法保证每个入口都遵守阻断，因此不采用。

## 状态与数据

- 未决调用仅为可能产生副作用的调用创建，按 `(session_id, tool_call_id)` 唯一标识，至少保存 `turn_id`、可选 `task_run_id`、工具名、创建时间和状态；不保存工具参数、凭证或工具结果正文。其用途是崩溃恢复门禁，不是全量历史审计。
- 状态从 `pending` 到短时 `resolving`，再到已清除；`resolving` 认领归属于 Runtime Host，可在 Host 租约过期后接管。
- 调用工具前必须成功写入 `pending`。ToolMessage 成功进入 checkpoint 后才清除 `pending`。若在 checkpoint 成功但清除标记失败之间崩溃，下一次核实流程可读取 checkpoint 中现有 ToolMessage 并幂等完成清除。
- 人工核实“已执行”要求提供实际结果文本；“未执行”写入固定的核实结果，供新 Turn 判断后续动作。两种选择都不会直接调用工具。后续如需再次调用，仍由新的模型提案、现有 Policy 和审批流程决定。
- 若人工结果已写入 checkpoint，但状态清除失败，重复提交相同结果可完成收尾；与 checkpoint 中现有结果不一致时拒绝覆盖。
- 结果正文只进入既有 Session checkpoint，不复制进新的生命周期表。若 SQLite/checkpoint 的底层持久介质不可用，无法保证写入未决记录；因此必须在登记失败时 fail closed，且报告此故障限制。
- 任务调度恢复需要额外持久化 Task 状态、依赖关系和可读取的输出或输出引用；单独一个成功/失败布尔值不足以供依赖任务继续。本阶段不持久化完整 Plan Blackboard、结果快照或执行游标；Turn/TaskRun 与工具事件的历史观测继续由现有生命周期元数据和 Trace/Audit 承担。
- 只读工具（例如 `read_text_file`、`read_text_range`、`list_directory`、`search_files`）在结果未能 checkpoint 时不创建未决状态；Runtime 可再次执行它们。`ToolType.ACTION` 不能单独表达副作用，因此必须依赖单独的副作用分类，未分类的第三方/远程工具默认受保护。

## API 与交互

- 增加按 Session 列出未决调用的只读 API，响应只包含调用 ID、工具名、发生时间和待核实状态。
- 增加带 Session 租约的显式核实 API，接受 `completed`（需结果正文）或 `not_applied`，并返回更新状态。未知 Session/调用为 404，重复或并发冲突为 409。
- ChatPanel 进入会话时加载未决调用并显示醒目的阻断卡片；聊天提交遇到未决状态时显示安全提示，保留草稿。
- 用户选择“暂时无法核实”时不提交请求，未决状态保持不变。

## 安全与兼容性

- 所有 Chat 路由都必须在 Selector、模型、Skill 和工具之前经过未决调用门禁；route-only 评测不执行工具，沿用现有只读行为。
- 核实 API 使用现有本地 API 来源校验与 Session 生命周期租约，不改变对话既有请求/响应字段。
- SQLite Store 由 State Services 插件注入，Runtime 只依赖 Port；测试覆盖多 Store/多 Host 共享数据库。
- 不承诺发现用户填写结果的真实性；界面明确这是人工核实输入。保持未核实时，任何新 Turn 都不能绕过阻断。

## 验收

- pending 写入失败时工具执行次数为零。
- 工具执行后 checkpoint 写入失败，跨新 Runtime/新请求仍阻断模型、Skill 与工具。
- 已核实结果幂等进入正确 ToolMessage，后续新 Turn 可读到它；没有重复工具 handler 调用。
- 未执行结论进入关联 ToolMessage，下一步仍需普通模型提案并经过工具治理。
- 并发核实、Host 崩溃期间认领接管、重复提交、checkpoint 与状态更新之间崩溃均保持 fail closed。
- API/Web UI 展示和提交核实状态；凭证、参数及结果正文不会出现在未决调用表/API 列表中。
