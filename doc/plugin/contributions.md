# 扩展点：贡献契约与“声明即授权”

插件的所有能力都通过 `Contribution` 声明，由 `ContributionRegistry` 统一注册。
新插件请使用 `contributions()`，不要直接在 `initialize()` 里 import 注册表（旧轨仅兼容）。

## 1. 贡献值对象

```python
from clipwright.plugins.contribution import Contribution, ContributionKind

Contribution(kind=ContributionKind.TOOL, id="my_tool", object=tool_instance, meta={})
```

- `id`：同 kind 内唯一，必须在 `plugin.yaml` 的 `contributions` 中声明；
- `object`：实例/类/可调用对象，契约由 kind 决定；纯数据 kind 必须为 `None`；
- `meta`：kind 专属字段（如 hook 的 `point`、ui_slot 的 `slot`、prompt 的 `agent`）。

## 2. 24 类扩展点（名单冻结）

| kind | object 契约 | 说明 |
|---|---|---|
| `tool` | `BaseTool` | 原子能力；建议声明 `capability` 语义名 |
| `skill` | `BaseSkill` | 可组合技能 |
| `agent` | 插件 Agent | 由宿主按需调起 |
| `material_source` | `MaterialSourcePlugin` | 实现 `search(query, **kwargs)` |
| `category` | 类型插件 | 与内置视频类型同构 |
| `style_interpreter` | `StyleInterpreterPlugin` | `interpret(visual_config, persona_context)` |
| `hook` | 可调用对象 | `meta.point` 取 `HookPoint`（如 `PRE_RENDER`） |
| `prompt` | 纯数据 | `meta.agent` + `meta.prompt` + `priority` |
| `llm_provider` | Provider 协议 | 见 [模型与 Provider](providers.md) |
| `tts_provider` | TTS 工厂 | 同上 |
| `stt_provider` | STT 工厂 | `transcribe/align/is_available` |
| `renderer_backend` | 渲染后端 | `render(timeline, output_path, settings, ...)` |
| `animation` | `AnimationDef` | id 即 `animation_id` |
| `embedding_provider` | RAG 工厂 | `embed/embed_query/dim` |
| `rerank_provider` | RAG 工厂 | `rerank(query, documents, model, top_n)` |
| `ui_slot` | 纯数据（`None`） | 见 [可视化视图](ui.md) |
| `export_preset` | 纯数据（`None`） | `meta.payload` 提供导出参数 |
| `display_spec` | 纯数据（`None`） | 声明式展示契约，engine 不执行插件代码 |
| `clip_kind` | 纯数据（`None`） | 插件轨道/片段类型 |
| `image_provider` | 图像 Provider | `default_model/capabilities/generate` |
| `text_motion` | `TextMotionProvider` | 文字动效实现 |
| `text_prompt_pack` | 纯数据 | 文字动效提示词包 |
| `text_style_pack` | 纯数据 | 文字效果样式包 |
| `text_asset_pack` | 纯数据 | 文字效果素材包 |

新增 kind 必须走契约评审，不要在插件里自造 kind。

## 3. 声明即授权

- manifest 里的 `(kind, id)` 是允许注册的白名单；
- 运行时注册未声明的条目 → 拒绝并写错误通道；
- 声明了却没注册 → `GET /api/plugin/list` 的 `declared_only`；
- 内置贡献 owner 为 `__builtin__`，插件默认不能覆盖。

覆盖内置需要同时满足：`override_builtin: true`、有效 manifest 签名、管理员策略
（`plugins/override_policy.py`）。冲突默认 `REJECT`，不会静默替换。

## 4. 旧轨兼容

只实现 `initialize()` 并在其中手写注册的旧插件仍然能加载，但会写迁移提示。
新插件不要这样做；`setup()` 只做副作用，注册一律通过 `contributions()`。

旧的 `PluginPromptRegistry.register(...)` 仍存在，但新写法是：

```python
Contribution(ContributionKind.PROMPT, "my_prompt", None,
             meta={"agent": "structure", "prompt": "## 我的能力\n...", "priority": 5})
```

可注入的 Agent 以当前代码为准（structure/animation/material/requirements 等；
edit/audio/quality 不消费提示词）。

## 5. 验收

```bash
python scripts/plugin_health_report.py          # 声明/注册一致性 + 旧轨/零贡献体检
python scripts/plugin_contribution_snapshot.py out.json   # 迁移前后 diff
curl http://127.0.0.1:8080/api/plugin/list
```

`declared_only` 为空、`contributions` 与声明一致、目标消费面可用，才算接好。
