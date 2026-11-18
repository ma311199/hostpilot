# 2026-11-12：字段化 BM25 + RRF 活跃 Runtime 首轮评测与复测

## 初次评测：Embedding 降级快照

- 数据集：`stage-c-model-evaluation` v2 的安全子集，14 条；会议纪要 Skill 的 4 条样例因该 Skill 未启用而跳过。
- Runtime：本机活跃 HostPilot Chat；Trace 确认检索版本为 `fielded_bm25_rrf_v1`。
- 模型角色：主模型 `gpt-6-luna`；轻量 LLM 未配置，路由判断回退主模型。Embedding 配置显示 `qwen3-embedding-4b`，Reranker 配置显示 `qwen3-reranker-4b`；两者 API Key 均报告为已配置。
- 命令：`.venv/bin/python scripts/evaluate_live_routing.py --output /tmp/hostpilot-live-routing.json`。
- 报告只包含路由与 Trace 指标，不包含 API Key、请求正文或回复正文。每条评测会话在完成后删除。

## 初次评测指标

| 指标 | 结果 | 说明 |
| --- | ---: | --- |
| 端到端路由正确率 | 12/14（85.71%） | 1 条 clarify 误路由为 Plan；1 条业务 Skill 误路由为 direct |
| 业务 Skill 精确命中 | 3/4（75.00%） | 当前仅启用 `business/code-review-check` |
| RRF 候选 Recall@1 / @3 / @5 / @10 | 3/4（75.00%） | 4 条相关 Skill 样例；被漏掉的目标未进入候选 |
| RRF MRR | 0.750 | 命中的 3 条均排在第 1 位；1 条未召回 |
| Reranker 后 Recall@1 / @3 / @5 / @10 | 3/4（75.00%） | 与 RRF 相同；只有一个业务 Skill 启用，无法衡量多候选重排能力 |
| Reranker 后 MRR | 0.750 | 同上 |
| 业务 Gate 覆盖率 | 3/4（75.00%） | 3 条被尝试的业务样例均通过 Gate |
| 业务 Gate 精确率 | 3/3（100%） | 样本量很小，不能据此判断生产可靠性 |
| 延迟 P50 / P95 | 9.751 / 47.917 秒 | 包含完整 Chat Runtime；P95 主要受一次 Plan 误路由影响 |
| 报告 Token 总量 | 81,953 | 评测脚本从响应读取的 Token 用量 |
| 已执行工具调用 | 0 | 没有工具副作用发生 |

## 初次评测的降级状态与失败分析

- 12 条触发检索的请求中，Embedding 全部失败：10 次为 `APIConnectionError`，2 次为 `InternalServerError`。虽然配置状态和 API Key 状态均显示已配置，但活跃 Runtime 未能成功完成一次向量检索。因此本结果是“向量服务不可用时，字段化 BM25 与业务路由的降级运行结果”，**不是 Embedding 正常工作的完整目标链路基线**。
- Reranker 在 3 条有业务候选的请求中调用成功。其余 9 条 Trace 中的 `not_configured` 是因为候选列表为空，selector 不会发起 Reranker 请求；不能将这 9 条解释为模型凭证不可用。
- 轻量 LLM 未配置。12 次需要路由模型判断的请求均由主模型完成，符合已设计的主模型回退路径。
- `skill-code-003` 的目标 Skill 没有进入候选，最终实际路由为 direct。Trace 同时记录 Embedding `InternalServerError` 和空的 RRF 候选列表。单 Skill 目录下此例足以暴露召回缺口，但需修复 Embedding 后重跑，才能判断混合召回本身的完整效果。
- `clarify-002` 期望澄清缺失的代码内容，实际被路由到 Plan，路由错误。该 Plan 提出两次 `run_shell_script` 请求；两次均被确认层拒绝，相关 4 个 Trace 事件对应 2 次被拒绝的调用，没有工具执行或副作用。
- 评测脚本的 `unexpected_tool_events=4` 统计的是上述两次调用各自的 `tool.confirmation` 与失败 `tool.span` 事件，不等于 4 次工具执行。
- 其他 12 条路由符合标注。四条澄清样例中有一条错误，澄清子集为 3/4；direct 子集为 5/5。

初次全量评测时 Embedding 12 次请求全失败，且 `skill-code-003` 因目标 Skill 未进候选而走 direct。该运行结果保留为降级快照。

## 中文元数据更新后的复测

初次评测后，在[代码审查 Skill 清单](../../src/hostpilot/skill_packages/code-review-check/skill.yaml)中增加了中文检索示例，覆盖“代码评审”“代码规范和潜在缺陷”“代码变更缺陷检查”等表达。使用活跃 Runtime 对 `clarify-002`、`skill-code-003` 定向复测，两例均符合 v2 标注；随后用相同的 14 条安全子集重跑完整评测。

| 指标 | 初次降级运行 | 元数据更新后全量复测 | 口径 |
| --- | ---: | ---: | --- |
| 端到端路由正确率 | 12/14（85.71%） | 13/14（92.86%） | 数据集 v2，活跃 Skill 安全集 |
| 业务 Skill 精确命中 | 3/4（75.00%） | 3/4（75.00%） | 同 4 条代码审查样例 |
| RRF Recall@1/@3/@5/@10 | 3/4（75.00%） | 4/4（100%） | 同 4 条相关 Skill 样例 |
| RRF MRR | 0.750 | 1.000 | 同上 |
| Reranker 后 Recall@1/@3/@5/@10 | 3/4（75.00%） | 4/4（100%） | 同上 |
| Reranker 后 MRR | 0.750 | 1.000 | 同上 |
| Gate 覆盖率 / 精确率 | 3/4 / 3/3 | 3/4 / 3/3 | 同上 |
| 延迟 P50 / P95 | 9.751 / 47.917 秒 | 13.387 / 29.086 秒 | 端到端请求；环境负载可能不同 |
| 报告 Token 总量 | 81,953 | 84,736 | 每轮共 14 条请求 |
| Embedding / Reranker | Embedding 12 次失败；Reranker 3 次成功 | 两者均各成功 12 次 | 另 2 条确定性澄清未触发检索 |
| 被拒绝工具请求 / 已执行工具 | 2 / 0 | 0 / 0 | 首轮请求都来自 `clarify-002` 的误 Plan |

更新后的全量复测中唯一错误为 `skill-code-004`：RRF 和 Reranker 均将 `business/code-review-check` 排在第 1 位，但分类器选择了 direct，因此没有业务 Gate 事件。候选召回已恢复到 4/4，当前主要错误转为召回后分类决策。Gate 的 3/3 精确率仍只有 3 个正例，且单 Skill 目录无法验证多候选竞争或 margin 判别。

随后单独重跑 `skill-code-004` 一次，仍为 direct；Embedding 和 Reranker 均成功，RRF 与 Reranker 排名均为第 1，Gate 仍未触发。该失败已在两次活跃请求中复现，不能只按单次偶发误差处理。

Embedding 和 Reranker 在复测的 12 次真实请求中均完成，证明当前活跃配置可以调用两者。初次失败与后续成功的差异提示服务状态可能暂时不稳定；需要继续观察连续运行表现，不能据单轮成功推断稳定可用性。

## 结论与下一步

中文元数据更新后的 14 条复测建立了当前活跃配置的首个完整召回基线：端到端路由 13/14，RRF 与 Reranker 后候选 Recall@K 均为 4/4，MRR 均为 1.0。初次 Embedding 故障结果保留为降级快照，不与该完整链路结果混为同一次运行。样本量和单 Skill 目录都有限，结果只代表当前评测集。

下一轮应观察分类置信度与候选分数的一致性、重复请求的稳定性，并扩充业务 Skill 目录后再测多候选 Reranker 排序和 margin。评测集 v3 已按新规则将 `clarify-002` 标注为业务 Skill 执行层澄清，v2 的结果保留为旧标注历史。

## 基线后的实现更新与本地验证

- 分类器提示现在明确：业务意图匹配但必填项缺失时仍选择业务 Skill，只传入已有字段，由 Skill 执行层询问缺失项；代码审查请求要将已提供的代码放入 `code_or_diff`。
- 新增脱敏 Trace 事件 `skill.classification`，记录分类器决策、决策置信度、最高业务候选及其分数和 margin。活跃评测脚本会采集这些字段，并默认使用评测集 v3，便于诊断候选召回正确但分类器选择 direct 的情况。
- v3 `clarify-002` 在首次预检时，活跃服务尚未加载新代码，返回内置 `clarify` 且没有分类 Trace。用户重启并重新配置模型后，完整 v3 复测确认新提示生效。
- 提示词、Trace 及评测集调整的定向测试 21 项通过，Ruff 检查通过，Mypy 100 个后端源文件通过。其后的 v3 活跃 Runtime 结果见下节。

## v3 活跃 Runtime 复测

用户重启服务并重新配置主模型、Embedding 和 Reranker 后，使用当前默认评测集 v3 执行完整安全子集：

```bash
.venv/bin/python scripts/evaluate_live_routing.py --output /tmp/hostpilot-live-routing-v3.json
```

| 指标 | v3 结果 | 说明 |
| --- | ---: | --- |
| 端到端路由正确率 | 14/14（100%） | 包括业务 Skill 执行后的缺槽位澄清 |
| 业务 Skill 精确命中 | 5/5（100%） | 4 条代码审查样例 + 1 条缺少代码的澄清样例 |
| RRF Recall@1/@3/@5/@10 | 5/5（100%） | 单 Skill 注册目录，不能代表多候选竞争 |
| RRF MRR | 1.000 | 所有相关目标排第 1 |
| Reranker 后 Recall@1/@3/@5/@10 | 5/5（100%） | 与 RRF 排名相同 |
| Reranker 后 MRR | 1.000 | 同上 |
| Gate 覆盖率 / 精确率 | 5/5 / 5/5 | 所有业务样例均通过；小样本指标 |
| 延迟 P50 / P95 | 14.092 / 27.677 秒 | 完整 Chat Runtime |
| 报告 Token 总量 | 91,127 | 14 条请求 |
| Embedding / Reranker | 各 12/12 次成功 | 2 条确定性澄清未触发检索 |
| 已执行工具调用 | 0 | 没有工具调用或副作用 |

分类决策计数为 direct 5、clarify 2、business Skill 5；12 次模型分类均由主模型完成，符合轻量 LLM 缺失时回退主模型的策略。5 个业务 Gate 的 top confidence 和 margin 均在 0.97–0.99，超过 0.85 / 0.30 阈值。当前只启用一个业务 Skill，因此 margin 对多候选区分的效果尚未验证。

`clarify-002` 被正确分到 `business/code-review-check`，Gate 通过；执行层发现缺少 `code_or_diff` 后向用户澄清，响应路由为 `clarify`，执行 Skill ID 仍是该业务 Skill。`skill-code-004` 在 v3 复测中由 direct 改为 `business_skill`，Gate 通过并执行 Skill，先前分类偏差在新提示词下未复现。

v2 和 v3 的 `clarify-002` 标注口径不同，结果用于各版本各自的判定，不将两版百分比作为严格同集优化对照。v3 结果作为当前路由约定下的首轮活跃 Runtime 基线；会议纪要 4 条仍因未启用 Skill 而跳过。

本轮活跃进程持久化了分类决策、top confidence 和 margin。分类 Trace 的其他新增属性在服务启动后才加入本地安全字段白名单，因此本次活动进程未持久化 top candidate ID、决策置信度和打分候选数；对应代码和持久化单测已更新，下次服务重启后可采集这些附加字段。本轮候选身份仍可从检索排序 Trace 与已执行 Skill ID 交叉确认。
