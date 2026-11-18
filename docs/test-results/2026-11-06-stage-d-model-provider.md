# Stage D：模型 Provider 契约与适配器验证

- 日期：2026-11-06
- 范围：HostPilot 模型 Provider 契约、OpenAI 兼容 Chat API 适配器及现有模型调用点迁移
- LLM 接入类型：OpenAI 兼容 Chat API
- 新增运行依赖：无

## 实现

- `hostpilot.models` 定义统一的消息角色、模型请求/响应、可选工具 schema、规范化工具调用、结束原因、Provider/模型标识及分类错误。
- `OpenAICompatibleChatProvider` 适配当前 OpenAI 兼容 Chat API，将普通文本和聚合流式响应统一为 HostPilot 文本/用量结果；工具调用、超时及取消也转换为稳定契约。
- 模型凭证只由 Provider factory 从 `Settings` 注入 SDK，不属于模型请求或响应对象；Provider 错误不携带原始异常文本，避免把请求或敏感细节写入上层日志。
- Skill 选择、单轮回答、Planner、Verifier、准入/结果评估和 YAML `llm.complete` action 已改为依赖 `ModelProvider`。
- DeepAgents/LangGraph Agent 图保留 LangChain 的模型接口和工具循环，但由 `ProviderChatModel` 桥接到相同的 `ModelProvider`，历史工具调用、工具 schema、工具选择、调用结果及 Token 用量均在桥接层转换。LangChain 仍是 Agent 编排框架接口，不再直接构造第二套模型调用链。
- 当前适配覆盖文本消息和函数工具调用；多模态消息、供应商原生 API、自托管服务及其他 Provider 类型未实现。

## 验证

- 定向测试：**36 passed**，覆盖模型请求/响应归一化、流式聚合文本与 Token 用量、工具调用往返映射、Provider 错误脱敏、超时/取消、模型工厂配置、选择器、Plan、代码审查 action 和运行时装配；后端全量测试 **219 passed**。
- Ruff lint、Mypy（`src/hostpilot`）和本次涉及源码/测试文件的格式检查通过。全仓格式检查只报告既有的 `tests/unit/api/test_app.py` 格式差异，该文件包含此前已有的未提交改动，本次未重排其内容。
- 前端 lint、15 项测试及生产构建/类型检查通过。
- 没有发起真实模型调用；多模态消息、供应商原生和自托管 Provider 未验证。

## 后续

- 根据实际需求增加其他 LLM 接入类型，不因 Provider 首轮实现预设特定供应商或推理引擎。
- 按 Stage D 继续实现 MCP Tool Facade 和 Memory Controller。
