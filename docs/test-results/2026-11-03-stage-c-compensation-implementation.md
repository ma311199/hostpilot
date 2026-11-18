# Stage C 受治理文件补偿实现记录

- 日期：2026-11-03
- 范围：既有文本文件覆盖、取消时补偿、内存快照及持久化取消摘要
- 架构依据：[ADR-0012](../decisions/0012-business-skill-fast-path-and-plan-fallback.md)、[Skill Runtime ADR](../decisions/0009-skill-runtime-and-yaml-contract.md)

## 实现

- 新增高影响 MCP action `write_existing_text_file`，仅允许覆盖工作区内已存在的 UTF-8 文件。普通 `write_text_file` 仍允许创建或覆盖，并继续按不可逆副作用处理。
- Skill Runtime 在 reversible action 执行前调用注册的快照准备回调；快照 token、原内容和待写入内容只保存在进程内，最多保留 256 份。SQLite 取消摘要只持久化安全元数据，不包含文件内容或 handle。
- 取消时，Runtime 把已完成副作用和带快照 token 的 in-flight 副作用分别提交给补偿注册表。in-flight 只有在 handler 注册时显式设置 `safe_for_uncertain_effect=True` 才能补偿。
- 恢复操作通过单独的内部 Tool Executor 注册表执行，不作为模型工具暴露；策略、请求预算和用户审批仍会重新检查。
- 条件恢复在文件内容仍为原内容时视为原写入尚未发生；仍为本次写入内容时恢复旧内容；出现其他内容、文件缺失或快照不可用时失败并转人工处理。
- 业务 Skill 在进入 Plan fallback 前仍要求 reversible 副作用具有 handle 和 handler，并先成功补偿。不可逆副作用仍阻止自动 fallback。

## 验证结果

| 检查 | 结果 |
| --- | --- |
| 后端全量测试 | 197 passed |
| Ruff lint | 通过 |
| Ruff 格式检查 | 94 个文件通过 |
| mypy | 61 个源码文件通过 |
| Python compileall | 通过 |
| `uv lock --check` | 通过 |
| `git diff --check` | 通过 |
| 覆盖率 | 未采集 |
| 前端检查 | 本次只改动后端和文档，未重跑 |

专项测试覆盖既有文件覆盖与补偿、写入尚未发生时的无操作恢复、外部修改冲突保护、恢复审批、缺失 handler fail-closed，以及 in-flight 副作用只能由显式声明安全的 handler 处理。

执行命令：`.venv/bin/pytest -q`、`.venv/bin/ruff check src/hostpilot tests`、`.venv/bin/ruff format --check src/hostpilot tests`、`.venv/bin/mypy src/hostpilot`、`.venv/bin/python -m compileall -q src/hostpilot`、`uv lock --check`、`git diff --check`。

FastMCP 的 Authlib 依赖链仍输出一条既有弃用警告，不影响测试。

## 限制

- 快照只存在于当前进程，进程重启后无法恢复；超过 256 份后，最早的快照会被淘汰，相关补偿将安全失败。
- 文件比较后写入存在操作系统层面的并发窗口；当前实现防止常见外部修改被覆盖，但不提供跨进程文件系统 compare-and-swap 保证。
- 远程 MCP 工具仍没有可验证的逆操作契约，不自动补偿。
- 恢复需要预算和明确审批；预算耗尽、用户拒绝、连接中断或冲突都会留下未完成补偿记录供人工处理。
