# 2026-11-11：Embedding 与 Reranker 连通性检查

## 当前配置

- Embedding Model：`qwen3-embedding-4b`，`http://127.0.0.1:8080/v1`，运行时状态显示 API Key 已配置。
- Reranker Model：`qwen3-reranker-4b`，`http://127.0.0.1:8081/v1`，运行时状态显示 API Key 已配置。
- 主模型：`gpt-6-luna`，状态显示已配置。
- 轻量 LLM 未配置；路由 fallback 使用主模型。
- 没有读取或输出任何 API Key。

## 检查结果

- 两个服务的 `/health` 和 `/v1/health` 均返回 HTTP 200，说明服务可达并报告健康。
- 未携带认证访问两个服务的 `/v1/models` 均返回 HTTP 401，说明接口要求认证；该探测不验证应用中保存的密钥。
- 使用当前 HostPilot Chat Runtime 提交只读代码评审请求后，路由返回 `skill_execute` 并选中 `business/code-review-check`；请求报告 10,487 Token。Skill 最终返回 `unknown_error`，因此该次请求不能算端到端执行成功。
- 当前 Trace 只记录最终 Skill 与 Chat 结果，没有 Embedding/Reranker 的逐角色调用结果。业务路由成功可能由 Reranker 或主模型 fallback 完成；Embedding 故障也会静默回退 BM25。因此，现有在线探测**尚不能分别证明两个模型的认证和推理调用成功**。
- 初次检查时发现旧实现错误地把 Reranker 当作 Chat 模型。现已改为调用 llama.cpp `/v1/rerank`；Reranker 只重排候选，业务置信度仍由轻量 LLM 或主 LLM 判断。
- 2026-11-12 后端重启并重新配置凭证后，两种角色的配置状态均为已配置。两条只读代码评审请求通过 Chat Runtime，均选择并完成 `business/code-review-check`；其中一条是此前误澄清样例的原始输入，现已在线通过。

## 结论与后续

- 用户已分别本地验证两个 llama.cpp 模型可用，应用重启后也已用当前配置完成路由与只读 Skill 执行。
- Trace 未区分 Embedding、Reranker 与主模型 fallback 的单独结果；模型角色级调用状态仍待增加可观测记录。
