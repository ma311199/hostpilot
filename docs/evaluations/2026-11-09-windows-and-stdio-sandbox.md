# Windows 代码与 STDIO MCP 沙箱评估

## 结论

- HostPilot 原生 Windows 进程暂不支持 Python、JavaScript 或 Shell 沙箱工具。保持工具不可用并拒绝执行；不通过普通 `subprocess` 或仅设置超时来替代隔离。
- WSL2 中运行的 HostPilot 可按 Linux 支持，但前提是应用实际运行于 Linux 用户空间，且 bubblewrap/prlimit 探针成功。原生 Windows 进程不转交 WSL 执行。
- STDIO MCP 子进程单独建模，不复用脚本沙箱。现有管理页已提示进程使用当前 OS 用户权限且不受沙箱保护。

## Windows 候选技术

| 方案 | 可以提供的边界 | 限制 | 评估 |
| --- | --- | --- | --- |
| Windows Job Object | CPU/内存等作业限制、并发/进程限制和进程树生命周期管理 | 不负责文件系统或网络访问控制；单独使用不是沙箱 | 作为资源限制与清理组件，不作为隔离后端 |
| AppContainer / Lowbox token | 以能力和 ACL 限制文件、注册表及网络访问 | 需要正确创建受限令牌、配置运行时访问 ACL 和安全启动流程；需原生 Windows 集成测试 | 原生 Windows 后端的首选候选，配合 Job Object 和独立辅助程序实现 |
| Windows Sandbox | 虚拟化隔离环境，可通过配置控制网络和映射目录 | 依赖 Windows 版本/功能、虚拟化和系统组件；启动成本、用户体验及结果回传需单独集成 | 可作为可选的强隔离执行器，不作为默认通用后端 |
| 普通 Windows 子进程或 Job Object | 子进程独立、可限制部分资源 | 仍继承可访问的用户资源，不能提供所需文件/网络默认拒绝边界 | 不满足安全要求，禁止作为降级路径 |

因此，原生 Windows 的候选实现应使用独立启动辅助程序：在恢复运行前创建受限进程令牌/隔离容器、设置只读运行时和私有临时目录、将进程加入 Job Object、清理环境变量，并以有界管道收集输出。默认拒绝网络和宿主文件访问。平台无法建立完整边界时，执行器必须返回不可用。

## STDIO MCP 边界

MCP Server 是用户配置的独立程序，不是任意 Python 代码片段。它可能需要读取指定文件夹、写入用户批准的文件或访问网络服务；统一套用“无网络、无宿主工作区”的 Python policy 会破坏这些功能，也无法表达最小必要访问范围。

当前 STDIO 生命周期保留现有边界：使用独立子进程、不经过 Shell、限制可执行程序、过滤父环境凭证，并在关闭时终止 Server；但 Server 与 HostPilot 共享 OS 用户权限。用户须只配置可信命令与包。上述措施减少命令注入和密钥继承风险，不构成 OS 沙箱。

未来按 Server 配置隔离 profile，至少要定义：

- 明确的只读/读写路径及路径撤销语义。
- 网络默认拒绝及可审计的目的地址 allowlist；DNS、重定向和本机回环同样纳入策略。
- 凭证仅经受控 IPC/命名环境变量传入，不暴露给其他 Server。
- CPU、内存、进程树、输出和启动/调用时限。
- 超时、取消、退出码未知时的进程树终止、审计和用户提示。
- 每个支持 OS 的真实子进程验收；不能建立 profile 时拒绝启动 Server。

## 参考资料

- Microsoft： [AppContainer isolation](https://learn.microsoft.com/en-us/windows/win32/secauthz/appcontainer-isolation)
- Microsoft： [Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)
- Microsoft： [Windows Sandbox overview](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/)
- Microsoft： [Windows Sandbox configuration](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)

本评估根据当前平台实现和上述 Windows 能力边界做设计筛选；没有在 Windows 主机上安装、运行或验证候选后端。
