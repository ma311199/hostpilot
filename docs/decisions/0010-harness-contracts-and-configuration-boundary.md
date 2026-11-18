# ADR-0010：Harness 服务契约与配置边界

- 状态：已采纳；Stage D 首轮 Provider、Tool Facade 与 Memory Controller 已实现，扩展能力继续分阶段迁移
- 日期：2026-11-02
- 关联：[新架构文档](../新架构文档.md)、[需求基线](../requirements.md)、[路线图](../roadmap.md)、[依赖管理](../dependency-management.md)

## 背景

新架构需要先稳定 Skill Runtime 使用的模型、工具和记忆边界，避免业务 Skill 依赖 LangChain、MCP Transport、SQLite 或 Markdown 等具体实现。模型 Provider 首轮实现见[Stage D Provider 验证](../test-results/2026-11-06-stage-d-model-provider.md)；Tool Facade 与 Memory Controller 首轮实现见[Stage D Facade 与记忆控制器验证](../test-results/2026-11-06-stage-d-facade-memory-controller.md)。

## 决定

### Skill 与 Runtime

- `skill_id` 是路由、执行、授权和 Trace 关联 Skill 的稳定标识。内置 Skill 使用 `builtin/direct_answer`、`builtin/clarify`、`builtin/orchestrator_plan`；业务 Skill 使用包内唯一且稳定的 ID。ID 不编码具体执行框架。
- Router 只返回已注册的 `skill_id` 和路由理由，不直接执行 Skill 或工具。
- Runtime 输入包含 `request_id`、`skill_id`、经输入 schema 校验的输入、不可变 `ContextSnapshot` 和请求级预算/取消上下文；Runtime 输出包含经输出 schema 校验的结构化结果、状态及可关联的调用标识。
- Runtime 通过 HostPilot 定义的服务接口使用模型、工具、记忆及 Trace 能力。领域契约不暴露 LangChain、LangGraph、DeepAgents、FastAPI 或特定 MCP Client 类型。
- Runtime 和 Skill 共用现有 Policy Engine、预算、审批、超时、结果验证、错误映射及 Audit/Trace 横切能力；Skill 声明不授予工具权限。子 Skill 候选范围由父 Skill 的 `dependencies.skills` 限定，但目标仍须处于启用的有效注册表中，并受委派深度与请求预算限制，具体见 [ADR-0009](0009-skill-runtime-and-yaml-contract.md)。

### LLM Provider

- Provider 接收规范化的消息、模型选项、工具 schema（可选）、预算/超时及取消信号；返回规范化的文本或工具调用、结束原因、用量和 Provider/模型标识。错误映射为可分类的 HostPilot 错误，不向上层泄露 SDK 专有对象。
- Provider 适配按**LLM 接入类型**划分，例如 OpenAI 兼容 API、供应商原生 API、自托管推理服务。每种类型的字段由适配器声明；凭证不属于请求上下文、Skill 契约或普通日志。
- 具体模型供应商、推理引擎、SDK、健康检查和降级策略在相应实现阶段评估；当前不指定某一推理引擎。

### MCP Tool Facade

- Skill 只引用稳定 `tool_id`，不声明 endpoint、Transport 或凭证。内置工具保留现有名称；远程工具以 Server 名称区分冲突，并由连接层维护 `tool_id` 到具体 Client / 原始工具名的映射。
- Facade 提供工具发现/描述和按 `tool_id` 调用的统一接口；调用结果规范化为 HostPilot 工具结果或分类错误。
- Facade 必须把每次调用交给现有 Tool Registry、Policy Engine 和 Tool Executor，继续执行参数校验、预算、审批、超时、结果验证和审计。发现到工具不代表获准调用。
- 内置 FastMCP 保持进程内连接；外部 Server 可通过独立 Streamable HTTP Client 或本地 STDIO 子进程连接，具体规则见 ADR-0016。
- 首轮 Facade 是 MCP Provider 外侧的 HostPilot 稳定接口；Provider 负责发现和 transport 路由，Facade 不绕过 Provider 的治理链。更换其他工具来源时可实现同一 adapter contract。

### Memory Controller

- Skill、Executor 和 steps 解释器只通过 Memory Controller 请求记忆召回或提交归档候选，不直接读写 SQLite、Markdown 或未来的检索存储。
- 召回结果是有范围、有来源且受大小限制的记忆片段；入口预召回和子任务按需召回使用同一接口。
- 写入接口提交候选内容和来源元数据。Controller 负责过滤、摘要、权限范围、审计和审批流程；提交候选不等于立即持久化。现有显式用户批准要求继续有效。
- 当前 Markdown 后端仅支持按 conversation_id 读取会话摘要和读取本地用户共享记忆，并受总字符预算限制；不提供语义相关性排序。首轮 archive 将来源 Skill ID 和内容作为提案提交既有审批队列，未经批准不得写入。
- ContextBuilder 入口与 Plan 子任务使用相同 recall 接口；Recall/archive Trace 仅包含操作状态、耗时、字符数、chunk 数和来源 Skill ID 等白名单元数据，不记录 query 或记忆正文。Trace Sink 故障不改变记忆访问或审批行为。
- 先保留 SQLite 会话检查点与 Markdown 记忆。是否需要向量召回及其存储由检索质量和规模需求驱动，不作为 Stage A 的前置依赖。

### 配置与秘密

- 浏览器通过本地配置 API 管理 Provider 选择、模型参数、MCP Server 连接元数据及 Harness 策略；Skill 的执行步骤和业务 Prompt 不属于 Harness 配置。
- 配置资源按 Provider profile、MCP Server connection 和 Harness policy 分开管理。读取操作返回稳定 ID、接入类型、非敏感参数和运行状态；MCP 工具目录来自连接后的动态发现，不由配置 API 手工维护。
- 秘密值只允许通过写入、轮换和清除操作提交。读取接口只返回是否已配置及非敏感元数据，不返回秘密值；秘密不得进入模型上下文、Skill 包、工具 schema 或普通日志。
- MCP Server 的连接元数据可以本地持久化；`api_key` 只在运行期间由后端进程内存持有，重启后要求重新输入。该边界及读取语义见 [ADR-0014](0014-mcp-config-persistence-with-ephemeral-api-keys.md)。
- 模型配置本地持久化；主 LLM 与轻量 LLM Key 仅存于后端进程内存，Embedding 与 Reranker Key 按用户确认存入本地 SQLite，读取接口只返回已配置状态。MCP Key 仍只在进程内存中持有。模型侧行为见 [ADR-0015](0015-model-config-persistence-with-ephemeral-api-keys.md)。
- 浏览器不负责长期保存模型或 MCP 凭证；任何 API Key 都不得写入本地持久配置。
- OpenAI 兼容模型设置、模型/MCP 非敏感元数据持久化、进程内 API Key 管理和本地页面配置 API 已交付；`HOSTPILOT_MCP_SERVERS` 环境配置继续兼容。
- 通用 Harness 首批仅允许配置当前运行时已支持的上下文/消息限制、单次请求预算和超时；这些配置持久化至本地 SQLite，并在聊天请求运行时锁保护下在线应用。未实现执行行为的重试、Agent 循环上限、工具白/黑名单和沙箱控制不进入页面配置。

## 影响

- Stage A 契约可用于后续 Skill Registry / Runtime 和 Provider、Facade、Controller 的实现；这些服务可分阶段交付，不要求一次性重写现有运行链。
- 当前 DeepAgents Skill 格式与新 `skill.yaml` 的过渡方式仍是 Stage B 的实现门槛；需要在启动 Registry 迁移前通过 ADR 明确迁移或兼容方案。
- 记忆后端、具体 LLM 接入类型的首批范围、凭证加密实现和框架适配方式仍需在对应模块实现前确定。
- 后续应为规范化契约、Provider 适配、Facade 治理委托、记忆范围隔离与秘密脱敏分别添加契约和集成测试。

## 复核条件

当首批 LLM 接入类型、远程 MCP 管理方式、记忆检索质量或安全存储实现无法满足上述契约时，复核对应的适配器边界，不改变 Skill 对底层 SDK、Transport 和存储实现不可见的原则。
