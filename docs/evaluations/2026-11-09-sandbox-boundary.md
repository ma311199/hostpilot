# Stage F：不可信执行隔离边界与首轮实现

## 当前执行入口

| 能力 | 当前状态 | 隔离边界 |
| --- | --- | --- |
| LLM 生成或用户提供的脚本 | Python、JavaScript 和 POSIX Shell 分别由 `run_python_code`、`run_javascript_code`、`run_shell_script` 经用户审批后调用 Linux bubblewrap 沙箱 | 隔离进程无网络、无宿主工作区挂载、无继承环境变量；按运行时限制 CPU、地址空间、进程数、墙钟时间、源码与输出大小；系统运行时只读挂载；隔离启动失败时拒绝执行 |
| 内置文件工具 | Linux 上通过固定操作程序在 Bubblewrap 隔离进程执行 | read-only/workspace-write 只挂载所选工作区；danger-full-access 按次只挂载目标文件或目录；Bubblewrap 不可用时拒绝执行，Windows 暂不可用 |
| 本地 STDIO MCP Server | 由 MCP Client 启动独立子进程 | 子进程仍使用 HostPilot 的 OS 用户身份，可访问该身份允许的资源；密钥环境变量隔离不构成文件、网络或系统调用隔离 |
| 远程 Streamable HTTP MCP | 请求发送到远端 Server | HostPilot 无法在本机限制远端进程；依赖远端部署方的隔离与授权 |

会话级 `read-only` / `workspace-write` / `danger-full-access` 只控制内置文件工具，不改变 STDIO 子进程权限。代码工具使用独立的固定沙箱策略，和会话文件权限级别无关；当前仅支持 Linux，依赖 bubblewrap、prlimit、Linux 命名空间能力及对应的可挂载运行时。Node.js 运行时只读挂载其安装目录；Shell 可使用只读挂载的系统命令，但不能访问网络或写入宿主文件系统。工具目录分别通过无害运行时探针确认可用，实际调用重新建立隔离进程，失败时拒绝执行。

## 安全文案修订

README 与路线图已更新为当前实际能力。安全描述限定在 Linux 脚本工具的上述边界；不能据此声称所有不可信代码、插件或 MCP Server 都已隔离。

## 后续实现边界

Linux 脚本执行首轮后端已存在，不再把执行入口、首发平台和通用 capability 设计列为待决事项。新增其他平台或执行源时仍须单独设计后端及其 profile，不从现有实现推导跨平台等价性：

- Windows Python：原生 Windows 进程保持不可用；候选 AppContainer + Job Object 启动辅助程序见[后续评估](2026-11-09-windows-and-stdio-sandbox.md)。
- STDIO MCP：按每个 Server 设计文件路径、网络、凭证和资源 profile；不复用 Python 工具的零网络/零工作区策略。
- 各 OS 后端都要实测文件、网络、进程树、资源上限、超时取消和秘密传递；无法证明隔离时 fail closed。

容器或运行时名称本身不能证明隔离强度；验收要验证实际 namespace、挂载、网络和权限配置。

## 后续

Windows 原生候选与 STDIO MCP 独立隔离方向已在[后续评估](2026-11-09-windows-and-stdio-sandbox.md)和 [ADR-0030](../decisions/0030-cross-platform-code-and-mcp-sandbox-boundaries.md)中记录。当前 Linux 脚本沙箱不保护 MCP 子进程、进程内插件、远端 MCP 或宿主系统上由同一用户可访问的服务。
