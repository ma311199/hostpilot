# 文档导航

| 文档 | 内容 |
| --- | --- |
| [新架构文档](新架构文档.md) | HostPilot 本地浏览器 Web Harness 的当前架构和已确认边界；MCP 接入和命名以用户确认的修订为准 |
| [当前架构](新架构文档.md) | 当前实现、产品边界、模块职责与明确未完成项 |
| [模块职责边界](模块职责边界.md) | Planner、Plan Validator、Orchestrator、Task Executor、SkillEngine、Tool Executor 和子 Agent 的职责与治理边界 |
| [需求基线](requirements.md) | 当前功能需求、非功能需求和待确认项 |
| [ADR-0034：Runtime Core 与进程内插件生命周期](decisions/0034-runtime-core-and-plugin-lifecycle.md) | Runtime Host、共享上下文、插件生命周期及分阶段迁移边界 |
| [Runtime Host 生命周期验证](test-results/2026-11-17-runtime-host.md) | Host 插件生命周期、FastAPI lifespan 集成、定向与全量测试结果及失败诊断 |
| [Runtime Turn 生命周期验证](test-results/2026-11-18-runtime-turn-lifecycle.md) | Turn 状态记录、会话/停机取消、插件回归与全量测试结果 |
| [开发指南](development-guide.md) | 命名、注释、代码质量和协作流程 |
| [依赖管理](dependency-management.md) | 依赖选择、版本锁定和分阶段引入规则 |
| [Linux 安装与沙箱前置依赖](linux-installation.md) | Linux 系统包、Python/Node.js 运行时、命名空间检查和沙箱故障提示 |
| [测试策略](testing-strategy.md) | 测试层次、覆盖要求、安全验证和结果记录 |
| [模块测试总览](testing-catalog.md) | 按项目模块索引测试类型、代表用例、已知问题、指标状态和详细报告 |
| [持续测试与质量指标台账](testing-metrics.md) | 按模块维护测试状态、质量指标及优化前后对照；后续测试持续更新 |
| [Phase 0 开发回顾与优化记录](optimization-records/2026-10-30-phase0-review.md) | 本阶段开发亮点、已完成优化和后续改进项 |
| [内置文件工具与人工审批开发回顾](optimization-records/2026-10-31-tool-confirmation-and-files.md) | 本轮核心实现、安全与协议改进、验证结果及后续优化项 |
| [MCP 与浏览器审批自动化联调验证](test-results/2026-10-31-mcp-browser-approval-integration.md) | MCP 文件写入审批的 WebSocket 集成验证、前端交互和真实模型手动联调结果 |
| [本地配置 RAG MCP 工具验证](test-results/2026-10-31-configured-rag-mcp.md) | 后端凭证注入、query-only 工具 schema、EvidenceSet 校验和模拟服务集成结果 |
| [本地工具目录页面验证](test-results/2026-10-31-tool-catalog.md) | 注册工具只读 API、页面可用状态、审批要求和凭证保护验证 |
| [多个远程 MCP Server 接入验证](test-results/2026-11-01-remote-mcp-servers.md) | Streamable HTTP、多 Server 会话、工具命名路由、审批治理和质量检查结果 |
| [MCP STDIO 与连接管理验证](test-results/2026-11-07-mcp-stdio.md) | 本地 STDIO 子进程、HTTP 配置、密钥生命周期和连接故障隔离 |
| [Stage D：模型 Provider 契约与适配器验证](test-results/2026-11-06-stage-d-model-provider.md) | 统一模型请求/响应契约、OpenAI 兼容适配器、超时/取消与接入检查结果 |
| [Stage D：Tool Facade 与 Memory Controller 首轮验证](test-results/2026-11-06-stage-d-facade-memory-controller.md) | 稳定工具 Facade、作用域记忆召回、审批队列归档和质量检查结果 |
| [Stage D：MCP 连接状态 API 验证](test-results/2026-11-07-stage-d-mcp-connection-status.md) | 按 Server 的连接生命周期、发现工具数量和错误信息脱敏验证 |
| [Trace 持久化与观测页面验证](test-results/2026-11-07-trace-observability.md) | SQLite 元数据保留、请求关联、查询 API、前端列表/详情及敏感字段白名单 |
| [Trace Skill、工具与审批 Span 补充验证](test-results/2026-11-07-trace-skill-tool-approval-spans.md) | Skill/工具/审批事件的父子 Span 关联、字段白名单与页面展示 |
| [Stage E：Harness 运行时配置验证](test-results/2026-11-07-harness-runtime-configuration.md) | Harness 限制与预算配置页面、SQLite 重启恢复、在线应用及质量检查 |
| [Stage E：浏览器管理跨页面验收](test-results/2026-11-07-browser-management-acceptance.md) | Vitest 跨页面 UI 验收和 Playwright 真实浏览器/本地 API E2E |
| [2026-11-08：Playwright 浏览器与 API 联动](test-results/2026-11-08-playwright-browser-api-e2e.md) | 配置持久化、MCP 工具发现、Skill 管理、Workspace、沙箱、Trace 详情和审批交互 |
| [Stage F：Skill JSON Schema 安全边界验证](test-results/2026-11-07-skill-json-schema-hardening.md) | 本地引用限制、Schema 复杂度上限和用户 Skill 包预检结果 |
| [Stage F：子 Skill 委派授权验证](test-results/2026-11-07-skill-delegation-authorization.md) | `dependencies.skills` allowlist、Planner 限制和 Plan 执行前拒绝验证 |
| [Stage F：YAML Skill 工具授权验证](test-results/2026-11-07-skill-tool-authorization.md) | `dependencies.tools` 在注册表、Runtime、Planner 和 Plan Validator 的 allowlist 验证 |
| [Stage F：远程 MCP 参数 Schema 验证](test-results/2026-11-07-remote-mcp-schema-validation.md) | MCP Server inputSchema 的复杂度与引用限制、参数执行前校验 |
| [Stage F：STDIO 凭证参数隔离验证](test-results/2026-11-07-stdio-credential-argument-isolation.md) | 后端、参数表单与 JSON 导入拒绝凭证型 args，并验证拒绝值不落库/不回显 |
| [Stage F：MCP 超时与取消清理验证](test-results/2026-11-07-mcp-timeout-cancellation.md) | 工具发现超时、启动取消时关闭各 Server 会话、调用等待取消和关闭时限 |
| [Stage F：有限重规划与 Audit 边界验证](test-results/2026-11-07-retry-audit-boundaries.md) | 重规划上限、失败任务去重、无工具自动重试和 Audit trace_id/脱敏边界 |
| [共享模型预算与并行熔断验证](test-results/2026-11-07-shared-model-budget.md) | Agent、YAML 与 Plan 共用 Token 预算；并行超额后阻止排队请求和后续任务 |
| [真实模型 Plan 与 YAML 预算冒烟验证](test-results/2026-11-08-live-plan-yaml-budget-smoke.md) | 真实模型完成 Plan + 只读工具与 YAML Skill 探针；记录用量、恢复状态和并行 YAML 待验证项 |
| [Plan 与并行 YAML 混合预算验证](test-results/2026-11-08-plan-yaml-mixed-budget.md) | 同一 Plan 请求委派并行 YAML Skill 时，共享预算和耗尽后的停止行为 |
| [工作区目录选择与会话删除验证](test-results/2026-11-08-workspace-selection-and-conversation-delete.md) | 原生目录选择、权限降级/拒绝、工作区持久化、会话删除和质量检查 |
| [会话级沙箱权限阶梯验证](test-results/2026-11-08-conversation-sandbox-levels.md) | 会话权限持久化、单向升级审批、文件边界执行、Audit 和质量检查 |
| [工具能力与执行边界目录验证](test-results/2026-11-10-tool-permission-catalog.md) | 工具传输、信任等级、能力范围、运行位置、审批和 OS 沙箱状态 |
| [工作区文件发现与分段读取工具验证](test-results/2026-11-10-workspace-file-tools.md) | 目录列表、受限文件名搜索、大文件 UTF-8 分段读取及路径权限边界 |
| [Linux Python 沙箱首轮验证](test-results/2026-11-09-linux-python-sandbox.md) | bubblewrap/prlimit 执行边界、工具目录隔离预检、fail-closed 行为及完整质量检查 |
| [Windows 代码与 STDIO MCP 沙箱评估](evaluations/2026-11-09-windows-and-stdio-sandbox.md) | 2026-11-09 的 Windows 候选与 STDIO 隔离历史评估；当前平台路线已由 ADR-0030 修订 |
| [多语言脚本沙箱验证](test-results/2026-11-10-script-sandboxes.md) | Python、JavaScript、Shell 的 Bubblewrap 隔离、审批链、运行时探针和 fail-closed 行为 |
| [内置文件操作 OS 沙箱验证](test-results/2026-11-10-filesystem-sandbox.md) | 六个文件工具的 Bubblewrap 隔离、按会话权限挂载、失败关闭和路径越界验证 |
| [Linux 安装前置依赖与沙箱诊断验证](test-results/2026-11-10-linux-install-prerequisites.md) | 安装文档、缺失系统包/运行时提示、工具目录诊断和聊天错误反馈 |
| [ADR-0030：Linux 工具能力沙箱与 MCP 子进程边界](decisions/0030-cross-platform-code-and-mcp-sandbox-boundaries.md) | Linux 工具能力 profile、STDIO Server 沙箱进程池，以及 Linux/Windows Agent 交付顺序 |
| [Stage E：MCP 连接状态页面验证](test-results/2026-11-07-stage-e-mcp-connection-panel.md) | MCP 连接状态页面首轮只读显示验证；后续配置管理和 JSON 导入见 MCP STDIO 与连接管理验证 |
| [Stage E：Skill 库只读目录验证](test-results/2026-11-07-stage-e-skill-catalog.md) | YAML 与 SKILL.md 包目录 API、只读页面、无效包统计和前后端质量检查 |
| [Stage E：Skill 包详情只读查看验证](test-results/2026-11-07-stage-e-skill-details.md) | 按 Skill ID 返回经过校验的 manifest、说明文件与文本资源 |
| [Stage E：Skill ZIP 导入验证](test-results/2026-11-07-stage-e-skill-zip-import.md) | ZIP 大小与路径限制、格式校验、原子安装和前后端导入流程 |
| [Stage E：Skill 启停与删除验证](test-results/2026-11-07-stage-e-skill-package-management.md) | 用户包状态管理、内置包保护、确认删除与运行时停用行为 |
| [Skill 执行方式管理验证](test-results/2026-11-07-skill-execution-selection.md) | 双格式统一目录、YAML/DeepAgents/both 选择、持久化和单入口分发 |
| [ADR-0014：MCP 配置持久化与临时 API Key](decisions/0014-mcp-config-persistence-with-ephemeral-api-keys.md) | Server 非敏感配置跨重启保存，API Key 仅在进程内存使用 |
| [ADR-0015：模型配置持久化与按角色管理 API Key](decisions/0015-model-config-persistence-with-ephemeral-api-keys.md) | Embedding/Reranker Key 本机持久化，主 LLM/轻量 LLM Key 保持进程内存生命周期 |
| [Embedding 与 Reranker API Key 本地持久化验证](test-results/2026-11-13-routing-model-key-persistence.md) | SQLite 保存、服务重启恢复、API 不回传和角色 Key 清理验证 |
| [模型配置面板分角色配置与连接测试](test-results/2026-11-13-model-settings-panels.md) | 四种角色独立展开、连接测试、单角色保存和脱敏验证 |
| [2026-11-14 活跃路由 Route-only 组合评测](test-results/2026-11-14-live-routing-route-only.md) | 当前四个启用 Skill 的安全路由评测、RRF/Reranker 指标与失败样例 |
| [2026-11-14 上下文指代解析验证](test-results/2026-11-14-contextual-reference-resolution.md) | 会话与记忆候选、预算内模型匹配、无匹配澄清及质量检查结果 |
| [2026-11-14 Skill 格式分流、Plan 兜底与按需工具检索](test-results/2026-11-14-skill-format-plan-fallback.md) | 格式分流、失败原因传递、Plan 能力优先及不确定副作用只读核对 |
| [上下文指代模型评测集 v1](evaluations/contextual-reference-resolution-v1.yaml) | 合成的唯一匹配、无关记忆、多候选和会话历史样例 |
| [ADR-0016：增加本地 STDIO MCP Server](decisions/0016-local-stdio-mcp-servers.md) | 管理本地 MCP 子进程、参数、凭证和生命周期 |
| [ADR-0017：本地 Trace 元数据持久化与查询](decisions/0017-local-trace-storage-and-query.md) | SQLite 安全元数据白名单、保留上限、API 与观测页面边界 |
| [ADR-0029：按 Skill 格式执行并以 Plan 兜底](decisions/0029-three-skill-pipelines.md) | YAML SkillEngine、DeepAgents Markdown Skill、失败回退与按需工具检索 |
| [Stage B：YAML Skill 注册表与路由验证](test-results/2026-11-02-stage-b-yaml-skill-registry.md) | schema v1、包校验、依赖预检、路由和质量检查结果 |
| [验证记录](test-results/2026-10-29-skill-v1.md) | Skill 首版测试、质量检查结果和已知缺口 |
| [Phase 0 核心契约验证](test-results/2026-10-29-phase0-core.md) | 核心 DTO、配置和遥测接口的测试与质量检查结果 |
| [SQLite 与 Markdown 持久化验证](test-results/2026-10-29-session-persistence.md) | SQLite 检查点恢复、会话 API 和 Markdown 记忆存储验证 |
| [Chat API 与 ContextBuilder 验证](test-results/2026-10-30-chat-context.md) | 消息 API、会话上下文组装、记忆注入与质量检查结果 |
| [模型运行时接入验证](test-results/2026-10-30-model-runtime.md) | OpenAI 兼容模型配置、运行时装配和质量检查结果 |
| [MemoryWriteProposal 首版验证](test-results/2026-10-31-memory-write.md) | 显式批准、目标范围校验和 Markdown 写入门禁 |
| [记忆重要性分层写入验证](test-results/2026-11-08-memory-importance-tiers.md) | 低／中／高评分、审批后分层存储、归档上限与全量质量检查 |
| [记忆混合召回与 Skill 路由调整验证](test-results/2026-11-08-memory-recall-and-skill-pipelines.md) | 历史记录：本地词项召回、direct_answer 与通用 ReAct 路径调整 |
| [混合记忆召回离线评测补充](test-results/2026-11-08-hybrid-memory-retrieval-evaluation.md) | 固定顺序、BM25 命中项与命中优先回退策略的预算内检索指标 |
| [请求入口与预算首批验证](test-results/2026-10-31-request-entry-budget.md) | 消息大小、单请求超时、预算上下文和默认路由验证 |
| [工具治理首版验证](test-results/2026-10-31-tool-governance.md) | 注册、capability 策略、确认、预算、超时和结果验证 |
| [记忆提案审批 API 验证](test-results/2026-10-31-memory-approval-api.md) | 待审批列表、显式批准/拒绝和会话删除清理 |
| [L4 响应校验首版验证](test-results/2026-10-31-output-validation.md) | Agent 响应非空、长度限制与 API 错误映射 |
| [WebSocket 状态通道与前端看板验证](test-results/2026-10-31-websocket-status-dashboard.md) | Vue 进度看板、WebSocket 健康快照、心跳和联调检查 |
| [FastMCP、Token 用量与 RAG 检索验证](test-results/2026-10-31-fastmcp-token-rag.md) | Agent 工具治理链、Token 计量、RAG 配置和当前测试结果 |
| [内置文件工具与 WebSocket 人工审批验证](test-results/2026-10-31-tool-confirmation-and-files.md) | 读写工具、审批往返、测试与前端构建结果 |
| [本地 Skill 开发指南](skills-guide.md) | Skill 包格式、本地管理、权限与 DeepAgents 集成 |
| [ADR-0011：YAML Skill 注册表与旧格式过渡](decisions/0011-yaml-skill-registry-and-legacy-migration.md) | Stage B 的 schema 与注册表安全边界；执行格式策略已由 ADR-0013 更新 |
| [ADR-0013：并行支持 Markdown 与 YAML Skill](decisions/0013-dual-skill-formats-and-execution-selection.md) | 两种执行格式的默认发现、选择与未来 Web 配置规则 |
| [ADR-0012：业务 Skill 快速路径与 Plan 兜底](decisions/0012-business-skill-fast-path-and-plan-fallback.md) | 业务 Skill 评估门、副作用等级、执行快照和 Plan 升级规则 |
| [实施路线](roadmap.md) | 按架构边界整理的分阶段交付和验收重点 |
| [Stage C 真实模型质量评测计划](stage-c-model-evaluation.md) | 评测集构成、指标、执行安全边界及需要提供的业务样例 |
| [持续测试与质量指标台账](testing-metrics.md) | 各模块测试状态、质量指标和优化前后对照 |
| [首次混合路由实现记录](test-results/2026-11-11-hybrid-skill-routing.md) | 首轮 BM25 + ChromaDB 召回、置信度 Gate 和自动化验证历史 |
| [当前混合路由 ChromaDB 持久化验证](test-results/2026-11-11-hybrid-skill-routing-validation.md) | 真实 ChromaDB 建库、Skill 索引更新/删除和重启恢复集成测试 |
| [统一模型配置与降级策略验证](test-results/2026-11-11-unified-model-configuration.md) | 统一模型角色设置入口、主模型回退规则及前后端验证结果 |
| [运行中主模型路由冒烟评测](test-results/2026-11-11-live-route-smoke.md) | 使用页面已配置的主模型，对问答、澄清和只读代码评审 Skill 进行在线路由抽样 |
| [Embedding 与 Reranker 连通性检查](test-results/2026-11-11-routing-model-connectivity.md) | 记录模型角色配置、服务健康探测、实际路由请求及逐角色验证限制 |
| [llama.cpp Reranker 接入](test-results/2026-11-12-llama-cpp-reranker-integration.md) | 通过 `/v1/rerank` 重排候选及定向验证结果 |
| [字段化 BM25 + RRF 活跃 Runtime 评测](test-results/2026-11-12-fielded-bm25-rrf-live-baseline.md) | 新检索版本、降级快照、元数据更新后的全量复测与失败分析 |
| [新增 Skill 后的在线路由复测](test-results/2026-11-12-live-routing-after-skill-import.md) | v3 安全集复测结果、重排指标变化及新用户 Skill 的评测覆盖边界 |
| [新增用户 Skill 在线冒烟测试](test-results/2026-11-12-imported-skills-smoke.md) | 三个用户 Skill 的在线路由测试、Reranker 误排分析、加权融合修复与待复测项 |
| [新增用户 Skill 冒烟评测集](evaluations/imported-skills-smoke-v1.yaml) | 搜索、办公整理、PPT 大纲三种 Skill 的文本模式用例 |
| [Stage C 合成评测集 v1](evaluations/stage-c-model-evaluation-v1.yaml) | 内含 2 个评测专用业务 Skill 夹具、30 条路由行为用例和 3 条模型故障注入用例 |
| [Stage C 合成评测集 v2](evaluations/stage-c-model-evaluation-v2.yaml) | 复核过澄清/Plan 期望的 30 条用例，补齐工作区外路径测试输入 |
| [Stage C 合成评测集 v3](evaluations/stage-c-model-evaluation-v3.yaml) | 按当前路由约定标注缺少必填项的业务 Skill 请求由 Skill 执行层澄清 |
| [Stage C 首轮真实模型路由评测](test-results/2026-11-04-stage-c-live-model-selector-evaluation.md) | gpt-6-luna selector-only 评测结果、业务 Skill 命中率及样例复核 |
| [Stage C 隔离 Runtime 评测](test-results/2026-11-04-stage-c-live-runtime-stub-evaluation.md) | 真实模型、合成 Skill 和内存工具替身的 Runtime 联调及未覆盖边界 |
| [Stage C Runtime 工具边界补充验证](test-results/2026-11-04-stage-c-runtime-boundary-supplement.md) | 0 模型 Token 验证工作区路径、确认门和内存工具替身 |
| [Stage C Runtime 定向评测诊断复核](test-results/2026-11-05-stage-c-runtime-diagnostic-review.md) | 评测输入映射夹具问题、Plan 工具字段缺失及修正记录 |
| [ADR-0010：Harness 服务契约与配置边界](decisions/0010-harness-contracts-and-configuration-boundary.md) | Stage A 决策：Runtime、LLM Provider、Tool Facade、Memory Controller 与秘密配置边界 |
| [前端架构](frontend-architecture.md) | Vue 看板的目录、数据流、边界和首版范围 |
| [前端开发指南](frontend-development.md) | 依赖、命令、代码规范和前端测试策略 |
| [WebSocket 状态协议](frontend-architecture.md#websocket-状态协议) | 状态快照、心跳、错误和 Origin 校验约定 |
| [决策记录](decisions/README.md) | 重要技术决策及待确认事项 |
| [ADR-0031：双路 Skill 召回与路由模型](decisions/0031-hybrid-skill-routing-and-local-vector-index.md) | BM25 + ChromaDB 语义召回、0.85/0.30 业务 Gate、独立远端小模型与本地索引 |
| [ADR-0032：上下文指代候选解析](decisions/0032-contextual-reference-resolution.md) | 对话与长期记忆候选、模型受限选择、置信门槛及澄清路径 |

## 文档维护

架构、需求、依赖或测试策略发生变化时，同步更新相关文档。实现行为与文档冲突时，说明采用的行为并更新文档；尚未确认的方向明确记录为待决策，不作为已实现能力描述。
