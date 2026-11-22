# Plan 历史结果 Harness 配置设计

## 意图

让用户从 Harness 页面控制新 Plan 可参考的同一会话历史成功子任务数量和总上下文字符数，避免当前硬编码限制，并让这些结果实际进入 Planner 上下文。

## 目标与非目标

- Harness 页面配置最近历史结果数量和总字符数上限；默认值为 32 条、65,536 字符。
- 配置经现有 Harness API 与 SQLite 配置 Store 持久化，并即时应用于后续请求。
- 新 Plan 只读取同一 Session、当前 Turn 之前的成功结果；历史结果仅作 Planner 参考，不标记为当前 Turn 已完成，也不跳过任务执行。
- SQLite 继续保留已有成功记录；本次不按配置删除旧记录，不改变精确指纹缓存审批行为。
- 单条结果写入的 65,536 UTF-8 字节硬限制继续作为存储安全边界，与 Planner 上下文字符限制分开。

## 方案

1. **将历史结果上下文限制纳入 Harness，并接通 Planner（推荐）**：沿用现有 Harness 配置读写、即时更新和 TaskStore 查询边界；新 Plan 按配置读取历史并传给 Planner。配置有实际效果且不改变 SQLite 留存策略。
2. **只把硬编码值显示为 Harness 表单项**：改动较小，但历史结果列表目前未接入 Plan 调用链，设置不会影响运行行为。
3. **同时按配置清理或限制 SQLite 留存**：把上下文窗口与数据保留周期耦合，并删除既有会话结果；这超出本次只控制 Planner 可见历史范围的需求。

## 行为与数据

- 新增 `plan_result_history_limit`（默认 32，允许 1–128）与 `plan_result_history_max_chars`（默认 65,536，允许 1–262,144）两个 Harness 字段。
- 总字符数按返回给 Planner 的历史结果序列化字段长度累计；若追加下一条会超过字符上限，则停止追加，仍按时间正序传递已选结果。
- Store 以 Session 隔离，排除当前 Turn，只查询成功完成且有持久化结果的任务。失败、取消、中断和缺失结果记录不进入 Planner 上下文。
- `BlackboardSnapshot` 单独携带历史结果；Planner 提示明确标注为历史参考。当前 Turn 的 `results` 与 Executor 输入保持本轮状态，不合并历史结果。
- 每条记录的写入字节上限、历史缓存精确匹配审批、工具治理及 SQLite 清理策略保持现状。

## 架构位置与验证

配置字段位于 `HarnessConfiguration`，通过 API、SQLite Harness Store、Runtime 配置更新回调和 Vue `HarnessSettingsPanel` 全链路传递。`ChatService` 将当前设置传给 `TaskLifecycleManager` 查询；Plan 执行器将有界历史放进独立的 Planner 输入字段。

验证覆盖配置验证与保存/重载、运行时即时应用、Session/Turn 隔离、数量与字符预算、历史结果不进入当前 Blackboard/Executor、前端表单展示及保存。最后更新架构、路线图和测试目录文档。

## 待实现时的细节决定

- 字符预算按序列化记录中的字符数计量，保留字段包括任务说明、显式输入、依赖 ID、成功输出和执行摘要；不包含 SQL/SQLite 开销。
- 超出数量或字符预算的较旧记录不进入本次 Planner 请求，但仍保留在 SQLite。
- Harness 验证拒绝零值和超范围值；默认配置与旧配置加载自动获得以上默认值。
