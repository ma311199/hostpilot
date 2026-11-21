# ADR-0018：版本化 Plan 组件插件接口

## 状态

已采纳

## 背景

Plan 的 Planner、Validator、任务执行器、Verifier 和 Replanner 需要能够分别替换，以便更换模型策略或接入项目专用实现，同时保留 Orchestrator 的 DAG 调度和 HostPilot 的统一治理。

## 决定

- Plan Orchestrator 保持核心实现；五种组件通过既有 Port 契约注入。
- 使用显式 `PlanComponentRegistry` 按组件角色和实现 ID 注册工厂；接口版本固定为 `1.0`，未知版本、重复注册、未知实现和缺少角色方法均在启动组装时拒绝。
- 内置实现注册在 `builtin.llm`、`builtin.runtime` 和 `builtin.governed` 名称下。环境配置分别用 `HOSTPILOT_PLAN_PLANNER_PLUGIN`、`HOSTPILOT_PLAN_VALIDATOR_PLUGIN`、`HOSTPILOT_PLAN_TASK_EXECUTOR_PLUGIN`、`HOSTPILOT_PLAN_VERIFIER_PLUGIN` 和 `HOSTPILOT_PLAN_REPLANNER_PLUGIN` 选择。
- 自定义工厂由应用启动代码注册，并通过 `create_app(..., plan_component_registry=...)` 或 `create_configured_chat_agent_runtime(..., plan_component_registry=...)` 注入。当前不支持从任意路径导入、下载或热加载插件。
- 插件应使用 HostPilot 注入的 SkillEngine 和 Tool Provider，并遵守预算、授权、审批、取消、补偿及审计契约。Python 插件能访问进程内依赖，注册表本身不构成安全隔离，因此只允许受信任代码。

## 影响

- 应用可以独立替换一个或多个 Plan 角色，其他角色继续使用内置实现。
- 实现 ID 是部署配置，不包含凭证；插件工厂是受信任的应用代码。
- 第三方插件的进程隔离、包签名、兼容升级及管理界面不在本次范围内。若允许不可信第三方代码，必须先增加隔离执行边界。

## 复核条件

增加第三方插件分发、热加载或 UI 管理前，复核 API 版本兼容策略、安装来源验证、权限声明和故障隔离方案。
