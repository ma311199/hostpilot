# 本地配置 RAG MCP 工具验证

- 日期：2026-10-31
- 范围：本地 RAG 配置校验、FastMCP query-only 工具、后端凭证注入和 EvidenceSet 响应

## 实现结果

- `RAG_BASE_URL`、`RAG_USER_ID` 和 `RAG_KEY` 必须同时配置；`RAG_KEY` 继续以 `SecretStr` 保存。
- 配置完整时，`retrieve_knowledge` 调用 `RagRetrievalClient`；Agent 的 MCP schema 仅含 `query`。
- 后端按当前接口基线将 `user_id`、`key` 和 query 发送到配置的检索服务，并将通过 Pydantic 校验的 EvidenceSet 返回给 Agent。
- 没有 RAG 配置时保留“知识库暂未开启”提示；不增加依赖。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 完整后端质量脚本 | Ruff、mypy（49 个源文件）、uv lock 检查通过；119 passed |
| RAG MCP 集成测试 | MockTransport 验证服务路径、凭证注入、query-only schema 和 EvidenceSet 返回 |
| 配置测试 | 完整配置接受、不完整配置拒绝、RAG_KEY 使用 SecretStr |
| 覆盖率 | 本轮未采集 |

FastMCP 依赖仍会输出一条上游 Authlib 弃用警告，不影响验证结果。

## 待真实服务联调

当前实现遵循仓库已记录的接口基线：POST `{RAG_BASE_URL}{RAG_RETRIEVAL_PATH}`，JSON 请求体包含 `user_id`、`key`、`query`，响应映射为 EvidenceSet。连接实际服务前，仍需核对其 URL、请求认证字段和响应 JSON；如接口不同，再调整客户端映射。
