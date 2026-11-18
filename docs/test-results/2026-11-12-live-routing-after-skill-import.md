# 2026-11-12：新增 Skill 后的在线路由复测

## 环境与范围

- 通过本机运行中的 HostPilot Chat API 执行，使用 `stage-c-model-evaluation` v3 固定安全子集；报告文件由评测脚本写到 `/tmp/hostpilot-live-routing-2026-11-12.json`。
- 模型角色：主模型 `gpt-6-luna`；轻量 LLM 未配置，路由判断按降级策略使用主模型；Embedding 为 `qwen3-embedding-4b`，Reranker 为 `qwen3-reranker-4b`。三种已配置角色在本次请求中均报告凭证已配置；报告未读取或保存密钥。
- 运行目录发现 3 个有效用户 Skill：`legacy/multi-search-engine`、`legacy/office-automation`、`legacy/ppt-generator`；无无效 Skill 包。
- 固定集实际运行 14 条：普通问答、澄清和只读代码评审；4 条会议纪要用例因对应的 `business/meeting-minutes` 未启用而跳过。
- 本固定集没有覆盖这 3 个新增 Skill。未执行它们的实际任务；本次请求没有发生工具调用。

## 结果

| 指标 | 结果 |
| --- | ---: |
| 端到端路由准确率 | 14/14（100%） |
| 业务 Skill 精确命中 | 5/5（100%） |
| 业务 Gate 通过 / 精确率 | 5/5（100% / 100%） |
| RRF 相关 Skill Recall@1 / @3 / @5 / @10 | 3/5 / 5/5 / 5/5 / 5/5 |
| RRF MRR | 0.800 |
| Reranker 后相关 Skill Recall@1 / @3 / @5 / @10 | 2/5 / 5/5 / 5/5 / 5/5 |
| Reranker 后 MRR | 0.600 |
| Embedding / Reranker 成功调用 | 各 12 次 |
| 请求延迟 P50 / P95 | 17.475 / 30.640 秒 |
| 已报告 Token 总数 | 96,812 |
| 意外工具事件 | 0 |

## 分析与限制

- 当前 v3 运行的端到端路由与业务 Skill 精确选择均为满分；RRF 与重排候选中都能找到 5 个相关 Skill。
- 本次重排后 MRR 从 RRF 的 0.800 降到 0.600，Recall@1 从 3/5 降到 2/5；应检查逐条 reranker 排名和候选集合，不能仅凭端到端结果判定重排效果良好。
- 与先前 v3 活跃 Runtime 记录相比，评测数据集与评测流程相同，但当前用户 Skill 集合和模型角色配置已变化，指标应作为新的运行快照，不能直接归因于某一项配置变更。
- 这轮不代表新增三个 Skill 的执行质量，也没有覆盖其脚本沙箱调用、外部网络访问或文件生成行为；需要单独设计受控用例后评测。

## 可复核数据

- 原始汇总及逐条结果：`/tmp/hostpilot-live-routing-2026-11-12.json`（运行环境临时文件，不纳入版本库）。
- 执行命令：`.venv/bin/python scripts/evaluate_live_routing.py --output /tmp/hostpilot-live-routing-2026-11-12.json`
