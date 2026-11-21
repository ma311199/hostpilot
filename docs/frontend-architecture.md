# HostPilot 前端架构

- 状态：首版架构
- 日期：2026-10-31
- 范围：frontend/ 下的本地项目看板、聊天和 Harness 管理 Web
- 目标架构关联：[新架构文档](新架构文档.md)

## 目标与需求

前端用于查看后端开发阶段、已交付模块、待办事项和后端 API 健康状态，并通过既有会话与聊天 API 进行对话。遇到高影响工具调用时，前端展示操作摘要并收集明确批准或拒绝；前端不执行工具或写入文件。

## 模块划分

```text
frontend/
├── public/               静态图标和浏览器资源
├── src/
│   ├── api/              后端 HTTP 与 WebSocket 客户端
│   ├── data/             与 docs/roadmap.md 对齐的阶段展示数据
│   ├── App.vue           看板布局、主导航和后端状态 WebSocket
│   ├── ChatPanel.vue     工作区选择、会话管理、聊天和工具审批
│   ├── HarnessSettingsPanel.vue Harness 运行时配置
│   ├── McpConnectionsPanel.vue MCP Server 配置和连接状态
│   ├── ModelSettings.vue 模型配置
│   ├── ToolsPanel.vue    工具目录和能力状态
│   ├── SkillsPanel.vue   Skill 浏览、导入、启停和删除
│   ├── TracesPanel.vue   Trace 列表与 Span 详情
│   ├── AuditPanel.vue    Audit 筛选、分页和 CSV 导出
│   ├── main.ts           Vue 应用入口
│   └── style.css         全局主题和响应式样式
├── e2e/                  Playwright 浏览器验收与隔离服务 fixture
├── package.json          开发、检查、单测和 E2E 脚本
└── pnpm-lock.yaml        可复现的依赖解析
```

页面状态使用 Vue Composition API；阶段数据用类型化的本地模块维护；API 调用集中放在 api/。主导航连接项目看板、聊天、工具、MCP、Skill、Trace、Audit 和 Harness 配置页面。当前不增加 Vue Router 或 Pinia。

## 数据流与边界

- 阶段、模块和待办内容在前端随版本发布，编辑时同步更新 docs/roadmap.md 与 src/data/roadmap.ts。
- 后端状态通过 /api/v1/ws/status WebSocket 获取初始快照并维持连接；断线时显示离线，并按退避策略重连。
- WebSocket 使用 JSON 消息：服务端发送 status.snapshot，客户端每 15 秒发送 ping 并以 pong 确认连接；协议错误使用 error 消息返回。
- API 地址由 VITE_HOSTPILOT_API_BASE_URL 设置，默认 http://127.0.0.1:8000/api/v1。
- 聊天界面通过 POST /conversations 创建会话，GET /conversations 加载会话列表，GET /conversations/{id}/messages 加载历史；消息由 WebSocket 发送并接收回复。
- 聊天侧栏通过 POST `/api/v1/workspace/select` 请求本机原生文件夹选择器；后端校验读取和写入权限并持久化所选路径。只读工作区的写入操作会在 Tool Executor 参数校验阶段被拒绝；会话删除通过 DELETE `/api/v1/conversations/{id}` 完成，并提示用户确认。
- 聊天消息通过 /api/v1/ws/chat WebSocket 发送并接收完整回复；会话创建、列表和历史仍使用现有 HTTP API。该通道当前不做 Token 流式传输。
- “模型配置”面板将主 LLM、轻量 LLM、Embedding 和 Reranker 分为独立可展开卡片，每个角色可单独测试连接并保存配置。`POST /api/v1/model/configuration/test` 只发起一次最小模型请求，不保存草稿；成功/失败状态只展示经过脱敏的结果。模型名、Base URL 和流式选项保存在本地 SQLite。主 LLM 与轻量 LLM 的 API Key 只由本机后端进程内存持有，重启后需重新输入；Embedding 与 Reranker 的 Key 保存在本机 SQLite，重启后恢复。所有 Key 均不返回给前端或写入浏览器存储。流式选项只控制后端到模型服务的 SSE 请求；当前聊天页面仍在整条回复完成后显示结果。
- GET /api/v1/model/configuration 只返回 configured、saved、api_key_required、model_name、base_url 和 streaming，不返回凭证。GET /api/v1/routing-model/configuration 对各角色只返回 API Key 是否已配置。保存配置在运行时重建模型并立即替换；替换与聊天请求通过同一把运行时锁串行化。
- “Harness 配置”页面通过 GET/PUT `/api/v1/harness/configuration` 管理上下文消息数、记忆上下文字符数、请求/响应字符数、请求 Token/工具/知识检索预算和超时。完整配置保存在本地 SQLite；启动时恢复，在线更新与聊天请求通过同一把运行时锁串行化，设置立即应用于后续请求。当前未暴露尚无执行机制支撑的重试、Agent 循环、工具白/黑名单或沙箱控制。
- MCP 连接页通过 GET/PUT/DELETE /api/v1/mcp/servers 管理本地 STDIO Server 的 command/args/cwd 与远程 Server 的 URL、超时、启用状态和凭证名称；“添加 Server”或“编辑”才打开配置编辑器，参数表单与 JSON 导入模式位于编辑器内且互斥。JSON 模式支持导入标准 `mcpServers` 的无密钥连接配置；JSON 中存在 `env` 或 `headers` 值时拒绝导入，密钥需单独提交。密钥只在 PUT 请求提交并留在后端进程内存；重启后配置显示等待 API Key。环境变量配置的 Server 只读展示，连接失败以状态码显示。Server 用途描述需待后端 API 提供字段后再接入。
- “工具”页面通过 GET /api/v1/tools 读取后端注册表，只显示名称、用途、类型、配置状态和审批要求；不返回参数 schema、工具 handler、RAG_USER_ID 或 RAG_KEY。
- 模型未配置或请求失败时，页面展示后端返回的错误，不在前端模拟模型回复。
- RAG MCP 工具只向 Agent 暴露 query；本地配置完整 RAG 服务信息后由后端注入凭证并调用服务，未配置时返回“知识库暂未开启”。
- 前端只访问本地后端；模型密钥、会话正文和记忆文件不进入前端构建变量。

## 长列表与响应式浏览

工具、Skill 和 Trace 页面在当前后端返回的数据范围内进行前端搜索、筛选和每页 12 项分页；工具和 Skill 的长描述最多折叠显示三行，同时提供鼠标提示及可点击的展开按钮，键盘和触屏用户均可查看全文。会话列表固定在侧栏可滚动区域，支持按标题搜索和按今天、昨天、更早分组；每个会话的操作菜单可重命名、置顶或通过现有 API 删除。重命名和置顶偏好保存在浏览器本机存储，不改变后端会话元数据。侧栏滚动条隐藏但保留滚轮、触控和键盘滚动。聊天工作区高度只随视口调整，会话列表和消息各自在固定面板内滚动，不因会话数量增长而撑高页面。当前工作目录与读写状态单独展示。Harness 配置分组使用原生折叠面板，首组默认展开。管理页面共享约 1280px 的最大宽度，并在窄屏下保持单列布局。
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

聊天通道接收 `{"type":"chat.send","session_id":"...","message":"..."}`；完成后发送 `chat.response`，失败时发送 `chat.error`。当前一次返回完整回复，不逐 Token 推送。

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

## 当前浏览器 Web 交付状态

Stage E 的本地浏览器管理页面已覆盖项目看板、聊天、模型与 Harness 配置、工具目录、MCP Server 管理、Skill 库、Trace 和 Audit。页面通过本机 FastAPI 提供的 HTTP API 和 WebSocket 工作；阶段内容作为前端版本化数据维护，不另建进度 API。

MCP 连接页通过只读 `GET /api/v1/mcp/connections` 加载连接 ID、transport、生命周期状态、发现工具数和分类错误码；另通过 `/api/v1/mcp/servers` 管理持久化的本地 STDIO 与远程 HTTP Server 配置。配置编辑区默认显示参数表单，可切换到 JSON 导入视图，两个视图不会同时显示。密钥只提交至本地后端并在进程内保存，配置读取仅暴露密钥是否已提供；环境变量配置的 Server 只读展示。连接状态代表最近一次建立连接与工具发现结果，当前没有周期性健康探测。

`GET /api/v1/skills` 聚合用户目录与只读 bundled 目录中的 YAML 和 `SKILL.md` 包，只返回通过校验的 Skill ID、名称、说明、检测格式、当前及可选执行入口、启用状态、来源和无效包数量；不返回文件路径、资源或指令正文。`GET /api/v1/skills/detail?skill_id=...` 按目录 ID 返回通过校验的 YAML manifest、`SKILL.md` 和文本资源，不接受文件系统路径。`POST /api/v1/skills/import` 接收原始 ZIP 请求体，不依赖 multipart；限制压缩文件 4 MiB、解压后文件总量 2 MiB 和最多 100 个文件，校验一个包后才安装。拒绝路径穿越、符号链接、隐藏路径、二进制可执行文件、无效包和重复 Skill；UTF-8 的 `.py`、`.js`、`.sh` 可作为普通文本资源导入，不会在导入或加载时运行。需要执行脚本时，Agent 将源码交给对应的内置沙箱工具；当前工具接收源码字符串，不会把 Skill 目录挂载到脚本进程。`PATCH /api/v1/skills/state` 按 Skill ID 启停用户包；`PATCH /api/v1/skills/execution` 按 Skill ID 持久化兼容的执行入口；`DELETE /api/v1/skills?skill_id=...` 删除用户包。管理操作只修改用户目录内经校验的直接子目录，bundled 包不可编辑。双格式包可选择 YAML、Markdown 或 `both`，默认 YAML；`both` 的分类器每次请求只选择一个入口，Markdown 执行器只收到被选中的 Skill。执行偏好在 SQLite 持久化，并在运行时锁内即时应用。历史 `deepagents` 偏好会迁移为 `markdown`，旧 API 输入值也会被兼容转换。Harness 配置只管理运行时参数和凭证，不保存 Skill steps 或业务 Prompt。MCP 连接面板显示连接状态及动态发现的工具，不复制 MCP 工具定义。Trace 展示应遵守凭证和敏感参数脱敏要求。

前端形态确认为本地浏览器 Web，不打包为 Electron、Tauri 等桌面壳。Harness 运行参数配置、MCP/Skill 管理、Trace 和 Audit 页面均已交付。Trace 展示请求及 Skill、工具、审批等关联 Span；Audit 支持筛选、分页和 CSV 导出。当前尚未提供记忆编辑页面，也未提供模型回复的 WebSocket 流式传输。

Trace 页面从 `GET /api/v1/observability/traces` 读取最近请求，并从 `GET /api/v1/observability/traces/{trace_id}` 读取事件详情。SQLite 只保存固定白名单属性，最多保留 30 天或 100,000 个事件；日志查询不包含消息正文、工具参数或凭证。

增加流式传输、工具审批或记忆管理时，应先更新需求、后端契约和本架构文档。
