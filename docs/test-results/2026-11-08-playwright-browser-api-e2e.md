# Playwright 浏览器与 API 联动 E2E

## 范围

- 使用 Playwright Test 1.65.0 和真实 Chromium Headless Shell 执行浏览器测试。
- Playwright 独立启动 FastAPI（127.0.0.1:8011）和 Vite（127.0.0.1:5174），不复用已有服务。
- 后端数据、记忆和 Skill 目录使用每次运行新建的 `/tmp/hostpilot-e2e-*` 临时目录；全局清理钩子在测试后删除该目录。
- 页面验证覆盖状态 WebSocket 在线、Harness Token 预算写入后刷新恢复、MCP 参数/JSON 模式互斥，以及 Skill、Trace、Audit 导航。
- 第二条流程通过页面导入 Streamable HTTP MCP JSON，连接隔离的本地 FastMCP 测试 Server；刷新后验证配置恢复、连接成功并发现 1 个工具。
- Trace 流程由隔离 API 的 `SQLiteTraceStore` 测试工厂写入确定性事件，浏览器从真实 Trace 查询 API 打开详情并验证敏感属性未展示。
- Skill 流程使用复制到临时目录的 YAML Skill 包，在页面停用、刷新验证状态、重新启用；删除操作分别验证取消与确认。
- Workspace 流程通过注入的原生选择器边界返回可写临时目录，验证权限检查、页面状态和重载后的配置恢复，最后恢复测试进程启动目录。
- 会话删除流程在浏览器中创建两个会话，分别验证取消删除、删除当前会话后切换到剩余会话，并通过 API 确认被删除会话不可再读。
- Sandbox 流程验证默认只读、取消升级、逐级确认升级、刷新后的 SQLite 状态，以及 API 拒绝降级。
- 独立审批流程使用确定性 Agent Runtime，通过真实 Chat WebSocket 和 `MCPToolProvider` 调用 `ToolExecutor`；批准流程在隔离工作区创建文件，拒绝和取消流程验证目标文件未创建。

## 结果

- Playwright：7 项通过，真实 Chromium 和本地 FastAPI/Vite/FastMCP 联动成功。
- Playwright 审批专用配置：1 项通过，覆盖工作区可写升级、真实工具执行、批准后的文件写入，以及拒绝和取消后的无副作用结果。
- 前端 Vitest：17 个文件、66 项通过。
- 前端 TypeScript 检查及 Vite 生产构建通过。
- ESLint 通过。
- 后端 pytest：351 项通过；Authlib 产生 1 条第三方 `authlib.jose` 弃用警告。
- `git diff --check` 通过。

初次运行时 Chromium 缺少 `libasound2`、`libnspr4`、`libnss3`；临时解包这些 Ubuntu 软件包后完成首轮验证。随后用户安装了 Playwright 系统依赖，并以不设置 `LD_LIBRARY_PATH` 的标准命令复验通过。开发机和 CI 需预装 Playwright Chromium 所需系统依赖。

## 运行

在 `frontend` 目录安装 Chromium：

```sh
PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright \
PLAYWRIGHT_BROWSERS_PATH=/tmp/hostpilot-playwright-browsers \
pnpm exec playwright install chromium
```

系统已安装 Chromium 运行库后，运行：

```sh
pnpm test:e2e
```

## 后续覆盖

审批 E2E 使用确定性 Agent Runtime 控制工具调用顺序；工具仍经真实 `MCPToolProvider`、`ToolExecutor`、策略审批和隔离工作区文件处理器执行。Workspace 测试使用注入的选择器返回值，不会打开真实桌面文件对话框；该原生对话框仍需本机手动验收。真实模型或外部 MCP 流程应通过可控假服务完成。
