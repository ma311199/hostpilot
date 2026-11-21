# Runtime Chat 默认路径切换验证

- 日期：2026-11-21
- 决定：默认 Chat Runtime 从 `builtin.deepagents` 切换为 `builtin.runtime-chat`。
- 外部模型调用/真实工具副作用：本轮为 0；切换依据包含此前用户执行并回传的 Runtime Chat 真实模型评测，以及本地故障注入恢复验证。

## 改动

- `Settings.chat_runtime_plugin` 默认值现为 `builtin.runtime-chat`。
- `builtin.deepagents` 仍注册，可通过 `HOSTPILOT_CHAT_RUNTIME_PLUGIN=builtin.deepagents` 显式回退。
- 默认 Runtime Chat 使用 Runtime-native Model/Tool 循环。YAML Skill 继续由 SkillEngine 执行，Markdown Skill 通过统一 `SkillExecutor` 适配到 DeepAgents；LangGraph 继续提供 Checkpoint 和已有工作流实现。
- HTTP/WebSocket 契约、数据库格式、Skill 格式和工具治理策略未变。

## 验证

- 默认配置测试先按预期失败：配置仍返回 `builtin.deepagents`；更新默认值后通过。
- 首轮全量回归暴露一个工厂测试仍假设旧默认值。将测试夹具调整为 Runtime Chat 工厂，并断言默认工厂构造 Runtime Chat、同时保留 DeepAgents Skill Runtime 适配器。
- 配置、Runtime 工厂和插件定向测试：**27 passed**。
- 全量后端 pytest：**570 passed**，耗时 38.24 秒；1 条既有 Authlib 弃用警告。
- Ruff lint：通过；Ruff format：3 个文件均已格式化；mypy：123 个源码文件通过；`git diff --check`：通过。

## 切换依据与边界

此前真实模型评测覆盖直接回答、只读工具、Skill 澄清、有材料代码审查、Plan 两步读取、成功 checkpoint 后的同进程/跨进程恢复。SQLite + Chat API 集成测试覆盖工具副作用已发生而结果 checkpoint 失败后的跨应用生命周期安全澄清，模型与内存工具均只调用一次。真实模型评测数据见[Runtime Chat 迁移评测](2026-11-20-runtime-chat-live-evaluation.md)；未知结果恢复边界见[故障注入记录](2026-11-21-unknown-tool-outcome-recovery.md)和[SQLite/API 集成记录](2026-11-21-runtime-chat-sqlite-api-recovery.md)。

默认切换不代表 Plan 或两种 Skill 执行器都已重写为 Runtime-native；它们仍通过现有统一契约和受约束的框架适配器工作。旧 Chat Runtime 可显式回退，底层 DeepAgents/LangGraph 依赖不会随本次默认值调整移除。
