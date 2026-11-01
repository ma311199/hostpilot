# 内置文件工具与 WebSocket 人工审批验证记录

- 日期：2026-10-31
- 范围：命令 Router、工作区文件工具、MCP 工具调用审批、聊天 WebSocket 审批协议及前端审批弹窗

## 实现结果

- 新增 `/read <path>` 与 `/write <path>` 命令；写入命令在路径后换行提供文本内容，缺少必要参数时返回澄清消息。
- `read_text_file` 和 `write_text_file` 限定在后端启动工作目录内，只接受 UTF-8 文本并限制文件大小；绝对路径、路径穿越、符号链接和不存在的父目录会被拒绝。
- `write_text_file` 标记为高影响工具。策略授权后，WebSocket 会发送不含文件正文的确认摘要；用户批准后才继续执行。拒绝、取消、等待超时或连接断开都会阻止执行。
- HTTP 聊天没有交互式确认通道，因此高影响工具在该入口 fail-closed。
- RAG MCP 工具仍作为占位工具，调用时返回“知识库暂未开启”。

## 验证结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 全量后端测试 | `.venv/bin/pytest -q` | 114 passed |
| Python lint | `.venv/bin/ruff check src/hostpilot tests` | 通过 |
| Python 格式检查 | `.venv/bin/ruff format --check src/hostpilot tests` | 72 个文件格式正确 |
| 前端 lint | `pnpm lint` | 通过 |
| 前端类型检查与生产构建 | `pnpm build` | 通过 |
| 覆盖率 | 未采集 | 不报告覆盖率百分比 |

测试期间 FastMCP 依赖输出一条 Authlib `authlib.jose` 弃用警告；不影响测试结果。

## 手动试用

从项目根目录启动后端后，在聊天页面输入：

- `/read README.md`：读取项目根目录下的 README。
- `/write approval-demo.txt`，再换行输入文本：弹出确认对话框；批准后在项目根目录写入文件。

文件工具根目录取决于启动后端时的当前工作目录。示例只建议用于本地开发环境。
