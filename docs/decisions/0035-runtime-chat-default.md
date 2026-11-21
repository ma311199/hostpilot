# ADR-0035：Runtime Chat 作为默认 Chat 控制路径

## 状态

已采纳，2026-11-21 起生效。

## 背景

`builtin.runtime-chat` 已实现 Runtime-native Model/Tool 执行循环、消息增量 checkpoint、已知工具结果重放和未知工具结果安全澄清。真实模型评测覆盖直接对话、只读工具、Skill 澄清与执行、Plan 转交、模型故障后恢复和跨进程 checkpoint 恢复。SQLite + Chat API 故障注入集成测试进一步验证工具副作用完成但结果 checkpoint 失败后，重启 Runtime 不会重复调用模型或工具。

## 决定

- 将 `Settings.chat_runtime_plugin` 的默认值设为 `builtin.runtime-chat`。
- 保留 `builtin.deepagents` 注册和 `HOSTPILOT_CHAT_RUNTIME_PLUGIN` 配置入口，便于显式回退或兼容部署。
- Runtime Chat 保持唯一执行控制面。DeepAgents 继续作为 Markdown Skill 执行器适配器使用，LangGraph 继续提供 Checkpoint 和现有工作流能力；本决策不移除这些依赖。
- 此次切换不改变 HTTP/WebSocket 契约、数据库格式、模型配置、Skill 格式或工具治理策略。

## 验证依据

- Runtime Chat 真实模型评测覆盖直接回答、只读工具、Skill 缺参澄清、有材料代码审查、Plan 两步读取和成功 checkpoint 后的跨进程恢复。
- 未知工具结果故障注入测试确认 handler 执行一次后，Runtime 会阻止模型和工具重试并请求用户核实。
- SQLite + Chat API 集成测试跨三个独立应用生命周期复用 session/checkpoint，确认重启后澄清且工具只执行一次。
- 配置单测覆盖 Runtime Chat 默认值及显式自定义插件选择；切换后全量后端测试通过。

## 影响与复核条件

- 新安装和未显式覆盖 `HOSTPILOT_CHAT_RUNTIME_PLUGIN` 的实例将使用 `builtin.runtime-chat`。
- 已显式设置为 `builtin.deepagents` 的部署保持原行为；若新默认出现兼容问题，可将该配置设为 `builtin.deepagents` 回退。
- 若新增 Skill/Plan 执行能力绕过 Runtime 的预算、审批、取消或恢复边界，或发现旧 checkpoint 无法读取，应暂停扩大迁移并复核该决定。
