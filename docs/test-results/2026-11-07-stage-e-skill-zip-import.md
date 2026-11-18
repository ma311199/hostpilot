# Stage E：Skill ZIP 导入验证

- 日期：2026-11-07
- 范围：YAML / `SKILL.md` Skill ZIP 导入 API、安装校验和 Vue 上传入口
- 新增运行依赖：无；使用原始 `application/zip` 请求体，不依赖 multipart 上传

## 实现

- Skill 库页面新增 ZIP 文件选择和导入状态提示；成功后自动刷新目录。
- `POST /api/v1/skills/import` 将压缩文件限制为 4 MiB；读取请求流时即执行上限检查。
- ZIP 最多 100 个文件、200 个目录项，解压内容总量不超过 2 MiB；支持包文件位于根目录或单一顶层目录。
- 校验路径遍历、绝对路径、隐藏路径、重复路径、符号链接和加密 ZIP；使用现有注册表验证且必须恰好包含一个有效 Skill 包。UTF-8 的 `.py`、`.js`、`.sh` 作为文本资源允许导入，其他不支持的扩展名和二进制可执行文件仍会拒绝。
- 校验通过后复制到用户 Skill 根目录下的同盘临时目录，再原子重命名安装；重复 Skill 名称/ID 被拒绝，不覆盖已有文件。
- Skill 根目录来自 `HOSTPILOT_SKILLS_DIR` 或默认 `~/.hostpilot/skills`。现有 Runtime 在后续请求时重新发现 Skill，无需重启。

## 验证

- 后端全量测试：**241 passed**；涵盖 YAML 和 Markdown 包、单层目录 ZIP、路径遍历、符号链接、可执行文件、重复 Skill 与 API 导入。
- 前端 Vitest：**29 passed**。
- Ruff lint/格式检查、Mypy（72 个源文件）、ESLint、`vue-tsc` 类型检查和 Vite production build：通过。
- 覆盖率未采集。测试期间出现锁定 FastMCP 依赖的 Authlib 弃用警告，未影响结果。

## 当前边界

- 一次只导入一个 Skill；批量 ZIP 和覆盖更新不支持。
- 用户 Skill 仍按不可信输入处理；导入只安装经过 schema/资源校验的文件，不代表 Skill 指令或工具调用自动获得授权。
- 删除、启停和 Runtime 选择仍待实现。

## 2026-11-12 行为补充

- 用户 Skill ZIP 可以携带 `.py`、`.js`、`.sh` 脚本；上传、解压、注册表发现及加载都只保存/读取文本，不执行脚本。
- 调用脚本时应通过 `run_python_code`、`run_javascript_code` 或 `run_shell_script` 内置沙箱工具，并受现有工具治理策略约束。工具当前接收源码字符串，不会自动挂载 Skill 包目录，因此依赖同目录文件的脚本需要将依赖内容一并提供或改为自包含。
- 以上是后续功能修正说明；本节未记录新的测试执行结果。此前“非文本/可执行文件”描述中的脚本部分由本补充取代。
