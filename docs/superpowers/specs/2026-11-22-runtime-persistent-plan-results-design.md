# Runtime 已完成 Plan 子任务结果持久化设计

## 意图

进程重启或用户开启新 Turn 后，Plan Planner 可以参考同一 Session 中已完成子任务的结果，重新决定后续工作，避免只能看到“完成”状态却拿不到依赖结果。

## 当前状态

`OrchestratorPlanExecutor` 将 Blackboard 保存在一次执行的内存中。SQLite `TaskStore` 只保存 TaskRun 的标识、状态、归属和时间；Runtime 重启后不会恢复输出、依赖值或 Plan 游标。工具结果不确定时仍由独立的 pending outcome 门禁处理。

## 目标

- 每个成功完成且结果可 JSON 编码的 Plan 子任务，可按 Session 持久化任务说明、显式输入、依赖 ID 和输出。
- 新 Turn 获取有限数量的历史成功结果，并把它们作为 Planner 的参考资料。
- 新 Plan 必须重新生成、验证并执行；历史结果不会自动满足新 Plan 的任务，也不会自动跳过工具调用。
- 失败、取消或中断的 TaskRun 不提供可复用输出。
- 任务结果只通过可替换的 `TaskStore` Port 与 SQLite 实现存取，不把 Plan 游标绑定到 LangGraph。
- 结果只在本地持久层保存，不进入 trace、audit 或 pending tool outcome API。

## 非目标

- 恢复旧 Plan 的 DAG、执行游标或在途任务。
- 自动复用历史结果、自动重试任务、自动重放工具或绕过 Policy、审批和副作用核实。
- 持久化失败/中断任务的输出、工具参数、凭证、原始异常或完整 Blackboard。
- 新增浏览、编辑或删除历史 Plan 结果的 API/UI。
- 本阶段设计记录清理周期；结果历史读取有固定上限，超出上限的旧结果不会作为 Planner 上下文。

## 方案

1. **只保存 TaskRun 元数据（现状）**：不增加结果数据风险，但重启后的依赖结果无法交给新 Plan。
2. **在 TaskStore 中单独保存成功结果，并作为历史规划上下文（选用）**：沿用 Runtime 已注入的可替换存储边界；把成功结果与 TaskRun 原子关联；新 Turn 仅将最近的有限历史作为参考，新 Plan 仍要重新决策并经过现有验证/治理。
3. **持久化整个 Plan/Blackboard 并从旧游标恢复**：能更接近原流程续跑，但需要未决副作用、并发、依赖版本和游标迁移协议；超出当前阶段且可能误重放，不采用。

## 数据与行为

- TaskRun 生命周期表继续只存生命周期元数据。新增独立结果表，按 `task_run_id` 关联成功结果，包含 Session/Turn/Task 标识、任务说明、显式输入、依赖 ID、JSON 输出和完成时间。
- 子任务状态与结果记录在同一 SQLite 事务中更新；只有 `completed` 允许写结果。没有成功结果记录的旧 TaskRun、失败、取消和中断记录都不会进入规划上下文。
- 输出和输入必须是有限大小、有效 JSON 值；超限或不可编码时，结果记录失败并使当前 Plan 报告持久化失败，不静默丢弃依赖数据。SQLite 文件继续使用现有受限文件权限。
- 查询按 Session 隔离，排除当前 Turn，最多读取最近 32 条且总 payload 不超过 65,536 字节，返回后恢复为时间正序。结果数据不包含工具参数、凭证或原始异常；模型仅通过 Planner 请求上下文看到这些历史内容。
- `BlackboardSnapshot` 增加独立 `prior_results`，当前 Plan 的 `results` 仍只包含本次执行已完成的状态，避免历史 Task ID 与新 Plan 的本地 ID 冲突，也避免自动认定历史任务已完成。
- LLM Planner 收到历史结果并明确被指示将其视为可核对的上下文；是否采用由新 Plan、Plan Validator 和后续工具治理决定。
- 存储升级使用 `CREATE TABLE IF NOT EXISTS`，与已有数据库兼容；不修改既有 TaskRun 表结构。

## 验收

- 成功结果跨 SQLite Store/应用重开读取；不同 Session 之间隔离。
- 失败、取消、中断或缺失结果记录的 TaskRun 不会出现在历史结果中。
- Planner 收到历史结果，但新 Plan 任务仍会正常执行，不会因为历史 Task ID 相同而自动跳过。
- 超限/不可编码结果不会被静默标记为可恢复的成功输出。
- 现有工具结果未知门禁仍先于新 Turn 的模型、Skill 和工具执行。
- Python 全量测试、静态检查及文档/差异检查通过。
