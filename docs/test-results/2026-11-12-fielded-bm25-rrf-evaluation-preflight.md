# 2026-11-12：字段化 BM25 + RRF 评测准备与服务版本检查

## 实现与本地验证

- 路由候选现使用字段化 BM25、description 向量召回与 1:1 RRF；候选同时覆盖启用的 YAML 和 Markdown Skill。
- 增加活跃本机 Chat Runtime 评测脚本：仅提交预先标注的安全样例，通过 Trace 读取路由、候选排名、模型角色和降级状态；不读取或保存 API Key、请求正文或回复正文。每例会话在评测后删除。
- Trace 增加 `skill.retrieval`、`skill.routing_model`、`skill.classification` 和 `skill.business_gate` 诊断事件，允许字段仅包含算法版本、Skill ID、候选排名、分类决策、置信度、模型角色、状态、原因码、分数门槛结果和耗时。
- 路由、Trace 与评测指标定向单测：40 项通过。
- Ruff 检查和格式检查通过；Mypy 100 个后端源文件通过。

## 活跃服务预检

- 本机 Chat API 状态：主模型 `gpt-6-luna` 已配置；轻量 LLM 未配置；Embedding `qwen3-embedding-4b` 和 Reranker `qwen3-reranker-4b` 状态为已配置。
- 用 `direct-001` 安全样例验证了请求、响应和 Trace 查询链路：期望与实际路由均为 `direct`，耗时 12.377 秒，报告 6,066 Token；没有工具事件。
- Trace 中没有 `fielded_bm25_rrf_v1` 版本标记，因此不能确认当前运行进程已加载新的检索与观测代码。该样例仅为服务预检，不计入新算法准确率、Recall@K、MRR 或延迟基线。

## 活跃服务评测尝试

- 执行时间：2026-11-12（本机时区）；数据集：`stage-c-model-evaluation` v2；命令：`.venv/bin/python scripts/evaluate_live_routing.py --output /tmp/hostpilot-live-routing.json`。
- 成功完成 14 条安全子集请求：端到端路由与标注一致 14/14；其中启用的代码审查 Skill 样例精确命中 4/4。会议纪要 Skill 的 4 条样例因当前未启用而跳过。
- 报告延迟 P50 为 10.836 秒、P95 为 25.653 秒，累计报告 Token 为 90,097；没有意外工具事件。
- 该进程的 Trace 未包含任何 `skill.retrieval`、`skill.business_gate` 或路由模型事件，也未报告检索版本。因此候选 Recall@K、MRR 和 Gate 覆盖率没有可用数据；端到端路由结果只能说明当前运行服务对这组用例的行为，**不作为字段化 BM25 + RRF 的质量基线，也不与旧路由结果作直接比较**。
- 评测报告写入 `/tmp/hostpilot-live-routing.json`，该文件不包含请求/回复正文或凭证。脚本因缺少新算法 Trace 标记以退出码 2 结束，这是预期的版本保护行为。

## 后续

2026-11-12 已完成服务重启、模型密钥重新提交和完整安全子集评测；新 Trace 确认 `fielded_bm25_rrf_v1` 已加载。活跃 Runtime 结果及 Embedding 失败分析见[本轮评测报告](2026-11-12-fielded-bm25-rrf-live-baseline.md)。该预检记录保留为旧进程版本检查，不作为新算法基线。

当前活动目录只有 `business/code-review-check` 一个启用 Skill；评测集中的会议纪要 Skill 样例会自动跳过。因此后续结果可以验证端到端回归，但候选排序指标的样本覆盖仍有限，不能代表大规模 Skill 目录的表现。
