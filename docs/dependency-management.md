# 依赖管理

## 当前状态

Linux OS 沙箱依赖不属于 Python 包：需要系统安装 Bubblewrap 和 util-linux（提供 `prlimit`）；JavaScript 沙箱另需 Node.js。用户安装步骤和命名空间故障排查见 [Linux 安装指南](linux-installation.md)。

项目使用 Python 3.11+ 与 uv。Python 版本下限、依赖范围和开发工具在 pyproject.toml 中声明；uv.lock 用于可复现安装。FastAPI、aiosqlite、Uvicorn、Pydantic 和 JSON Schema 验证器是本地后端运行依赖；HTTPX 在 dev 组用于 API 测试，在 agents 组用于 RAG HTTP 适配器。agents 组包含 LangChain、LangGraph、DeepAgents、langchain-openai、langgraph-checkpoint-sqlite、FastMCP 和 langchain-mcp-adapters。模型适配首版采用 OpenAI 兼容 Chat API，不限定供应商；运行时不配置 Base URL 时采用 SDK 默认地址。已在当前依赖锁定组合中验证 DeepAgents 0.7.32、LangGraph 1.2.18 与 SQLite checkpointer 3.1.2。Web 前端独立开发，不纳入本仓库依赖；Gradio 仅用于开发期原型。CrewAI 未纳入项目依赖。FastMCP 与 langchain-mcp-adapters 已加入 agents 依赖组并通过工具调用集成测试；当前锁定 FastMCP 2.14.7、langchain-mcp-adapters 0.3.2。Agentic RAG 是独立项目能力，不作为 HostPilot 内部实现依赖；HostPilot 用 FastMCP 定义工具，并由 MCP 适配器接入 DeepAgents。Markdown 记忆使用标准文件接口，不额外引入存储服务。

上述为当前代码的依赖事实，不等于新架构的完整实现。HostPilot 自有的路由、Plan、评估和 Skill action 已通过 `ModelProvider` 契约调用模型；首个 OpenAI 兼容适配器仍使用 `langchain-openai`，DeepAgents 图也继续使用框架 ChatModel。未来 LLM 接入类型按需增加 Provider 适配器，不预设特定推理引擎。SkillEngine 与 MCP Tool Facade 依赖 HostPilot 契约；当前 LangChain、LangGraph、DeepAgents 和进程内 MCP 仍服务于已交付实现，后续边界按迁移设计逐步调整。MCP 使用内置进程内 FastMCP，以及 FastMCP 支持的本地 STDIO 与远程 Streamable HTTP Client；STDIO 连接规则见 ADR-0016。新架构目标以 [新架构文档](新架构文档.md) 及已确认修订为准。

## 选择规则

- 先明确依赖对应的模块、使用理由、许可证、维护状态及安全边界。
- 核心代码依赖 HostPilot 契约，不直接依赖具体供应方对象或框架专有类型。
- 当前实现使用 LangChain、LangGraph、DeepAgents；SQLite checkpointer 3.1.2 已通过重启恢复和会话隔离集成验证。Runtime Chat 是 Agent 执行控制面；框架可通过插件/适配器提供 Agent、DAG 调度、模型互操作和 Checkpoint 能力，但必须遵守 Runtime 的能力、预算、审批、取消、状态和恢复契约。替换 `builtin.deepagents` 不等于移除所有框架；仅在相关插件/适配器不再依赖某框架后，才移除对应依赖。
- FastAPI 仅作为后端 HTTP API 适配层；核心业务模块和稳定领域契约不得依赖 FastAPI 专有类型。
- Web 前端独立开发，不纳入 HostPilot 后端依赖。
- Gradio 仅用于开发期交互原型，不能被核心业务模块或稳定领域接口依赖，也不代表正式 Web 前端。
- Agentic RAG 服务通过 HostPilot 的检索工具适配边界集成，不安装其内部实现依赖。
- 沙箱 SDK 或运行时按 Phase 3 的后端决策再引入。
- 版本采用可复现锁定；升级需记录兼容性检查、变更影响和安全公告检查结果。
- 禁止把密钥或私有索引地址写入依赖配置。

## 依赖组

- dev：Ruff、mypy、pytest、PyYAML 和 JSON Schema 类型存根等开发与质量工具。
- agents：LangChain、LangGraph、DeepAgents、OpenAI 兼容模型适配器和 SQLite checkpointer。
- prototype：Gradio 开发期原型界面。
- 当前 FastMCP 与 langchain-mcp-adapters 将 MCP 工具映射到 DeepAgents，并由拦截器接入治理链。内置工具使用进程内 MCP；外部 Server 通过独立 Streamable HTTP Client 或 STDIO Client 连接；Skill 模块不直接依赖传输库。
- CrewAI 不属于当前选型，不在项目依赖清单或锁文件中。

基础开发环境：

    uv sync --locked --python 3.11 --group dev

启用 Agent 框架：

    uv sync --locked --python 3.11 --group dev --group agents

启用 Gradio 原型界面：

    uv sync --locked --python 3.11 --group dev --group prototype

同时开发 Agent 与原型界面：

    uv sync --locked --python 3.11 --group dev --group agents --group prototype

## 锁定流程

1. 在 pyproject.toml 声明项目元数据、Python 版本范围、直接依赖和依赖组。
2. 执行 uv lock 更新 uv.lock，CI 和开发环境使用 uv sync --locked 安装。
3. 通过依赖扫描及许可证检查；升级依赖时同步更新文档和兼容性测试。
4. 不以传递依赖替代直接依赖声明；保留有界版本范围并定期更新锁文件。

## 待决策

私有包源、依赖扫描工具、其他 LLM 接入类型及其依赖、SkillEngine/Plan 对现有 Agent 框架的长期替换范围、MCP 客户端库的后续封装方式，以及长期记忆向量检索存储仍需确认；这些待决策不改变当前已交付的 Runtime、Plan 与工具治理边界。
