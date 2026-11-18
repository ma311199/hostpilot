# 记忆混合召回与 Skill 路由调整验证

## 实现

- MemoryController 以 Markdown 标题和段落为单位切片，使用共享的 BM25 风格本地词项评分优先召回命中片段；未命中内容按会话摘要优先、共享长期记忆其次补入，并继续受单次字符预算限制。
- 候选在读取时先执行会话范围隔离；Trace 只保留计数、耗时和状态，不记录查询或记忆正文。
- 保留三个内置 Skill ID 和 Runtime 注册行为：普通单轮无工具问答由 `direct_answer` 处理，关键输入缺失进入 `clarify`，复杂或需要工具/多步执行进入 `orchestrator_plan`。
- 删除独立 ReAct 工具循环路径；Plan 内 reasoning task 与工具任务仍经现有 Plan、Runtime 和 HostPilot 治理边界。
- 更新 ADR-0028、ADR-0029、新架构、旧架构基线、需求、路线图和开发指南。

## 验证

- 定向路由、Plan、MemoryController 和检索评测测试：35 passed。
- 全量后端测试：350 passed，1 条第三方 Authlib 弃用警告。
- 前端 Vitest：17 个文件、66 项测试通过。
- Ruff 检查通过；MyPy 检查全部 92 个后端源文件通过；`git diff --check` 通过。
- 离线检索评测命令 `.venv/bin/python scripts/evaluate_memory_retrieval.py --char-budget 220`：9 个合成样例；固定顺序 Recall@budget 66.7%、MRR 0.291；BM25 命中项排序 Recall@budget 88.9%、MRR 0.722。

## 限制

- 离线指标评估 BM25 命中项排序，不代表“未命中回退”混合策略的生产质量；语义改写样例仍可能没有词项命中，回退只在预算允许时保留它。
