# 真实模型 Agent 预算冒烟验证

## 验证范围

使用本地已配置的 OpenAI 兼容模型服务验证 DeepAgents Agent 图的模型调用、Token 用量和低预算停止行为。探针将工具与知识检索预算设为 0，使用用户提供的短代码片段，不触发工具副作用。

## 结果

- 默认 20,000 Token 配置下，Direct 路由请求成功，服务返回 5,772 Token 用量。
- 将临时总预算设为 12,000，并把代码审查 Skill 切换至 DeepAgents 后，模型请求成功完成，路由为 `skill_execute`，总用量 8,371 Token。该流程通过 ProviderChatModel 的动态 `max_tokens` 约束，OpenAI 兼容服务接受了请求。
- 总预算降至 6,000 后，请求返回 HTTP 429 `Request budget exhausted for total_tokens.` Trace 显示代码审查 Skill 已进入 DeepAgents 执行并以 `BudgetExceededError` 结束；工具预算为 0。
- 未在报告中记录 API Key 或模型服务 URL。低预算错误响应不提供逐模型调用用量，因此未记录该请求的精确 Token 数。

## 恢复与清理

- Harness 配置已恢复为探测前值：20,000 Token、5 次工具调用、2 次知识检索，其他字段也已恢复。
- `business/code-review-check` 执行模式已恢复为 `yaml`。
- 两个探针会话已删除；与请求关联的脱敏 Trace 元数据依既有保留策略留存。

## 边界

本次验证覆盖一个 OpenAI 兼容服务和一个只读 DeepAgents Skill。不能据此代表其他服务商对 `max_tokens` 的处理，也未覆盖 Plan 与并行 YAML steps 的混合预算路径。
