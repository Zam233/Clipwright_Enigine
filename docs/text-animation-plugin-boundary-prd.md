# 文字动画插件边界与 Engine 接口 PRD（v2）

> 状态：草案 v2（按 2026-11-17 新裁定重写）  
> 范围：仅文字动画（不含 MG 图形动画、转场、字幕样式）  
> 关键变化：**typewriter / word_by_word / fade / slide / scale / highlight 等基础动效从 engine 拆出，成为独立插件能力；engine 只保留接口、运行时、布局原语、渲染与校验。**  
> 同时移除旧约束“插件领域只保留提示词”：插件可提供提示词、动效、样式包、资产、渲染后端和预览扩展。

## 1. 结论先行

### 1.1 Engine 只保留什么

- `TextAnimationStyle` / `TextMotion` 的**契约与校验**；
- 文字布局、测量、字幕带避让、字号下限、box/描边/阴影等**渲染原语**；
- `TextAnimationEngine` 门面（plan / render / preview / apply_style / capabilities）；
- `MGRenderer`、Hyperframes、MOV 合成、缓存、并发、安全；
- timeline 写入、质量门禁、任务终态、失败/重试策略；
- 插件注册、能力发现、冲突处理、卸载后的显式失败。

**Engine 不再内置具体 motion 实现。**

### 1.2 插件提供什么

- 文字**动效 provider**：fade / slide / scale / typewriter / word_by_word / highlight / glitch / neon / 3D 等；
- 文字**提示词包**：类型语义、prompt、约束；
- 文字**样式包**：预设 style（允许，但必须显式声明，不作为静默兜底）；
- 文字**资产包**：字体、纸张纹理、贴图；
- 文字**渲染后端**：WebGL/Blender/Canvas 等隔离渲染；
- **静态效果卡**：渐变/撕纸/霓虹/glitch 的 PNG 卡（由 engine 插入图片轨）。

插件**不能**直接写 timeline、不能改质量结论、不能替 engine 决定任务 done。

## 2. 重新评估的依据

旧版 PRD 把基础动效留在 engine，理由是“跨类型共用、保证一致”。新裁定改变了这个取舍：

- 基础动效也需要可替换/可扩展，不应写死在 engine；
- 用户需要插件化试验不同动效实现；
- “插件只保留提示词”不再成立，插件可以承载实现；
- 但**一致性、预览、安全、timeline、质量**仍必须由 engine 收口。

因此采用“**Engine 提供接口 + 插件提供 motion 实现**”，而不是“engine 内置所有基础动效”。

## 3. 能力归属矩阵

| 能力 | 现在实现 | 新归属 | 说明 |
|---|---|---|---|
| `TextAnimationStyle` schema、默认值、校验 | `text_style.py` | **Engine** | 稳定契约；插件可声明 style extension |
| 字体、字号、字距、行高、对齐、字幕带避让 | `text_mg.py` | **Engine 原语** | 画布/可读性/字幕轨耦合 |
| 圆角底板、描边、阴影、发光 | `text_mg.py` | **Engine 原语** | 跨动效复用；插件调用原语 |
| 文字测量、token/字符宽度、布局 | `text_mg.py` | **Engine 原语** | 不用每个插件重复实现 |
| fade_in / fade_out | `text_mg.py` | **`text-motion-core` 插件** | 独立 provider |
| slide_in | `text_mg.py` | **`text-motion-core` 插件** | 独立 provider |
| scale_in | `text_mg.py` | **`text-motion-core` 插件** | 独立 provider |
| typewriter | `text_mg.py` | **`text-motion-core` 插件** | token/时间/光标逻辑归插件 |
| word_by_word | `text_mg.py` | **`text-motion-core` 插件** | 同上 |
| highlight | `text_mg.py` | **`text-motion-core` 插件** | 黄条/文字分离/阴影归插件 |
| glitch / neon | `text_mg.py` | **`text-motion-fx` 插件（可选）** | 也可放 core，但建议独立，便于替换 |
| 3D/粒子/shader 动效 | 无 | **`text-motion-3d` 插件** | 走隔离 renderer backend |
| 文字类型 prompt（emphasis/quote/...） | `prompts/text/*.md` | **`text-prompt-pack` 插件** | 纯 prompt；允许新增类型 |
| 类型默认 style 预设 | `DEFAULT_STYLE`/persona | **`text-style-pack` 插件** | 显式选择；失败不静默套用 |
| 静态 `text_effect_card` | tool | **`text-effect-card` 插件** | PNG/纹理/撕纸；engine 插图片 |
| 字体/纹理资产 | 分散 | **`text-asset-pack` 插件** | 路径、SHA、授权由 engine 校验 |
| Hyperframes/MG HTML 渲染 | `MGRenderer` | **Engine** | 统一渲染运行时 |
| `apply_text_style` 写回 | tool/builder | **Engine API** | 唯一写入口 |
| 预览 `mgRenderer.ts` | 前端 | **Engine** | 消费 MotionPlan/PreviewOps |
| 质量门禁 | `quality_agent` | **Engine** | 插件不能决定 done |
| 固定动画模板/预设 | `AnimationCatalog` 旧路径 | **不用于文字动画** | 新插件可提供显式 style/motion preset，但不得静默兜底 |

## 4. Engine 接口

### 4.1 Python 门面

```python
class TextAnimationEngine(Protocol):
    def capabilities(self) -> TextCapabilities: ...
    def normalize_style(self, style: dict) -> dict: ...
    def validate_style(self, style: dict) -> list[str]: ...

    def plan(
        self, *, text: str, style: dict, motion: dict,
        width: int, height: int, display_sec: float,
        provider_id: str | None = None,
    ) -> TextPlan: ...

    def render_html(self, plan: TextPlan, *, fps: float) -> str: ...
    def render_preview(self, plan: TextPlan, *, t: float) -> PreviewFrame: ...

    def apply_style(
        self, *, timeline, clip_id: str,
        style: dict, motion: dict, duration_reason: str = "",
    ) -> ApplyStyleResult: ...
```

`TextPlan` 是唯一交付物：

```python
@dataclass
class TextPlan:
    provider_id: str
    mg_def: dict          # engine 可渲染 IR
    width: int
    height: int
    duration_sec: float
    fidelity: str         # exact / approx / unsupported
    preview_ops: list     # engine preview 指令
    style_extensions: dict
```

### 4.2 Motion Provider 接口

```python
class TextMotionProvider(Protocol):
    id: str
    version: str

    def capabilities(self) -> MotionCapabilities:
        """motions / required_style_fields / preview / fidelity / schema"""

    def supports(self, *, text: str, style: dict, motion: dict) -> bool: ...

    def plan(self, request: TextMotionRequest) -> MotionPlan:
        """只返回 MGDef/元素/关键帧，不返回任意可执行 HTML/JS。"""

    def preview(self, plan: MotionPlan, t: float) -> PreviewOp | None: ...
```

`TextMotionRequest` 提供 engine 原语：

```python
@dataclass
class TextMotionRequest:
    text: str
    style: dict
    motion: dict
    width: int
    height: int
    duration_sec: float
    primitives: TextPrimitives
    assets: AssetHandle | None
```

`TextPrimitives` 是 engine 提供的安全 API：

- `add_text(content, x, y, style) -> element_id`
- `add_shape(rect/line/ellipse, ...)`
- `add_box(...)`
- `measure_text(...)` / `measure_token(...)`
- `shadow_for(style)` / `stroke_for(style)`
- `safe_area()` / `caption_band()`

插件不得自己计算字幕带、访问文件系统或返回任意 HTML。

### 4.3 HTTP 接口

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/text-animation/capabilities` | 基础能力 + 插件 + provider + fidelity |
| POST | `/api/text-animation/normalize-style` | 归一化/校验 |
| POST | `/api/text-animation/plan` | text/style/motion → TextPlan |
| POST | `/api/text-animation/render-html` | TextPlan → MG HTML |
| POST | `/api/text-animation/preview-frame` | t → PreviewFrame |
| POST | `/api/text-animation/apply-style` | 写回文字 clip |
| POST | `/api/text-animation/validate-effect` | 插件/用户效果是否可渲染 |

错误码至少：

- `TEXT_MOTION_UNAVAILABLE`：没有 provider；
- `TEXT_MOTION_PLAN_INVALID`：provider 返回非法 MGDef；
- `TEXT_STYLE_UNSUPPORTED`：style 字段不被 provider 支持；
- `TEXT_PREVIEW_UNSUPPORTED`：无预览实现或 fidelity unsupported；
- `TEXT_PROVIDER_UNLOADED`：插件被卸载但 clip 仍引用。

## 5. 插件目录与契约

### 5.1 `text-motion-core`（基础动效独立插件）

这是本次裁定的核心：**一个独立的 motion plugin**，默认随 engine 发布并启用，但代码、注册、卸载、版本都在插件层。

```text
plugins/text_motion_core/
  plugin.yaml
  main.py
  providers/
    fade.py
    slide.py
    scale.py
    typewriter.py
    word_by_word.py
    highlight.py
```

manifest：

```yaml
id: text-motion-core
kind: capability
api_version: "2"
contributions:
  - kind: text_motion
    id: fade_in
  - kind: text_motion
    id: fade_out
  - kind: text_motion
    id: slide_in
  - kind: text_motion
    id: scale_in
  - kind: text_motion
    id: typewriter
  - kind: text_motion
    id: word_by_word
  - kind: text_motion
    id: highlight
```

- 每个 motion 是一个独立 provider；
- 打包在一个 plugin 中，保证基础能力一起安装/升级；
- 插件卸载后 `text-motion-core` 能力消失，engine 必须报 `TEXT_MOTION_UNAVAILABLE`，**不得回退 ASS/白字**；
- 旧时间线引用 typewriter 等，若插件不可用，重渲染必须失败并报告，而不是静默变成静态文字。

### 5.2 `text-motion-fx`（可选）

- glitch / neon / 3D / 粒子；
- 独立插件，可按项目启用；
- 可声明 `renderer_backend` 使用 WebGL/Blender；
- 不安装时，引用这些 motion 的 clip 显式失败。

### 5.3 `text-prompt-pack`

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
      "prompt": "..."
    }
  ]
}
```

- 纯 prompt + 类型语义，不包含固定关键帧；
- 可新增/覆盖（覆盖需签名/管理员策略）；
- engine 校验 7 段结构与版本。

### 5.4 `text-style-pack`

- 允许提供 style preset（颜色、字体、底板、阴影、motion 默认参数）；
- 必须作为显式 `style_pack_id` 被选择；
- 不得在 LLM 失败时静默作为兜底；
- engine 负责 schema/安全/预览一致性。

### 5.5 `text-effect-card` / `text-asset-pack`

- `text-effect-card` 提供静态 PNG 卡（渐变/撕纸/霓虹/glitch）；
- `text-asset-pack` 声明字体/纹理/贴图（path、sha256、license）；
- engine 校验路径、SHA、字体授权；插件不能任意读盘。

### 5.6 `renderer_backend`

- 复用现有 `RENDERER_BACKEND`；
- 只接收 engine 的 `TextPlan`/帧请求，返回图像/视频；
- 隔离进程；不得访问 timeline/quality；
- 无 preview 能力必须声明。

## 6. 预览与质量

- 每个 provider 必须返回 `preview` 或声明 `preview: "none"`；
- engine 生成 `PreviewFrame`，前端 `mgRenderer.ts` 只消费 `PreviewOps`/`mg_def`，不加载插件代码；
- `fidelity` 三态：`exact` / `approx` / `unsupported`；
- 质量门禁由 engine 执行：
  - provider 未声明支持却返回效果 → 拒绝；
  - preview unsupported 仍出现在成片 → 质量 error；
  - 同一 clip 多 provider 冲突 → 拒绝，不自动择优；
  - 任何回退/不可用必须记录，不允许白字兜底。

## 7. 迁移步骤

### M1 接口冻结

- 新增 `TextAnimationEngine` / `TextMotionProvider` / `TextPrimitives`；
- 把 `text_style`、`text_mg` 的公共入口收敛；
- 建立能力注册表（`TEXT_MOTION`）。

### M2 基础动效迁出

- 新建 `text-motion-core` 插件，把 fade/slide/scale/typewriter/word_by_word/highlight 从 `text_mg` 移到 provider；
- engine 的 `text_mg` 只保留布局、box、shadow 等原语；
- 默认启用该插件，行为与现有 19 条文字动画一致；
- 增加“禁用插件 → 显式失败”回归测试。

### M3 prompt/style/资产插件

- `text-prompt-pack`、`text-style-pack`、`text-asset-pack`；
- `text-effect-card` 拆为插件；
- 允许 style preset，但禁止静默兜底。

### M4 复杂动效与 renderer backend

- `text-motion-fx`（glitch/neon/3D）；
- 隔离 renderer；
- preview `approx` 标准。

### M5 E2E

- 不装插件：基础文字动画全部 `TEXT_MOTION_UNAVAILABLE`，不产生白字；
- 装 core：19 条文字动画、video/audio 702s；
- 装 fx：glitch/neon/3D 真实可见；
- 卸载插件后重渲染：明确失败；
- 抽帧 + 预览对比。

## 8. 验收标准

1. **Engine 不内置 motion 实现**：`text_mg` 不再包含 fade/slide/scale/typewriter/word/highlight 的语义分支，只保留原语；
2. **`text-motion-core` 可独立启停**：启用时现有行为不变，禁用时引用这些 motion 的 clip 报 `TEXT_MOTION_UNAVAILABLE`；
3. **`apply_text_style` 全部文字**：所有文字 clip 必须经 engine apply；插件不可绕过；
4. **提示词/样式/资产插件**：可新增类型、style pack、字体/纹理；
5. **复杂效果**：glitch/neon/3D 由插件提供，preview fidelity 明确；
6. **无静默回退**：不支持的 motion/style 不得变白字/ASS/模板，必须失败并报告；
7. **E2E**：任务 done，video/audio 均为 702s，文字动画全部可见，预览与成片对比通过；
8. **可卸载**：插件卸载后能力列表、错误码、已有 clip 行为一致。

## 9. 风险与开放问题

- `TEXT_MOTION` 是新增贡献类型，需要冻结名单评审；是否复用 `ANIMATION` 需决定（建议不要，语义不同）；
- engine 原语边界：`TextPrimitives` 是否允许插件调用 `add_text` 任意坐标？建议 engine 二次校验安全区；
- `text-motion-core` 是否默认随 engine 发布但以插件形式安装（建议是，便于禁用测试）；
- 旧时间线中 `renderer=mg_hyperframes` 的 clip 若插件卸载，是否允许“只读预览/禁止重渲染”；
- style preset 的显式选择方式（clip 字段/项目设置）需要定义；
- 3D/WebGL renderer 的 license、GPU 依赖和预览近似标准；
- 质量门禁如何判定 `preview: none` 的效果是否可接受（建议 error，不放行）。
