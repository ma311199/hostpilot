# 文档导航

| 文档 | 内容 |
| --- | --- |
| [架构设计初版](architecture.md) | HostPilot 个人客户端 Agent 的分层、模块边界、依赖方向和阶段边界 |
| [需求基线](requirements.md) | 当前功能需求、非功能需求和待确认项 |
| [开发指南](development-guide.md) | 命名、注释、代码质量和协作流程 |
| [依赖管理](dependency-management.md) | 依赖选择、版本锁定和分阶段引入规则 |
| [测试策略](testing-strategy.md) | 测试层次、覆盖要求、安全验证和结果记录 |
| [Phase 0 开发回顾与优化记录](optimization-records/2026-10-30-phase0-review.md) | 本阶段开发亮点、已完成优化和后续改进项 |
| [内置文件工具与人工审批开发回顾](optimization-records/2026-10-31-tool-confirmation-and-files.md) | 本轮核心实现、安全与协议改进、验证结果及后续优化项 |
| [MCP 与浏览器审批自动化联调验证](test-results/2026-10-31-mcp-browser-approval-integration.md) | MCP 文件写入审批的 WebSocket 集成验证、前端交互和真实模型手动联调结果 |
| [本地配置 RAG MCP 工具验证](test-results/2026-10-31-configured-rag-mcp.md) | 后端凭证注入、query-only 工具 schema、EvidenceSet 校验和模拟服务集成结果 |
| [本地工具目录页面验证](test-results/2026-10-31-tool-catalog.md) | 注册工具只读 API、页面可用状态、审批要求和凭证保护验证 |
| [多个远程 MCP Server 接入验证](test-results/2026-11-01-remote-mcp-servers.md) | Streamable HTTP、多 Server 会话、工具命名路由、审批治理和质量检查结果 |
| [验证记录](test-results/2026-10-29-skill-v1.md) | Skill 首版测试、质量检查结果和已知缺口 |
| [Phase 0 核心契约验证](test-results/2026-10-29-phase0-core.md) | 核心 DTO、配置和遥测接口的测试与质量检查结果 |
| [SQLite 与 Markdown 持久化验证](test-results/2026-10-29-session-persistence.md) | SQLite 检查点恢复、会话 API 和 Markdown 记忆存储验证 |
| [Chat API 与 ContextBuilder 验证](test-results/2026-10-30-chat-context.md) | 消息 API、会话上下文组装、记忆注入与质量检查结果 |
| [模型运行时接入验证](test-results/2026-10-30-model-runtime.md) | OpenAI 兼容模型配置、运行时装配和质量检查结果 |
| [MemoryWriteProposal 首版验证](test-results/2026-10-31-memory-write.md) | 显式批准、目标范围校验和 Markdown 写入门禁 |
| [请求入口与预算首批验证](test-results/2026-10-31-request-entry-budget.md) | 消息大小、单请求超时、预算上下文和默认路由验证 |
| [工具治理首版验证](test-results/2026-10-31-tool-governance.md) | 注册、capability 策略、确认、预算、超时和结果验证 |
| [记忆提案审批 API 验证](test-results/2026-10-31-memory-approval-api.md) | 待审批列表、显式批准/拒绝和会话删除清理 |
| [L4 响应校验首版验证](test-results/2026-10-31-output-validation.md) | Agent 响应非空、长度限制与 API 错误映射 |
| [WebSocket 状态通道与前端看板验证](test-results/2026-10-31-websocket-status-dashboard.md) | Vue 进度看板、WebSocket 健康快照、心跳和联调检查 |
| [FastMCP、Token 用量与 RAG 检索验证](test-results/2026-10-31-fastmcp-token-rag.md) | Agent 工具治理链、Token 计量、RAG 配置和当前测试结果 |
| [内置文件工具与 WebSocket 人工审批验证](test-results/2026-10-31-tool-confirmation-and-files.md) | 读写工具、审批往返、测试与前端构建结果 |
| [本地 Skill 开发指南](skills-guide.md) | Skill 包格式、本地管理、权限与 DeepAgents 集成 |
| [实施路线](roadmap.md) | 按架构边界整理的分阶段交付和验收重点 |
| [前端架构](frontend-architecture.md) | Vue 看板的目录、数据流、边界和首版范围 |
| [前端开发指南](frontend-development.md) | 依赖、命令、代码规范和前端测试策略 |
| [WebSocket 状态协议](frontend-architecture.md#websocket-状态协议) | 状态快照、心跳、错误和 Origin 校验约定 |
| [决策记录](decisions/README.md) | 重要技术决策及待确认事项 |

## 文档维护

架构、需求、依赖或测试策略发生变化时，同步更新相关文档。实现行为与文档冲突时，说明采用的行为并更新文档；尚未确认的方向明确记录为待决策，不作为已实现能力描述。
