# ADR-0030：Linux 工具能力沙箱与 MCP 子进程边界

- 状态：已采纳
- 日期：2026-11-10（2026-11-10 修订）
- 相关：[ADR-0016](0016-local-stdio-mcp-servers.md)、[Windows 代码与 STDIO MCP 沙箱评估](../evaluations/2026-11-09-windows-and-stdio-sandbox.md)

## 背景

Linux 代码执行工具依赖 bubblewrap、prlimit 和可用的 Linux 命名空间。当前产品阶段只开发适用于 Linux 的 Agent；Linux Agent 完成后，再独立开发 Windows Agent。STDIO MCP Server 以本地命令启动，不同工具可能需要不同的文件、网络和运行时权限，不能直接套用代码沙箱的零网络、零宿主工作区策略。

## 决定

- 当前 Agent 与 OS 沙箱后端仅支持 Linux。工具目录必须确认 bubblewrap、prlimit、对应运行时和 Linux 命名空间隔离探针成功；不满足条件时拒绝执行，不回退到宿主运行时。
- 不为一个逻辑 STDIO MCP Server 设定覆盖所有工具的单一沙箱 profile。MCP Client 根据 HostPilot 为目标工具配置的能力 profile，从该 Server 的隔离进程池选择或启动受对应 Linux 沙箱约束的实例；不同 profile 的实例不得共享进程。并行调用可由各自符合 profile 的实例承载。
- 进程池直接启动标准 MCP Server 命令，不要求第三方 Server 实现 HostPilot 专用 Supervisor、Gateway 或 Worker 协议。为不同 profile 启动该 Server 的独立实例，并由 MCP Client 将工具调用路由至匹配实例。
- 工具能力由 HostPilot 的受信任配置确定，不直接采信 Server 自报信息或工具描述。Tool Registry / Policy Engine 在调用前授权稳定 tool ID，并据能力 profile 选择文件挂载、网络、凭证和资源限制；Server 进程的 OS 权限是其 profile 中工具能力的上限。
- Profile 是操作系统进程边界。同一 profile 下的多个工具共享该 profile 的 OS 权限上限；Tool Registry 仍限制实例可被调用的工具 ID。Profile 应按最小权限分组，避免把能力差异显著的工具放入同一组。
- profile 必须显式声明可读/写路径及工作区挂载方式、网络策略、凭证注入、CPU 配额、内存/Swap 上限、PID 上限、磁盘/临时存储上限、并发/池大小、启动与调用时限，以及取消、回收和故障清理策略。磁盘限制要说明是受限挂载、限额文件系统还是临时文件系统容量；未有操作系统机制支撑时不能声称有硬磁盘配额。不得把同一进程复用于权限不同的 profile；profile 无法落实或沙箱探针失败时 fail closed。
- profile 的资源数值不在本 ADR 中预设统一值。实现前按第三方 Server 的启动/峰值资源基线分级，验证 Linux cgroup/prlimit/namespace/文件系统能力，并记录各 profile 的 CPU、内存、PID、存储和并发预算；不能可靠施加的维度必须标为未支持并拒绝依赖该限制的 profile。
- STDIO Server 执行的是进程级 OS 权限。工具级能力路由只能在调用前限制 HostPilot 允许调用的工具；实际 OS 限制由承载调用的 Server 实例 profile 提供。因此，同一实例暴露的工具都处于该 profile 的 OS 能力上限内，不能声称获得独立于进程的内核隔离。
- Windows Agent 与原生 Windows 沙箱不属于当前阶段；Linux Agent 完成后再独立立项，届时重新评估平台后端，不将 Linux 的隔离保证外推到 Windows。

## 影响

- 当前支持范围为 Linux；其他平台不得展示可用的 Linux 沙箱执行能力，执行路径保持 fail closed。
- STDIO 配置和工具目录展示实际选中的能力 profile、沙箱状态及其边界；profile 未启用或不可用时不得暗中退回未隔离执行。
- MCP 进程池与沙箱实现必须提供系统级测试，覆盖 profile 分流、并发实例、文件、网络、进程树、资源上限、凭证隔离、超时取消、进程回收和失败关闭行为。

## 复核条件

Linux 工具能力 profile（含 CPU、内存、PID、文件存储和网络等限制）、进程池生命周期及 MCP Server 状态兼容行为经实现和系统级验收后复核。数值预算须在第三方 Server 基准测试后确定。Linux Agent 完成后，单独立项并评估 Windows Agent 和原生 Windows 沙箱。
