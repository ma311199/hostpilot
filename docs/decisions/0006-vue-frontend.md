# ADR-0006：Vue 前端与本地进度看板

- 状态：已接受
- 日期：2026-10-31

## 背景

HostPilot 后端已有 FastAPI 健康检查、聊天和会话 API，但没有项目进度查询接口。需要建立独立前端，先呈现仓库当前阶段与后端可用状态。

## 决定

- 前端位于 frontend/，采用 Vue 3、TypeScript、Vite 和 pnpm。
- 首版交付后端进度看板和聊天界面；阶段数据维护在前端，并与 docs/roadmap.md 同步。聊天页面使用既有 HTTP 会话 API，并通过 WebSocket 提交聊天消息和接收回复。
- 后端在线状态通过 /api/v1/ws/status WebSocket 获取，浏览器使用原生 WebSocket。
- 使用浏览器 Fetch 和 CSS；首版不引入 Vue Router、Pinia、Axios、图表库或 UI 框架。
- 测试采用 Vitest、Vue Test Utils、jsdom；静态检查采用 ESLint。

## 后果

- 后端 API 与前端独立运行，Vite 开发源需加入 HOSTPILOT_WEB_ORIGINS；WebSocket 握手同样校验 Origin。
- Node 版本需满足 Vite 8 和 Vitest 5 支持矩阵（Node 22.12+ 或 24+）；依赖解析提交到 pnpm-lock.yaml。
- 看板上的阶段状态是仓库随版本发布的数据，不代表从运行中的后端动态查询。
- /api/v1/ws/chat 承载完整消息请求和回复；当前不提供逐 Token 流式传输。
