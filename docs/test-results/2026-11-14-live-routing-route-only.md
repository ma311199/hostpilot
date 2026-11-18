# 2026-11-14：活跃路由 Route-only 组合评测

## 范围与环境

- API：本机 HostPilot，重启后加载 route-only 评测接口。
- 模型角色：主模型 `gpt-6-luna`；轻量 LLM 未配置；Embedding `qwen3-embedding-4b`；Reranker `qwen3-reranker-4b`。
- 数据集：`stage-c-model-evaluation` v3 与 `imported-skills-smoke` v1。
- 命令：`.venv/bin/python scripts/evaluate_live_routing.py --output /tmp/hostpilot-live-routing-route-only.json`。
- 模式：只执行 Skill Selector，不执行 Skill、Plan、工具或最终回答；评测上下文不包含共享长期记忆。
- 覆盖：选中 20 条用例；4 条未启用的 `business/meeting-minutes` fixture 用例跳过。当前启用的 4 个 Skill 均有目标样例。

## 结果

| 指标 | 结果 | 说明 |
| --- | ---: | --- |
| 路由选择准确率 | 18/20（90%） | 两条澄清用例被路由到办公自动化 Skill |
| 业务 Skill 精确命中 | 11/11（100%） | 代码审查 5 条，用户 Markdown Skill 6 条 |
| RRF Recall@1 / @3 / @5 / @10 | 8/11 / 11/11 / 11/11 / 11/11 | 目标 Skill 全部进入前 3 |
| RRF MRR | 0.8485 | 11 条目标 Skill 用例 |
| 原始 Reranker Recall@1 / @3 / @5 / @10 | 2/11 / 10/11 / 11/11 / 11/11 | 未应用保护融合前的排名 |
| 原始 Reranker MRR | 0.4470 | 原始重排在本轮明显弱于 RRF |
| Guarded fusion 后 MRR | 0.8485 | 保护规则保留 RRF 排序，未让低位候选重排覆盖前列 |
| YAML 业务 Gate 覆盖率 / 精确率 | 5/5 / 5/5 | 只对 YAML 执行路径统计；Markdown Skill 不适用此 Gate |
| Embedding / Reranker 成功 | 18/18 / 18/18 | 两条确定性澄清没有请求检索模型 |
| 分类模型调用 | 主模型 18 次 | 轻量 LLM 未配置 |
| 报告中的模型 Token | 73,468 | 分类模型返回用量；未包含 Embedding/Reranker 未报告的用量 |
| 延迟 P50 / P95 | 10.437 / 15.978 秒 | 包含 route-only HTTP 处理与路由模型调用 |
| 工具事件 | 0 | 符合隔离预期 |

## 失败样例与判断

- `clarify-001`（“帮我整理成会议纪要。”）和 `clarify-003`（“把这段内容整理一下。”）都选中了 `legacy/office-automation`，而评测标签期望顶层澄清。
- 当前办公自动化 Skill 的描述覆盖文档处理、数据整理和会议纪要。依照“业务意图匹配但缺少输入时仍选择 Skill、由执行层澄清”的路由规则，`clarify-001` 的标签可能需要改为“命中 Skill 后澄清”。`clarify-003` 仍可能因为目标材料与格式都不明确而应顶层澄清。两例应人工复核后再作为模型缺陷或标注修订处理。
- 其余 18 条路由符合现有标注。所有 11 个业务目标都被 RRF 召回；原始 Reranker 对候选排序较差，guarded fusion 保留了 RRF 最终排序。

## 报告完整性说明

全部 20 个模型请求完成，无 HTTP 错误。首轮汇总因脚本从错误层级读取 `expected.skill_id`，把覆盖率误记为 0 并返回不完整状态；随后修复统计逻辑，并从已保存的逐例结果重算覆盖字段，没有重复模型请求。新增单元测试覆盖该字段读取路径。更新后的机器报告保存在 `/tmp/hostpilot-live-routing-route-only.json`。

## 新版 Skill 分流后的复测

重新配置本机模型后，使用相同评测集再次运行 route-only，以检查新版路由选择。该轮不会执行 Skill、Plan 或工具；机器报告保存在 `/tmp/hostpilot-live-routing-after-skill-plan-fallback.json`。

| 指标 | 结果 | 说明 |
| --- | ---: | --- |
| 路由选择准确率 | 18/20（90%） | 20 条完整，4 条未启用的会议纪要 Skill 样例跳过 |
| 业务 Skill 精确命中 | 10/11（90.9%） | `clarify-002` 预期命中代码审查 Skill 后由执行层澄清，实际顶层澄清 |
| RRF Recall@1 / @3 / @5 / @10 | 8/11 / 11/11 / 11/11 / 11/11 | 目标 Skill 全部进入前 3 |
| RRF / guarded fusion MRR | 0.8485 / 0.8485 | Guarded fusion 保留 RRF 排序 |
| YAML 业务 Gate 覆盖率 / 精确率 | 4/5 / 4/4 | `clarify-002` 未进入 Skill Gate |
| 分类模型调用 | 主模型 17 次 | 确定性澄清样例没有请求模型 |
| 报告中的模型 Token | 69,989 | 不含 Embedding / Reranker 未报告用量 |
| 延迟 P50 / P95 | 9.314 / 16.148 秒 | route-only HTTP 处理与模型调用 |
| 工具事件 | 0 | 符合隔离预期 |

本轮两条偏差：

- `clarify-001`“帮我整理成会议纪要。”被路由到 Plan，候选中的 `legacy/office-automation` 置信度为 0.76。Route-only 不能验证 Plan 最后是否会追问会议记录，因此当前只能确认顶层路由与评测标签不一致。
- `clarify-002`“帮我做一次代码评审。”的标注要求选中 `business/code-review-check`，再由 Skill 执行层询问代码或 diff。检索把该 Skill 排在 RRF 第 1；模型给它 0.98 候选置信度，却选择了顶层 `clarify`，并且分类 Trace 只记录了一个候选评分。该分类没有进入业务 Gate 或 Plan 兜底，是当前需要处理的路由契约缺口。

本轮启用的 4 个 Skill 均被目标样例覆盖，检索版本为 `fielded_bm25_rrf_reranker_guarded_fusion_v3`；所有 20 个请求均完成，没有工具调用。该结果是路由选择基线，不表示 Skill Runtime 或 Plan 的端到端质量已经验证。

## 后续

- 已修正 `clarify-002` 暴露的选择器缺口：分类为 `clarify` / `direct_answer`、评分结果不完整且存在高置信 YAML 候选时，选择器转 Plan 并携带 `skill_routing_evaluation_failed`。48 项定向测试通过；此处 90% 路由指标仍是修改前基线，需重启 API 后复测确认。
- 复核 `clarify-001` 是否应接受 Plan 路由；如果评测期望仍是澄清，需要确保 Plan 最终向用户询问会议记录。
- 增加能区分通用办公 Skill 与顶层澄清的近似边界样例，再测多候选竞争与跨轮稳定性。
- 不根据本轮小样本调整 0.85 / 0.30 Gate；当前高置信 Gate 只覆盖代码审查 YAML Skill。

## 选择器兜底修正后的在线复测

API 重启并重新配置模型后，加载选择器兜底修正并重新运行完整 route-only 评测。结果报告为 `/tmp/hostpilot-live-routing-after-skill-fallback-fix.json`。

| 指标 | 结果 | 说明 |
| --- | ---: | --- |
| 路由选择准确率 | 18/20（90%） | 20 条有效；未启用的 4 条会议纪要样例跳过 |
| 业务 Skill 精确命中 | 10/11（90.9%） | `clarify-002` 本轮直接正确选择代码审查 Skill |
| RRF Recall@1 / @3 / @5 / @10 | 8/11 / 11/11 / 11/11 / 11/11 | 目标 Skill 全部进入前 3 |
| RRF / guarded fusion MRR | 0.8485 / 0.8485 | 保护融合保留 RRF 排序 |
| YAML 业务 Gate 覆盖率 / 精确率 | 5/5 / 5/5 | 本轮所有适用样例均进入 Gate 并通过 |
| 主模型调用 | 16 次完成，1 次失败 | 失败样例为 `office-automation-001`，Trace 原因 `ValueError`，选择器安全返回澄清 |
| 报告中的模型 Token | 65,487 | 不含 Embedding / Reranker 未报告用量 |
| 延迟 P50 / P95 | 9.267 / 14.267 秒 | route-only HTTP 处理与模型调用 |
| 工具事件 | 0 | 符合隔离预期 |

边界复核：

- `clarify-002` 现被模型直接分类为 `business_skill`，置信度 0.99，Gate 通过，route-only 期望匹配。它证明了当前模型可以执行“Skill 意图命中、执行层澄清”的分层，但没有触发新增的分类矛盾兜底分支；该分支由选择器定向单测覆盖。
- `clarify-001`“帮我整理成会议纪要。”选中 `legacy/office-automation`，置信度 0.91。Route-only 无法判断 Skill 最终是否会询问会议笔记；需复核该标签，或使用隔离 Runtime 验证追问行为。
- `office-automation-001` 分类调用失败（`ValueError`），实际返回澄清。没有绕过故障继续执行 Skill；该样例使路由准确率减少 1 条。

## Chat Runtime 定向验证

通过本机 Chat API 对 `clarify-002` 运行一条端到端请求：响应路由为 `clarify`，`business/code-review-check` 的 YAML Skill 执行 Trace 状态为 `completed`，并返回缺少代码/diff 的澄清提示。Trace 含 `skill.execute`，没有工具事件；验证完成后删除了临时会话。该用例验证了“Skill 选择成功，执行层检查必填输入并澄清”，但不代表 Plan 失败回退或 DeepAgents 结果评估已经端到端复测。
