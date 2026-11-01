# Chat API 与 ContextBuilder 验证记录

- 日期：2026-10-30
- 范围：SQLite 会话检查点读取、Markdown 记忆上下文、LangGraphAgentRuntime、消息提交/历史 API、免登录写请求来源校验
- 模型供应商：未选择；自动化测试使用注入的 fake AgentRuntime 和确定性 LangGraph 节点，不访问真实模型服务

## 结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 完整质量检查 | scripts/check_quality.sh | 通过 |
| 格式 | Ruff format check | 53 files already formatted |
| Lint | Ruff check | All checks passed |
| 类型检查 | mypy src/hostpilot | Success: no issues found in 39 source files |
| 单元与集成测试 | pytest -q | 53 passed |
| 锁文件一致性 | uv lock --check | Resolved 113 packages |

## 覆盖范围

- ContextBuilder 按 conversation_id 校验会话，读取该 thread 的消息、会话摘要和共享长期记忆，并限制消息数量及 Markdown 字符数。
- LangGraphAgentRuntime 以 conversation_id 对应的 thread_id 执行一轮消息并从检查点恢复可见消息。
- DeepAgents runtime 将 Markdown 摘要与长期记忆作为每轮运行时上下文动态注入，并在提示中标记为不可信参考数据。
- FastAPI 提供 POST 消息和 GET 消息历史；测试用 runtime 验证请求上下文与返回结果。
- 未配置 AgentRuntime 时，消息 API 返回 503；带有不允许 Origin 的写请求返回 403。
- DeepAgents 调用保留现有 Skill 快照注入和 SQLite checkpointer 参数。

## 已知缺口

- 真实模型供应商、模型实例及凭证尚未配置；按 [模型运行时接入记录](2026-10-30-model-runtime.md) 配置 OpenAI 兼容服务后可启用聊天接口，未配置模型时仍返回 503。
- Chat API 为非流式响应；SSE/流式传输和前端联调未实现。
- MemoryWriteProposal 策略审核、Markdown 摘要自动生成及长期记忆更新流程未接入。
- 生产预算扣减、工具策略执行、Trace / Cost / Audit 导出仍属于后续 L0–L4 主流程。
