# Stage F：STDIO 凭证参数隔离验证

## 范围

MCP Server 的非敏感 `args` 会持久化并由配置 API 返回。为避免用户把秘密误填到这些参数中，后端保存接口和浏览器配置入口增加凭证形态检查；API Key 仍通过单独字段提交，在进程内存中使用。

## 实现与安全检查点

- 后端拒绝明显的凭证参数名称，如 `--api-key`、`--token`、password/secret/credential 类选项。
- 检查 header 赋值（例如 `Authorization: Bearer ...`）、变量赋值（例如 `MCP_ACCESS_TOKEN=...`）和 URL 查询参数。
- 后端 Pydantic 验证先于 SQLite 写入；验证错误只返回安全错误信息，不包含提交值。
- 启动时清理旧数据库中匹配到凭证形态的 args，并停用对应 Server；凭证值从该配置记录中移除。
- 参数配置表单和 `mcpServers` JSON 导入做同类检查，拒绝后不会调用保存 API。
- 合法的命令参数仍然跨重启保存；确需凭证的 STDIO Server 使用单独的 API Key 环境变量名和值。

## 验证

- API 定向测试：`.venv/bin/pytest -q tests/unit/api/test_app.py`，46 项通过。
- 后端全量测试：`.venv/bin/pytest -q tests`，293 项通过，0 项失败；有 1 条第三方 Authlib 弃用警告。
- 前端测试：`pnpm test`，13 个测试文件、52 项通过。
- 前端 lint 与构建：`pnpm lint`、`pnpm build`，均通过。
- Ruff 检查和格式检查通过；`.venv/bin/mypy src/hostpilot` 检查 80 个源文件通过。
- 验证 API Key option、Authorization header 和环境变量赋值不出现在响应或 SQLite 数据库中，并验证旧记录启动时清理且禁用。

## 限制与后续

检测基于参数名/赋值格式，无法识别伪装成普通参数值的任意秘密字符串。API Key 不得放在 `args` 的规则已在 UI 和文档中明确；需要其他秘密类型时应扩展单独的进程内凭证输入接口，而不是将凭证放进持久参数。
