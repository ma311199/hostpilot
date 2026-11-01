# FastMCP 工具链、Token 用量与 RAG 检索首版验证

- 日期：2026-10-31
- 范围：FastMCP MCP 工具接入 DeepAgents、Tool Executor 治理拦截、模型实际 Token 用量、RAG 配置与检索适配器。

## 实现结果

- FastMCP 2.14.7 与 langchain-mcp-adapters 0.3.2 已加入 agents 依赖组并锁定。
- Agent 调用 MCP 工具时经过 Tool Registry 参数校验、Policy Engine 授权、Tool Executor 预算/超时控制和结果验证。请求级 RequestBudget 显式传入 LangGraph runtime context。
- 模型返回的 usage_metadata 或 OpenAI 兼容 token_usage 按当前用户轮次累加；保留提供方报告的 input、output、total 数值，写入聊天响应和 CostEvent，并计入请求 Token 预算。超预算时返回 HTTP 429。
- RAG MCP 工具模型输入 schema 仅有 query；后端读取 RAG_USER_ID、RAG_KEY 并发送 POST 请求，凭证不进入模型 schema 或日志。RAG_KEY 使用 SecretStr。响应受 1 MiB 上限保护，并按 EvidenceSet 校验。
- RAG 地址、路径、超时可通过 RAG_BASE_URL、RAG_RETRIEVAL_PATH、RAG_TIMEOUT_SECONDS 配置；默认路径为 /retrieve。

## 验证结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 完整质量脚本 | bash scripts/check_quality.sh | Ruff、mypy（48 个源文件）、uv lock 检查通过；103 passed |
| 覆盖率 | uv run --locked --group dev --group agents pytest --cov=src/hostpilot --cov-report=term | 103 passed，整体 86%；tools/mcp.py 85%，retrieval/client.py 93%，orchestration/chat.py 98% |
| MCP 工具治理 | FastMCP 本地工具服务与拦截器单测 | schema、预算共享、缺失预算拒绝、策略拒绝和结果验证通过 |
| RAG 客户端 | HTTPX MockTransport 单测 | 密钥字段、路径配置、EvidenceSet 校验、错误脱敏通过 |
| Token 计量 | AIMessage usage metadata 单测 | 多次模型调用汇总当前轮次，并保留提供方 total_tokens |

## 未覆盖范围与待联调事项

- 未调用真实模型供应商；Token 计量使用 provider message metadata 的本地夹具验证。
- 未连接真实 RAG 服务；当前按 POST {RAG_BASE_URL}{RAG_RETRIEVAL_PATH}、JSON 字段 user_id/key/query 和 EvidenceSet 响应实现。拿到服务地址与响应样例后需验证并调整契约。
- FastMCP 导入产生 Authlib 的上游弃用警告；测试和类型检查通过。
