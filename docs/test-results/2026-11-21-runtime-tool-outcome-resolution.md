# Runtime 工具结果核实与恢复验证

## 范围

2026-11-22 在 `feature/runtime-tool-outcome-resolution` worktree 验证可能产生副作用的工具派发保护、只读工具重试、Session 门禁、跨 Runtime Host 重启和人工核实 API/UI。运行使用 SQLite 与隔离测试工具；没有调用真实外部系统，也没有产生真实工具副作用。

## 实现结果

- `ToolDescriptor.side_effect` 支持 `none` / `possible`；未声明时默认为 `possible`。文件读取、范围读取、目录查看、文件名搜索与知识检索显式标为 `none`。
- 可能产生副作用的调用在进入 Tool Port 前写入 SQLite 未决记录。记录仅含 Session/Turn/Task/工具调用 ID、工具名、Host 与时间戳，不含参数、凭证或结果正文。注册失败时不会调用 handler。
- ToolMessage 进入 Checkpoint 后清除未决状态。只读工具不登记未决状态；只读结果写入失败时可在新 Turn 再执行。
- 未决状态存在时，`ChatService.send()` 与 route-only 评估在 Context、Router、Selector、Model、Skill 和 Tool 工作前阻断该 Session。
- 本地 API 只列出调用 ID、工具名、时间和 pending 状态。人工提交的完成结果或“确认未执行”写入原 `tool_call_id` 对应的 ToolMessage；相同 checkpoint 结果可幂等收尾，冲突结果拒绝。确认未执行不会自动调用工具。
- ChatPanel 显示阻断卡片并禁用消息输入；结果需用户根据目标系统实际状态填写。未决状态保持时不修改记录。

## 故障与修复记录

1. 第一次全量 pytest 为 **640 passed、1 failed**。唯一失败是既有 `test_chat_api_retry_clarifies_without_repeating_tool_after_sqlite_checkpoint_failure` 仍匹配旧提示词“结果不确定”；新 Session 门禁提示改为“尚未核实”。已更新断言并单测复跑，结果 **1 passed**。
2. 随后全量 pytest 为 **641 passed**。仅有既有 Authlib 弃用警告。
3. 全仓 Ruff 首次发现新集成测试的导入排序、未用导入和两处行宽问题。已格式化并修正；最终 Ruff、mypy、集成测试和 diff 检查通过。

## 复测指标

| 检查 | 结果 | 备注 |
| --- | ---: | --- |
| 全量 Python 测试 | 641 passed | 含旧 API/Runtime 恢复回归和新增集成测试 |
| 新增恢复故障注入集成测试 | 3 passed | Host 重启、checkpoint 确认丢失、只读失败 |
| 全量前端测试 | 84 passed | 18 个测试文件 |
| 前端 TypeScript | 通过 | `vue-tsc -b --pretty false` |
| 前端 ESLint | 通过 | `pnpm lint` |
| 前端生产构建 | 通过 | Vite build |
| Ruff | 通过 | 全仓 `ruff check .` |
| mypy | 通过 | 127 个源码文件 |
| `git diff --check` | 通过 | 无空白错误 |

## 执行命令

```bash
/home/mameng/hostpilot/.venv/bin/pytest -q
/home/mameng/hostpilot/.venv/bin/ruff check .
/home/mameng/hostpilot/.venv/bin/mypy src/hostpilot
git diff --check
cd frontend
pnpm test
pnpm typecheck
pnpm lint
pnpm build
```

## 限制

- 人工填写的结果没有被系统独立向外部目标核验；UI 明确提示用户检查真实状态。
- SQLite 与 Checkpoint 不支持跨数据库事务。Checkpoint 写入后、pending 清除前崩溃时，Session 继续阻断；人工核实流程会读取已有 ToolMessage，相同结果时只完成清理。
- 仅当前 Runtime-native Chat 工具循环纳入派发前登记。Plan Blackboard、任务依赖结果快照、执行游标和完整 Task 恢复不属于本阶段。
- 未决记录清除后不保留新的工具调用历史；既有 Turn/Task/Trace/Audit 元数据职责不变。未决元数据的保留清理策略尚未定义。
- 可恢复性依赖 Runtime Host 可访问的 SQLite 数据库和 Checkpoint。未决 Store 写入失败时 fail closed，无法保证存储介质本身损坏时的恢复。
