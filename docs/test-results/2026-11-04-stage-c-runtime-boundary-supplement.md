# Stage C Runtime 工具边界零模型 Token 补充验证

- 日期：2026-11-04
- 执行命令：`.venv/bin/python scripts/evaluate_stage_c_runtime.py --static-only --output /tmp/hostpilot-stage-c-tool-boundaries.json`
- 模型调用：0 次；提供方 Token：0
- 真实工具副作用：0

## 结果

| 场景 | 结果 | 处理器执行 | 确认请求 | 审计/落盘 |
| --- | --- | ---: | ---: | --- |
| 对 `/etc/hosts` 发起写入 | 拒绝，`invalid_arguments` | 0 | 0 | 拒绝事件已记录；没有调用 handler |
| 对工作区内路径写入且没有确认 Provider | 拒绝，`confirmation_provider_unavailable` | 0 | 0 | 拒绝事件已记录 |
| 对工作区内路径写入并由测试 Provider 批准 | 测试 stub 接收提案 | 1 | 1 | 只写入内存记录；没有创建磁盘文件 |
| 读取工作区相对路径 | 返回合成文本 | 1 | 0 | 没有读取真实文件 |

静态边界检查全部通过。越界路径和缺少审批均在 handler 执行前被拒绝；批准写入只到达内存 stub。该验证使用真实 `WorkspaceFiles` 路径边界、`PolicyEngine` 和 `ToolExecutor`，不调用模型。

完整模型 Runtime 结果见[隔离 Runtime 评测](2026-11-04-stage-c-live-runtime-stub-evaluation.md)。该报告中的三个场景没有触发工具任务；本补充单独覆盖了路径校验、审批门和 stub handler，但不能证明 Planner 会生成预期工具任务。
