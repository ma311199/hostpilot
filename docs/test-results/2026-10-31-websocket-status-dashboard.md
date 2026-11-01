# WebSocket 状态通道与前端看板验证记录

- 日期：2026-10-31
- 范围：FastAPI 状态 WebSocket、Vue 项目进度看板、依赖锁定和构建

## 实现结果

- FastAPI 新增 /api/v1/ws/status：连接后发送 status.snapshot；客户端 ping 获得 request_id 对应的 pong。
- 服务端拒绝未允许的 Origin；消息大小上限为 1 KiB；不符合协议的消息返回 error。
- Vue 看板呈现 Phase 0 至 Phase 3、Phase 1 交付与待办，并通过原生 WebSocket 显示 API 在线状态和自动重连。
- 阶段数据随前端版本发布，编辑时需同步 docs/roadmap.md 和 frontend/src/data/roadmap.ts。
- 前端依赖由 pnpm-lock.yaml 固定。未添加专门的 WebSocket 客户端、HTTP 客户端、Vue Router、Pinia 或 UI 组件库；浏览器原生 WebSocket 满足首版需求。

## 验证结果

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 前端依赖锁 | pnpm install --frozen-lockfile | 通过 |
| 前端单元与组件测试 | pnpm test | 2 个测试文件，5 passed |
| 前端 lint | pnpm lint | 通过 |
| 前端类型检查与生产构建 | pnpm build | vue-tsc 通过，Vite 构建成功 |
| 后端完整质量检查 | bash scripts/check_quality.sh | 91 passed；Ruff、mypy 和 uv lock 检查通过 |

未采集覆盖率，因此不报告覆盖率百分比。

## 首版边界

- WebSocket 当前只传输 API 健康快照和心跳；项目阶段信息是前端静态数据。
- 聊天流式 WebSocket 不属于此首版；若增加，需要单独定义会话消息、流式事件、取消和错误消息契约。
