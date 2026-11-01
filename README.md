# HostPilot

HostPilot 是部署在用户终端上的 Agent 后端项目，当前面向单个本地用户，免登录并支持多个独立会话；它提供请求处理、上下文组织、路由、受控工具调用和响应治理，并主要使用 FastAPI 提供 HTTP API。Web 前端作为独立项目开发，通过 API 契约与后端对接。Agentic RAG 作为独立项目开发；HostPilot 仅通过检索工具契约调用它。项目架构、需求、模块边界和分阶段实施建议见 [文档导航](docs/README.md)。

## 项目状态

当前仓库已具备本地 Skill、会话持久化、ContextBuilder、模型运行时、FastMCP 工具治理链、工作区文件工具、WebSocket 人工审批和本地工具目录页；真实模型发起 MCP 高影响操作、浏览器审批、批准写入和拒绝阻断已完成本机手动联调。知识库 MCP 工具只向 Agent 暴露 query，后端可通过 RAG_BASE_URL、RAG_USER_ID、RAG_KEY 等本地配置连接服务；真实 RAG 服务接口联调是 Phase 1 下一项工作。未配置模型时，聊天接口返回 503。目标架构和当前阶段见 [架构设计初版](docs/architecture.md) 与 [实施路线](docs/roadmap.md)。

## 目录概览

~~~text
src/hostpilot/       Python 应用与领域模块
tests/               单元、集成和安全测试
configs/             非敏感的默认配置与示例
scripts/             本地开发及维护脚本
docs/                架构、需求、规范、测试和决策文档
~~~

## 开发入口

开发环境和依赖分组见 [开发指南](docs/development-guide.md) 与 [依赖管理](docs/dependency-management.md)。项目要求 Python 3.11 或更高版本，使用 uv 根据 pyproject.toml 和 uv.lock 创建可复现环境。

~~~bash
uv sync --locked --python 3.11 --group dev --group agents
~~~

本机 API 使用 FastAPI、Uvicorn 和 SQLite。启动后端：

~~~bash
uv run --locked --group agents python -m hostpilot.api.main
~~~

会话与记忆目录可通过 HOSTPILOT_DATA_DIR、HOSTPILOT_MEMORY_DIR 配置；前端开发源地址通过 HOSTPILOT_WEB_ORIGINS 配置为 JSON 字符串数组。

配置真实模型时，在运行环境中设置 HOSTPILOT_MODEL_NAME、HOSTPILOT_MODEL_API_KEY；兼容服务使用自定义地址时设置 HOSTPILOT_MODEL_BASE_URL。密钥不要写入仓库文件。示例：

~~~bash
export HOSTPILOT_MODEL_NAME="your-model-name"
export HOSTPILOT_MODEL_API_KEY="your-secret-key"
export HOSTPILOT_MODEL_BASE_URL="https://your-provider.example/v1"
uv run --locked --group agents python -m hostpilot.api.main
~~~

在上述三个环境变量已设置的同一终端，可以运行真实模型冒烟验证。脚本使用临时数据库目录，检查模型回复、重启续聊和会话隔离，完成后删除临时数据：

~~~bash
uv run --locked --group dev --group agents python scripts/verify_model_chat.py
~~~

Gradio 仅用于开发期原型，不是正式 Web 前端。

独立 Vue 进度看板位于 [frontend/](frontend/README.md)，其架构、依赖和 WebSocket 状态协议见 [前端文档](docs/frontend-architecture.md) 与 [开发指南](docs/frontend-development.md)。

Gradio 原型依赖：

~~~bash
uv sync --locked --group dev --group prototype
~~~

同时启用框架和 Gradio：

~~~bash
uv sync --locked --group dev --group agents --group prototype
~~~

## 安全原则

- 所有工具调用在执行前经过 Policy Engine 授权和参数约束。
- 不可信脚本默认使用零权限隔离；WASM / OS 原生沙箱按阶段接入。
- 不提交个人会话数据、密钥或外部服务运行时凭证。
- 外部 Agentic RAG 服务负责其自身的用户认证、数据范围和多用户隔离。
