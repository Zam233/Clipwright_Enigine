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

## 2. 可贡献的 16 类扩展点

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
| `renderer_backend` | 渲染后端 | `render(...)` 必须接受 `cancel_token` |
| `embedding_provider` | 向量化（RAG） | 工厂形态，产出 `embed/embed_query/dim` |
| `rerank_provider` | 重排序（RAG） | 工厂形态，产出 `rerank(query, documents, model, top_n)` |
| `ui_slot` | 前端插槽 | `meta.slot` 见下节 |
| `export_preset` | 导出预设 | `meta.payload = {name,width,height,fps,bitrate,container?}` |

## 3. UI 槽位与前端约定

- **slot 名单冻结**：`nav.primary` / `nav.settings` / `page` / `editor.left-panel` /
  `editor.right-panel` / `editor.bottom-panel` / `editor.toolbar` / `timeline.tool` /
  `timeline.context-menu` / `props.section` / `export.preset` / `settings.section` / `shortcut`。
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
- **受信任 bundle**：`meta.payload.bundle = true` 时由 `<iframe sandbox="allow-scripts">`
  承载，需后端开启 `plugin_allow_ui_bundle` **且** manifest 带签名；
  与宿主只通过 `postMessage({type:'resize'|'call'|'result'})` 通信，插件拿不到凭据。

## 4. Provider 源的写法（模型 / 嵌入 / 重排序 / 语音）

- **不要在插件里硬编码密钥**：运行时凭据由「设置 → 模型提供商」的源按角色解析
  （`main/flash/vision/embed/rerank/tts`），插件只声明 provider 名。
- 插件注册的 provider 会自动出现在该类型下拉里（`/api/providers/available`），
  也能被 `settings.llm_provider` / `settings.rag_embed_provider` / `settings.tts_default_provider`
  的选项列表看到。
- LLM provider 的 `build_client` 需接受 `timeout_sec / proxy / headers`（可为空）；
  核心会把源里的这三项传下来（自定义头优先级最高）。

## 5. 生命周期与失败可见性

- `contributions()` 抛异常 → 写入插件错误通道并**让本次加载失败**（不再静默零贡献）。
- `shutdown()` 由桥接层自动追加门面回收（工具/技能/Agent/hook/prompt 等一并注销）。
- 插件启停会失效前端 Slot 缓存，界面无需刷新即同步。
- 诊断入口：`GET /api/plugin/errors`（错误通道）、`GET /api/plugin/list`
  （贡献与 `declared_only`）、`GET /api/plugin/health`（总览）。

## 6. 迁移清单（旧契约 → 新契约）

1. 把 `initialize()` 里的 `XxxRegistry.register(...)` 逐条改成 `contributions()` 返回值；
2. `plugin.yaml` 加 `api_version: "2"` 与 `contributions:` 声明白名单；
3. 删除 `initialize()` 主体（或保留为空），交给桥接层短路；
4. 若依赖 `teardown` 清单回收，确认注册进的是门面可回收的 kind（否则在 `shutdown()` 自行清理）。
