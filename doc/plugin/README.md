# 插件开发指南 🌠

欢迎来到 Clipwright 插件开发指南。本文档按“先跑起来，再逐步扩展”的顺序组织；每页只解决
一类问题。开始之前，希望你有：

1. 基本的 Python 编程经验；
2. 基本的 Git / GitHub 使用经验；
3. 一个可运行的 Clipwright 后端（`scripts/start.ps1`，默认 `http://127.0.0.1:8080`）。

> 当前插件契约以 `clipwright/plugins/` 的实际代码为准；旧文档里的 `initialize()` 手写注册
> 只作为兼容期说明，新插件请使用 `contributions()`。

## 环境准备

插件放在后端仓库的 `plugins/<plugin_id>/`（开发期）或 `PluginData/plugins/<plugin_id>/`
（运行时安装）。最小目录：

```
plugins/my_plugin/
├── plugin.yaml     # 必需：清单 + 贡献声明
├── main.py         # 推荐入口（默认 <id>.main）
├── config.yaml     # 可选：配置默认值
└── ui.json         # 可选：声明式设置面板
```

`entry_point` 留空时加载器导入 `<plugin_id>.main`；入口必须是本插件包路径，且插件类必须
定义在本插件包内（不能从别的模块“借”一个 `BasePlugin` 子类）。

## 调试插件

1. 启动后端：`scripts/start.ps1`；
2. 修改插件代码后，在「管理控制台 → 插件」里点该插件的 **重载**，或调用
   `POST /api/plugin/load/{plugin_id}`；
3. 加载失败看 `GET /api/plugin/errors` 与「加载失败插件」列表；
4. 体检脚本：`python scripts/plugin_health_report.py`（声明/注册不一致、零贡献、旧轨写法）。

## 开发原则

- 功能要经过测试；不要因为一个异常把整个插件/宿主带崩；
- 持久化数据写 `PluginData/`（通过 workspace/artifact API），**不要**写插件自身目录，
  否则升级/重装会丢；
- 提交前跑 `ruff`；网络请求用异步库（`httpx` / `aiohttp`），不要用阻塞 `requests`；
- 想扩展现有插件能力时优先给上游提 PR，而不是复制一个插件；
- 借鉴其他项目的设计/代码时，在 README 标注来源并遵守原项目许可证。

> 文档组织参考 AstrBot 插件开发指南（“从这里开始 → 最小实例 → 配置/视图/发布”的渐进结构），内容按 Clipwright 当前实现重写。

## 文档导航

| 页面 | 内容 |
|---|---|
| [最小实例](simple.md) | 从零写一个可加载、可调用的工具插件 |
| [扩展点](contributions.md) | 24 类贡献、声明即授权、覆盖与冲突 |
| [插件配置](config.md) | `config.yaml`、`PluginData` 覆盖、secret、配置 API |
| [可视化视图](ui.md) | 15 个 UI 槽位、布局 JSON、`visibleWhen`、bundle |
| [模型与 Provider](providers.md) | LLM/TTS/STT/Image/Embedding/Rerank/Renderer/TextMotion |
| [子进程插件](subprocess.md) | `runtime: subprocess`、NDJSON v2、宿主 RPC、事件、错误码 |
| [存储与任务](storage.md) | workspace / artifact / materials / job 的用法与限制 |
| [发布与签名](publish.md) | 权限、manifest 签名、bundle 摘要、发布前检查 |
| [其他](other.md) | 生命周期、`/api/plugin/*`、旧文档迁移、排错 |
