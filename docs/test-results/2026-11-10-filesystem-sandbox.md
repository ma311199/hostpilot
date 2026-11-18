# 内置文件操作 OS 沙箱验证

## 实现

- 六个内置文件工具的内容读取、范围读取、目录列表、文件名搜索、写入和补偿恢复都由固定 Python 操作程序在 Linux Bubblewrap 隔离进程内执行。
- `read-only` 只读挂载所选工作区；`workspace-write` 读写挂载所选工作区；`danger-full-access` 读取只挂入目标文件或目录，写入只挂入目标父目录。
- 文件工具按工具名纳入 OS 沙箱可用清单。运行时探测失败时工具目录标为不可用，策略拒绝调用，执行路径不回退到进程内 `WorkspaceFiles`。
- Windows 暂无文件系统沙箱后端；文件工具不可用。STDIO MCP 与文件工具继续使用各自的隔离边界。

## 安全边界

- 沙箱内再次拒绝绝对路径、`..` 和符号链接，并确认解析后的目标仍在授权挂载内。
- danger-full-access 仅表示工具可按用户审批访问 OS 用户有权访问的路径；每次隔离进程只获得当前请求必要的挂载。
- 工具输入仍经过 ToolExecutor 参数校验、预算、策略和写操作审批。宿主进程只负责路径授权、构造挂载和传递操作参数，不在进程内读取或写入文件内容。

## 验证结果

- 定向测试 `tests/unit/sandbox/test_filesystem_sandbox.py` 与 `tests/unit/tools/test_mcp.py`：32 项通过。
- 完整后端测试：371 项通过。
- `mypy src/hostpilot`：94 个源文件无类型问题。
- Ruff 全量检查和 `git diff --check` 通过。
