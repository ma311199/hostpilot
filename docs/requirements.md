# 需求基线

## 项目目标

为本地浏览器 Web 客户端提供本地优先、Skill 驱动、可治理且可观测的 Agent Harness。模型、Skill 和 MCP 工具通过统一接口适配；请求路由、工具使用、长任务编排和输出均受策略、预算与审计约束。目标架构见[新架构文档](新架构文档.md)及其已确认修订。

Agentic RAG 是独立项目。HostPilot 通过检索工具调用其服务，不负责实现 RAG 索引、检索流水线或服务侧多用户隔离。

当前仓库聚焦 HostPilot 后端，对外 HTTP API 主要使用 FastAPI；Web 前端作为独立项目开发，通过后端 API 契约对接。

## 功能需求

| 编号 | 需求 | 阶段 |
| --- | --- | --- |
| F-01 | 接收本地 Web 客户端请求，完成输入校验、归一化、限流及 PII / 注入初筛；当前单用户本地部署免登录 | 基础层 |
| F-02 | Context Builder 按 session_id 从 SQLite 恢复会话状态，读取相关 Markdown 记忆并初始化预算，输出 ContextSnapshot | 基础层 |
| F-03 | Router 从已注册的业务 Skill 和内置 Skill 中选择一个 skill_id；Router 负责选择，不负责执行 Skill 或工具 | 新架构基础层 |
| F-04 | Tool Registry 管理检索、动作和代码工具及其信任级别、执行环境和超时配置 | 基础层 |
| F-05 | Policy Engine 在工具执行前校验用户授权、参数、数据范围和 capability | 基础层 |
| F-06 | SkillEngine 通过统一 Tool Facade 调用 MCP 工具；内置 FastMCP Server 沿用进程内连接，外部 MCP Server 支持本地 STDIO 子进程和独立 Streamable HTTP Client；所有调用均经 HostPilot 准入校验、Tool Registry、Policy Engine、预算和结果验证 | 新架构基础层 |
| F-07 | 知识库启用前，FastMCP 工具作为普通工具受治理并返回明确的未开启提示；服务启用后再调用独立 RAG 服务并校验 EvidenceSet | Phase 1 起 |
| F-08 | L4 对输出做校验、策略复核、记忆写入提案和响应格式化 | 基础层 |
| F-09 | 横向治理记录 Trace、Cost、Audit、Eval 和 Policy 决策；模型用量记录提供方实际 Token 数 | 基础层，逐步完善 |
| F-10 | 计划编排作为内置 Skill，通过内置算子注册表分发到原生 OrchestratorExecutor；支持 DAG、子 Skill 委派、验证和有限重规划 | 新架构计划编排能力 |
| F-11 | HostPilot 将检索请求和预算传给独立 RAG 服务；Agentic Retrieval 内部循环由该服务负责 | 外部服务能力，按接口演进 |
| F-12 | LLM 生成脚本及不可信工具按策略路由到隔离沙箱 | Phase 3 |
| F-13 | Sandbox Router 可按平台和信任等级选择 WASM、OS 原生或远程后端 | Phase 3，后端待决策 |
| F-14 | Skill Registry 支持 Markdown `SKILL.md` 与 SkillEngine `skill.yaml` 两种执行格式；按包文件默认选择，存在 `skill.yaml` 时走 YAML（契约无效则禁用且不回退），只有 `SKILL.md` 时使用 Markdown 执行器；Skill 管理配置允许选择 YAML、Markdown 或两者 | 新架构基础层，Web 配置后续交付 |
| F-15 | 高影响工具操作须在策略授权后取得用户明确确认，拒绝或取消时不执行 | 基础层 |
| F-16 | 支持同一终端用户创建多个相互隔离的会话，每个会话按独立 session_id 读取上下文 | 基础层 |
| F-17 | 提供本地项目进度看板，展示后端阶段、已完成模块、下一步事项及 API 健康状态 | 前端首版 |
| F-18 | 提供本地聊天界面，管理会话列表、加载历史，并通过 WebSocket 发送消息及接收回复 | 前端首版 |
| F-19 | 在本地页面配置 OpenAI 兼容模型并立即启用运行时；模型凭证不返回前端或持久化到浏览器 | 前端首版 |
| F-20 | 页面展示后端实际注册的工具、配置状态和审批要求，不暴露工具凭证 | Phase 1 |
| F-21 | MCP 工具 Facade 屏蔽内置进程内 MCP 会话、本地 STDIO 子进程与独立远程 Streamable HTTP 会话；Skill 引用稳定工具标识，不使用强制的 `local::` / `team::` 命名空间 | 新架构基础层 |
| F-22 | 路由先语义消解并检查 query 可解析性，再以 BM25 与本地 ChromaDB 语义向量召回业务 Skill，按配置的 Reranker Model / 轻量 LLM / 主 LLM 顺序降级判断候选。默认仅当 top1 置信度 ≥0.85 且 top1-top2 margin ≥0.30 时快速执行唯一高置信业务 Skill；没有唯一高置信 Skill 时，多个置信度 >0.50 的业务候选最多取前 5 个进入 Plan，低于阈值的候选不进入 Plan；全低时再判断直答、澄清或 Plan。缺少必需槽位时由 Skill 执行层澄清。Embedding 不可用时退化为 BM25；只有主 LLM 配置时仍使用主 LLM 进行路由处理 | 新架构路由治理 |
| F-23 | UI 提供 Harness 配置、MCP 连接管理、Skill 库管理和 Trace 观测能力 | 新架构客户端层 |
| F-24 | 全局记忆控制器负责入口记忆召回、子任务按需召回和归档；Skill/Executor 不直接读写持久记忆存储 | 新架构横切能力 |
| F-25 | 模型 Provider 提供统一推理接口，并可适配 OpenAI 兼容 API、供应商原生 API、自托管推理服务等 LLM 接入类型；超时、健康状态和降级行为可配置 | 新架构模型接入层 |
| F-26 | 业务 Skill 声明副作用等级；Runtime 在执行前评估适用性，执行后评估过程与结果；允许升级 Plan 时携带完整执行快照，并按副作用、幂等性、补偿路径和全局剩余预算阻止重复执行 | 新架构执行治理 |
| F-27 | MCP Server 的非敏感连接配置跨应用重启持久化；`api_key` 不持久化，仅在运行期间由用户提交并保存在进程内存，重启后需重新输入。配置读取仅返回密钥是否已提供，不返回密钥值 | Stage E Harness 配置 |
| F-28 | 模型名称、接入类型、Base URL 和推理选项等配置跨应用重启持久化；Embedding 与 Reranker `api_key` 存入本地 SQLite 并在重启后恢复，主 LLM 与轻量 LLM `api_key` 仍只保存在后端进程内存。配置读取仅返回密钥是否已配置，不返回密钥值 | Stage E Harness 配置 |
| F-29 | Harness 页面管理已接入运行时的上下文消息/记忆字符限制、请求/响应字符限制、单请求 Token/工具/知识检索预算及超时，并管理 Plan 最大子任务数、工具子 Agent 最大轮数、每任务工具候选数和路由候选阈值/数量；配置在本地 SQLite 持久化，启动恢复并在线应用 | Stage E Harness 配置 |
| F-30 | 用户 Skill 输入/输出 JSON Schema 必须限制结构复杂度，且只允许自包含引用；加载或执行用户包不得访问外部 Schema 资源 | Stage F 安全隔离 |
| F-31 | 业务 Skill 的 Plan fallback 只能委派给 manifest `dependencies.skills` 显式声明且当前启用的子 Skill；默认拒绝未声明目标，顶层 builtin Planner 限定为已注册的启用 Skill | Stage F 安全隔离 |
| F-32 | YAML Skill 的 MCP 工具 action 和 Plan fallback 工具任务必须声明在 manifest `dependencies.tools` 中；空列表默认拒绝，顶层 builtin Planner 只能使用当前注册工具，执行前再次校验 | Stage F 安全隔离 |
| F-33 | 远程 MCP 工具参数必须符合 Server 提供的有界 JSON Schema；Schema 仅允许有效的本地引用，拒绝外部引用和超限结构，并在策略、审批及工具执行前校验 | Stage F 安全隔离 |
| F-34 | MCP 凭证不得通过 STDIO args 持久化或回显；凭证型参数在 API 和页面导入/表单入口 fail-closed，凭证通过单独的 API Key 环境变量配置；启动时清除旧配置中的凭证型 args 并停用对应 Server | Stage F 安全隔离 |
| F-35 | MCP 本地工具发现和远程 Server 连接/发现必须有超时；调用超时受 Server 与请求预算共同约束；启动取消须清理当前及已启动会话，关闭操作有界且错误可定位 | Stage F 运行可靠性 |
| F-36 | Plan 重规划次数有上限，且不得重复执行无新信息的失败任务；工具调用不自动重试；Audit 元数据携带 trace_id 并禁止写入原始参数和正文 | Stage F 运行可靠性 |
| F-37 | Audit 事件在本地 SQLite 有界留存，并提供按 trace/时间筛选、offset 分页与公式安全 CSV 导出的只读接口 | Stage F 可观测性 |
| F-38 | 所有请求内模型调用须共享同一预算，按剩余额度限制输出 Token 并在收到提供方用量后立即记账；用量超额后锁存耗尽状态，排队的后续模型请求和新工具调用不得启动 | Stage F 运行可靠性 |
| F-39 | 用户可从本机原生文件夹选择器设置全局工作区；工作区路径本地持久化，目录不可读时保留原配置，可读不可写时以只读模式使用并阻止写工具 | Stage E 工作区管理 |
| F-40 | 用户可从聊天会话列表删除会话；删除其消息、检查点、会话摘要和待审批记忆提案，并在删除当前会话后安全切换到其他会话或空白会话 | Stage E 会话管理 |
| F-41 | 每个会话独立持久化沙箱级别，新会话默认为 read-only；用户明确批准后可单向升级至 workspace-write 或 danger-full-access，或从 workspace-write 升至 danger-full-access；不得降级 | Stage E 会话安全 |
| F-42 | STDIO MCP 工具按 HostPilot 受信任的工具能力 profile 授权和路由；MCP Client 从 Linux 沙箱 Server 进程池分配匹配实例，不同 profile 不共享进程；profile 明确文件/工作区挂载、网络、凭证、CPU 配额、内存/Swap、进程数、磁盘或临时存储上限、并发及超时限制，隔离不可用时拒绝执行 | Stage F Linux 安全隔离 |
| F-43 | 统一模型配置页面按 LLM Model、轻量 LLM Model、Embedding Model、Reranker Model 顺序管理模型角色；主 LLM 为唯一必需配置，可选模型缺失或不可用时按降级策略继续处理。模型配置写入本地 SQLite；Embedding 与 Reranker Key 跨重启保存，主 LLM 与轻量 LLM Key 保存在进程内存并在重启后重新输入 | 模型配置与降级 |
| F-44 | 业务 Skill 路由向量及其版本元数据存储在 `data_dir` 下的本地 ChromaDB；向量按 Skill 元数据内容哈希增量更新，Skill 删除/禁用后不再进入召回；Embedding 模型变化时重建对应索引 | 路由检索 |

## 非功能需求

- **安全**：默认拒绝未声明 capability；工具调用遵循最小权限；WASM 默认无网络、无文件系统访问；高影响操作受 Policy Engine 控制。
- **个人数据保护**：会话读取遵循最小必要原则；不同 session_id 的上下文必须隔离；个人数据和服务凭证不得进入不必要的日志或测试夹具。
- **本地持久化**：会话目录与检查点存入本地 SQLite；会话摘要和跨会话长期记忆以 Markdown 管理。记忆文本是不可信数据，不构成工具授权。
- **本地访问边界**：当前版本前后端部署在同一终端，Web 操作免登录；默认仅供该终端本机访问。远程访问不属于当前需求。
- **工作区权限**：read-only 禁止本会话文件写入；workspace-write 仅允许在用户明确选择的工作区内读写；danger-full-access 允许内置文件工具访问当前操作系统用户有权限访问的任意路径。任何模式均不提升进程权限或修改目录权限；会话级授权与操作系统权限取交集。高影响工具操作仍需逐次审批。
- **会话沙箱升级**：级别仅可单向升级并持久化到会话；每次升级由用户确认，审计保存会话 ID 和级别转换。完全访问仅作用于已注册的本地文件工具，不等于 shell、网络或 MCP 权限。
- **外部服务边界**：调用独立检索服务时保护服务凭证，并按双方确认的协议传递用户身份或数据范围；RAG 服务负责其内部多用户隔离。
- **可观测性**：Agent、工具及检索调用使用关联 Trace；记录耗时、预算消耗、策略决策和证据选择过程。
- **可控性**：独立设置请求预算、工具超时、外部检索调用限制及沙箱资源限制；超限时返回可识别的部分结果或失败状态。
- **STDIO 沙箱资源**：资源限制按工具能力 profile 配置，覆盖 CPU、内存/Swap、进程数、文件系统挂载与写入容量、临时磁盘/内存文件系统、网络、凭证、并发和超时。各 profile 的具体数值及磁盘配额实施机制须经 Linux 基准测试与底层隔离能力验证后确定；未落实的限制不得对外宣称已生效。
- **可测试性**：策略、契约和业务逻辑可在不访问真实模型/外部服务的情况下测试；外部依赖通过适配接口隔离。
- **Skill 安全**：本地 Skill 内容按不可信输入处理；加载不执行包内代码；Skill 不能授予自身工具权限；高影响操作须经用户确认且仍受策略授权约束。
- **可扩展性**：检索服务、模型提供方、工具和沙箱后端通过明确接口替换或扩展。
- **可维护性**：按领域模块组织代码；关键契约、依赖决策、测试结果及优化记录持续文档化。
- **平台范围**：当前产品阶段支持 Linux Agent；Linux Agent 完成后再单独开发 Windows Agent。不得将 Linux 沙箱保证描述为 Windows 能力。
- **前端边界**：Vue 前端置于 frontend/，以本地浏览器 Web 方式运行；阶段进度由前端按路线文档维护；后端状态和聊天消息分别通过 FastAPI WebSocket 端点通信。
- **传输隔离**：Skill 文件只引用工具唯一标识；MCP Transport 的选择、连接生命周期和凭证由 Harness 连接管理层负责。
- **凭证保护**：主 LLM、轻量 LLM 和 MCP 的 `api_key` 只由后端进程内存持有并在重启后重新输入；Embedding 与 Reranker 的 `api_key` 写入本机 SQLite，以支持本地模型服务重启恢复。所有模型/MCP 配置 API 均不得返回密钥值；密钥不进入浏览器存储、Skill 文件、模型上下文、工具 schema 或普通日志。SQLite 数据库文件权限限制为当前账户读写（`0600`），文件内容不加密。

## 关键接口基线

1. 知识库 MCP 工具始终只向 Agent 暴露 query。RAG_BASE_URL、RAG_USER_ID、RAG_KEY 在本地后端成组配置；配置后后端调用 POST {RAG_BASE_URL}{RAG_RETRIEVAL_PATH}（默认 /retrieve），注入 user_id/key 并按 EvidenceSet 校验响应。未配置时工具返回“知识库暂未开启”。
2. ToolDescriptor：至少描述 tool_type、trust_level、execution_env 和 timeout_profile。
3. SandboxPolicy：显式描述 capability、资源限制及策略决策，未批准能力默认拒绝。
4. SkillPackage：本地来源、经校验的元数据、指令内容和受限资源引用；具体字段在实现前确定。
5. YAML SkillEngine 读取 `skill.yaml` 并使用其结构化路由签名；DeepAgents Runtime 执行 `SKILL.md`。双格式包默认走 YAML，可通过 Skill 管理配置显式选用一种或两种入口；不得自动转换格式或因一次执行失败而切换入口。
6. Skill 只引用 MCP 工具稳定标识，不在 Skill 契约中指定连接方式；内置工具沿用现有工具名，外部 Server 工具按 Server 名称区分冲突，不采用 `local::` / `team::` 命名。
7. Provider、Tool Facade、Memory Controller 和 Runtime 的边界契约见 [ADR-0010](decisions/0010-harness-contracts-and-configuration-boundary.md)；新 Skill/Executor 不得直接依赖供应商 SDK、MCP Transport 或持久记忆实现。
8. 业务 Skill 快速路径、执行前/后评估、`side_effect_level` 和带快照的 Plan 兜底按 [ADR-0012](decisions/0012-business-skill-fast-path-and-plan-fallback.md) 执行；评估不等于工具授权，所有副作用仍受 Policy Engine 与 Tool Executor 治理。

## 待确认事项

- 后端运行入口、启动与打包方式。
- API 版本策略、生产运行环境、会话与长期记忆保留期限、备份及数据库迁移策略。
- Python 项目配置基线为 3.11+ 并使用 uv；生产支持矩阵及 CI 平台待确认。
- LangChain、LangGraph、DeepAgents 的兼容版本与具体 API 组合；Gradio 仅用于开发期原型。
- DeepAgents 与 YAML Skill 的统一目录、执行方式显式配置及 Web Skill 管理能力按 ADR-0013 后续实现。
- OAuth 等非 `api_key` 认证方式的秘密管理策略；以及记忆向量召回是否需要和所用存储后端。模型与 MCP 的非敏感配置目前存入应用 SQLite 数据库。
- 首批 LLM 接入类型与适配器范围；Provider 通用契约已在 ADR-0010 中明确。
- 独立 Agentic RAG 服务的接口协议、服务凭证传递、用户范围映射及过滤语义。
- STDIO MCP 工具能力 profile 如何表达有状态 Server、凭证作用域及进程池容量；Windows Agent 在 Linux Agent 完成后的独立技术方案。
- SLA、并发量、延迟、成本和数据驻留目标。
