# Stage E：浏览器管理跨页面验收

## 验收流程

Vitest 主导航集成流程验证用户能够：

1. 打开 Harness 配置、修改上下文历史数并提交完整配置；
2. 进入 MCP 连接页并切换到 JSON 配置，确认参数表单与 JSON 表单互斥；
3. 打开 Skill 库和 Trace 页面，正确呈现各自的数据状态；
4. 返回 Harness 配置页，重新读取已保存的值。

测试使用 Vitest、Vue Test Utils 和模拟 API，覆盖真实 Vue 页面组件和导航生命周期，但不启动浏览器或 FastAPI 服务，因此属于跨页面 UI 集成验收，不作为真实浏览器 E2E 结果。

## 验证结果

- 前端全量：13 个测试文件、51 项通过。
- ESLint 与 TypeScript 类型检查通过。
- Playwright 尚未加入项目：`pnpm add -D @playwright/test` 在 npm registry 元数据请求时多次超时，未能下载依赖；未修改 package manifest 或 lockfile。

## 后续

Vitest 模拟 API 流程之外，Playwright 真实浏览器和 FastAPI 联动已单独接入；当前覆盖项与结果见 [Playwright E2E 验证](2026-11-08-playwright-browser-api-e2e.md)。聊天工具审批和真实桌面目录对话框仍需加入浏览器/本机验收。
