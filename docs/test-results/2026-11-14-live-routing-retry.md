# 2026-11-14：模型配置修复后的活跃路由评测尝试

## 配置复核

- 后端重启后，Embedding `qwen3-embedding-4b` 和 Reranker `qwen3-reranker-4b` 的 Key 状态均为已配置。
- 使用当前模型名和地址重试保存路由模型配置，API 返回 HTTP 200、`saved: true`；未读取或输出凭证。

## 活跃评测尝试

- 命令：`.venv/bin/python scripts/evaluate_live_routing.py --output /tmp/hostpilot-live-routing-current.json`。
- 评测未完成并以 HTTP 429 中止，未生成可用于计算准确率的完整报告；本轮不作为路由质量基线。
- 中止前的 Trace 确认检索版本为 `fielded_bm25_rrf_reranker_guarded_fusion_v3`。Embedding 与 Reranker 请求均成功，说明这次中断不是这两个模型的连接失败。
- 活跃 Skill 目录包含 `business/code-review-check` 以及 `legacy/multi-search-engine`、`legacy/office-automation`、`legacy/ppt-generator`。当前评测集没有覆盖后三者。
- 其中一个请求被分类到 `legacy/office-automation`，随后触发 3 次目录列表和 1 次文件读取；Trace 以 `BudgetExceededError` 结束。工具事件均为读取操作，没有写入事件。
- Harness 请求 Token 预算为 20,000。由于活跃 Skill 目录超出当前评测集覆盖范围，分类可能执行评测预期之外的 Skill；不能把这类运行纳入普通问答/澄清质量统计。

## 结论与后续

模型配置保存修复及 Embedding/Reranker 连通得到验证。活跃路由评测需要先与当前启用的 Skill 目录对齐，并为测试建立禁止非预期工具调用的隔离边界，再重跑完整用例；在此之前不更新准确率、Recall 或 MRR 基线。

## 评测隔离实现

- 新增 `POST /api/v1/conversations/{conversation_id}/routing-evaluation`，只运行顶层 Skill Selector 并返回路由、Skill ID 和原因码；不会调用 Skill Runtime、Plan、工具或生成最终回答。
- Route-only 上下文清除共享长期记忆和会话摘要，避免将本机记忆内容发送给评测模型；评测会话在每例完成后删除。
- 活跃评测脚本默认组合 Stage C v3 与用户 Skill 冒烟集，报告启用 Skill 覆盖度。遇到 HTTP 错误会写出部分结果并标记 `incomplete`；未覆盖全部启用 Skill 时退出为不完整基线。
- 后端 API/服务定向测试 64 项通过；新增覆盖率聚合回归测试后，route-only 脚本相关验证继续通过。Ruff、格式检查和 Mypy（100 个后端源码文件）通过。随后完成的真实模型 route-only 组合评测及指标见[实测报告](2026-11-14-live-routing-route-only.md)。
