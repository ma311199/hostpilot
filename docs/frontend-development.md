# 前端开发指南

## 技术栈与依赖

- Node.js 22.12+ 或 24+；Vite 8 与 Vitest 5 的版本范围都要满足。
- Vue 3 + TypeScript + Vite，使用 pnpm 和 pnpm-lock.yaml 固定解析结果。
- 后端通信使用浏览器原生 WebSocket，不增加 Socket.IO 或 WebSocket 客户端依赖。
- 页面测试采用 Vitest、Vue Test Utils 和 jsdom。
- 使用 ESLint 与 Vue / TypeScript 规则检查代码。
- 单页看板暂不引入 Vue Router、Pinia、Axios、图表库或 UI 组件库；数据请求使用浏览器 Fetch，样式由项目 CSS 实现。

| 依赖 | 类型 | 用途 |
| --- | --- | --- |
| Vue 3 | 运行时 | 单页进度看板与响应状态 |
| Vite 8、@vitejs/plugin-vue | 开发/构建 | 本地服务、Vue SFC 编译和生产打包 |
| TypeScript 6、vue-tsc、@vue/tsconfig | 开发/构建 | 类型检查与 Vue 类型支持 |
| Vitest 5、Vue Test Utils、jsdom | 测试 | API 协议和 Vue 组件测试 |
| ESLint 10、eslint-plugin-vue、typescript-eslint、globals | 质量检查 | JavaScript、TypeScript、Vue 模板检查及浏览器全局定义 |

浏览器原生 WebSocket、Fetch 和 CSS 已满足首版需求，未引入额外通信、路由、状态管理、图表或 UI 依赖。直接依赖、开发依赖和锁定版本以 frontend/package.json 和 frontend/pnpm-lock.yaml 为准。修改依赖范围后运行 pnpm install 并审查锁文件。

## 开发命令

在 frontend/ 目录运行：

```bash
pnpm install --frozen-lockfile
pnpm dev
pnpm test
pnpm lint
pnpm build
```

开发服务器默认监听 Vite 端口 5173。启动后端前，将 HOSTPILOT_WEB_ORIGINS 设置为 JSON 数组 `['http://localhost:5173']`。状态 WebSocket 地址可通过 VITE_HOSTPILOT_WS_URL 覆盖；聊天 WebSocket 地址可通过 VITE_HOSTPILOT_CHAT_WS_URL 覆盖，默认值为 ws://127.0.0.1:8000/api/v1/ws/chat。

## 代码约定

- Vue 组件使用 <script setup lang="ts"> 和 Composition API。
- 页面、API 客户端、阶段数据分别放在 App、src/api/ 和 src/data/。
- 共享 API 数据定义 TypeScript 类型；API 失败显示明确状态，不伪装为成功。
- 阶段数据更新时同时检查 docs/roadmap.md，避免看板与项目文档不一致。
- 不在 VITE_* 变量、源码或测试夹具中存放密钥或个人会话内容。

## 测试策略

- Vitest 覆盖健康检查响应、非成功状态和网络错误。
- 构建命令包含 vue-tsc 类型检查。
- 交互扩展后，为加载、错误、空状态和关键审批拒绝路径补充组件测试。
- 测试数据使用本地替身，不访问真实后端或模型。
