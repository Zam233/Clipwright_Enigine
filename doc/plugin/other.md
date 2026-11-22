# 其他：生命周期、API 与迁移

## 1. 生命周期

1. **discover**：扫描 `plugins/*`（或 `PluginData/plugins/*`）中有 `plugin.yaml` 的目录；
2. **load**：严格解析 manifest → 签名/权限校验 → 依赖解析 → 合并配置 → 导入入口模块 → 实例化；
3. **initialize()**：宿主调用；新契约下先跑 `setup()` 副作用，再由门面注册 `contributions()`；
4. **运行**：工具/技能/Agent/provider/UI 槽位按 kind 被消费；
5. **disable/unload**：调 `shutdown()` 释放外部资源，门面按 owner 回收贡献；
6. **配置变更**：写 `PluginData/plugins/<id>/config.yaml`，必要时重载。

加载器的硬规则：

- `entry_point` 必须是 `<id>` 或 `<id>.*`；默认 `<id>.main`；
- 只实例化本插件包内、非抽象的 `BasePlugin` 子类；
- manifest 的 `(kind, id)` 是注册白名单；未声明的注册会被拒绝；
- 依赖必须“已安装 + 已启用 + 加载成功”，三者缺一都算缺失。

## 2. 常用 API

| 方法/路径 | 说明 |
|---|---|
| `GET /api/plugin/list` | 已加载清单（`contributions` / `declared_only` / 错误） |
| `GET /api/plugin/discover` | 可发现但未加载的目录 |
| `POST /api/plugin/load/{id}`、`POST /api/plugin/unload/{id}` | 加载/卸载 |
| `POST /api/plugin/load-all` | 批量加载 |
| `POST /api/plugin/{id}/enable`、`POST /api/plugin/{id}/disable` | 启停（持久化） |
| `GET/PUT/DELETE /api/plugin/{id}/config` | 配置读写/清除 |
| `GET /api/plugin/contributions?kind=&slot=&owner=` | 已注册贡献 |
| `GET/DELETE /api/plugin/errors` | 错误通道 |
| `GET /api/plugin/health` | 插件健康聚合 |
| `GET /api/plugin/{id}/ui`、`GET /api/plugin/{id}/bundle` | 声明式 UI / bundle |
| `GET /api/plugin/{id}/capabilities`、`POST/DELETE .../{name}` | 子进程能力授权 |
| `GET/POST /api/plugin/runtimes` | 子进程 runtime 登记 |
| `GET /api/plugin/permissions` | 允许的权限集合 |
| `POST /api/plugin/proxy/{id}/{method}` | 受控代理（旧 UI 兼容） |

## 3. 从旧文档迁移

| 旧写法 | 新写法 |
|---|---|
| `initialize()` 里 `ToolRegistry.register(...)` | `contributions()` 返回 `Contribution(kind=tool, ...)` |
| `initialize()` 里 `PluginPromptRegistry.register(...)` | `ContributionKind.PROMPT` + `meta.agent/prompt/priority` |
| 旧文档说“16/18 类扩展点” | 当前 **24 类**（见 [扩展点](contributions.md)） |
| `ui.json` 单独一份布局 | 优先放进 `ui_slot.meta.layout`，`ui.json` 仍支持 |
| 手写 manifest 签名/权限 | 用 `plugin_sign.py` 与 `plugin_allowed_permissions` |
| 子进程权限表写死在插件里 | 以 `plugins/runtime/*` 与 `services/plugin_rpc.py` 为准 |

## 4. 排错索引

| 现象 | 先看 |
|---|---|
| 加载失败 | `/api/plugin/errors`、`plugin.yaml` 解析错误、`entry_point` |
| 已加载但零贡献 | 是否实现 `contributions()`；manifest 是否声明 `(kind,id)` |
| `declared_only` 非空 | 声明了但运行时没注册（异常被吞/条件分支没走到） |
| 工具不出现 | `agent_callable`、`capability`、`permission_tier`、白名单/收口期 |
| UI 不显示 | `api_version`、`meta.slot` 是否在 15 个槽位内、`visibleWhen`、icon 白名单 |
| Provider 不可选 | 贡献声明、`config_schema`、能力声明、模型清单 |
| 子进程超时/死 | worker 日志、`/api/plugin/runtimes`、`TIMEOUT`/`WORKER_DEAD` |
| 配置不生效 | 是否只写了源码 `config.yaml`、是否需要重载、secret 回写是否用掩码 |

## 5. 参考

- 当前实现：`clipwright/plugins/`、`clipwright/services/plugin_*.py`、`clipwright/schema/plugin.py`；
- 前端：`src/features/plugins/`、`src/features/plugins/slots.ts`；
- 文档目录：[`doc/plugin/README.md`](README.md)。
