# Runtime Plan 持久恢复与工具幂等验证（2026-11-22）

## 范围

为 Runtime Plan 增加跨 Host 的持久恢复，并在恢复边界按工具副作用等级和显式幂等契约决定重试或人工核实。保留同一 `plan_run_id` 和已验证 DAG/Blackboard；新的 RequestContext 使用当前预算、授权和策略。

## 行为与数据边界

- PlanRun 在执行前以版本化快照持久化 DAG、目标、任务和 Blackboard；Host 启动只将失效 Host 的运行记录标为 `interrupted`，不会自行恢复。
- UI 必须由用户确认恢复。页面经 `/ws/chat` 发送 `plan.resume`，恢复期间沿用现有工具审批消息；HTTP 恢复接口也要求显式 POST，但没有交互审批 Provider 时高影响工具仍会失败关闭。
- 工具在进入 Provider 前持久化逻辑调用记录、稳定调用 ID、规范参数指纹、重试类别、幂等键（如有）和精确重放参数。没有写前记录时，恢复边界确定调用未派发；存在未决记录时，纯读可重试，写调用只有在支持声明、请求指纹和幂等期限均有效时才使用相同键重放。
- 不满足幂等条件的调用暂停等待当前用户核实。用户报告“已执行”必须附实际观察结果，存为 `user_reported` 且明确未独立验证；确认“未执行”后重开原逻辑调用，不生成随机替代键。
- API 仅返回任务进度和调用安全元数据。调用参数、幂等键和用户报告结果留在 SQLite，不进入 Trace/Audit；Session 删除时同步清理。使用本机数据库现有权限，不声称静态加密。
- 旧 TaskRun 没有 PlanRun DAG/Blackboard 快照，不能自动恢复。通用 MCP/STDIO 未声明 HostPilot 幂等适配时，不会自动重放不确定写操作。

## 故障与修复记录

- 首轮恢复回归发现 TaskRun 解析器误读只属于工具调用的 `reported_result`，6 项测试失败；将该字段映射移到 `ToolInvocationRecord` 后，恢复相关 Task/Plan/API 测试通过。
- API 恢复测试发现 ChatService 创建新 RequestContext 时漏传原 `plan_run_id`，会使恢复入口被当成新 Plan；测试先失败后补齐字段，跨 Host API 集成验证通过。
- 自审发现 REST 恢复没有 WebSocket 审批 Provider。为保持高影响工具逐项确认，ChatPanel 改为通过共享聊天 WebSocket 发送显式恢复事件；HTTP 接口保留 fail-closed 行为。新增 WebSocket 审批测试覆盖请求、批准和恢复结果。
- 补充工具恢复测试验证 `user_reported` 结果交给后续步骤时不会重复派发，`not_executed` 会重开原调用再执行。
- 新增 SQLite 关闭并重开后的 Plan 幂等重放集成测试：模拟远端写入已发生但本地结果尚未落库，恢复后 Provider 收到原幂等键和相同参数，并由外部幂等账本返回既有结果；没有产生第二次外部副作用。

## 最终验证

- Python：`bash scripts/check_quality.sh` → **708 passed**；含 API/SQLite Host 重启恢复、幂等键重放和人工核实测试。存在 1 条既有 Authlib 弃用警告。
- Ruff：全仓 `ruff format --check` 与 `ruff check src tests` 通过。
- mypy：128 个源码文件通过；`uv lock --check` 通过。
- 前端：Vitest **89 passed**（18 个文件）；ESLint、TypeScript 检查及 Vite 生产构建通过。
- `git diff --check` 通过。
- 没有运行需要真实模型密钥的评测。故障和 Provider 路径使用确定性 fake，不产生真实外部副作用。没有单独生成 coverage 百分比，本报告按定向行为和全量通过数记录。

## 限制

外部服务没有幂等键契约时，Runtime 无法判断带副作用调用是否已发生，只能等待人工核实。REST 恢复接口不能交互审批高影响工具；浏览器使用 WebSocket 恢复以复用逐工具确认 Provider。SQLite 恢复数据遵循现有本机文件权限和 Session 删除策略，未提供静态加密。
