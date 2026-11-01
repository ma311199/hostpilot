# 实施路线

阶段是依赖顺序建议，HostPilot 整体功能尚未完成；当前已实现本地 Skill 管理与确认门首版。HostPilot 作为个人客户端 Agent 开发；Agentic RAG 服务独立建设并通过检索工具契约集成。

## 前端首版

- [已完成首版] frontend/ 使用 Vue 3、TypeScript 和 Vite 展示后端阶段、交付模块及待办事项，并提供会话列表、历史、WebSocket 聊天和页面模型配置；阶段内容与本路线文档同步维护。
- [已接入] 前端通过 /api/v1/ws/status 获取后端健康快照并使用 ping/pong 保持连接；状态协议和测试见前端架构文档。

## Phase 0：项目基线

- 确认 Python 版本、包管理、后端运行入口和存储选型；后端 HTTP API 采用 FastAPI，Web 前端作为独立项目开发。
- 定义 FastAPI 层与应用服务之间的请求 / 响应映射，避免领域契约依赖 FastAPI 类型。
- [已决策] 本地免登录、多会话隔离；SQLite 保存会话索引与 LangGraph 检查点，Markdown 保存会话摘要和跨会话长期记忆。
- [已完成首版] SQLite 会话索引与 LangGraph 持久检查点、Markdown 记忆存储和本机 FastAPI 会话 CRUD API。
- [已完成首版] 定义核心 DTO：ContextSnapshot、BudgetContext、RouteDecision、ToolDescriptor、RetrievalToolRequest、EvidenceSet、SandboxPolicy。
- [已完成首版] ContextBuilder 从 conversation_id 对应的检查点和 Markdown 记忆组装请求上下文；FastAPI 已提供消息提交与历史查询接口，真实模型运行时通过可注入 AgentRuntime 接入。
- [已完成首版] 默认启动装配支持 OpenAI 兼容 Chat API；模型名、API 密钥和可选 Base URL 由环境变量配置，未配置模型时聊天 API 保持 503。
- [已通过真实联调] 讯飞星火 OpenAI 兼容接口已完成回复、重启续聊和多会话隔离冒烟验证；详见模型运行时验证记录。
- [已完成首版] MemoryWriteProposal 契约、应用服务和 HTTP 审批工作流要求显式用户批准，检查目标会话及内容大小，并按声明范围写入 Markdown；聊天中的模型自动提案尚未接入。
- [已完成首版] 建立环境变量配置、结构化日志配置以及 Trace / Cost / Audit 事件和导出 Protocol。
- [已完成首版] 提供可复用质量检查脚本，覆盖格式、lint、类型、单元测试和锁文件一致性；CI 平台选择与流水线接入仍待确认。
- 定义 SkillPackage、Skill Registry 与 DeepAgents 适配边界，以及高影响操作确认契约。
- Gradio 只用于开发演示，调用应用服务接口。
- 明确外部检索服务凭证保护、用户范围映射和请求契约的决策归属。

**验收重点**：决策有文档记录；契约可校验；开发环境可复现；基础测试命令稳定。

## Phase 1：受控主路径与检索工具接入

- [进行中首批] L0 已加入消息字符上限、单请求超时和显式预算配置；预算上下文传递给 Agent，工具调用由 Tool Executor 预留预算。模型运行时已采集提供方返回的实际 Token 用量并纳入预算。
- [已接入首批] L2 已建立可注入 Router 边界；默认命令 Router 支持 `/read <path>` 与 `/write <path>` 后接换行内容，其他消息进入 direct，缺少必要参数时走 clarify。
- [已完成首版模块] Tool Registry、Policy Engine、Tool Executor 和 Task Verifier 已具备模块级契约；工具执行包含参数校验、capability 默认拒绝、高影响操作异步确认、预算预留、超时及审计。FastMCP 工具已接入 DeepAgents 调用循环，并通过 MCP 拦截器进入现有 Tool Executor。WebSocket 聊天已提供请求级人工审批；无审批 Provider 的 HTTP 调用对高影响工具 fail-closed。
- [已接入首批] 内置 `read_text_file` 与 `write_text_file` 仅访问后端启动工作目录下的 UTF-8 文本文件；读写大小受限，禁止绝对路径、路径穿越和符号链接，写入不得创建新父目录。写入在策略允许后仍须用户明确批准。
- [已接入首版] 使用 FastMCP 开发 MCP 工具；LangChain MCP 拦截器将 Agent 调用送入 Tool Registry、Policy Engine 和 Tool Executor，使用请求级共享预算并校验工具结果。
- [已接入首版] 汇总当前 Agent turn 中模型返回的 usage_metadata / token_usage，写入聊天响应、请求预算及 Cost 事件。
- [已完成首版] MemoryWriteProposal 已接入待审批列表、显式批准和拒绝 API，并记录不含提案正文的结构化审计事件；队列仅保存在进程内，重启会清除，尚未接入模型自动生成提案或持久化待审批项。
- [已完成首版] L4 对 direct 和 clarify 响应执行非空、长度校验与空白规范化；策略复核和完整审计存储仍待主流程装配。
- 建立 L0–L2、Tool Registry、Policy Engine、Tool Executor、Task Verifier 和 L4 基础流程。
- [已接入首版] FastMCP 知识库工具只向 Agent 暴露 query；本地配置 RAG_BASE_URL、RAG_USER_ID、RAG_KEY 后由后端注入凭证、调用 RagRetrievalClient 并校验 EvidenceSet。未配置时仍返回“知识库暂未开启”。真实 RAG 服务联调仍待服务方确认实际接口。
- 建立端到端 Trace、审计事件和检索 EvidenceSet 契约。
- [已完成首版] 本地 Skill 发现、校验、添加/修改后的重新加载，以及 DeepAgents 状态快照和按需加载适配。
- [已完成首版策略门] 高影响操作须先通过策略授权，再确认；拒绝、取消和超时均阻断执行，确认不覆盖策略拒绝。
- [已完成首版] WebSocket 以 `tool.confirmation.request` 展示不含正文的操作摘要，并以匹配 request_id 的 `tool.confirmation.decision` 接收批准、拒绝或取消；审批等待上限为 60 秒。
- [自动化验证已完成] 使用确定性运行时贯通 MCP Provider、Tool Executor、WebSocket 审批和工作区文件写入；批准后才写入，拒绝、取消和超时均不产生文件副作用。前端覆盖审批拒绝/取消和状态 WebSocket 重连。
- [已通过本机真实模型联调] 用户已验证模型发起 MCP 写入、浏览器审批展示、批准后写入及拒绝后不执行；自动化测试仍使用确定性运行时替身。
- [已完成首版] Vue 前端增加工具目录页，通过 `/api/v1/tools` 展示后端注册工具、可用状态和审批要求；凭证与原始工具参数不会返回前端。
- [下一项] 使用真实 RAG 服务核对接口路径、身份凭证传递和 EvidenceSet 响应格式，并完成检索工具联调。
- 暂不启用 plan_execute 或 WASM 沙箱。

**验收重点**：客户端请求校验明确；工具调用先授权后执行；query 已完成指代消解；Skill 包路径与元数据验证有效；高影响操作未经确认不会执行；外部调用的凭证与用户范围按接口协议传递；超时/预算可控；检索来源可追踪。

## Phase 2：受控计划编排

- 加入 planner、plan critic、DAG orchestrator、隔离上下文的 executor agents、验证、局部有限重规划和聚合。
- 子任务信封携带已解析的检索 query；子 Agent 不负责解析对话指代。
- 对外部检索能力只通过检索工具契约调用。Agentic Retrieval 的改写、内部循环及其服务侧隔离属于独立 RAG 项目及其路线。

**验收重点**：依赖注入可审计；失败任务可局部恢复且重规划有上限；外部检索服务失败时状态明确。

## Phase 3：不可信代码隔离

- 对 LLM 生成脚本和不可信第三方工具接入沙箱；按信任和平台选择 WASM、OS 原生或远程后端。
- WASM 默认零网络、零文件系统、零宿主函数；capability 经 Policy Engine 明确批准。
- 确定性 API / DB 工具继续采用宿主或服务执行，配合策略与网络控制。

**验收重点**：资源上限可强制执行；超时后进程/实例清理；每次 capability 授权和拒绝都有审计；明确普通 Python 工具不自动受执行后端隔离。
