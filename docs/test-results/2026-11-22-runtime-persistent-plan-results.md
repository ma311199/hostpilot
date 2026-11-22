# Runtime Plan 子任务结果持久化验证

## 范围

成功 Plan 子任务的说明、显式输入、依赖 ID 和 JSON 输出由 `TaskStore` 与 TaskRun 完成状态原子写入独立结果表。新 Turn 只把同一 Session 的历史结果作为 Planner 参考；当前 Plan Blackboard、任务状态、执行游标和工具重放策略保持独立。

单项结果最多 65,536 字节；Planner 每次最多接收最近 32 项，历史结果总 payload 不超过 65,536 字节。结果写入失败、不可 JSON 编码或超限时，不会将其暴露为可复用成功结果。失败、取消和中断 TaskRun 不提供历史输出。

## 回归用例

- SQLite Store 重开后读取完成结果；当前 Turn 与其他 Session 的结果不会泄漏进历史查询。
- 查询返回最近结果窗口并按时间正序排列，且总 payload 有大小上限。
- 失败状态不能附带成功结果；超限结果的状态更新回滚，TaskRun 保持非完成状态。
- 新 Turn 的 Planner 收到历史结果；即使新 Plan 的 task_id 与历史 ID 相同，也不会自动跳过当前任务。
- Task Executor 收到的 Blackboard 不含历史结果；历史数据只提供给 Planner/Replanner 重新判断。
- Planner 输入清楚标注历史输出只是参考，不代表本 Turn 已执行或完成任务。

## TDD 记录

- 持久层测试先因缺少 `CompletedPlanTaskResult` 和结果查询接口失败，然后由独立结果表和事务式完成更新通过。
- 新 Turn 集成测试先因 `BlackboardSnapshot` 没有历史字段而失败，接入 TaskLifecycle/Planner 后通过。
- 聚合大小测试先观察到 3 个各约 50 KB 的结果全部进入规划上下文，超过上限；加入总 payload 上限后只返回最近一项。
- 导入检查发现 Persistence Port 与 Skill 模块循环依赖，数据契约移至 `hostpilot.task_results` 中立模块后解决。

## 验证结果

- 定向持久层、TaskLifecycle、Plan、模型 Planner、State Services 和重启集成：**64 passed**（1 条既有 Authlib 弃用警告）。
- 全量 Python 测试：**648 passed**（1 条既有 Authlib 弃用警告）。
- Ruff 全仓 lint：通过。
- mypy：128 个源码文件通过。
- 改动的 11 个 Python 文件 Ruff format 检查：通过。
- `git diff --check`：通过。

## 边界与后续

这项工作没有恢复旧 Plan DAG、在途任务或执行游标。新 Turn 的 Planner 可以参考旧结果，但必须生成并验证新的 Plan；Task Executor、Validator 和当前 Blackboard 不会把历史结果当作当前任务完成状态。结果数据保留在本地 SQLite，目前没有设置过期清理策略。
