# OpenAI 兼容模型运行时验证记录

- 日期：2026-10-30
- 范围：模型配置校验、SecretStr 密钥保护、默认 API 启动装配、DeepAgents / LangGraph 运行时创建
- 模型供应商：未指定；自动化测试替换模型构造器和 Agent runtime，没有调用外部服务

## 实现结果

- `HOSTPILOT_MODEL_NAME` 与 `HOSTPILOT_MODEL_API_KEY` 可启用聊天运行时；`HOSTPILOT_MODEL_BASE_URL` 用于 OpenAI 兼容服务的自定义地址。
- 模型密钥通过 Pydantic `SecretStr` 保存，未配置明文密钥时不会出现在 Settings 的 repr 中。
- API 生命周期在模型已配置时自动创建 `ChatOpenAI`、DeepAgents graph 和 LangGraph SQLite checkpointer；依赖组未安装或模型配置不完整时不会悄悄使用假模型。
- 每个模型运行时创建独立的 OpenAI 异步 HTTP 客户端，并在 API 生命周期结束前关闭，避免多个应用实例复用已关闭的 SDK 缓存客户端或在事件循环结束后才清理 transport。
- 未配置模型时聊天接口维持 503；注入 `runtime_factory` 仍用于测试和应用集成。
- `langchain-openai` 已添加到 `agents` 依赖组，`uv.lock` 已同步。

## 验证结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 完整质量检查 | `uv run --locked --group dev --group agents scripts/check_quality.sh` | 通过 |
| 格式 | Ruff format check | 54 个文件格式正确 |
| Lint | Ruff check | 通过 |
| 类型检查 | mypy `src/hostpilot` | 39 个源文件无错误 |
| 单元与集成测试 | pytest | 57 passed |
| 锁文件一致性 | `uv lock --check` | 解析 117 个包 |

## 真实模型联调

- 2026-10-31，用户使用讯飞星火 OpenAI 兼容接口运行 `scripts/verify_model_chat.py`，报告初次模型回复、进程重启后的续聊和会话隔离全部通过。
- 首轮联调曾发现 OpenAI SDK 默认异步 HTTP 客户端被全局缓存；第一个 TestClient 关闭后，第二个 runtime 得到已关闭客户端。现为每个 runtime 创建独立客户端，并在 lifespan 结束时显式关闭；修复后的真实 follow-up 已通过。

## 未验证范围

- 供应商速率限制、实际费用、并发隔离和错误响应映射尚未验证。
- 本次未采集覆盖率；不报告覆盖率数值。
