# Plan 与并行 Skill 的共享模型预算

## 实现范围

- 新增统一 `complete_with_budget`，用于 Agent 的 ProviderChatModel、Skill Selector、direct_answer、YAML `llm.complete`、Plan Planner/Verifier/Reasoning task。
- 每个请求的模型调用共享一个异步锁。Provider 调用前检查剩余 Token，并把 `max_output_tokens` 限制在当前额度内；收到用量后在释放锁前记账。
- 模型提供方报告用量超出剩余额度时，请求预算锁存为耗尽；之后排队的模型调用和新工具调用会在 Provider/handler 启动前被拒绝。
- Runtime 消费 Provider 已记账标志，避免 Agent、SkillRuntime 和 ChatService 对同一批 Token 重复扣除。

## 回归覆盖

- 并发提交三个模型请求，Provider 调用保持串行；第一个消耗 6/10 Token，第二个受限为 4 Token 但报告总用量 6 并触发熔断，第三个因预算耗尽未进入 Provider。
- 并行 YAML `llm.complete` 根步骤报告超额时，只进入两个 Provider 请求，第二次请求输出上限为 4 Token；第三个模型步骤和依赖它们的后续 action 均不启动。
- Plan 并行 reasoning tasks 报告超额时，排队模型请求不启动，依赖 reasoning tasks 的后续工具任务不执行。
- 既有 Agent、Skill Selector、Plan、YAML `llm.complete` 用量测试继续通过，覆盖共享记账路径。

## 验证

- 后端全量测试：308 项通过，0 项失败；1 条第三方 Authlib 弃用警告。
- Ruff 全仓检查通过；MyPy 检查 83 个源文件通过。

## 边界

- 请求级模型锁会串行化同一请求内的模型 Provider 调用；YAML/Plan 的非模型步骤仍可并行。
- 如果预算超额只能在一个模型调用返回用量后发现，同一 wave 中已启动的非模型副作用不能追溯撤销，仍由既有取消、快照和补偿协议处理。
- 真实模型冒烟只覆盖一个 DeepAgents Skill；Plan、并行 YAML 与工具任务组合的预算边界由确定性 Provider 测试验证，仍需真实 Provider 集成验证。
