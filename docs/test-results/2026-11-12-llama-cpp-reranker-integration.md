# 2026-11-12：llama.cpp Reranker 接入

## 变更

- 将 Reranker 从 OpenAI Chat provider 改为通过配置的 Base URL 调用 `/rerank`，请求使用 `model`、`query` 和已召回候选文档；Authorization 使用该角色单独保存的 API Key。
- Reranker relevance score 只用于候选排序，不被当作路由置信度，也不参与 0.85 / 0.30 Gate 计算。
- Reranker 未配置或调用失败时保留 BM25/Embedding 混合排序；轻量 LLM 负责分类和置信度判断，未配置或不可用时由主 LLM 处理。
- Embedding 仍使用 OpenAI 兼容 `/v1/embeddings`。

## 验证

- `tests/unit/routing`：30 项通过，包含 `/v1/rerank` URL、请求体、Bearer 凭证、响应排序和候选重排测试。
- API 与路由模型配置持久化定向测试：59 项通过。
- Mypy：98 个后端源文件通过；相关 Ruff 与 `git diff --check` 通过。
- llama.cpp 服务 `/health` 返回 HTTP 200；用户确认两个模型的本地推理验证均已通过。
- 用户重启后，模型配置状态显示 Embedding 与 Reranker 的 API Key 均已配置。两条真实 Chat Runtime 只读代码评审请求都路由到 `business/code-review-check` 并成功完成，分别报告 12,437 与 12,377 Token；第二条使用此前误澄清的原始提示，在线回归确认路由到了 Skill。

## 限制

- 在线 Chat Runtime 路由及 Skill 执行已通过；Trace 尚未提供逐角色调用结果，无法从应用记录中区分 Reranker 成功与其失败后由主模型 fallback 的情况。用户已在本地分别验证两个模型可用。
- 需执行完整 v2 路由评测，并补充逐角色调用的可观测结果。
