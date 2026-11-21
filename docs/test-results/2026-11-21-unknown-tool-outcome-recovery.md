# 工具结果未能 checkpoint 时的恢复保护

- 日期：2026-11-21
- 范围：Runtime Chat 在工具 handler 已返回后，工具结果写入 session checkpoint 失败的恢复路径。
- 外部服务调用/真实副作用：0；通过故障注入模拟一次已发生的工具副作用。

## 发现

Runtime 已在 history 中记录 Assistant 工具提案，并有同名同参数的工具重放保护。但工具结果写入 checkpoint 失败后，旧路径仍会调用模型，并依赖模型遵循“结果不确定，请用户核实”的提示。模型若在恢复时改写工具参数或提出其他工具，存在发起新副作用的风险。

## 修复

- Runtime 加载 session 后，如果发现工具提案存在但没有对应工具结果，立即将本轮路由为 `clarify`。
- 保存明确的 Assistant 回复：上次工具调用结果不确定，可能已执行；本次恢复不会调用模型或工具，用户应先核实目标状态。
- 等待后续用户消息前不自动重试。正常已 checkpoint 的工具结果恢复路径不变。

## 故障注入验证

测试顺序为：

1. Runtime 保存 User 消息和 Assistant 工具提案。
2. 工具桩执行一次写入副作用并返回。
3. session state 在保存 Tool 结果时注入 checkpoint 写入失败。
4. 创建新的 Runtime，使用同一 session 重试请求。
5. 新 Runtime 返回确定性 `clarify`；恢复模型调用次数为 0，工具副作用计数仍为 1。

新增回归测试修复前按预期失败，失败时恢复 Runtime 调用了模型；实现安全门后通过。Runtime Chat 与执行循环定向测试 **21 passed**；全量 pytest **569 passed**，1 条既有 Authlib 弃用警告；Ruff lint/format、全仓 mypy（123 个源码文件）和 `git diff --check` 通过。

这是 session-state 边界的确定性故障注入测试，没有真实外部工具副作用，也没有在此用例中验证 SQLite 写盘介质损坏。此前跨进程真实模型用例已验证“工具结果成功 checkpoint 后”的恢复。两者共同覆盖已知结果与结果不确定两种路径。
