# 开发指南

## 开发流程

1. 开始模块实现前更新需求、接口契约和架构边界。
2. 将外部框架与服务封装在适配边界内；先实现可替换接口，再接入具体供应方。
3. 每个行为变更补充对应测试；安全策略变更补充拒绝路径和边界测试。
4. 运行与改动相匹配的质量检查，并在变更说明中记录命令、结果和未覆盖范围。
5. 发现设计决策、测试结论或优化项时更新相关文档与决策记录。

## 环境准备

- 使用 Python 3.11 或更高版本与 uv；仓库以 .python-version 选择 Python 3.11，并通过 uv.lock 固定解析结果。
- 在仓库根目录运行 uv sync --locked --python 3.11 --group dev 创建或同步虚拟环境并安装基础及开发依赖。
- 启用已选 Agent 框架组合：增加 --group agents；启用开发期 Gradio 原型：增加 --group prototype；两组可同时启用。
- 用户本地 Skill 按不可信内容处理；编辑后重新校验。高影响工具操作必须先完成策略授权，再取得用户明确确认。
- 更新依赖范围后运行 uv lock 并审查锁文件变更；CI 使用 uv sync --locked 保证环境与锁文件一致。
- 本地完整质量检查使用 scripts/check_quality.sh；CI 平台确定后可直接调用该脚本。

## Python 代码约定（建议基线）

- 遵循 PEP 8；使用类型标注描述公共接口和跨模块数据。
- 使用 snake_case 命名模块、函数和变量，PascalCase 命名类型，常量使用 UPPER_SNAKE_CASE。
- 公共类、函数和复杂安全逻辑应写清用途、约束、异常与副作用；注释解释原因，不复述代码。
- 避免模块循环依赖、重复策略实现和隐式全局状态；配置通过显式对象注入。
- 日志使用结构化字段，禁止记录密钥、个人会话敏感数据或不必要的完整提示词。
- 错误应使用可区分的类型或错误码；不要吞掉策略拒绝、超时和验证失败。

格式化与 lint、类型检查工具在 pyproject.toml 中使用 Ruff 和 mypy 配置；尚未建立 CI 自动化门禁。

## 配置与密钥

- configs/ 仅存放非敏感默认值和脱敏示例。
- 环境差异通过环境变量或部署配置注入；密钥由运行环境的 secret store 提供。
- OpenAI 兼容模型通过 HOSTPILOT_MODEL_NAME、HOSTPILOT_MODEL_API_KEY 配置；兼容服务可选 HOSTPILOT_MODEL_BASE_URL。密钥以 SecretStr 加载，模型名称、密钥和 Base URL 必须配套校验。
- 本地页面也可通过 /api/v1/model/configuration 配置 OpenAI 兼容模型；页面提交的 API Key 只保存在当前后端进程内存，服务重启后需重新提交。状态接口不会返回密钥。
- 当前知识库 MCP 工具不访问外部服务，调用时返回“知识库暂未开启”。恢复真实检索时，通过 RAG_BASE_URL、RAG_RETRIEVAL_PATH、RAG_USER_ID 和 RAG_KEY 配置；RAG_KEY 以 SecretStr 加载，凭证不向模型或前端暴露，响应按 EvidenceSet 校验。
- 远程 MCP Server 通过 `HOSTPILOT_MCP_SERVERS` 配置为 JSON 数组；每项使用唯一 `name`、Streamable HTTP `url`、可选 `headers` 和 `timeout_seconds`。认证请求头按 SecretStr 处理；远程工具名加 Server 前缀，默认调用前要求用户审批。

```bash
HOSTPILOT_MCP_SERVERS='[{"name":"knowledge","url":"https://mcp.example.com/mcp","headers":{"Authorization":"Bearer ${KNOWLEDGE_MCP_TOKEN}"},"timeout_seconds":15}]'
```

Shell 环境变量不会在 JSON 字符串内部展开；部署时应把完整 JSON 作为 secret/config 值注入，并将示例中的占位值替换成真实凭证。不要把真实凭证提交到仓库。
- 请求入口可通过 HOSTPILOT_MAX_REQUEST_CHARS、HOSTPILOT_MAX_RESPONSE_CHARS、HOSTPILOT_REQUEST_TOKEN_BUDGET、HOSTPILOT_REQUEST_TOOL_CALL_BUDGET、HOSTPILOT_REQUEST_RETRIEVAL_CALL_BUDGET 和 HOSTPILOT_REQUEST_TIMEOUT_SECONDS 调整单请求限制；默认消息上限为 32,000 字符、响应上限为 100,000 字符，超时为 60 秒。模型提供方返回的 Token 用量会写入响应、请求预算和 Cost 事件。
- 新增配置项时记录类型、默认行为、敏感级别和校验规则。

## 变更记录

每项显著优化记录问题、基准条件、修改、验证结果及回退方式。测试结果记录实际命令、通过/失败数量、覆盖率（若采集）和已知缺口。
