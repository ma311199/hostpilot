# Stage C 取消补偿协议验证

- 日期：2026-11-03
- 范围：YAML Skill/Plan cancellation snapshot、可逆副作用补偿注册表、Plan fallback 前补偿
- 架构依据：[ADR-0012](../decisions/0012-business-skill-fast-path-and-plan-fallback.md)、[Skill Runtime ADR](../decisions/0009-skill-runtime-and-yaml-contract.md)

## 实现

- 新增 opt-in `CompensationRegistry`。只有副作用声明为 `reversible`、具备 `compensation_handle` 且有 HostPilot 侧注册 handler 时才允许自动补偿。
- 补偿按完成顺序逆序执行，并收到 request context、预算与 confirmation provider；handler 必须继续经过 Tool Executor/Policy Engine/审批链。
- Skill workflow 和 Plan DAG 被取消时，已完成的副作用尝试补偿；in-flight 操作由于结果未知，不会盲目反向调用。
- `ExecutionCancelled` 作为取消异常传播，附带 `CancellationSnapshot`（scope、已知副作用、未确定的操作、补偿结果），Skill cancellation 还保留 Runtime 快照。
- 业务 Skill 结果评估要求 Plan fallback 时，只有所有可逆副作用都有注册 handler 才会继续；Runtime 会先补偿，成功后再交给 Plan。补偿缺失或失败时转人工复核。
- 默认 YAML MCP action 暂按 `irreversible` 处理，未注册任何具体 MCP 补偿 handler；框架不会推测工具的逆操作。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | 190 passed |
| Ruff lint / 格式检查 | 通过 |
| mypy | 60 个源码文件通过 |
| `uv lock --check` / `git diff --check` | 通过 |
| 前端测试 | 14 passed，5 个测试文件通过 |
| 前端 lint / production build | 通过，build 包含 Vue TypeScript 类型检查 |
| 覆盖率 | 未采集 |

专项覆盖包括 Skill 和 Plan 取消时的已完成 reversible side effect 补偿、Plan 中 in-flight task 的结果不确定状态、逆序调用、缺失 handler fail-closed、fallback 前补偿，以及补偿结果记录。

执行命令：`.venv/bin/pytest -q`、`.venv/bin/ruff check src/hostpilot tests`、`.venv/bin/ruff format --check src/hostpilot tests`、`.venv/bin/mypy src/hostpilot`、`uv lock --check`、`git diff --check`、`pnpm test`、`pnpm lint`、`pnpm build`。前端命令从 `frontend/` 目录执行。

## 限制

- 当前没有为默认 MCP action 注册补偿 handler；不可逆工具和没有可信逆操作的工具只记录待复核状态。
- cancellation snapshot 随异常传播，不写入持久化审计库；上层必须捕获 `ExecutionCancelled` 并记录快照，当前 Chat API 尚未持久化该记录。
- 远端工具收到取消后未必停止执行；若取消发生在调用完成响应之前，结果归入 `uncertain_operations`，不能保证已成功补偿。
- 补偿同样受请求预算和确认 Provider 约束；预算已耗尽、连接已断开或补偿需要额外批准时，可能无法自动完成并应由调用方人工处理。

FastMCP 依赖链报告一条既有 Authlib 弃用警告，不影响验证结果。
