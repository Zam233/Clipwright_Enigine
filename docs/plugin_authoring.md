# 插件作者指南（P10）——新契约：`contributions()`

> 适用版本：Clipwright 插件契约 v2（2026-09 插件化方案 A/D 落地后）。
> 旧契约 `initialize()` 仍可用，但**只作为兼容期退路**，并会写入「建议迁移」提示。

## 1. 最小插件

```
plugins/my_plugin/
├── plugin.yaml          # manifest（含 contributions 声明白名单）
├── main.py              # Plugin 子类，实现 contributions()
└── ui.json              # 可选：旧格式设置面板（{title, widgets}）
```

```python
from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind

class MyPlugin(BasePlugin):
    def setup(self):
        """可选：副作用（建目录 / 读配置 / 预热），在 contributions() 之前执行。"""

    def contributions(self):
        return [
            Contribution(ContributionKind.TOOL, "my_echo", MyEchoTool(), meta={}),
            Contribution(
                ContributionKind.UI_SLOT, "my_panel", None,
                meta={
                    "slot": "editor.right-panel",   # slot 名走冻结名单
                    "title": "我的面板",
                    "order": 150,
                    "icon": "Puzzle",               # 图标走前端白名单
                    "visibleWhen": "context.hasTimeline === true",
                    "layout": {"title": "我的面板", "widgets": [
                        {"type": "string", "key": "endpoint", "label": "端点"},
                    ]},
                },
            ),
        ]
```

`plugin.yaml` 必须**预声明**每一条贡献（「声明即授权」）：

```yaml
id: my_plugin
name: 我的插件
version: 1.0.0
kind: capability
api_version: "2"
permissions: []
contributions:
  - { kind: tool, id: my_echo }
  - { kind: ui_slot, id: my_panel }
```

> 声明了却没注册的条目会出现在 `GET /api/plugin/list` 的 `declared_only` 里；
> 注册了却没声明的会被门面拒绝并写错误通道。

## 2. 可贡献的 17 类扩展点

| kind | 用途 | 关键 meta / 说明 |
|------|------|------------------|
| `tool` | 原子能力 | `object` 需符合 `BaseTool`；同 id 覆盖内置需 `override_builtin` |
| `skill` | 组合技能 | 同上，`BaseSkill` |
| `agent` | 主管线 Agent | `meta.deps=[...]` 决定并入 DAG 的位置 |
| `material_source` | 素材源 | 实现 `search()` |
| `category` | 视频类型 | 结构同内置类型插件 |
| `style_interpreter` | 风格解释器 | 同一时刻只允许一个非内置所有者 |
| `hook` | 生命周期钩子 | `meta.point`（`HookPoint` 名，如 `PRE_RENDER`） |
| `prompt` | Agent 提示词注入 | `meta.agent` + `meta.prompt`（+ `priority`） |
| `llm_provider` | 大模型提供者 | 必须实现 `build_client/capabilities/normalize_messages/uses_system_kwarg/default_model`（注册时校验） |
| `tts_provider` | 语音合成 | `meta` 走 `TtsProviderEntry` 工厂形态 |
| `stt_provider` | 语音识别 | `transcribe/align/is_available` |
| `renderer_backend` | 渲染后端 | 实现 `render(timeline, output_path, settings, on_progress=None, cancel_token=None)`；详见下方「渲染后端贡献」 |
| `embedding_provider` | 向量化（RAG） | 工厂形态，产出 `embed/embed_query/dim` |
| `rerank_provider` | 重排序（RAG） | 工厂形态，产出 `rerank(query, documents, model, top_n)` |
| `animation` | 动画定义 | `object` 需是 `AnimationDef`；id 即 `animation_id` |
| `ui_slot` | 前端插槽 | `meta.slot` 见下节 |
| `export_preset` | 导出预设 | `meta.payload = {name,width,height,fps,bitrate,container?}` |

## 3. UI 槽位与前端约定

### 渲染后端贡献（`renderer_backend`）

插件可以贡献一个**渲染后端**，让成片交给远端 worker 或常驻 sidecar（例如
`@hyperframes/producer`）产出。协议与内建后端一致：

```python
class MyProducerBackend:
    name = "producer"                     # 与 contribution id 一致，供 settings.renderer_backend 选择

    def capabilities(self) -> dict:       # 能力声明：前端 /api/render/backends 会原样展示
        return {"name": self.name, "supports_gpu": True, "sidecar": "@hyperframes/producer"}

    async def probe(self) -> bool:        # 可用性探测（sidecar 是否在线）
        return True

    async def render(self, timeline, output_path, settings, on_progress=None, cancel_token=None):
        # settings 是 dict（width/height/fps/bitrate/audio_bitrate/audio_file_path/... 收口在这里）
        # on_progress(percent: float, message: str)；cancel_token 是字符串 id 或对象
        return {"success": True, "output_path": output_path, "duration_sec": 12.3, "warnings": []}
```

接入方式：
1. 插件在 `contributions()` 里注册 `Contribution(ContributionKind.RENDERER_BACKEND, "producer", backend)`；
2. 管理员把 `renderer_backend` 设为该 id（设置页 / `PUT /api/settings`）；
3. `GET /api/render/backends` 会列出所有后端（含内建 local/remote）与其可用性，
   未注册或探测失败时渲染入口**自动回退本机**并在日志里说明原因。

协议只到 `render()` 为止：只实现该协议的插件后端会经内部适配器（kwargs 收口 +
`progress_callback` → `on_progress` 映射）接入，既有调用方无需改动。

## 3.1 UI 槽位与前端约定

- **slot 名单冻结（15 个）**：`nav.primary` / `nav.settings` / `page` / `editor.left-panel` /
  `editor.right-panel` / `editor.bottom-panel` / `editor.toolbar` / `timeline.tool` /
  `timeline.context-menu` / `props.section` / `export.preset` / `settings.section` /
  `create.dub` / `app.overlay` / `shortcut`（权威名单见前端
  `src/features/plugins/slots.ts` 的 `SLOT_NAMES`）。**新增 slot 必须走 SlotRegistry 评审**，
  在插件里写个新名字不会被渲染。
- **`visibleWhen`** 是受限表达式（`context.xxx` 真值或 `===`/`!==`/`==`/`!=` 比较），
  **不执行任意 JS**；解析失败一律视为不可见。
  上下文键：`hasSelection` / `clipKind` / `trackKind` / `playing` / `hasTimeline` / `isAdmin`。
- **`icon`** 只能取前端白名单里的 lucide 名（见 `src/features/plugins/slots.ts`）。
- **`props.section` 必须声明 `meta.payload.kinds`**（如 `["video"]`）；未声明视为
  「不适用任何片段类型」，并会在控制台给一次性告警。
- **`shortcut`** 的 `meta.payload = {combo, label, action:{endpoint, method?, body?}}`，
  仅触发一次受控 API 调用；同样受 `visibleWhen` 约束。
- **导航类**（`nav.*`、`page`）的 `meta.payload.to` 必须是站内绝对路径
  （单斜杠开头、不含 `..`），否则前端不渲染该导航项。
- **页面可见性（P6）**：插件页与界面**默认对所有登录用户开放**。要限制为管理员，
  在贡献 `meta` 里声明 `admin_only: true`（或 `payload.admin_only: true`）——
  后端 `contributions` 端点会**直接不投递**给非管理员（前端拿不到定义），
  页面壳再按本地角色二次拦截并给出「仅管理员可访问」提示。
  注意：`off` / `token` 鉴权模式没有用户身份，按本仓库既有语义视为管理员。
- **受信任 bundle**：`meta.payload.bundle = true` 时由 `<iframe sandbox="allow-scripts">`
  承载，需后端开启 `plugin_allow_ui_bundle` **且** manifest 带签名；
  与宿主只通过 `postMessage({type:'resize'|'call'|'result'})` 通信，插件拿不到凭据。

## 3.2 面板控件集（`meta.layout.widgets`，14 类）

声明式 JSON，不写 React。**值一律经 `onChange` → `Slot.onValuesChange` 交回宿主**；
未知 `type` 不会静默丢弃，渲染器会给可见占位（方便发现「前端还没实现这个控件」）。

| type | 值 / 关键字段 | 说明 |
|---|---|---|
| `string` / `input` | 字符串（`key` / `label` / `placeholder` / `defaultValue`） | 单行文本 |
| `textarea` | 字符串（`rows`） | 多行文本 |
| `select` | 字符串（`options` 或 `optionsFrom`） | `optionsFrom` 只能引用**宿主白名单数据源**（当前 `voices`），插件拿不到任意数据；给不出选项时显示「暂无可用选项」 |
| `checkbox` | 布尔 | |
| `slider` | 数字（`min` / `max` / `step`） | |
| `file` | `File` 对象（`accept` / `placeholder`） | **上传执行归宿主**：控件只把选中的文件交回宿主页面，插件不发起上传、拿不到上传 API/路径/凭据，因此没有 `action`/`endpoint` 字段 |
| `button` | —（`action: {endpoint, method, body?, resultMap?}`） | 只能发 JSON 请求（多部分表单上传请用 `file` 交给宿主） |
| `image` | —（`sourceField` 指向状态里的 URL） | 无 URL 时不渲染 |
| `spinner` | —（`label`） | 加载态 |
| `alert` | —（`severity` + `textField`） | 文本为空时不渲染 |
| `text` | —（`content` 或 `contentField`） | 说明文案 |
| `row` / `column` / `group` | —（`children` / `gap` / `title`） | 布局容器 |

见 `plugins/tts_feature` 的 `create_dub_entry`：`select voice_id`（`optionsFrom: voices`）+
`checkbox auto_dub` + `file upload_voice` + 说明文案，就是创作页「03 配音与字幕」的全部入口。

## 4. Provider 源的写法（模型 / 嵌入 / 重排序 / 语音）

- **不要在插件里硬编码密钥**：运行时凭据由「设置 → 模型提供商」的源按角色解析
  （`main/flash/vision/embed/rerank/tts`），插件只声明 provider 名。
- 插件注册的 provider 会自动出现在该类型下拉里（`/api/providers/available`），
  也能被 `settings.llm_provider` / `settings.rag_embed_provider` / `settings.tts_default_provider`
  的选项列表看到。
- LLM provider 的 `build_client` 需接受 `timeout_sec / proxy / headers`（可为空）；
  核心会把源里的这三项传下来（自定义头优先级最高）。

## 5. 运行形态：进程内 vs 子进程沙箱

manifest 用 `runtime` 选择执行方式：

| runtime | 说明 | 权限强制 |
|---|---|---|
| `inprocess`（默认） | 插件在宿主进程内加载（内置/自研插件） | 仅 manifest 声明校验 |
| `subprocess` | 独立子进程加载，**宿主不 import 插件代码** | 进程内审计钩子（真拦截） |

`subprocess` 档的语义：

- 宿主 `sys.modules` 里没有你的模块，插件碰不到宿主对象图；
- 贡献以**纯数据声明**跨进程（kind/id/meta），宿主为每条贡献造同形代理对象
  （调用时转发 RPC），因此 `contributions()` 必须只返回声明、副作用放 `setup()`；
- **必须逐条声明**：`runtime=subprocess` 时 manifest 的 `contributions` 为强制项，
  未声明直接拒载（不可信形态必须走「声明即授权」，fail-closed）；
- 权限在**你的进程内**强制：写盘需 `fs_write` 且只能写自己的 PluginData 目录、
  网络需 `network`/`http`、命令执行一律拒绝（权限白名单没有 `shell`）；
- 单次调用超时由 `runtime_config.call_timeout` 控制（默认 30s）；worker 崩溃或超时后
  该插件所有调用都会收到可读错误（`RpcError`），不会静默返回空结果；
- 宿主侧存在一个 `SubprocessPluginHandle`：`load()` / `get()` / `reload()` 对子进程
  插件与进程内插件行为一致（可重复加载幂等、配置改动即时生效、卸载会回收贡献）；
- 目前支持跨进程的 kind：`tool` / `hook` / `material_source` / `prompt` /
  `ui_slot` / `export_preset`；其它 kind 会在加载时被拒绝并写入错误通道
  （防止「装了却静默无效」）。

```yaml
runtime: subprocess
permissions: [network]          # 只给网络；写盘会被拒
runtime_config:
  call_timeout: 15
```

参考实现：`plugins/sandbox_demo/`（工具 + 钩子 + 提示词 + UI 声明，含写盘被拒的探针）。

## 6. 生命周期与失败可见性

- `contributions()` 抛异常 → 写入插件错误通道并**让本次加载失败**（不再静默零贡献）。
- `shutdown()` 由桥接层自动追加门面回收（工具/技能/Agent/hook/prompt/动画/素材源/
  风格解释器一并注销）；禁用插件不会留下可用的陈旧能力。
- **零贡献是可见状态**：如果 `contributions()` 因缺少配置而返回空列表，
  请在实现里用 `logger.info` 说明原因（内置素材源插件都这么做）；插件管理页会
  显示「N 项未生效」（hover 列出具体 `kind:id`），后端也能从 `declared_only` 查到。
- 插件启停会失效前端 Slot 缓存，界面无需刷新即同步。
- 同一时刻只有一个**风格解释器**活跃：`meta.priority` 越大越优先（同优先级先注册者胜），
  `GET /api/plugin/health` 的 `style_interpreter` 会给出活跃 owner 与候选项。
- 诊断入口：`GET /api/plugin/errors`（错误通道）、`GET /api/plugin/list`
  （贡献与 `declared_only`）、`GET /api/plugin/health`（总览 + 解释器状态）。

## 7. 迁移清单（旧契约 → 新契约）

1. 把 `initialize()` 里的 `XxxRegistry.register(...)` 逐条改成 `contributions()` 返回值；
   非注册的副作用（`mkdir`/环境清理/预热）搬到 `setup()`；
2. `plugin.yaml` 加 `api_version: "2"` 与 `contributions:` 声明白名单；
3. 删除 `initialize()` 主体（或保留为空），交给桥接层短路；
4. 若依赖 `teardown` 清单回收，确认注册进的是门面可回收的 kind（否则在 `shutdown()` 自行清理）。
