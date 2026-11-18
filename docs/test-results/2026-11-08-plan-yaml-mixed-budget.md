# Plan 与并行 YAML Skill 混合预算验证

## 覆盖场景

- 一次 `orchestrator_plan` 请求先调用模型 Planner，再委派一个含三个并行 `llm.complete` 根步骤的 YAML Skill。
- Planner 与 YAML 步骤使用同一个 `RequestBudget` 和模型调用锁；Provider 收到的输出上限依次受剩余 Token 限制。
- Provider 在第二个 YAML 分支报告超出剩余额度后，请求预算锁存为耗尽；排队的第三个 YAML 分支不启动，Plan 也不继续调用 Verifier 或 Replanner。

## 断言

- Provider 仅收到 Planner 和两个 YAML 模型请求，共 3 次调用。
- 每次输出上限分别为 10、8、5 Token，证明 Plan 与 YAML 共用实时剩余额度。
- 超额报告后预算进入耗尽状态；Plan 返回 `budget_exceeded`，没有启动第三个分支或后续模型阶段。

## 验证结果

- 后端全量测试：321 项通过，0 项失败；1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查 85 个源文件，无问题。
- 前端测试：66 项通过；ESLint、TypeScript 检查和生产构建通过。
- 此用例使用确定性 Provider 替身验证统一预算边界，不调用真实模型或外部服务。

## 边界

- 同一请求的模型调用由共享锁串行；并行 YAML 的非模型步骤仍遵循已有 wave 调度。
- 真实 Provider 的 Plan + 并行 YAML 预算冒烟仍待执行。
