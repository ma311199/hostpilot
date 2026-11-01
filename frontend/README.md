# HostPilot 前端进度看板

## 本地运行

需要 Node.js 22.12+（或 Node 24+）和 pnpm。

```bash
pnpm install --frozen-lockfile
pnpm dev
```

Vite 默认使用 localhost:5173。后端需要将该来源加入 HOSTPILOT_WEB_ORIGINS。

```bash
export HOSTPILOT_WEB_ORIGINS='["http://localhost:5173"]'
uv run --locked --group agents python -m hostpilot.api.main
```

页面顶部的“模型配置”可配置 OpenAI 兼容模型，保存后立即生效。API Key 仅保存在后端进程内存中，重启后需重新配置；也可在启动后端时使用 HOSTPILOT_MODEL_NAME、HOSTPILOT_MODEL_API_KEY 和可选 HOSTPILOT_MODEL_BASE_URL 环境变量。

自定义状态 WebSocket 地址时，设置 VITE_HOSTPILOT_WS_URL；自定义聊天 WebSocket 地址时，设置 VITE_HOSTPILOT_CHAT_WS_URL。也可通过 VITE_HOSTPILOT_API_BASE_URL 配置 HTTP API 地址，聊天 WebSocket 默认从该地址推导。

## 检查

```bash
pnpm test
pnpm lint
pnpm build
```

项目架构、状态协议、依赖理由和开发规范见 ../docs/frontend-architecture.md 与 ../docs/frontend-development.md。
