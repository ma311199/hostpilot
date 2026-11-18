# Linux 安装前置依赖与沙箱诊断验证

## 实现

- 新增 [Linux 安装指南](../linux-installation.md)，记录 Bubblewrap、util-linux/prlimit、Python/uv、Node.js、POSIX Shell 和 Linux 命名空间前置条件，并提供 Ubuntu/Debian、Fedora、Arch 安装命令。
- 沙箱预检会区分非 Linux 平台、缺少 Bubblewrap、缺少 prlimit、缺少对应语言运行时及命名空间探针失败，并返回可操作的安装或排查提示。
- 工具目录展示具体沙箱缺失原因；沙箱策略拒绝后，聊天错误提示用户查看工具页和 Linux 安装指南。
- 安装、排错文档已链接至项目 README 和文档导航。

## 验证结果

- 后端完整测试：373 项通过。
- 前端 Vitest：17 个测试文件、68 项通过。
- `mypy src/hostpilot`：94 个源文件无类型问题。
- Ruff、ESLint、Vue TypeScript 检查和 `git diff --check` 通过。

## 运行环境说明

当前开发环境中 `/usr/bin/bwrap` 与 `/usr/bin/prlimit` 均可用；文件、Python、JavaScript 和 Shell 隔离探针均成功。依赖缺失与探针失败的文案由单元测试覆盖。
