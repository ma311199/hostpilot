# 推理模型连接测试误报排查

- 日期：2026-11-21
- 范围：模型配置页的 LLM / 轻量 LLM 连接测试；未调用用户提供的 API，也未读取或保存用户密钥。

## 现象与代码证据

用户报告同一 OpenAI-compatible Base URL 和相同 curl 结构下，`qwen3.8-flash`、`glm-5.3` 都能返回结果，但在 HostPilot 切换模型后连接测试失败。

应用连接探针与 curl 请求并不相同。此前探针额外固定 `temperature=0`、`max_tokens=8`、15 秒超时；curl 没有这些限制，并显式包含服务商扩展 `enable_thinking=true`。推理模型可能在八个输出 Token 内没有产生可见答复，或超过 15 秒，因此连接探针存在误报路径。此根因是从请求代码确认的风险点；由于当前接口此前隐藏了 provider 错误类别，不能据此断定用户这次失败一定由该路径触发。

## 修复

- 移除连接探针强制的 temperature，避免额外限制服务商默认行为。
- 将最大输出提高至 128 Token，超时提高至 45 秒，让带推理过程的模型有机会返回最终文本。
- 对 `ModelProviderError` 返回脱敏错误类别及 HTTP 状态，并显示相应中文说明；不返回服务商错误正文、API Key 或 provider code。
- 当前通用模型配置没有 `enable_thinking` 字段，HostPilot 仍不会自动发送该服务商扩展。若模型在新的探针限制下仍返回 `invalid_request`，应据错误类别评估是否需要可配置的 provider-specific extra body 参数，而不是对所有 OpenAI-compatible 模型硬编码该扩展。

## 自动化验证

- 修复前新增的两个测试按预期失败：分别观察到旧探针参数 `temperature=0`、`max_output_tokens=8`、`timeout_seconds=15`，以及错误类别只返回 `ModelProviderError`。
- 修复后连接探针参数与错误类别定向测试通过。
- 全量 pytest：**568 passed**，1 条既有 Authlib 弃用警告。
- Ruff lint/format、123 个源码文件 mypy、`git diff --check` 通过。
- 尚未连接真实服务复测，因此不把自动化验证等同于 `qwen3.8-flash` 或 `glm-5.3` 在线连接已通过。

## 用户复测

用户反馈应用连接测试现已正常。该结果是在探针放宽 Token/超时限制并加入安全错误分类后由用户实测确认；本记录不保存密钥或服务商响应正文。
