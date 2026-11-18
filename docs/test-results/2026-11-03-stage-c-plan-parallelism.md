# Stage C Plan DAG 并行调度验证

- 日期：2026-11-03
- 范围：Plan 依赖 wave、并发上限、失败状态收集、取消清理和并发审批保护
- 架构依据：[新架构文档](../新架构文档.md)、[ADR-0012](../decisions/0012-business-skill-fast-path-and-plan-fallback.md)

## 实现

- Plan DAG 拓扑排序会生成依赖 wave；wave 内就绪 task 并发执行，所有 wave task 完成后再调度其依赖节点。
- 默认并发上限为 4，可通过 `OrchestratorPlanExecutor(max_parallel_tasks=...)` 调整；无效并发值在构造期拒绝。
- 同 wave 的等价 task 复用同一执行，保留结果、调用工具 ID、副作用和幂等键，避免并发重复执行。
- 一个 task 失败时先收齐当前 wave 已启动任务的结果和副作用，再进入有限重规划；预算耗尽立即终止后续 wave。
- 外部取消会取消并等待同 wave 子协程退出，避免遗留后台 task。WebSocket 确认 Provider 将并发审批请求排队处理，避免多个协程同时读取同一连接。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | 185 passed |
| Ruff lint / 格式检查 | 通过 |
| mypy | 59 个源码文件通过 |
| `uv lock --check` / `git diff --check` | 通过 |
| 前端测试 | 14 passed，5 个测试文件通过 |
| 前端 lint / production build | 通过，build 包含 Vue TypeScript 类型检查 |
| 覆盖率 | 未采集 |

Plan 定向测试覆盖并行根节点、汇聚节点依赖、最大并发数、失败 wave 兄弟任务结果保留和取消清理。API 测试确认并发审批请求串行接收。

执行命令：`.venv/bin/pytest -q`、`.venv/bin/ruff check src/hostpilot tests`、`.venv/bin/ruff format --check src/hostpilot tests`、`.venv/bin/mypy src/hostpilot`、`uv lock --check`、`git diff --check`、`pnpm test`、`pnpm lint`、`pnpm build`。前端命令从 `frontend/` 目录执行。

## 当前边界

- 取消会停止协程，但无法保证已开始的远程工具调用立刻停止；也尚未实现已发生副作用的补偿/回滚协议。
- 并行 task 共享请求预算，各工具调用仍须在 Tool Executor 内同步预留预算；模型/工具真实服务的并行负载和压力阈值尚未做集成压测。
- FastMCP 依赖链报告一条既有 Authlib 弃用警告，不影响验证结果。
