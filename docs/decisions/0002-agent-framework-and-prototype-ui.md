# ADR-0002：Agent 框架组合与原型界面

- 状态：已接受
- 日期：2026-10-29

## 背景

HostPilot 是面向个人客户端的 Agent 项目，需要确定模型/工具接口、主流程编排、Agent 能力运行时，以及开发期快速演示方式。

## 决定

- 使用 LangChain、LangGraph 与 DeepAgents 组合。
- LangChain 用于模型和工具集成边界。
- LangGraph 管理 HostPilot 的顶层请求状态和流程控制。
- DeepAgents 提供 Skill 等 Agent 能力，通过受控适配器嵌入 LangGraph 流程；避免维护两个彼此独立且无约束的顶层执行循环。
- 使用 Gradio 构建开发期间的快速原型与交互演示。Gradio 只属于演示接口，不进入领域契约，不据此决定生产 UI 或部署形态。
- 不使用 CrewAI，且不将其声明为项目依赖。

## 后果

- 三个框架的具体 API 与版本兼容性需通过小型集成验证确认；核心业务 DTO 不依赖其专有类型。
- LangChain、LangGraph、DeepAgents 与 Gradio 使用独立依赖组，基础安装不下载可选框架。
- 原型 UI 调用 HostPilot 应用服务，不直接实现策略、预算或工具执行逻辑。
