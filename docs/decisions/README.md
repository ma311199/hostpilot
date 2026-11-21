# 技术决策记录

重要决策用独立记录维护，包含状态、背景、决定、影响和复核条件。提案状态不代表已选定。

## 当前记录

- [ADR-0001：Python 优先项目骨架](0001-python-project-skeleton.md)
- [ADR-0002：Agent 框架组合与原型界面](0002-agent-framework-and-prototype-ui.md)
- [ADR-0003：本地 Skill 与高影响操作确认（安全原则继续有效）](0003-local-skills-and-confirmation.md)
- [ADR-0004：FastAPI 后端接口与前后端边界](0004-fastapi-backend-api.md)
- [ADR-0005：SQLite 会话检查点与 Markdown 记忆](0005-sqlite-checkpoints-and-markdown-memory.md)
- [ADR-0006：Vue 前端与本地进度看板](0006-vue-frontend.md)
- [ADR-0007：进程内 FastMCP 内置工具（新架构确认继续采用）](0007-in-process-fastmcp-tools.md)
- [ADR-0008：多个远程 MCP Server 与 Streamable HTTP](0008-remote-mcp-servers.md)
- [ADR-0016：增加本地 STDIO MCP Server](0016-local-stdio-mcp-servers.md)
- [ADR-0017：本地 Trace 元数据持久化与查询](0017-local-trace-storage-and-query.md)
- [ADR-0009：SkillEngine 与 YAML 执行契约](0009-skill-runtime-and-yaml-contract.md)
- [ADR-0010：Harness 服务契约与配置边界](0010-harness-contracts-and-configuration-boundary.md)
- [ADR-0011：YAML Skill 注册表与旧格式过渡](0011-yaml-skill-registry-and-legacy-migration.md)
- [ADR-0012：业务 Skill 快速路径与 Plan 兜底](0012-business-skill-fast-path-and-plan-fallback.md)
- [ADR-0013：并行支持 Markdown 与 YAML Skill](0013-dual-skill-formats-and-execution-selection.md)（执行入口管理已实现）
- [ADR-0014：持久化 MCP 连接配置但不持久化 API Key](0014-mcp-config-persistence-with-ephemeral-api-keys.md)
- [ADR-0015：持久化模型配置但不持久化 API Key](0015-model-config-persistence-with-ephemeral-api-keys.md)
- [ADR-0018：版本化 Plan 组件插件接口](0018-versioned-plan-component-plugins.md)
- [ADR-0019：版本化对话运行时插件](0019-versioned-chat-runtime-plugins.md)
- [ADR-0020：版本化模型 Provider 插件](0020-versioned-model-provider-plugins.md)
- [ADR-0021：会话 Store 与 Checkpointer Port](0021-conversation-store-and-checkpointer-ports.md)
- [ADR-0022：模型与 MCP 配置 Store Port](0022-configuration-store-ports.md)
- [ADR-0023：Harness、Workspace 与 Skill 偏好 Store Port](0023-harness-workspace-skill-preference-store-ports.md)
- [ADR-0024：Audit、Cancellation 与 Trace Store Port](0024-audit-cancellation-trace-store-ports.md)
- [ADR-0025：Memory Store Port 与记忆治理边界](0025-memory-store-port.md)
- [ADR-0026：MCP 动态工具路由表](0026-mcp-tool-routing-table.md)
- [ADR-0027：记忆重要性评分与分层存储](0027-memory-importance-tiers.md)
- [ADR-0028：本地词项记忆召回与顺序回退](0028-hybrid-memory-recall.md)
- [ADR-0029：按 Skill 格式执行并以 Plan 兜底](0029-three-skill-pipelines.md)
- [ADR-0030：Linux 工具能力沙箱与 MCP 子进程边界](0030-cross-platform-code-and-mcp-sandbox-boundaries.md)
- [ADR-0031：双路 Skill 召回、轻量路由模型与本地 ChromaDB](0031-hybrid-skill-routing-and-local-vector-index.md)
- [ADR-0032：上下文指代候选解析](0032-contextual-reference-resolution.md)
- [ADR-0033：Plan 任务生命周期 Trace](0033-plan-task-lifecycle-trace.md)
- [ADR-0034：Runtime Core 与进程内插件生命周期](0034-runtime-core-and-plugin-lifecycle.md)
- [ADR-0035：Runtime Chat 作为默认 Chat 控制路径](0035-runtime-chat-default.md)

## 后续待记录

- 独立 RAG 服务的用户范围映射和索引存储。
- 会话和长期记忆保留期限及审计保留方案。
- 记忆向量召回存储、首批 LLM 接入类型、模型/MCP 非敏感配置的持久化格式和其他认证类型的秘密生命周期。
- STDIO MCP 工具能力 profile、Linux 沙箱进程池的兼容与生命周期边界；Linux Agent 完成后的 Windows Agent 平台方案。
