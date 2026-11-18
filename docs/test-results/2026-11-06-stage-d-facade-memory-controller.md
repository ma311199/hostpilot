# Stage D：Tool Facade 与 Memory Controller 首轮验证

- 日期：2026-11-06
- 范围：稳定工具目录/调用入口、入口与 Plan 子任务记忆召回、脱敏 Trace 和审批队列归档
- 新增运行依赖：无

## 实现

- 新增 `ToolFacade`，提供稳定工具描述目录、按稳定工具 ID 调用，以及既有文件补偿委托。
- Chat 工具路由、YAML MCP action、Plan Runtime 和工具目录 API 通过 Facade 访问描述或调用；具体 MCP 发现、内置进程内会话、远程 Server 路由及 Tool Executor 治理仍由 MCP Provider 管理。
- 新增 `MemoryController`，集中读取 conversation_id 对应的会话摘要及本地用户共享长期记忆，并施加总字符上限。`ContextBuilder` 通过 Controller 召回，并把当前用户消息作为 recall 查询输入。
- 归档候选包含来源 Skill ID，并进入现有 MemoryProposalReview 队列；只有显式批准后才由 MemoryWriteService 写入 Markdown。
- Plan 的业务 Skill 和推理子任务在执行前按子任务描述经 Controller 再次召回，使用新的 request-local ContextSnapshot，不修改父任务快照。
- Recall/archive 发出结构化 Trace 元数据，包括操作状态、耗时、字符数、chunk 数和来源 Skill ID；query 与记忆正文不会进入 Trace。Trace Sink 故障不会中断请求。
- 当前 Markdown 存储没有语义索引；首轮 recall 返回两个允许范围内现存记忆，不做 query 相关性排序，也不写入长期记忆。归档候选的自动过滤、摘要和去重尚未实现。

## 验证

- 后端全量测试：**228 passed**；覆盖 Controller scope/预算/错误/取消/Trace 脱敏/Trace Sink 故障，以及 Plan 子任务注入独立记忆上下文。
- Ruff lint：通过。
- Mypy（`src/hostpilot`）：通过，检查 71 个源文件。
- `git diff --check`：通过。
- 测试期间出现一个来自锁定 FastMCP 依赖的 Authlib 弃用警告；未影响测试结果。未采集覆盖率。
- Ruff lint：通过。
- Mypy（`src/hostpilot`）：通过，检查 71 个源文件。
- 前端 Vitest：**15 passed**；ESLint：通过；`vue-tsc` 类型检查与 Vite production build：通过。
- `git diff --check`：通过。
- 测试期间出现一个来自锁定 FastMCP 依赖的 Authlib 弃用警告；未影响测试结果。

## 当前边界与后续

- Facade 首轮以 MCP Provider 作为底层 adapter，未把动态工具映射表、连接健康状态或多种非 MCP 工具 Provider 抽离为独立组件。
- 记忆归档尚未有自动过滤、摘要或去重；当前保持完整提案、人工审批和已有 Markdown 原子写入规则。
- 后续如需相关性召回，应先用真实使用场景评估检索质量，再决定索引和存储，不预先引入向量数据库。
