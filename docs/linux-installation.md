# Linux 安装与沙箱前置依赖

本文面向在 Linux 上安装和运行 HostPilot 的用户。沙箱依赖是操作系统级程序，不会由 `uv sync` 安装。

## 1. 安装系统前置依赖

HostPilot 的文件操作以及 Python、JavaScript、Shell 隔离运行都需要 Bubblewrap 和 `prlimit`。`prlimit` 由 util-linux 提供。

### Ubuntu / Debian

```bash
sudo apt update
sudo apt install bubblewrap util-linux
```

### Fedora

```bash
sudo dnf install bubblewrap util-linux
```

### Arch Linux

```bash
sudo pacman -S bubblewrap util-linux
```

确认程序可被当前应用进程找到：

```bash
command -v bwrap
command -v prlimit
```

Bubblewrap 还需要 Linux 内核和当前运行环境允许创建用户等命名空间。容器、受限主机或关闭了非特权用户命名空间的系统可能安装了软件包却仍无法启动沙箱。HostPilot 会执行真实隔离探针；探针失败时禁用相关工具，不回退到宿主进程运行。

## 2. 安装应用运行时

- Python 3.11 或更高版本及 `uv` 是后端运行依赖。
- Node.js 用于 `run_javascript_code` 沙箱，以及使用 `npx` 的 STDIO MCP Server。Node.js 缺失时 JavaScript 沙箱会禁用，其他可用沙箱不受影响。
- Shell 沙箱使用系统 POSIX `/bin/sh`。

在仓库根目录安装应用依赖：

```bash
uv sync --locked --python 3.11 --group agents
```

开发和测试环境再安装 `dev` 依赖组：

```bash
uv sync --locked --python 3.11 --group dev --group agents
```

Node.js 请使用当前 Linux 发行版支持的 Node.js LTS 安装方式。安装后确认 `node` 在启动 HostPilot 的同一用户和环境的 `PATH` 中：

```bash
node --version
command -v node
```

如果只使用文件、Python 或 Shell 工具，不需要为了 JavaScript 沙箱单独安装 Node.js；工具目录会分别显示各个沙箱的可用状态。

## 3. 验证 HostPilot 沙箱

确保以上命令在启动 HostPilot 的同一个终端可见，然后重启后端并打开工具目录页面。页面会分别报告文件、Python、JavaScript 和 Shell 沙箱状态。安装 `bwrap`、`prlimit` 或 Node.js 后需要重启 HostPilot，以重新执行运行时探针。

常见提示：

| 页面提示 | 处理方式 |
| --- | --- |
| 缺少 Bubblewrap | 按发行版命令安装 `bubblewrap`，然后重启 HostPilot |
| 缺少 `prlimit` | 安装 `util-linux`，然后重启 HostPilot |
| 未找到 Node.js 沙箱运行时 | 安装 Node.js，并确认启动 HostPilot 的 `PATH` 中可找到 `node` |
| 命名空间探针失败 | 检查内核用户命名空间设置、容器安全策略或主机对 Bubblewrap 的限制；安装软件包本身不能解除这些系统限制 |
| 当前平台不支持 | 代码和文件 Bubblewrap 沙箱目前只支持 Linux；原生 Windows/macOS 后端尚未实现 |

## 4. 安装故障排查

若页面仍显示依赖不可用：

1. 在 HostPilot 实际运行的用户账户下执行 `command -v bwrap`、`command -v prlimit`；JavaScript 沙箱另执行 `command -v node`。
2. 若命令只在交互式 Shell 中可见，检查 systemd、容器或桌面启动器提供给 HostPilot 的 `PATH`。
3. 若程序均存在但探针失败，查看 Linux 用户命名空间和容器的 seccomp/AppArmor 等策略是否允许 Bubblewrap；不要通过切换为宿主进程执行来绕过失败。
4. 安装或修改系统权限后重启 HostPilot，工具目录会重新探测。

STDIO MCP Server 是独立子进程边界，目前不由上述 Bubblewrap 文件/代码沙箱自动包裹。配置的 STDIO 命令及其所需程序必须单独安装；子进程仍使用 HostPilot 的系统用户权限。
