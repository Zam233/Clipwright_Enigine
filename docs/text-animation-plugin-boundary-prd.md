# 文字动画能力拆分与 Engine 接口 PRD

> 状态：草案（待评审）  
> 日期：2026-11-17  
> 范围：`clipwright/animation/text_*`、`MGRenderer`、Hyperframes、预览 `mgRenderer`、`apply_text_style`、`text_effect_card`、插件贡献契约  
> 结论先行：**基础文字动画能力留在 engine，做成稳定接口；可选效果/提示词包/静态卡/替代渲染后端拆成插件。插件只提供“能力声明 + 纯数据/隔离渲染”，不拥有 timeline、预览、质量门禁和降级决策。**

## 1. 背景

当前文字动画已经形成一条完整链路：

```
text_specs / prompts/text
  → text_generation（LLM）
  → TextAnimationStyle / text_mg
  → MGRenderer → Hyperframes → 成片
  → apply_text_style（写回 clip）
  → mgRenderer.ts（预览）
  → quality（样式/叠字/单调检查）
```

同时，插件系统已有 20 类贡献（`ContributionKind`），其中与文字动画相关的是：

- `ANIMATION`：动画定义（预设模板/关键帧），**不适合**承载“提示词驱动”的文字动画；
- `PROMPT`：提示词注入；
- `RENDERER_BACKEND`：渲染后端；
- `TOOL` / `CLIP_KIND`：工具、轨道/片段类型；
- `CATEGORY`：视频类型插件。

用户此前的硬约束：

> 插件领域只保留提示词，而不是预设模板；由 LLM 动态生成动画。

因此本 PRD 不把“固定文字动画模板”做成插件；要把**提示词包**和**可选效果实现**做成插件，把**契约、渲染、预览、质量、时间线**留在 engine。

## 2. 拆分原则

### 2.1 留在 Engine 的判据

满足任一条即属于 engine：

1. 定义跨模块的**稳定数据契约**（style/motion/timeline）；
2. 决定**成片与预览是否一致**；
3. 涉及**安全/沙箱/资源访问**；
4. 决定**任务终态、质量门禁、失败/重试**；
5. 基础渲染能力，所有视频类型都要用；
6. 写入 timeline、资产、项目状态；
7. 插件卸载后必须仍然可解释/可失败的行为。

### 2.2 拆成插件的判据

满足全部才可拆：

1. 可选能力，缺失不影响基础出片；
2. 可以用**纯数据**或**隔离渲染**表达；
3. 不直接写 timeline/质量/任务状态；
4. 有明确的能力声明（supports）和 fidelity；
5. 卸载后 engine 能显式报“能力不可用”，而不是静默降级；
6. 不要求插件执行任意 JS 或访问任意文件系统。

## 3. 能力清单与归属

| 能力 | 当前实现 | 归属 | 理由 / 接口 |
|---|---|---|---|
| `TextAnimationStyle` 字段、默认值、归一化 | `text_style.py` | **Engine** | 跨生成/渲染/预览/质量的稳定契约 |
| motion 枚举：fade/slide/scale/typewriter/word_by_word/highlight/glitch/neon | `text_style.py`、`text_mg.py` | **Engine 基础实现** | 基础语义必须一致；glitch/neon 可后续做成可选 provider |
| 文字布局（center/top、字幕带避让、字号下限、box/padding） | `text_mg.py` | **Engine** | 与画布、字幕轨、可读性耦合 |
| typewriter/word_by_word 的 token 切分、reveal 时间 | `text_mg.py` | **Engine** | 基础文字动画，所有类型共用 |
| 圆角底板、描边、阴影、发光、对齐、line_height | `text_mg.py` / `MGRenderer` | **Engine** | 预览与成片必须同源 |
| 渐变文字 `background-clip` | `MGRenderer` | **Engine** | 需要 HTML 渲染安全与预览一致 |
| `highlight` 黄条 | `text_mg.py` | **Engine** | 基础 motion，语义固定 |
| glitch/neon 的 CSS 近似 | `text_mg.py` | **Engine（一期）** | 目前是内建效果；复杂 shader 版另开 provider |
| 文字类型 prompt（emphasis/quote/number/...） | `text_specs.py` + `prompts/text/*.md` | **Plugin：Prompt Pack** | 用户要求插件只保留提示词；不提供固定关键帧 |
| 每种类型的默认视觉指引/风格模块 | prompt md | **Plugin：Prompt Pack** | 纯文本，可版本化、可卸载 |
| LLM 调用、JSON 解析、repair、校验 | `text_generation.py` | **Engine** | 统一错误处理、重试、报告 finish_reason |
| `apply_text_style` 写回 clip | tool + builder | **Engine API/Tool** | 必须唯一入口，插件不能直接改 timeline |
| Hyperframes 调用、MOV 合成、缓存 | `render.py` / `hyperframes_renderer.py` | **Engine** | 渲染编排、并发、资源、安全 |
| 预览 `mgRenderer.ts` 的 style/motion 解释 | 前端 | **Engine** | 预览一致性；插件只能提供声明/近似策略 |
| 质量（纯白、叠字、样式单调、style 字段） | `quality_agent` | **Engine** | 硬门禁不能由插件决定 |
| 静态 `text_effect_card`（渐变/撕纸/霓虹/glitch PNG） | tool | **Plugin：Text Effect/Asset** | 可选、静态、不写 timeline；由引擎插入图片 |
| 撕纸纹理、纸张素材、字体包 | 无统一实现 | **Plugin：Asset Pack** | 资产与授权由插件声明，engine 校验路径 |
| 3D/粒子/shader/复杂故障 | 无 | **Plugin：Effect Provider** | 可选效果，需要隔离渲染/fidelity |
| 替代渲染器（Blender/WebGL/Canvas） | 无 | **Plugin：Renderer Backend** | 只实现 render 接口，不碰 timeline |
| 视频类型专属文字风格指南 | 部分 category | **Plugin：Prompt Pack / Category** | 文本提示，不生成模板 |
| 固定动画模板/预设关键帧 | `AnimationCatalog` 旧路径 | **不拆、禁止** | 违反“只保留提示词”；只保留为旧兼容，不新增 |

## 4. Engine 对外接口

### 4.1 Python 协议

建议新增一个稳定的门面模块 `clipwright/animation/text_engine.py`（或 service），所有内部/插件调用都走它：

```python
class TextAnimationEngine(Protocol):
    def normalize_style(self, style: dict) -> dict: ...
    def validate_style(self, style: dict) -> list[str]: ...

    def plan(
        self, *, text: str, style: dict, motion: dict,
        width: int, height: int, display_sec: float,
    ) -> "TextPlan": ...

    def render_html(self, plan: "TextPlan", *, fps: float) -> str: ...
    def render_preview(self, plan: "TextPlan", *, t: float) -> "PreviewFrame": ...

    def apply_style(
        self, *, timeline, clip_id: str,
        style: dict, motion: dict, duration_reason: str = "",
    ) -> "ApplyStyleResult": ...

    def capabilities(self) -> "TextCapabilities": ...
```

`TextPlan` 只包含：

- 规范化 style/motion；
- `mg_def`（可渲染 IR）；
- `duration_sec`、layout、fidelity；
- 可选 effect provider 的 `render_hint`。

插件不直接拿 `Timeline`、不直接写 `mg_html`；需要写回时只能调用 `apply_style`。

### 4.2 HTTP 接口（供前端/外部 Agent）

建议 engine 暴露以下端点（或内部 service 等价）：

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/api/text-animation/capabilities` | 基础能力 + 已装插件能力 + fidelity |
| `POST` | `/api/text-animation/normalize-style` | 归一化/校验 style |
| `POST` | `/api/text-animation/plan` | text/style/motion → TextPlan |
| `POST` | `/api/text-animation/render-html` | TextPlan → MG HTML |
| `POST` | `/api/text-animation/preview-frame` | t → 预览绘制指令/近似标记 |
| `POST` | `/api/text-animation/apply-style` | 写回文字 clip |
| `POST` | `/api/text-animation/validate-effect` | 插件/用户 style 是否可渲染 |

要求：

- 响应必须带 `fidelity`（`exact` / `approx` / `unsupported`）；
- 不能返回“成功”但实际忽略字段；
- 插件不可用时返回明确错误码，由 engine 决定是否阻止出片。

### 4.3 前端接口

`mgRenderer.ts` 只消费 engine 的 `TextPlan.preview_ops` 或 `mg_def`，不直接读插件代码。建议：

```ts
type TextPreviewFrame = {
  ops: PreviewOp[];
  fidelity: "exact" | "approx" | "unsupported";
  missing?: string[];
}
```

插件提供的新效果必须同时提供 preview 映射，或声明 `preview: "none"`；engine 在预览上显示“近似/不可预览”，不能静默当普通文字。

## 5. 插件契约

### 5.1 新增贡献类型建议

现有 `ANIMATION` 是“动画定义”，不适合文字提示词。建议新增：

```text
TEXT_PROMPT_PACK   # 纯数据：文字类型语义 + prompt 文本 + 约束
TEXT_EFFECT        # 可选：复杂文字效果 provider（隔离/纯数据）
TEXT_ASSET_PACK    # 可选：纹理/纸张/字体资产声明
```

`RENDERER_BACKEND` 复用；`TOOL` 用于静态卡/独立效果工具；`CLIP_KIND` 不用于文字样式。

> 是否新增 `TEXT_EFFECT` 需要走现有“20 类贡献名单冻结”的契约评审；本 PRD 建议新增，不要塞进 `ANIMATION` 或 `TOOL` 混淆语义。

### 5.2 `TEXT_PROMPT_PACK`

```json
{
  "schema": 1,
  "types": [
    {
      "id": "emphasis_keyword",
      "name": "关键词强调",
      "intent": "...",
      "constraints": ["..."],
      "anti_patterns": ["..."],
      "prompt": "完整 prompt 文本",
      "version": "2026-11-17"
    }
  ]
}
```

规则：

- 只能是**提示词 + 类型语义**，不能带固定关键帧/模板；
- 必须包含现有 7 段（Intent/Semantic constraints/Composition/Motion/Anti-patterns/Example/Style reference）或声明扩展段；
- engine 负责 schema 校验、版本、冲突、卸载；
- 不合法/重复 type 拒绝整包，不做“部分加载”；
- prompt 中的占位符必须可被 engine 白名单替换。

### 5.3 `TEXT_EFFECT`

```python
class TextEffectProvider(Protocol):
    id: str
    version: str

    def capabilities(self) -> dict:
        """{styles, motions, fidelity, preview, permissions, schema_version}"""

    def supports(self, *, style: dict, motion: dict) -> bool: ...

    def build(self, request: "TextEffectRequest") -> "TextEffectResult":
        """返回 MGDef/IR；不得返回任意可执行 HTML/JS。"""

    def preview(self, frame: "TextEffectFrame") -> "PreviewOp | None": ...
```

约束：

- `build` 返回结构化 `mg_def`，由 engine 的 `MGRenderer` 渲染；若必须用 WebGL/Blender，走 `RENDERER_BACKEND` 隔离进程；
- 不允许插件写 timeline、改 clip_id、改 quality、改任务终态；
- 不允许下载/读取任意 URL；资产只能来自声明的 `TEXT_ASSET_PACK` 或白名单路径；
- 卸载后，已有 clip 的 `style/motion` 若不再 supported，engine 必须报 `TEXT_EFFECT_UNAVAILABLE`，不能回退普通白字；
- 必须声明 preview fidelity；无 preview 的效果不能伪装成精确预览。

### 5.4 `TEXT_ASSET_PACK`

```json
{
  "assets": [
    {"id": "paper_torn_01", "kind": "texture", "path": "assets/paper_torn_01.png",
     "license": "CC0", "sha256": "..."}
  ],
  "fonts": [
    {"family": "Source Han Sans", "path": "fonts/...", "license": "OFL"}
  ]
}
```

engine 校验路径、SHA、字体授权；插件不能直接访问任意文件系统。

## 6. 不建议拆分的部分（并说明原因）

| 能力 | 不拆原因 |
|---|---|
| `apply_text_style` 写回 | 必须唯一写入口，否则插件可绕过质量/owner/时间线校验 |
| 质量门禁 | 插件不能决定任务是否 done |
| 预览渲染器核心 | 预览与成片一致性是 engine 的契约 |
| Hyperframes/MOV 合成 | 并发、资源、缓存、超时属于 engine |
| TextAnimationStyle schema | 插件各写一套会导致预览/渲染/质量分裂 |
| 基础 typewriter/word/box/shadow | 所有视频类型共用，拆出去会增加不可控组合 |
| 固定动画模板 | 与用户“只保留提示词、LLM 动态生成”冲突；旧 catalog 仅兼容，不扩展 |
| 字幕带避让/字号下限 | 属于安全/可读性硬约束 |

## 7. 迁移与里程碑

### M1 契约冻结（engine）

- 新增 `TextAnimationEngine` 门面；
- 把 `text_style`、`text_mg`、`text_effect_card` 的公共入口收敛到 engine；
- 定义 `TextPlan`、`TextPreviewFrame`、`fidelity`。

### M2 Prompt Pack 插件化

- `text_specs` 内置类型改为 builtin `TEXT_PROMPT_PACK`；
- 插件可新增类型 prompt；
- 禁止插件提供固定关键帧/模板；
- 冲突：builtin 不可被未签名插件覆盖。

### M3 可选 Effect 插件

- 把 `text_effect_card` 拆成 plugin tool + asset pack；
- 新增 `TEXT_EFFECT` provider 承载撕纸/复杂 glitch/3D；
- 发动机只接受结构化结果，显式 fidelity。

### M4 Renderer Backend / 预览

- 插件可实现隔离 renderer backend；
- preview 必须提供 `PreviewOp`，否则明确标记不可预览；
- engine 统一校验 preview 与 render 的 style/motion 解释一致性。

### M5 E2E

- 装/卸插件各跑一次真实 animation→audio→render；
- 验证插件新增文字类型、卸载后显式失败；
- 验证无模板兜底、无静默白字；
- 抽帧与预览截图对比。

## 8. 验收标准

1. **基础不变**：不装插件时现有 19 条文字样式/动效/渲染结果不变；
2. **Prompt Pack**：插件可新增一个文字类型，LLM 生成成功，质量可通过；卸载后该类型从能力列表消失；
3. **Effect**：插件可新增一种复杂效果，成片有真实效果，预览有对应 fidelity；不支持时显式 `TEXT_EFFECT_UNAVAILABLE`；
4. **隔离**：插件不能写 timeline、不能改 quality、不能执行任意 JS/URL；
5. **无降级**：缺能力/坏输出时任务失败或阻止出片，不回退模板/白字；
6. **可观测**：能力列表、fidelity、错误码、报告都能看到；
7. **E2E**：任务 done，video/audio 702s，抽帧和预览对比通过。

## 9. 风险与开放问题

- 新增 `TEXT_EFFECT` 贡献类型需要冻结名单评审；是否复用 `RENDERER_BACKEND` 需要决定；
- 插件返回 MGDef 是否足够表达 shader/3D，还是必须允许隔离 renderer；
- 复杂效果 preview 的 `approx` 标准需要定义（像素阈值/结构对比）；
- 字体授权与机器级安装（Windows DirectWrite）属于 engine/asset 治理，不能由插件静默安装；
- builtin prompt pack 是否放插件目录：建议“内置插件”同契约，便于卸载测试，但默认随 engine 发布；
- 插件新增 type 与质量门禁的兼容策略需要明确。
