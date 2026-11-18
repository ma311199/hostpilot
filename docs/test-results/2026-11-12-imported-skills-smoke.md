# 2026-11-12：新增用户 Skill 在线冒烟测试

## 范围与环境

- 测试对象：用户目录中的 `legacy/multi-search-engine`、`legacy/office-automation`、`legacy/ppt-generator`，均已启用；Skill Catalog 返回 3 个用户 Skill、`invalid_package_count=0`。
- 测试集：[imported-skills-smoke-v1.yaml](../evaluations/imported-skills-smoke-v1.yaml)，每个 Skill 两条只输出文本的低影响用例，明确要求不联网、不创建文件、不发送邮件。
- 通过本机 Chat API 和完整 Runtime 测试，未直接执行脚本，也没有批准工具调用。
- 页面与配置 API 显示主模型 `gpt-6-luna`、Embedding `qwen3-embedding-4b`、Reranker `qwen3-reranker-4b` 已配置，轻量 LLM 未配置，主模型作为路由裁判降级方案。

## 结果

| Skill | 完成用例 | 路由/执行结果 | 检索结果 | 延迟 |
| --- | ---: | --- | --- | ---: |
| 多引擎搜索 | 3 次尝试 | 配置重绑后且临时提高请求预算时，成功路由并执行 Skill（文本结果，无工具调用）；20k 预算下执行因 `BudgetExceededError` 返回 429 | RRF 第 1；Reranker 后第 2；Embedding/Reranker 均 `completed`，语义候选 4 | 20k 预算约 38.7 秒失败；50k 预算 27.082 秒成功 |
| 办公自动化 | 重启后 2 条 | 0/2 命中：两条均路由到 `builtin/direct_answer`；重启前一次旧代码运行曾把 `business_skill` 分类结果转成 clarify | 第一条 RRF 第 1、重排第 3；第二条 RRF/重排均第 3；Embedding/Reranker 均 `completed` | 22.527 / 17.991 秒 |
| PPT 生成 | 2 次尝试 | 初测时分类器选中 Skill，但执行异常；重新绑定模型后，20k 预算仍因 `BudgetExceededError` 失败，临时提高预算后成功输出大纲 | RRF 第 1；Reranker 后第 3；Embedding/Reranker 均 `completed`，语义候选 4 | 20k 预算约 26.7 秒失败；50k 预算 27.810 秒成功 |

- 初测中，搜索实际路由到 direct，办公分类器选中 Skill 但最终走 clarify，PPT 分类器选中 Skill 但执行失败。重新绑定模型后，搜索和 PPT 在 50k 临时预算下均端到端成功；同样请求在现有 20k 预算下会因 `BudgetExceededError` 失败。办公 Skill 的分类器类型兼容修复已加入代码及单元测试，但需重启后才能对在线 Runtime 复测。
- 批量运行 6 条用例时 API 在第二条请求返回 `429 Too Many Requests`，未产生完整汇总；后续改为单条运行。另一次 PPT 单条请求发生上游 `ReadError`，Trace 同样记录执行失败。以上失败不计为成功样本。
- 初测时在线状态接口显示 Embedding/Reranker 已配置且 API Key 已在进程内可用，但 Skill Retrieval Trace 两者均为 `not_configured`，语义候选数为 0。向配置 API 重新提交相同的模型名和 endpoint（不传密钥，沿用进程内凭证）后，Trace 显示 Embedding/Reranker 均 `completed`、语义候选数 4。重新绑定修复了当前进程的检索状态；根因仍需通过启动与模型重建顺序的自动化回归确认。
- 多引擎搜索 Skill 带有约 31.7k 字符的 Python 脚本资源。测试时先将 Harness Token 预算从 20k 临时调至 50k、超时从 60 秒调至 120 秒，完成单条测试后立即恢复原值；搜索与 PPT 成功请求分别报告 30,601 和 24,458 Token。结果显示当前 20k 预算不足以完成这两个示例任务。50k 仅用于诊断，未保留为用户配置。
- Reranker 在搜索和 PPT 用例中分别把相关 Skill 从 RRF 第 1 降至第 2 和第 3；需使用更完整的样本集分析重排质量。
- 重启后重新验证了 API 状态和办公用例：主模型、Embedding、Reranker 凭证均可用，Harness 已恢复 20k/60 秒；请求 Trace 中语义召回与重排完成。办公 Skill 两条用例均被判为 direct（0/2），因此分类器当前未能选择该广义办公 Skill。重启前观察到的 `business_skill` → clarify 属于不同一次模型选择；兼容分支单测通过，但在线结果没有触发该分支。

## 失败分析与后续

1. 为模型配置后创建 Runtime、创建 Runtime 后配置路由模型两种顺序增加启动/重建集成回归，确认 Selector 绑定状态与配置 API 一致。
2. 改善办公 Skill 的意图选择：重启后两条近似任务都判为 direct；此前一次错误分类为 `business_skill` 时，旧 Selector 又会把 Markdown ID 当作 YAML 包并转入 clarify。已在本地 Selector 加入兼容逻辑：仅当所选 ID 是当前可用 Markdown Skill、且不属于 YAML Registry 时，才按 DeepAgents 入口执行；对应单元测试通过。另加强提示：只输出文本本身不应降低匹配 Skill 的优先级。两项代码改动发生在本轮运行服务启动之后，需再次重启才能在线复测；兼容分支也仍需业务 Skill 分类命中才能触发。
3. 分析 PPT Skill `OperationalError` 及上游 `ReadError` 的服务端异常详情；不要将分类命中当作执行成功。
4. 多引擎搜索在重绑后被 Reranker 从 RRF 第 1 降到第 2；扩展用例后复核重排排序质量。
5. 为这批用户 Skill 评估合适的 Token/超时预算；另明确将 Skill 源码传递到内置沙箱的运行契约。当前成功用例均未实际调用工具或执行脚本。

## 可复核数据

- 数据集：`docs/evaluations/imported-skills-smoke-v1.yaml`
- 多引擎搜索原始报告：`/tmp/hostpilot-imported-multi-search-2026-11-12.json`
- 办公自动化原始报告：`/tmp/hostpilot-imported-office-2026-11-12.json`
- PPT 失败没有生成评测汇总 JSON；Trace ID：`f9e2f591-59d5-4168-ad3c-ef6bba60285b`
- 路由配置重绑后的搜索请求 Trace ID：`a042c23d-f6bb-4e3c-8dcc-a76abca0e47a`
- 50k 临时预算搜索成功原始报告：`/tmp/hostpilot-imported-search-budget50k-2026-11-12.json`
- 50k 临时预算 PPT 成功原始报告：`/tmp/hostpilot-imported-ppt-budget50k-2026-11-12.json`
- 重启后办公用例原始报告：`/tmp/hostpilot-imported-office-post-restart-2026-11-12.json`
- 重启后办公第二条原始报告：`/tmp/hostpilot-imported-office-post-restart-2-2026-11-12.json`
- 最新路由提示规则单测：`.venv/bin/pytest -q tests/unit/routing/test_skill_selector.py`，18 passed；当前运行服务未加载本次随后修改的代码。
- 批量运行命令：`.venv/bin/python scripts/evaluate_live_routing.py --dataset docs/evaluations/imported-skills-smoke-v1.yaml`
- Skill Registry、ZIP 导入、DeepAgents 适配器和 Selector 定向测试：**62 passed**。Ruff、格式检查及 Selector 文件 Mypy：通过。

## 2026-11-13：最新路由提示在线复测

- 重启后确认主模型、Embedding、Reranker API Key 均已配置，3 个用户 Skill 均启用，Harness 请求预算恢复为 20k Token / 60 秒。
- 两条用例按单请求方式复测，均路由到 `legacy/office-automation`，端到端路由正确 **2/2**，没有工具调用；分类器置信度分别为 0.92 和 0.98。
- 两条请求的 Embedding/Reranker 均 `completed`，语义候选数为 4。RRF 排名分别为第 1 和第 3，Reranker 后均为第 3；Office 两条用例 MRR 从 RRF 的 0.667 降为重排后的 0.333，显示重排排序仍需优化。
- Token 分别为 9,982 和 9,874；延迟分别为 38.875 秒和 19.631 秒。2 条样本仅作为新提示版本的冒烟结果，不足以建立稳定准确率基线。此前另一条 Office 样例在旧提示版本下路由到 direct，不混入该版本指标。
- 原始报告：`/tmp/hostpilot-imported-office-latest-2026-11-13.json`、`/tmp/hostpilot-imported-office-latest-2-2026-11-13.json`。

## 2026-11-13：Reranker 排名退化分析与修复

### 原因

- 两条 Office Trace 中，Reranker 原始顺序都把 `business/code-review-check` 放在第 1，尽管用户请求是周报整理和虚构销售数据汇总，没有要求代码审查。第一条的 RRF 顺序以 Office 为第 1，Reranker 将其降至第 3；第二条 Office 在 RRF 与 Reranker 中均为第 3。
- 映射代码按 API 返回的 `index` 对应输入候选，定向测试已验证索引及分数配对正确；目前没有证据表明是候选索引映射错误。
- 代码检查发现两个可修复的薄弱点：送给 Reranker 的候选只是 name、description 等字段的无标签拼接；且系统完全用 Reranker 排名覆盖 BM25+向量 RRF 排名，使单个不相关的高分候选能大幅改变排序。

### 修复

- Reranker 候选改为带字段标签的文本，例如 `name:`、`description:`、`tags:`，避免丢失字段语义。
- 最终顺序改为有分歧保护的加权 RRF 融合：只有 Reranker 首选同时位于 RRF 前两名时才融合，原始 BM25+向量 RRF 权重为 3、Reranker 权重为 1；若首选排在 RRF 前两名之外，则完整保留 RRF 顺序。
- Trace 保留 Reranker 原始候选顺序 `reranker_raw_skill_ids` 和分数摘要 `reranker_score_summary`，融合后的顺序仍记为 `reranked_skill_ids`；评测报告分别计算原始 reranker 排名和融合排名。检索 Trace 版本升级为 `fielded_bm25_rrf_reranker_fusion_v2`。
- Trace 增加 `reranker_fusion_applied`，标明本次是否应用融合。基于此前两条 Office 的 RRF/原始 reranker 顺序离线重放，保护规则下 Office 排名应为第 1 和第 3，与 RRF 基线持平。离线重放不等于新代码调用真实 reranker 的在线结果。
- 为区分已运行的 v2 与新增保护规则，Trace 版本提升为 `fielded_bm25_rrf_reranker_guarded_fusion_v3`；在线评测脚本同时接受 v1、v2、v3。

### 验证与后续

- 分歧保护回退、支持时融合、字段标签和 Trace 标记定向测试：**27 passed**；Ruff 检查、格式检查和全量源代码 Mypy：通过。
- 2026-11-13 再次在线运行两条 Office 用例，路由和 Skill 命中均为 **2/2**，RRF MRR **0.667**、原始 Reranker MRR **0.292**、最终排名 MRR **0.625**。但响应 Trace 中 `reranker_fusion_applied` 缺失，且第二条结果仍是第 4 名（保护规则应直接回退到 RRF 第 3 名）；因此确认当前服务进程没有加载新增分歧保护。该在线结果仅验证当时运行中的 v2 加权融合，不作为新保护规则的在线验证。
- 本次在线报告：`/tmp/hostpilot-office-reranker-fusion-v2-live-2026-11-13.json`。需重启加载当前代码后再跑相同用例，确认 Trace 版本为 v3、包含 `reranker_fusion_applied` 且第二条回到 RRF 第 3 名；随后扩展搜索、PPT 和代码审查样例。
