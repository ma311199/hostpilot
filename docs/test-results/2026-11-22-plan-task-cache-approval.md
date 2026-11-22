# Plan 子任务缓存审批验证（2026-11-22）

## 范围

实现并验证同一 Session 内成功 Plan 子任务的精确匹配、逐项审批、历史输出展示、审批后续依赖注入、失败摘要隔离和 Trace 记录。没有审批决定时不复用，也不执行命中的子任务。

## 关键行为

- 版本化 SHA-256 指纹包含执行类型、Skill、规范化任务说明、预期输出、显式输入和已解析依赖输出；查询限定当前 Session，并排除当前 Turn。
- 每个命中通过聊天 WebSocket 展示历史输出、来源 Turn 和完成时间。明确选择“复用并跳过”才填入当前 Blackboard；明确选择“忽略并执行”才进入 Task Executor。
- 缺少审批 Provider 或等待超时，返回澄清并停在命中任务前；断连取消 Turn。Trace 记录候选 TaskRun 来源和选择，不写入结果正文。
- Planner 不再接收历史成功结果集合；只获得同 Session 最近最多 16 条、总量不超过 16,384 字节的失败摘要。摘要只包含任务说明、稳定错误码和副作用计数，不含失败输出、原始异常或任务输入，也不自动触发重试。

## 失败、修复与复测

1. 初次存储实现的迁移顺序在首次建库时可能先检查不存在的结果表；调整为先建表，再为旧表增加指纹列，并增加旧 schema 迁移测试。
2. 初轮集成测试发现 `ChatService.send` 尚未接收新的审批 Provider 参数，导致聊天 API 调用失败；补全从 WebSocket 到 RequestContext 的 Provider 传递，并复测 API/WebSocket 现有套件。
3. 旧 Planner 仍会收到成功历史结果；改为只传递失败摘要，并更新 Planner 与 Orchestrator 契约测试。
4. 复核发现“无 Provider/超时按 execute”仍会绕过用户要求的显式选择；改为 `cache_decision_missing`，当前任务不复用也不执行，并提示从交互式聊天查看和选择。两个明确决定分别验证了跳过/执行次数、TaskRun 数和下游依赖输出。
5. 最终静态检查指出审批超时返回类型和格式差异；修正类型并完成格式化。
6. 合并前独立复核发现失败摘要存储边界仍接受非空输入、远程结果不确定异常可能进入普通失败重规划；持久化层现在拒绝任何非空失败输入，`RemoteToolOutcomeUnknownError` 会终止当前 Plan 且不产生失败参考。另让指纹保留依赖 ID 与输出的映射，并增加映射互换测试。

## 最终验证

- 全量 Python：**661 passed**，1 条既有 Authlib 弃用警告。
- 前端 Vitest：**85 passed**（18 个测试文件）。
- 前端 ESLint、TypeScript 类型检查和 Vite 生产构建：通过。
- 全仓 Ruff lint 与 format check：通过；mypy：128 个源码文件通过；`git diff --check`：通过。

## 限制

本轮没有进行真实模型评测、完整 Chat WebSocket 路由往返或浏览器端到端自动化；UI 交互由前端单测、WebSocket Provider 协议单测和现有 Chat API/WebSocket 回归套件覆盖。用户确认通过 Trace 查看命中来源与决定；Trace 不展示缓存结果正文，正文只在等待决定时通过 WebSocket 提供。
