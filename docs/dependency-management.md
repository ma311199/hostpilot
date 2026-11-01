# 依赖管理

## 当前状态

项目使用 Python 3.11+ 与 uv。Python 版本下限、依赖范围和开发工具在 pyproject.toml 中声明；uv.lock 用于可复现安装。FastAPI、aiosqlite 和 Uvicorn 是本地后端 API 与会话目录的运行依赖；HTTPX 在 dev 组用于 API 测试，在 agents 组用于 RAG HTTP 适配器。agents 组包含 LangChain、LangGraph、DeepAgents、langchain-openai、langgraph-checkpoint-sqlite、FastMCP 和 langchain-mcp-adapters。模型适配首版采用 OpenAI 兼容 Chat API，不限定供应商；运行时不配置 Base URL 时采用 SDK 默认地址。已在当前依赖锁定组合中验证 DeepAgents 0.7.32、LangGraph 1.2.18 与 SQLite checkpointer 3.1.2。Web 前端独立开发，不纳入本仓库依赖；Gradio 仅用于开发期原型。CrewAI 未纳入项目依赖。FastMCP 与 langchain-mcp-adapters 已加入 agents 依赖组并通过工具调用集成测试；当前锁定 FastMCP 2.14.7、langchain-mcp-adapters 0.3.2。Agentic RAG 是独立项目能力，不作为 HostPilot 内部实现依赖；HostPilot 用 FastMCP 定义工具，并由 MCP 适配器接入 DeepAgents。Markdown 记忆使用标准文件接口，不额外引入存储服务。

## 选择规则

- 先明确依赖对应的模块、使用理由、许可证、维护状态及安全边界。
- 核心代码依赖 HostPilot 契约，不直接依赖具体供应方对象或框架专有类型。
- LangChain、LangGraph、DeepAgents 的组合已确定；SQLite checkpointer 3.1.2 已通过重启恢复和会话隔离集成验证。其他框架 API 组合在相关模块实现前继续验证。
- FastAPI 仅作为后端 HTTP API 适配层；核心业务模块和稳定领域契约不得依赖 FastAPI 专有类型。
- Web 前端独立开发，不纳入 HostPilot 后端依赖。
- Gradio 仅用于开发期交互原型，不能被核心业务模块或稳定领域接口依赖，也不代表正式 Web 前端。
- Agentic RAG 服务通过 HostPilot 的检索工具适配边界集成，不安装其内部实现依赖。
- 沙箱 SDK 或运行时按 Phase 3 的后端决策再引入。
- 版本采用可复现锁定；升级需记录兼容性检查、变更影响和安全公告检查结果。
- 禁止把密钥或私有索引地址写入依赖配置。

## 依赖组

- dev：Ruff、mypy、pytest 等开发与质量工具。
- agents：LangChain、LangGraph、DeepAgents、OpenAI 兼容模型适配器和 SQLite checkpointer。
- prototype：Gradio 开发期原型界面。
- FastMCP 用于 MCP 工具开发；langchain-mcp-adapters 将 MCP 服务工具映射为 LangChain Agent 工具，并由工具拦截器接入 HostPilot 治理链。
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

私有包源、依赖扫描工具、实际使用的模型供应商与模型，以及 LangChain / LangGraph / DeepAgents 的后续兼容升级策略仍需确认。
