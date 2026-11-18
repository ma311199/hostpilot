# Linux Python、JavaScript 与 Shell 沙箱验证

## 实现

- `run_python_code`、`run_javascript_code` 和 `run_shell_script` 是三个独立的高影响工具，调用前均需用户审批。
- 三者通过 Linux bubblewrap 建立 namespace，清除继承环境、禁用网络且不挂载宿主工作区；系统运行时以只读方式提供。不同运行时各自运行无害探针，目录状态逐项报告，执行失败时不回退到宿主进程。
- 所有工具限制源码 64 KiB、输出 16 KiB、CPU 2 秒、墙钟 5 秒。Python 限制地址空间 256 MiB 和单进程；Node.js 限制 V8 堆 256 MiB 和地址空间 2 GiB；Shell 限制地址空间 256 MiB 和最多 16 个进程，以支持受限系统命令。
- JavaScript 仅提供 Node.js 内置运行时模块，Node 可执行文件所在目录以只读方式挂载。Shell 可运行只读挂载的 `/usr` 系统命令。

## 验证结果

- 后端完整测试：366 项通过。
- 前端完整测试：17 个文件、67 项通过。
- mypy：93 个源文件通过；Ruff、ESLint、Vue TypeScript 类型检查和 `git diff --check` 通过。
- Linux 集成测试验证 JavaScript 可执行且不能访问宿主文件或网络；Shell 可执行只读系统命令、不能看到宿主文件或写入宿主文件系统；MCP Provider 测试验证 JavaScript 与 Shell 经审批后执行。

## 边界

三个工具均仅在对应运行时和 Linux namespace 探针通过时可用。Node 和系统工具运行时自身以只读形式暴露给脚本。脚本沙箱不提供网络或宿主文件访问，也不隔离 STDIO MCP 子进程、进程内插件或远程 MCP；它不等同于虚拟机隔离。
