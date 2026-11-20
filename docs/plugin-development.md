# 插件开发指南（当前契约）

> 适用代码：`clipwright/plugins/`、`clipwright/services/plugin_*.py`、`clipwright/schema/plugin.py`、
> 前端 `src/features/plugins/`。本文以**当前实现**为准；旧文档中的 `initialize()` 写法仅作为
> 兼容期说明，不是新插件应该使用的接口。
>
> 目录约定：`plugins/<plugin_id>/`（仓库内置）或 `PluginData/plugins/<plugin_id>/`（运行时安装）。

## 1. 最小插件

```
plugins/my_plugin/
├── plugin.yaml     # 必需：清单 + 贡献声明（声明即授权）
├── main.py         # 推荐入口（默认 <id>.main）
├── config.yaml     # 可选：配置默认值（结构化 fields）
└── ui.json         # 可选：声明式设置面板（前端渲染）
```

```python
from clipwright.plugins.base import CapabilityPlugin
from clipwright.plugins.contribution import Contribution, ContributionKind


class MyPlugin(CapabilityPlugin):
    def setup(self) -> None:
        """可选副作用：建目录、读配置、预热。不能在这里注册贡献。"""

    def contributions(self):
        return [
            Contribution(ContributionKind.TOOL, "my_echo", MyEchoTool()),
            Contribution(
                ContributionKind.UI_SLOT, "my_panel", None,
                meta={"slot": "editor.right-panel", "title": "我的面板",
                      "order": 150, "icon": "Puzzle",
                      "layout": {"title": "我的面板", "widgets": [
                          {"type": "input", "key": "endpoint", "label": "端点"}]}},
            ),
        ]
```

```yaml
id: my_plugin
name: 我的插件
version: 1.0.0
kind: capability
api_version: "2"
entry_point: my_plugin.main      # 必须属于 <id> 或 <id>.*
runtime: inprocess               # inprocess | subprocess
permissions: []
contributions:
  - { kind: tool, id: my_echo }
  - { kind: ui_slot, id: my_panel }
```

规则（`plugins/loader.py`）：

- `entry_point` 留空时默认 `<id>.main`；必须是本插件包路径，禁止 `requests` 这类顶层名；
- 加载器只实例化**定义在本插件包内**的、非抽象的 `BasePlugin` 子类；
- `api_version: "2"` 才走 `contributions()` 门面注册；
- manifest 里的 `(kind, id)` 是运行时注册的白名单，**注册未声明的条目会被拒绝**；
- 声明了却没注册 → 出现在 `GET /api/plugin/list` 的 `declared_only`。

## 2. manifest 字段（`PluginManifest`）

| 字段 | 默认 | 说明 |
|---|---|---|
| `id` | 必填 | 目录名即 id；`^[a-z][a-z0-9_-]{0,63}$` |
| `name` / `version` / `kind` | 必填/`1.0.0`/必填 | `kind` 见 `PluginKind`：category / material / agent / capability / style / editor |
| `api_version` | `"1"` | 新插件写 `"2"` |
| `contributions[]` | `[]` | `{kind, id, meta?, config_schema?, capabilities?}` |
| `override_builtin` | `false` | 覆盖内置贡献需签名 + 管理员权限 |
| `entry_point` | `""` | 模块路径；默认 `<id>.main` |
| `runtime` | `inprocess` | `inprocess` 或 `subprocess` |
| `runtime_config` | `{}` | 子进程的 `call_timeout` 等 |
| `permissions[]` | `[]` | `network` / `http` / `fs_write`；必须落在 `settings.plugin_allowed_permissions` 里 |
| `dependencies[]` | `[]` | 依赖插件 id；未安装/被禁用/加载失败都算缺失 |
| `signature` | `""` | manifest 的 HMAC-SHA256（覆盖除 signature 外全部字段） |
| `config_schema_version` | `1` | 配置迁移版本（插件可带 `migrations.py`） |
| `bundle_sha256` | `""` | 受信前端 bundle 的摘要（进入签名载荷） |

`plugin.yaml` 由 `plugins/manifest_yaml.py` 严格解析：重复键、非 mapping 根直接报错，不会静默覆盖。

## 3. 贡献类型（24 类，名单冻结）

`ContributionKind` 当前有 **24 类**；新增 kind 要走契约评审。

| kind | `object` 契约 | 关键 `meta` / 说明 |
|---|---|---|
| `tool` | `BaseTool` 实例 | 能力名建议用 `capability` 语义；覆盖内置需 `override_builtin` |
| `skill` | `BaseSkill` 实例 | 组合技能 |
| `agent` | 插件 Agent | 由宿主按需调起，不参与固定编排 |
| `material_source` | `MaterialSourcePlugin` | 实现 `search(query, **kwargs)` |
| `category` | 类型插件 | 与内置 category 同构 |
| `style_interpreter` | `StyleInterpreterPlugin` | 实现 `interpret(visual_config, persona_context)` |
| `hook` | 可调用对象 | `meta.point` = `HookPoint` 名（如 `PRE_RENDER`） |
| `prompt` | 纯数据 | `meta.agent` + `meta.prompt` + `priority`；注入对应 Agent 的 system prompt |
| `llm_provider` | Provider 协议 | 实现 `build_client / capabilities / normalize_messages / uses_system_kwarg / default_model`；注册时校验 |
| `tts_provider` | TTS 工厂 | 具体协议见 `clipwright/providers/` 与内置 `plugins/tts_*` |
| `stt_provider` | STT 工厂 | `transcribe / align / is_available` |
| `renderer_backend` | 渲染后端 | `render(timeline, output_path, settings, on_progress=None, cancel_token=None)`；`GET /api/render/backends` 可查 |
| `animation` | `AnimationDef` | id 即 `animation_id` |
| `embedding_provider` | RAG 工厂 | 产出 `embed / embed_query / dim` |
| `rerank_provider` | RAG 工厂 | 产出 `rerank(query, documents, model, top_n)` |
| `ui_slot` | 纯数据（`object=None`） | `meta.slot` + `title`/`order`/`icon`/`visibleWhen`/`layout` |
| `export_preset` | 纯数据（`object=None`） | `meta.payload = {name,width,height,fps,bitrate,container?}` |
| `display_spec` | 纯数据（`object=None`） | 声明式展示契约；engine 从不执行插件代码 |
| `clip_kind` | 纯数据（`object=None`） | 插件轨道/片段类型；展示信息与 schema 在 `meta` |
| `image_provider` | 图像 Provider | 实现 `default_model / capabilities / generate`（可选 `probe(config)`） |
| `text_motion` | `TextMotionProvider` | 文字动效 provider |
| `text_prompt_pack` | 纯数据 | 文字动效提示词包 |
| `text_style_pack` | 纯数据 | 文字效果样式包 |
| `text_asset_pack` | 纯数据 | 文字效果素材包 |

Provider 类 kind 在注册前会做协议符合性校验；纯数据 kind 的 `object` **必须为 `None`**。
`ui_slot` / `export_preset` / `display_spec` / `clip_kind` / `text_*_pack` 都没有后端注册表，
由门面 ledger + 对应消费方直接读取。

## 4. 注册与冲突规则

`ContributionRegistry`（`plugins/contribution.py`）是唯一门面：

- `register(owner, items)`：逐条校验「声明即授权」、id 格式、kind 契约；
- `list(kind)` / `list_by_owner(owner)`：查询；
- `unregister_plugin(owner)`：卸载时按 owner 回收；
- 内置贡献 owner 固定 `__builtin__`，插件默认**不能覆盖**；`override_builtin=true` +
  有效签名 + 管理员允许时才放行（`override_policy.py`）；
- 冲突默认 `REJECT` 并写入插件错误通道；不会静默替换。

实现细节由 `plugins/contribution_dispatch.py` 转调现有注册表（Tool/Skill/Agent/Material/
Category/Animation/Prompt/Hook/ClipKind/Text*）；provider 类只登记到门面，由消费方按需解析。

## 5. 生命周期

1. `discover`：扫描 `plugins/*`（或 `PluginData/plugins/*`）里有 `plugin.yaml` 的目录；
2. `load`：解析 manifest → 签名/权限校验 → 依赖解析 → 合并配置 → 导入入口模块 → 实例化；
3. `initialize()`：宿主调用；新契约下 `setup()` 先跑副作用，再由门面注册 `contributions()`；
4. 运行时：工具/技能/Agent/provider 按 kind 被消费；
5. `disable/unload`：调 `shutdown()` 释放外部资源；门面按 owner 回收贡献；
6. 配置变更：`save_config()` 写 `PluginData/plugins/<id>/config.yaml`，必要时重载插件。

兼容期：只实现 `initialize()` 的旧插件仍可加载，但会写迁移提示；`initialize()` 里手写注册表
属于旧轨，新插件不要这样写。

## 6. 权限、签名与安全边界

- **权限**：manifest `permissions` 必须 ⊆ `settings.plugin_allowed_permissions`，否则拒绝加载；
- **签名**：`sign_manifest()` 计算 HMAC；`plugin_require_signature=true` 或显式配置
  `plugin_signature_key` 时，未签名插件拒绝加载；签名不匹配同样拒绝；
- **覆盖内置**：除签名外还要 `override_builtin: true` 和管理员策略；
- **前端 bundle**：`bundle_sha256` 进入签名载荷；服务端读取时重算摘要，防篡改；
- **子进程**：见下节；`inprocess` 只适合受信任插件，Python 进程内无法做真正的权限隔离，
  所以「声明即授权」是当前模型的硬边界。

## 7. 子进程运行时（`runtime: subprocess`）

宿主**不 import** 插件代码；`PluginHost` 以 NDJSON 线协议与 worker 通信：

- 协议版本 `2`（仍兼容 v1）；消息示例：
  `{"v":2,"direction":"host","id":3,"method":"tool.call","params":{...}}`；
- 单帧上限 **8 MiB**、单方向在途 **32**、嵌套深度 **8**；
- 错误码：`BAD_REQUEST / UNKNOWN_METHOD / PERMISSION_DENIED / PLUGIN_ERROR / TIMEOUT /
  WORKER_DEAD / BOOT_FAILED / UNSUPPORTED_KIND / OVERLOADED / DEPTH_EXCEEDED /
  CANCELLED / SCOPE_DENIED / UNSUPPORTED_METHOD`。

worker 通过 `bridge.call_host(method, params)` 调宿主；当前注册的方法
（`services/plugin_rpc.py`）：

| 方法 | 用途 |
|---|---|
| `workspace.read/write/list/stat/hash/mkdir/delete/rename` | 插件命名空间内文件，原子写；写冲突 `REVISION_CONFLICT`；路径逃逸 `PERMISSION_DENIED` |
| `artifact.register/list/get` | 注册产物（宿主算 hash/size/mime）、按 own/task 查询 |
| `materials.list/read` | 项目 `materials/` 只读清单/读取（默认 4 MiB） |
| `job.submit/status/list/cancel` | `shell=False` 的子任务；状态 queued/running/succeeded/failed/cancelled/timed_out/interrupted |
| `plugin.call` | 跨插件 tool/skill 调用；需要 `dependencies` + 对方 `exposes` |
| `timeline.summary` | 任务级只读时间线摘要 |
| `capability.call` | 引擎能力；默认放行 `media_probe/frame_check/align`，其他需 `runtime_config.capabilities` 登记 |

事件通过 `@on_event` 订阅：`job.cancelled`、`job.timed_out`、`task.cancelled`（单向、无响应）。
`render_contract: media_overlay.v2` 的请求字段（clip/context/cues/audio/materials/neighbors/
render_settings/output/truncated）与上限（cues ≤200、neighbors ≤8、materials ≤32、JSON ≤256KB）
由 `services/plugin_context.py` 组装；只消费结构化字段，不执行插件给的 HTML/JS。

## 8. 前端扩展：UI 槽位与布局

slot 名单**冻结为 15 个**（权威：`src/features/plugins/slots.ts`）：

`nav.primary`、`nav.settings`、`page`、`editor.left-panel`、`editor.right-panel`、
`editor.bottom-panel`、`editor.toolbar`、`timeline.tool`、`timeline.context-menu`、
`props.section`、`export.preset`、`settings.section`、`create.dub`、`app.overlay`、`shortcut`。

`ui_slot` 的 `meta`：

```jsonc
{
  "slot": "editor.right-panel",
  "title": "我的面板",
  "order": 150,
  "icon": "Puzzle",              // 必须在前端 icon 白名单内
  "visibleWhen": "context.hasTimeline === true",
  "layout": { "title": "我的面板", "widgets": [ /* 见下 */ ] }
}
```

- `visibleWhen` 只允许「路径 + 比较」的受限表达式，禁止 eval；上下文字段来自
  `SlotVisibilityContext`（hasSelection/clipKind/trackKind/playing/hasTimeline/isAdmin）；
- 未知 icon 会被丢弃；未知 widget 类型会渲染成可读占位，不静默丢整块；
- 前端 UI JSON 支持：`textarea/input/string/select/checkbox/slider/button/image/spinner/
  alert/text/row/column/group/file`；
- `select.optionsFrom` 只能指向宿主白名单数据源（当前 `voices`），拿不到任意数据；
- `file` 控件值 = `File` 对象，上传由宿主执行，插件拿不到上传 API/凭据。

`GET /api/plugin/{plugin_id}/ui` 返回 `ui.json`；受信 bundle 走 `GET /api/plugin/{plugin_id}/bundle`。

## 9. 配置

`config.yaml` 使用结构化字段：

```yaml
fields:
  api_key: {type: string, value: "", label: "API Key", secret: true}
  max_results: {type: int, value: 10, label: "最大结果数"}
```

- 类型：`string / int / float / bool / dict / list`；`secret: true` 落盘加密、读取掩码；
- 源码默认值 `plugins/<id>/config.yaml`，运行时覆盖 `PluginData/plugins/<id>/config.yaml`
  （读取时按字段合并）；
- provider 类贡献可用 `ContributionDecl.config_schema` 声明表单，前端 `ProvidersPage` 直接渲染；
  未知控件/未知能力键必须显式展示，不能静默丢弃；
- 配置版本：`config_schema_version` + 插件目录可选 `migrations.py`。

API：

- `GET /api/plugin/{id}/config`（secret 字段为掩码）
- `PUT /api/plugin/{id}/config`（回传掩码即保留旧值）
- `DELETE /api/plugin/{id}/config`（清除运行时覆盖）

## 10. 与 Agent / 提示词集成

新契约用 `ContributionKind.PROMPT`：

```python
Contribution(ContributionKind.PROMPT, "my_prompt", None,
             meta={"agent": "structure", "prompt": "## 我的能力\n...", "priority": 5})
```

注入在 Agent 构建 system prompt 时聚合；可注入的 LLM Agent 以当前代码为准
（structure/animation/material/requirements 等，edit/audio/quality 不消费提示词）。
旧 `PluginPromptRegistry.register(...)` 仍存在，但属于旧轨写法；新插件请用贡献声明。

## 11. 常用 API（`/api/plugin/*`）

| 方法/路径 | 说明 |
|---|---|
| `GET /list`、`GET /discover` | 已加载清单（含 `contributions`/`declared_only`）、可发现目录 |
| `POST /load/{id}`、`POST /unload/{id}`、`POST /load-all` | 加载/卸载/批量加载 |
| `POST /{id}/enable`、`POST /{id}/disable` | 启停（持久化） |
| `GET/PUT/DELETE /{id}/config` | 配置读写/清除 |
| `GET /contributions?kind=&slot=&owner=` | 已注册贡献 |
| `GET /errors`、`DELETE /errors` | 插件错误通道 |
| `GET /health` | 插件健康聚合 |
| `GET /{id}/ui`、`GET /{id}/bundle` | 声明式 UI / 受信 bundle |
| `GET /{id}/capabilities`、`POST /{id}/capabilities/{name}` | 子进程能力授权 |
| `GET /runtimes`、`POST /runtimes` | 子进程 runtime 登记 |
| `GET /permissions` | 允许的权限集合 |
| `POST /proxy/{id}/{method}` | 受控的插件代理端点（旧 UI 兼容） |

## 12. 调试与验收

- 体检：`python scripts/plugin_health_report.py [--json out.json]`
  （声明/注册不一致、零贡献、结构性旧轨）；
- 迁移对账：`python scripts/plugin_contribution_snapshot.py out.json`（迁移前后 diff）；
- 签名：`python scripts/plugin_sign.py genkey|sign|verify <plugin_dir> --key=...`；
- 前端契约对账：`python scripts/check_frontend_contract.py`；
- 运行中查错：`GET /api/plugin/errors`、`GET /api/plugin/health`、`GET /api/plugin/list`
  的 `declared_only`；
- 最小验收清单：manifest 合法 → 加载成功 → `GET /list` 的 `contributions` 与声明一致
  （`declared_only` 为空）→ 目标消费面可用（工具可调/槽位出现/Provider 可选）→ 停用时
  贡献被回收、无残留错误。

## 13. 从旧文档迁移

- `initialize()` 手写注册 → `contributions()` 返回 `Contribution`（本指南第 1/4 节）；
- `PluginPromptRegistry.register(...)` → `ContributionKind.PROMPT`；
- `ui.json` 的 widgets 仍是同构布局，但建议直接放进 `ui_slot.meta.layout`；
- 旧的「16/18 类扩展点」表述已过时，当前是 **24 类**（第 3 节表）；
- 子进程 SDK 的权限/协议以 `plugins/runtime/*` 与 `services/plugin_rpc.py` 为准。
