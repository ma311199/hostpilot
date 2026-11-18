# ADR-0031：双路 Skill 召回、轻量路由模型与本地 ChromaDB

- 状态：已采纳
- 日期：2026-11-11
- 相关：[ADR-0029](0029-three-skill-pipelines.md)、[路由评测计划](../stage-c-model-evaluation.md)

## 背景

当前顶层 Skill Selector 使用模型在 builtin、YAML Skill 与 Markdown Skill 候选中分类；业务 Skill 使用混合召回。此决策按 2026-11-11 的模型配置修订更新，模型降级规则见下文。

上下文指代规则已由 [ADR-0032](0032-contextual-reference-resolution.md) 修订：语义指代可在检索前从受限的会话与记忆候选中进行模型匹配。

## 决定

- 普通请求先经确定性语义消解：完整请求保留原文；“继续”、代词等上下文依赖请求结合最近任务焦点及会话摘要形成检索文本。只有上下文候选唯一时才补入上下文；明显不可解析或存在多个指代对象时直接澄清并跳过 Skill 检索。该阶段不调用模型。显式 `/read`、`/write` 命令继续由命令路由优先处理。
- 仅业务 Skill 进入候选池；`direct_answer`、`clarify` 和 `orchestrator_plan` 不参加业务召回。
- 业务候选优先使用 BM25 与 Embedding 双路召回，并可由配置的 Reranker Model 重排；Embedding 不可用时退化为 BM25 召回。候选排序和最终分类使用配置的轻量 LLM；轻量 LLM 缺失或不可用时回退到主 LLM。主 LLM 是唯一必需的模型配置，只有主 LLM 时系统仍可完成语义路由与任务处理。
- Embedding 向量和 Skill 元数据内容哈希存储于应用数据目录内的本地 ChromaDB。索引只包含 Skill 路由元数据，不保存用户消息或会话内容；内容变更时增量更新，Embedding 模型变更时使用新的集合或重建索引。
- Gate 只在 top1 置信度 `>= 0.85` 且 top1 与 top2 的置信度差 `>= 0.30` 时走唯一业务 Skill 快速路径。业务方向存在但不满足唯一高置信条件时进入 Plan；所有候选均为低相关时再判断直答、澄清或 Plan。
- 业务 Skill 快速路径选定后，必需输入槽位缺失由 Skill 执行层返回 Skill 相关澄清，不启动该 Skill 的副作用步骤。
- 置信度分数必须由统一的重排/裁判输出产生；BM25 分数和向量距离只用于召回，不直接与 `0.85` 或 `0.30` 阈值比较。通过固定评测集持续检查分数校准和 Gate 精度。
- 业务 Skill 数量较大时，可在双路召回前增加本地业务标签规则过滤；标签只缩小候选集，不授予工具权限。

## 影响

- 模型通过统一的模型配置页面按 LLM Model、轻量 LLM Model、Embedding Model、Reranker Model 顺序配置；可选角色缺失或不可用时按降级策略切换，不创建独立的路由配置页面。
- 每个模型角色独立配置服务地址和 API Key，凭证不会跨角色复用。非敏感设置本地持久化；Embedding 与 Reranker Key 跨重启保存在本地 SQLite，主 LLM 与轻量 LLM Key 仅保存在后端进程内存。主 LLM 为必需配置；其他模型均为可选能力。密钥生命周期详见 [ADR-0015](0015-model-config-persistence-with-ephemeral-api-keys.md)。
- Embedding Model 使用 OpenAI 兼容的 `/v1/embeddings`；Reranker Model 使用 llama.cpp 兼容的 `/v1/rerank`。Reranker 分数只用于候选排序，不作为 Gate 置信度；业务准入置信度仍由轻量 LLM 或主 LLM 给出。Reranker 不可用时保留混合召回顺序并继续模型判断。
- 新增 ChromaDB 本地持久索引。索引缺失或失效时可从 Skill 元数据重建；Embedding 不可用时使用 BM25，不因可选模型故障停止用户请求。
- 模型调用均纳入请求预算。降级不得绕过预算、授权、Schema 或工具策略；当主 LLM 不可用时返回明确错误，不伪造成功结果。
- 评测覆盖 query 消解、双路召回、top1 置信度、margin、全低 fallback、缺槽澄清和路由模型不可用行为。

## 模型降级顺序（2026-11-11 修订）

1. **LLM Model** 是必须配置的最终推理与处理模型。
2. **轻量 LLM Model** 用于低成本路由和候选判断；未配置或调用失败时使用 LLM Model。
3. **Embedding Model** 用于业务 Skill 语义召回；未配置或调用失败时使用 BM25 词项召回。
4. **Reranker Model** 用于重排已召回候选；未配置或调用失败时保留 BM25/Embedding 混合召回顺序。候选置信度判断仍由轻量 LLM 负责，轻量 LLM 不可用时再回退到 LLM Model。
5. 每一级回退记录非敏感原因码和所用角色；任何调用仍受原请求的 Token、时间预算约束。只有 LLM Model 时，语义路由使用 BM25 候选并由 LLM Model 判断，不会因缺少可选模型而直接跳过模型处理。

## 复核条件

用脱敏、人工标注的业务路由数据集验证召回覆盖、top1 准确率、margin 分布、业务快速路径误路由率和全低 fallback 后，再调整阈值、候选数量或模型类型。若小模型准确率不达标，单独评估大模型路由，不默认启用。
