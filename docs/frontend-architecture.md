# HostPilot 前端架构

- 状态：首版架构
- 日期：2026-10-31
- 范围：frontend/ 下的本地项目看板与聊天界面

## 目标与需求

前端用于查看后端开发阶段、已交付模块、待办事项和后端 API 健康状态，并通过既有会话与聊天 API 进行对话。遇到高影响工具调用时，前端展示操作摘要并收集明确批准或拒绝；前端不执行工具或写入文件。

## 模块划分

```text
frontend/
├── public/             静态图标和浏览器资源
├── src/
│   ├── api/            后端 API 调用
│   │   ├── chat.ts     会话列表、历史和消息提交
│   │   ├── chatSocket.ts 聊天 WebSocket 客户端
│   │   ├── model.ts    模型配置状态与提交
│   │   └── tools.ts    后端注册工具目录
│   ├── data/           与 docs/roadmap.md 对齐的阶段展示数据
│   ├── App.vue         看板布局、导航和状态轮询
│   ├── ChatPanel.vue   会话列表、历史消息和聊天输入
│   ├── ModelSettings.vue 模型配置面板
│   ├── ToolsPanel.vue 工具目录和状态
│   ├── main.ts         Vue 应用入口
│   └── style.css       全局主题和响应式样式
├── package.json        脚本和直接依赖
└── pnpm-lock.yaml      可复现的依赖解析
```

页面状态使用 Vue Composition API；阶段数据用类型化的本地模块维护；API 调用集中放在 api/。导航在项目路线看板、聊天界面和工具目录之间切换，当前不增加 Vue Router 或 Pinia。

## 数据流与边界

- 阶段、模块和待办内容在前端随版本发布，编辑时同步更新 docs/roadmap.md 与 src/data/roadmap.ts。
- 后端状态通过 /api/v1/ws/status WebSocket 获取初始快照并维持连接；断线时显示离线，并按退避策略重连。
- WebSocket 使用 JSON 消息：服务端发送 status.snapshot，客户端每 15 秒发送 ping 并以 pong 确认连接；协议错误使用 error 消息返回。
- API 地址由 VITE_HOSTPILOT_API_BASE_URL 设置，默认 http://127.0.0.1:8000/api/v1。
- 聊天界面通过 POST /conversations 创建会话，GET /conversations 加载会话列表，GET /conversations/{id}/messages 加载历史；消息由 WebSocket 发送并接收回复。
- 聊天消息通过 /api/v1/ws/chat WebSocket 发送并接收完整回复；会话创建、列表和历史仍使用现有 HTTP API。该通道当前不做 Token 流式传输。
- 顶部“模型配置”面板通过 PUT /api/v1/model/configuration 配置 OpenAI 兼容模型；API Key 只发送到本机后端并在当前进程内存中持有，不返回给前端或写入浏览器存储。后端重启后需重新配置，或改用环境变量启动配置。
- GET /api/v1/model/configuration 只返回 configured、model_name 和 base_url，不返回凭证。保存配置在运行时重建模型并立即替换；替换与聊天请求通过同一把运行时锁串行化。
- “工具”页面通过 GET /api/v1/tools 读取后端注册表，只显示名称、用途、类型、配置状态和审批要求；不返回参数 schema、工具 handler、RAG_USER_ID 或 RAG_KEY。
- 模型未配置或请求失败时，页面展示后端返回的错误，不在前端模拟模型回复。
- RAG MCP 工具只向 Agent 暴露 query；本地配置完整 RAG 服务信息后由后端注入凭证并调用服务，未配置时返回“知识库暂未开启”。
- 前端只访问本地后端；模型密钥、会话正文和记忆文件不进入前端构建变量。
- 后端 CORS 来源通过 HOSTPILOT_WEB_ORIGINS 配置，例如 ["http://localhost:5173"]。
- 服务端校验 WebSocket Origin；拒绝不在同源或配置列表中的浏览器来源。

## WebSocket 协议

状态连接地址为 ws://127.0.0.1:8000/api/v1/ws/status。客户端可通过 VITE_HOSTPILOT_WS_URL 覆盖该地址。聊天连接为 ws://127.0.0.1:8000/api/v1/ws/chat，可通过 VITE_HOSTPILOT_CHAT_WS_URL 覆盖；未配置时由 API base URL 推导。

连接建立后服务端立即发送：

```json
{"type":"status.snapshot","data":{"api":"ok","protocol_version":1}}
```

客户端每 15 秒发送一次心跳：

```json
{"type":"ping","request_id":"dashboard-1"}
```

服务端回传同一 request_id 的 pong。服务端对超过 1 KiB 或不符合消息结构的数据返回 error；不可信 Origin 在握手时以关闭码 1008 拒绝。此通道只报告 API 健康状态，项目阶段数据仍以版本化的前端路线数据为准。

聊天通道接收 `{"type":"chat.send","conversation_id":"...","message":"..."}`；完成后发送 `chat.response`，失败时发送 `chat.error`。当前一次返回完整回复，不逐 Token 推送。

高影响工具调用暂停期间，服务端发送不含工具原始参数或文件正文的摘要：

```json
{
  "type": "tool.confirmation.request",
  "data": {
    "request_id": "confirmation-1",
    "tool_name": "write_text_file",
    "summary": "写入 approval-demo.txt（15 字节）"
  }
}
```

用户通过页面批准或拒绝后，客户端回复匹配 request_id 的决定；取消也可由客户端发送：

```json
{
  "type": "tool.confirmation.decision",
  "request_id": "confirmation-1",
  "decision": "approved"
}
```

`decision` 支持 `approved`、`rejected` 和 `cancelled`。服务端最多等待 60 秒；等待超时或 WebSocket 断开时工具调用不会执行。该交互由聊天 WebSocket 提供，HTTP 聊天入口对高影响工具 fail-closed。

## 当前边界

- 提供工具目录和高影响工具审批界面；暂不提供记忆编辑界面。
- 不提供模型回复的 WebSocket 流式传输。
- 不新增后端项目进度 API；阶段数据以仓库路线文档为准。
- 不引入图表、UI 组件、HTTP 客户端或路由依赖。原生 Fetch 和 CSS 足以满足当前页面。

增加流式传输、工具审批或记忆管理时，应先更新需求、后端契约和本架构文档。
