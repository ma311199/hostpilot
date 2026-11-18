# Plan 组件插件注册与替换记录

## 实现

- 新增版本 `1.0` 的 Plan Component Registry，按 Planner、Validator、Task Executor、Verifier、Replanner 五个角色注册工厂。
- 默认实现 ID 为 `builtin.llm`、`builtin.runtime` 和 `builtin.governed`；每个角色可以由 `HOSTPILOT_PLAN_*_PLUGIN` 配置单独选择。
- 应用组装允许注入注册表；可从默认注册表派生并增加受信任的自定义实现。
- 启动时拒绝未知实现、重复注册、接口版本不匹配和缺少角色所需方法的实现。
- 自定义插件是进程内 Python 代码，不提供沙箱隔离；不支持任意模块路径加载或下载。

## 验证

- 定向测试：`tests/unit/skills/test_plan_plugins.py`、`tests/unit/config/test_settings.py`、`tests/unit/skills/test_model_plan.py`、`tests/unit/orchestration/test_runtime.py`、`tests/unit/api/test_app.py`：88 passed。
- 全量后端测试：326 passed，1 条第三方 Authlib 弃用警告。
- Ruff 检查通过；MyPy 检查全部 86 个后端源文件通过。

## 后续

- 插件包安装/升级、管理界面、故障隔离和第三方来源验证尚未实现；引入不可信插件前必须设计独立执行边界。
