# 需求基线

## 项目目标

为个人客户端提供可治理、可观测、可分阶段扩展的 Agent。请求路由、工具使用、外部检索调用、长任务编排和输出均受策略、预算与审计约束。

Agentic RAG 是独立项目。HostPilot 通过检索工具调用其服务，不负责实现 RAG 索引、检索流水线或服务侧多用户隔离。

当前仓库聚焦 HostPilot 后端，对外 HTTP API 主要使用 FastAPI；Web 前端作为独立项目开发，通过后端 API 契约对接。

## 功能需求

| 编号 | 需求 | 阶段 |
| --- | --- | --- |
| F-01 | 接收本地 Web 客户端请求，完成输入校验、归一化、限流及 PII / 注入初筛；当前单用户本地部署免登录 | 基础层 |
| F-02 | Context Builder 按 conversation_id 从 SQLite 恢复会话状态，读取相关 Markdown 记忆并初始化预算，输出 ContextSnapshot | 基础层 |
| F-03 | Router 根据意图、复杂度和风险初判路由到 direct、clarify、tool_call 或 plan_execute | 基础层 |
| F-04 | Tool Registry 管理检索、动作和代码工具及其信任级别、执行环境和超时配置 | 基础层 |
| F-05 | Policy Engine 在工具执行前校验用户授权、参数、数据范围和 capability | 基础层 |
| F-06 | Agent 的 FastMCP 工具调用经 Tool Registry、Policy Engine、Tool Executor 执行，并由 Task Verifier 检查结果 | Phase 1 |
| F-07 | 知识库启用前，FastMCP 工具作为普通工具受治理并返回明确的未开启提示；服务启用后再调用独立 RAG 服务并校验 EvidenceSet | Phase 1 起 |
| F-08 | L4 对输出做校验、策略复核、记忆写入提案和响应格式化 | 基础层 |
| F-09 | 横向治理记录 Trace、Cost、Audit、Eval 和 Policy 决策；模型用量记录提供方实际 Token 数 | 基础层，逐步完善 |
| F-10 | 受控 DAG 编排支持规划、计划审查、子 Agent 执行、验证、有限重规划和聚合 | Phase 2 |
| F-11 | HostPilot 将检索请求和预算传给独立 RAG 服务；Agentic Retrieval 内部循环由该服务负责 | 外部服务能力，按接口演进 |
| F-12 | LLM 生成脚本及不可信工具按策略路由到隔离沙箱 | Phase 3 |
| F-13 | Sandbox Router 可按平台和信任等级选择 WASM、OS 原生或远程后端 | Phase 3，后端待决策 |
| F-14 | Skill Registry 支持校验并加载用户本地添加或修改的 Skill，通过 DeepAgents 适配器提供给 Agent | Phase 1 |
| F-15 | 高影响工具操作须在策略授权后取得用户明确确认，拒绝或取消时不执行 | 基础层 |
| F-16 | 支持同一终端用户创建多个相互隔离的会话，每个会话按独立 conversation_id 读取上下文 | 基础层 |
| F-17 | 提供本地项目进度看板，展示后端阶段、已完成模块、下一步事项及 API 健康状态 | 前端首版 |
| F-18 | 提供本地聊天界面，管理会话列表、加载历史，并通过 WebSocket 发送消息及接收回复 | 前端首版 |
| F-19 | 在本地页面配置 OpenAI 兼容模型并立即启用运行时；模型凭证不返回前端或持久化到浏览器 | 前端首版 |
| F-20 | 页面展示后端实际注册的工具、配置状态和审批要求，不暴露工具凭证 | Phase 1 |
| F-21 | 支持配置多个远程 MCP Server，通过 Streamable HTTP 发现和调用工具；调用仍经 HostPilot Tool Registry、Policy Engine、预算和用户审批 | Phase 1 |

## 非功能需求

- **安全**：默认拒绝未声明 capability；工具调用遵循最小权限；WASM 默认无网络、无文件系统访问；高影响操作受 Policy Engine 控制。
- **个人数据保护**：会话读取遵循最小必要原则；不同 conversation_id 的上下文必须隔离；个人数据和服务凭证不得进入不必要的日志或测试夹具。
- **本地持久化**：会话目录与检查点存入本地 SQLite；会话摘要和跨会话长期记忆以 Markdown 管理。记忆文本是不可信数据，不构成工具授权。
- **本地访问边界**：当前版本前后端部署在同一终端，Web 操作免登录；默认仅供该终端本机访问。远程访问不属于当前需求。
- **外部服务边界**：调用独立检索服务时保护服务凭证，并按双方确认的协议传递用户身份或数据范围；RAG 服务负责其内部多用户隔离。
- **可观测性**：Agent、工具及检索调用使用关联 Trace；记录耗时、预算消耗、策略决策和证据选择过程。
- **可控性**：独立设置请求预算、工具超时、外部检索调用限制及沙箱资源限制；超限时返回可识别的部分结果或失败状态。
- **可测试性**：策略、契约和业务逻辑可在不访问真实模型/外部服务的情况下测试；外部依赖通过适配接口隔离。
- **Skill 安全**：本地 Skill 内容按不可信输入处理；加载不执行包内代码；Skill 不能授予自身工具权限；高影响操作须经用户确认且仍受策略授权约束。
- **可扩展性**：检索服务、模型提供方、工具和沙箱后端通过明确接口替换或扩展。
- **可维护性**：按领域模块组织代码；关键契约、依赖决策、测试结果及优化记录持续文档化。
- **前端边界**：Vue 前端置于 frontend/，阶段进度由前端按路线文档维护；后端状态和聊天消息分别通过 FastAPI WebSocket 端点通信。

## 关键接口基线

1. 知识库 MCP 工具始终只向 Agent 暴露 query。RAG_BASE_URL、RAG_USER_ID、RAG_KEY 在本地后端成组配置；配置后后端调用 POST {RAG_BASE_URL}{RAG_RETRIEVAL_PATH}（默认 /retrieve），注入 user_id/key 并按 EvidenceSet 校验响应。未配置时工具返回“知识库暂未开启”。
2. ToolDescriptor：至少描述 tool_type、trust_level、execution_env 和 timeout_profile。
3. SandboxPolicy：显式描述 capability、资源限制及策略决策，未批准能力默认拒绝。
4. SkillPackage：本地来源、经校验的元数据、指令内容和受限资源引用；具体字段在实现前确定。
5. 远程 MCP Server 通过 `HOSTPILOT_MCP_SERVERS` JSON 数组配置，每项包含唯一名称、Streamable HTTP URL、可选认证请求头和超时；请求头凭证以 SecretStr 保存。远程工具使用 `{server_name}_{tool_name}` 唯一名称，默认要求用户审批。

## 待确认事项

- 后端运行入口、启动与打包方式。
- API 版本策略、生产运行环境、会话与长期记忆保留期限、备份及数据库迁移策略。
- Python 项目配置基线为 3.11+ 并使用 uv；生产支持矩阵及 CI 平台待确认。
- LangChain、LangGraph、DeepAgents 的兼容版本与具体 API 组合；Gradio 仅用于开发期原型。
- 独立 Agentic RAG 服务的接口协议、服务凭证传递、用户范围映射及过滤语义。
- Phase 3 首个沙箱后端，以及 Windows 原生支持的交付优先级。
- SLA、并发量、延迟、成本和数据驻留目标。
