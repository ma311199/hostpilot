# Agent 图内模型预算熔断

## 实现范围

- `LangGraphAgentRuntime` 在请求的 ContextVar 作用域内运行 Agent 图，让深层 ProviderChatModel 调用读取同一个 `RequestBudget`。
- ProviderChatModel 每次请求前检查剩余预算，并将请求的最大输出 Token 数限制为配置值与剩余额度中的较小值。
- 收到 Provider 的 Token 用量后立即记账；如果真实用量超过剩余额度，当前图调用失败，后续工具/模型节点不会继续。
- `AgentResponse.budget_usage_accounted` 透传记账状态；SkillRuntimeAgentAdapter 与 ChatService 跳过已逐次记账的用量，防止重复扣除。

## 验证

- 新增测试覆盖 Provider 余量上限、模型响应超限拒绝、Runtime 逐调用记账和 Skill 适配层避免重复记账。
- 后端全量测试：305 项通过，0 项失败；1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查 82 个源文件通过。

## 后续验证

- 当前为 Provider 契约和 LangGraph 边界的离线测试；需用真实模型确认各 OpenAI 兼容服务均遵守 `max_tokens` 参数。
- YAML Skill、Plan、Agent 图混合编排中的多 Provider 调用仍需进一步端到端评估。
