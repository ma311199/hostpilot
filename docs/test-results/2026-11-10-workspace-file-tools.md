# 工作区文件发现与分段读取工具验证

## 实现

- `list_directory` 仅列直接子项，最多检查 5,000 个目录项并返回至多 100 项；只返回名称、类型和文件大小，不跟随符号链接。
- `search_files` 使用 basename glob 搜索，最大深度 8（默认 6）、最多检查 5,000 项并返回至多 100 个匹配结果；不跟随符号链接，不读取文件内容。
- `read_text_range` 按 UTF-8 字节偏移读取大文件，每次最多 64 KiB；范围切断 UTF-8 字符时返回明确错误。
- 三个工具都作为只读内置工具，沿用 ToolExecutor 的参数校验、预算、超时、审计链和会话路径级别，不要求逐次审批。
- 工具目录显示各工具的能力范围、运行位置、审批要求和无 OS 沙箱状态。

## 验证结果

- 后端定向测试：91 项通过；完整后端测试：363 项通过。
- 前端 Vitest：17 个文件、67 项通过。
- mypy：3 个修改后的源文件通过。
- Ruff、ESLint、Vue TypeScript 类型检查和 `git diff --check` 通过。

## 边界

文件名搜索不会搜索文件内容。文件内容、目录扫描和写入现由 Linux Bubblewrap 文件沙箱执行；`read-only`/`workspace-write` 只挂入所选工作区，`danger-full-access` 按次只挂入授权目标。Windows 没有文件沙箱后端时，六个文件工具均禁用，不会回退到 HostPilot 进程内执行。新增隔离验证见 [文件操作 OS 沙箱验证](2026-11-10-filesystem-sandbox.md)。
