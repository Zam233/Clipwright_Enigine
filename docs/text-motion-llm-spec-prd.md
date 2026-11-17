# LLM MotionSpec 文字动效 PRD

> 状态：方案评审稿；下文标为“目标”的条款均未因写入 PRD 而视为已实现
> 修订说明：独立审阅指出的 P0/P1/P2 阻塞以第 25–28 节的收口契约和验收矩阵为准；前文示例是完整设计空间，不是当前代码能力表。
> 范围：仅文字动画（不含 MG 图形动画、转场、字幕样式）  
> 目标分支：`feat/zam_text_motion_llm_spec`  
> 关联：`docs/text-animation-plugin-boundary-prd.md`、`docs/text-animation-plugin-boundary-coding-plan.md`

## 1. 背景

当前文字动画链路是：

```text
text_generation（LLM 决定文字/类型/style/motion 类型）
  → text-motion-core（代码按 motion 分支生成固定 keyframes）
  → TextAnimationEngine
  → MGRenderer / Hyperframes
```

问题：

1. `fade/slide/scale/typewriter/highlight/glitch/neon` 的关键帧都是插件代码固定；
2. LLM 只能选 motion 类型和少量参数，不能真正编排复杂动作；
3. 复杂动画需要 3D、路径、弹簧、分层、遮罩、相机等能力，固定分支无法覆盖；
4. 如果让 LLM 在 render 时生成，会不可复现、不可控、慢且破坏预览一致性。

本 PRD 定义 **LLM 在 Animation Agent 阶段生成声明式 MotionSpec** 的完整方案。

## 2. 目标与非目标

### 2.1 目标

- LLM 在 **animation agent 阶段**生成 MotionSpec；当前先经 `text_generation` 再经 author，调用次数及重试见 §27；
- `MotionSpec` 写入文字 clip；目标是 render 阶段只验证并按版本编译 spec、**绝不调用 LLM**（目前只读取持久化产物）；
- 支持比现有模板丰富得多的动画：
  - 线性/贝塞尔/阶跃/弹簧/保持；
  - 2D/3D transform；
  - 路径运动；
  - 逐字/逐词/逐行 stagger；
  - 遮罩/揭示；
  - 相机；
  - 模糊/发光/色差等效果；
- engine 统一校验、编译、预览、渲染；
- 失败显式，不回退模板/白字。

### 2.2 非目标

- 不允许 LLM 输出 HTML/JS/CSS/Shader 源码；
- 不允许 render 时调用 LLM；
- 不允许插件直接执行任意代码；
- 不把 MotionSpec 做成无限参数集合；采用能力声明 + 白名单。

## 3. 总体架构

```text
AnimationAgent
  ├─ text_generation：内容 + 类型 + style + motion intent
  ├─ text-motion-llm.author(MotionAuthorRequest)
  │     └─ LLM → MotionSpec JSON
  ├─ MotionSpecValidator
  │     ├─ schema
  │     ├─ capability
  │     ├─ time/property/path
  │     └─ quality floor
  └─ 写回 clip.metadata.motion_spec

TextAnimationEngine
  ├─ compile(MotionSpec, CompileContext) → TextPlan / MGDef
  ├─ validate compiled MGDef
  └─ render_html / preview_ops

MGRenderer / Hyperframes / Preview
  └─ 只消费编译产物，不感知 LLM
```

### 3.1 关键不变量

1. **render 无 LLM**：渲染进程不 import author provider；
2. **spec 持久化**：同一 clip 重渲染必须使用同一份 spec/hash；
3. **单一编译源**：预览与成片必须使用同一个 compiler；
4. **失败显式**：无 spec/非法 spec/能力不支持都报错，不回退固定 motion；
5. **LLM 不碰时间线**：只产出 MotionSpec，clip 写入由 engine 完成。

## 4. MotionSpec v1 Schema

### 4.1 顶层

```json
{
  "schema": "motion_spec/1",
  "spec_id": "ms_01",
  "duration_sec": 3.2,
  "canvas": {"width": 1920, "height": 1080, "fps": 30},
  "requires": ["text", "transform3d", "blur", "path", "stagger", "camera"],
  "layers": [],
  "camera": null,
  "meta": {
    "provider": "text-motion-llm",
    "model": "gpt-...",
    "prompt_version": "2026-11-18",
    "seed": 0
  }
}
```

字段：

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `schema` | string | 是 | 固定 `motion_spec/1` |
| `spec_id` | string | 是 | 本次 spec 唯一 id |
| `duration_sec` | number | 是 | 必须等于 clip duration |
| `canvas` | object | 是 | 画布尺寸/fps |
| `requires` | string[] | 是 | 所需能力，engine 校验 |
| `layers` | Layer[] | 是 | 1–8 层 |
| `camera` | Camera? | 否 | 全局相机 |
| `meta` | object | 否 | 生成来源/模型/seed |

### 4.2 Layer

```json
{
  "id": "title",
  "type": "text",
  "parent": null,
  "z_index": 2,
  "content": "技术中立",
  "layout": {
    "x": 0.5,
    "y": 0.46,
    "anchor": "center",
    "max_width": 0.82,
    "safe_area": true
  },
  "style": {},
  "text_motion": {},
  "keyframes": [],
  "tracks": [],
  "motion_path": null,
  "effects": [],
  "blend_mode": "normal"
}
```

- `type`: `text | shape | line | image | group | mask`
- `parent`: 组动画；子层继承父变换；
- `z_index`: 渲染顺序；
- `content`: 仅 text 层；
- `layout`: 支持 0–1 归一化或 px；
- `style`: 静态样式；
- `keyframes`: 多属性关键帧；
- `tracks`: 独立属性通道；
- `motion_path`: 空间路径；
- `effects`: 可选效果。

### 4.3 Text motion

```json
"text_motion": {
  "unit": "char",
  "order": "forward",
  "stagger": 0.045,
  "unit_duration": 0.32,
  "seed": 0,
  "cursor": false
}
```

- `unit`: `char | word | line | range`
- `order`: `forward | reverse | center | random`
- `stagger`: 单元间隔秒
- `unit_duration`: 每单元关键帧压缩到的时长
- `seed`: random 的确定性种子
- `range`: `{start,end}` 指定部分文字

### 4.4 Style

```json
"style": {
  "font": "sans-serif",
  "font_size": 112,
  "font_weight": 700,
  "font_italic": false,
  "letter_spacing": 10,
  "line_height": 1.2,
  "fill": "#ffffff",
  "gradient_stops": null,
  "stroke_width": 3,
  "stroke_color": "#000000",
  "shadow": [
    {"x": 0, "y": 5, "blur": 12, "color": "#000000b3"}
  ],
  "glow": {"color": "#00e5ff", "blur": 18},
  "box": {
    "color": "rgba(18,18,26,0.42)",
    "radius": 8,
    "padding_x": 44,
    "padding_y": 26
  }
}
```

`gradient_stops` 示例：

```json
[
  {"offset": 0, "color": "#4f8cff"},
  {"offset": 1, "color": "#b46bff"}
]
```

### 4.5 Keyframe

```json
{
  "time": 0.45,
  "opacity": 1,
  "translate_x": 0,
  "translate_y": 0,
  "translate_z": 0,
  "scale_x": 1,
  "scale_y": 1,
  "rotate": 0,
  "rotate_x": 0,
  "rotate_y": 0,
  "rotate_z": 0,
  "skew_x": 0,
  "skew_y": 0,
  "perspective": 800,
  "transform_origin": "50% 50%",
  "font_size": 112,
  "letter_spacing": 10,
  "line_height": 1.2,
  "color": "#ffffff",
  "stroke_width": 3,
  "stroke_color": "#000000",
  "shadow": [],
  "blur": 0,
  "clip_inset": [0, 0, 0, 0],
  "mask": null,
  "easing": {"type": "cubic-bezier", "x1": 0.16, "y1": 1, "x2": 0.3, "y2": 1}
}
```

规则：

- `time` 升序，范围 `[0, duration]`；
- `easing` 作用于**当前 keyframe 到下一个 keyframe**；
- 最后一个 keyframe 的 easing 忽略；
- 未出现的属性沿用上一帧；
- 禁止 `NaN/Infinity`；
- 单层最多 16 个 keyframe。

## 5. 运动形式（Interpolation）

keyframe 的 `easing` 支持以下类型。

### 5.1 线性

```json
{"type": "linear"}
```

等速，用于机械滑动、线性推进。

### 5.2 命名缓动

```json
{"type": "named", "name": "ease-out-back"}
```

白名单：

```text
linear
ease
ease-in
ease-out
ease-in-out
ease-in-cubic
ease-out-cubic
ease-in-out-cubic
ease-in-expo
ease-out-expo
ease-in-out-expo
ease-out-back
ease-in-back
ease-out-elastic
ease-out-bounce
```

### 5.3 三次贝塞尔

```json
{
  "type": "cubic-bezier",
  "x1": 0.16,
  "y1": 1.0,
  "x2": 0.3,
  "y2": 1.0
}
```

约束：

- `0 <= x1,x2 <= 1`；
- `y1/y2` 可为负/大于 1，用于 overshoot；
- 编译到 MG CSS `cubic-bezier(...)`。

### 5.4 阶跃

```json
{"type": "steps", "count": 6, "position": "end"}
```

- `count` 1–60；
- `position`: `start | end | both`；
- 用于逐格闪烁、机械跳变、计数。

### 5.5 保持

```json
{"type": "hold"}
```

到下一个 keyframe 前完全不变；用于硬切、逐帧定格。

### 5.6 弹簧

```json
{
  "type": "spring",
  "stiffness": 180,
  "damping": 18,
  "mass": 1,
  "velocity": 0
}
```

- 用于自然入场/回弹；
- 编译为采样 keyframes（至少 60Hz）；
- 限制 `stiffness 20–1000`、`damping 2–100`、`mass 0.2–10`；
- 预览与成片均使用采样结果，保证一致。

### 5.7 每段独立缓动

同一层可混合：

```json
"keyframes": [
  {"time": 0.0, "opacity": 0, "easing": {"type": "cubic-bezier", "x1": 0.2, "y1": 0.9, "x2": 0.3, "y2": 1}},
  {"time": 0.5, "opacity": 1, "easing": {"type": "spring", "stiffness": 160, "damping": 16}},
  {"time": 1.2, "opacity": 1, "easing": {"type": "hold"}}
]
```

## 6. 空间运动形式（Path）

`easing` 只控制时间；`motion_path` 控制空间轨迹。

```json
"motion_path": {
  "type": "cubic-bezier",
  "points": [
    {"x": 0.2, "y": 0.7},
    {"x": 0.35, "y": 0.3},
    {"x": 0.65, "y": 0.3},
    {"x": 0.8, "y": 0.6}
  ],
  "orient": true,
  "progress": [
    {"time": 0.0, "value": 0.0},
    {"time": 1.0, "value": 1.0, "easing": {"type": "ease-in-out"}}
  ]
}
```

支持：

| 类型 | 语义 |
|---|---|
| `line` | 两点直线 |
| `polyline` | 多段折线 |
| `cubic-bezier` | 三次贝塞尔轨迹 |
| `arc` | 圆弧 |
| `circle` | 圆周，可设起点/方向 |

规则：

- 坐标归一化到画布；
- `progress` 必须 0→1；
- `orient` 让文字朝向切线；
- 编译为采样 translate/rotate keyframes；
- 不能穿出安全区（validator 采样检测）。

### 6.1 时间 x 空间组合

```json
"motion_path": {
  "type": "cubic-bezier",
  "points": [...],
  "progress": [
    {"time": 0, "value": 0, "easing": {"type": "ease-out-cubic"}},
    {"time": 0.8, "value": 1, "easing": {"type": "hold"}}
  ]
}
```

## 7. Tracks（独立属性通道）

当不同属性需要不同 easing 时使用：

```json
"tracks": [
  {
    "property": "rotate_z",
    "segments": [
      {"start": 0.0, "end": 0.6, "from": -8, "to": 0,
       "easing": {"type": "spring", "stiffness": 140, "damping": 14}}
    ]
  },
  {
    "property": "opacity",
    "segments": [
      {"start": 0.0, "end": 0.25, "from": 0, "to": 1,
       "easing": {"type": "linear"}}
    ]
  }
]
```

编译策略：

1. 有 `tracks` 时，按 `max(60, canvas.fps * 2)` 采样；
2. 合并 `keyframes` 和 `tracks`；
3. 输出统一 MGDef keyframes；
4. preview/render 共用采样结果；
5. `fidelity` 标记为 `exact`（因为二者使用同一采样结果）。

## 8. Camera

```json
"camera": {
  "keyframes": [
    {"time": 0.0, "scale": 1.0, "x": 0, "y": 0},
    {"time": 1.2, "scale": 1.04, "x": 0, "y": -8,
     "easing": {"type": "ease-in-out-cubic"}},
    {"time": 3.2, "scale": 1.04, "x": 0, "y": -8}
  ]
}
```

- 全片/片段级缩放、平移、旋转；
- 应用于所有层；
- 限制最大 zoom 1.15，避免裁掉安全区。

## 9. Effects

### 9.1 核心效果

```json
"effects": [
  {"type": "blur", "radius": 8},
  {"type": "glow", "color": "#00e5ff", "blur": 18},
  {"type": "rgb_split", "amount": 3},
  {"type": "shadow", "x": 0, "y": 5, "blur": 12, "color": "#000000b3"}
]
```

### 9.2 插件效果

```json
{"type": "shader", "plugin": "text-motion-fx", "id": "ripple", "params": {}}
```

规则：

- 插件效果必须在 `requires` 声明；
- 未安装/不支持 → `MOTION_EFFECT_UNAVAILABLE`；
- 不允许内联 shader 源码；
- 不允许外链资源。

## 10. 插件接口

### 10.1 Author

```python
class TextMotionAuthor(Protocol):
    id: str
    version: str

    async def author(self, request: MotionAuthorRequest) -> MotionSpec: ...
```

`MotionAuthorRequest`：

```python
@dataclass
class MotionAuthorRequest:
    text: str
    style: dict
    intent: dict
    width: int
    height: int
    duration_sec: float
    allowed_features: set[str]
    caption_band_px: int
    seed: int
    capabilities: dict
```

### 10.2 Compile

```python
class TextMotionCompiler(Protocol):
    def compile(self, spec: MotionSpec, ctx: CompileContext) -> TextPlan: ...
```

engine 提供唯一 compiler；插件不直接生成 MG HTML。

### 10.3 贡献声明

```yaml
id: text-motion-llm
kind: capability
api_version: "2"
contributions:
  - { kind: text_motion, id: llm_motion }
```

manifest 的 `meta` 声明：

```json
{
  "features": ["text", "transform", "transform3d", "blur", "path", "spring", "stagger"],
  "fidelity": "exact"
}
```

## 11. Animation Agent 流程

```text
1. text_generation 产出 text_animation
2. 若 motion 需要 LLM authoring：
   req = MotionAuthorRequest(...)
   spec = await provider.author(req)
3. MotionSpecValidator.validate(spec)
4. 失败：最多 1 次 repair prompt；仍失败 → 记录 MOTION_SPEC_INVALID，clip 失败
5. 写回：
   metadata.motion_spec
   metadata.motion_spec_hash
   metadata.text_provider_id
   metadata.motion_author
   metadata.fidelity
6. TextAnimationEngine.compile(spec)
7. clip 入库
```

**render 阶段：**

```python
spec = clip.metadata.get("motion_spec")
if not spec:
    raise TextMotionSpecMissing(...)
plan = compiler.compile(spec, ctx)
```

不调用 author。

## 12. 持久化与审计

```json
{
  "text_provider_id": "text-motion-llm",
  "motion_spec_version": "1.0",
  "motion_spec": { "...": "..." },
  "motion_spec_hash": "sha256:...",
  "motion_author": {
    "provider": "text-motion-llm",
    "model": "gpt-...",
    "prompt_version": "2026-11-18",
    "attempt": 1,
    "seed": 0
  },
  "fidelity": "exact"
}
```

- 重渲染只读 spec；
- 修改 style 默认保留 spec；
- 用户显式“重新生成 motion”才重新 author；
- spec hash 进 cache key。

## 13. 校验

### 13.1 Schema

- JSON Schema/Pydantic；
- 拒绝未知顶层字段（扩展放 `ext`）；
- 拒绝 NaN/Infinity；
- 时间升序、边界；
- 路径坐标/控制点；
- easing 参数范围。

### 13.2 Capability

- `requires` 必须全部可用；
- 3D 需要 `transform3d`；
- spring 需要 `spring`；
- path 需要 `path`；
- shader/particle 需要插件。

### 13.3 安全

- 禁止 `http(s):`、`file:`、`..`；
- 禁止 HTML/JS/CSS 字符串；
- 禁止任意代码/表达式；
- 限制文本长度、层数、关键帧数、路径采样点。

### 13.4 质量

- 主元素运动覆盖 ≥45%；
- 位移/缩放/旋转至少一种可观测变化；
- 入场 <1.2s；
- 末段静止 ≥0.5s；
- 不侵入字幕带；
- 不把文字完全移出安全区。

失败码：

```text
MOTION_SPEC_INVALID
MOTION_SPEC_UNSUPPORTED
MOTION_SPEC_WEAK
MOTION_SPEC_MISSING
MOTION_EFFECT_UNAVAILABLE
```

## 14. 编译规则

1. 校验通过后按 `duration_sec` 归一化；
2. `keyframes` + `tracks` 合并；
3. 不同 easing 的通道按 60Hz 或 fps*2 采样；
4. spring/path 采样为离散 keyframes；
5. 生成 MGDef：
   - 文本层；
   - 组变换；
   - shape/line；
   - camera；
6. 生成 `preview_ops`；
7. 校验 MGDef；
8. 写 `fidelity`。

## 15. 预览

- 前端只消费 `TextPlan`/`MGDef`；
- 与成片共用 compiler；
- `fidelity`：
  - `exact`：linear/cubic-bezier/steps/hold（CSS 支持）；
  - `exact`（采样后）：spring/path（同一采样）；
  - `approx`：插件 shader/3D；
  - `unsupported`：明确提示，不静默画白字。
- `preview_ops` 包含 `kind`、`fidelity`、`reason`。

## 16. 失败与降级

- LLM 输出不合法：1 次 repair；
- 仍失败：clip 失败，任务由主 Agent 重派；
- 无 provider：`MOTION_SPEC_MISSING`；
- 能力不支持：`MOTION_SPEC_UNSUPPORTED`；
- **禁止**：

```text
静默套 fade
静默删元素
静默把 3D 降成 2D 不标记
render 时再次问 LLM
```

## 17. 性能

- author 每段文字一次，动画 agent 阶段串行/有限并发；
- 按 `text + style + intent + model + prompt_version` 缓存；
- spec 体积上限 64KB；
- 采样最多 240Hz；
- 编译结果可缓存。

## 18. 迁移

### M1：Schema/Compiler

- 新增 MotionSpec schema；
- 新增 compiler；
- 不改现有行为。

### M2：text-motion-llm

- 新增 provider；
- `author` 只生成 spec；
- `compile` 由 engine 完成。

### M3：Agent 接入

- 生成阶段调用 author；
- 写回 clip；
- feature flag。

### M4：预览/质量

- preview 消费 spec；
- quality 检查；
- 失败码。

### M5：清理

- 旧固定 motion 作为 legacy provider，不再用于新 clip；
- 无 spec 的旧 clip 只读兼容；
- E2E、截图、PR。

## 19. 验收

- [ ] render 进程无 LLM 调用；
- [ ] LLM spec 可复现；
- [ ] linear/cubic-bezier/steps/hold/spring/path 均能编译；
- [ ] 3D/相机/遮罩/stagger 生效；
- [ ] 预览与成片一致；
- [ ] 非法 spec 显式失败；
- [ ] 插件禁用后明确 `MOTION_SPEC_MISSING`；
- [ ] 702s E2E 成片通过；
- [ ] 报告含 model/prompt/spec hash。

## 20. 风险

- LLM JSON 复杂度高 → schema 分阶段/能力协商；
- 参数爆炸 → 只开放白名单；
- spring/path 采样导致体积大 → 限层/帧/采样点；
- 预览与 3D 差异 → fidelity；
- 成本延迟 → 生成阶段一次 + 缓存；
- 不可复现 → spec/hash/model/prompt 全持久化。


## 21. 与现有文字动画插件的补充契约

### 21.1 `apply_text_style`

`apply_text_style` 仍然只负责“改已存在文字 clip 的样式/参数”，不直接生成 MotionSpec：

- 只改 `style` 且已有 `motion_spec`：**默认保留原 spec**，不重新调 LLM；
- 只改 `style` 且没有 spec：走当前 provider 或显式 `MOTION_SPEC_MISSING`；
- 显式 `regenerate_motion=true`：由 Animation Agent 重新 author，工具本身不调 LLM；
- `motion` 参数只能选 intent/类型，不得直接塞非法 keyframes；
- 写入前必须校验 `motion_spec_hash`；
- 工具不直接写 timeline，统一通过 engine。

### 21.2 Prompt 契约

`text-motion-llm` 的系统提示词必须包含：

- MotionSpec JSON Schema；
- 可用 capabilities；
- 画布/时长/字幕带/安全区；
- 文字内容必须原样，不得改字；
- 只允许 schema 白名单字段；
- 必须给出至少一个主运动；
- 不要生成 HTML/JS/CSS；
- 场景上下文（见 21.4）。

结构化输出：

```text
response_format: json_schema
schema: MotionSpec
max_output_tokens: 4096
```

失败修复：

1. 第一次非法 → 带 validator 错误 JSON 重试一次；
2. 仍非法 → `MOTION_SPEC_INVALID`；
3. 不允许把 LLM 错误转成固定 motion。

### 21.3 style / asset / prompt pack

- `text-prompt-pack` 提供类型语义与 prompt，不提供 motion 关键帧；
- `text-style-pack` 提供默认 style，不得覆盖显式 MotionSpec；
- `text-asset-pack` 提供字体/纹理/贴图，MotionSpec 只能引用 `asset_id`，不能带路径；
- 渲染时由 engine 将 `asset_id` 解析为受控资源；
- 插件包缺失 → `MOTION_ASSET_UNAVAILABLE`，不得静默忽略。

### 21.4 场景上下文与一致性

`MotionAuthorRequest` 增加：

```python
scene_context: dict
recent_motions: list[dict]
project_style: dict
```

- 同一场景连续文字不应重复相同 motion；
- 不得读取或修改其他 clip 的 spec；
- 同一项目 motion 风格由 prompt pack / project_style 约束；
- 只影响生成，不影响已有 spec。

### 21.5 可观测性与成本

记录：

- model、prompt_version、token 数、耗时；
- spec_hash、重试次数、失败码；
- 作者版本；
- 质量评分；
- 缓存命中。

## 22. 测试矩阵

| 层级 | 测试 | 验收 |
|---|---|---|
| Schema | MotionSpec 合法/非法/未知字段 | 非法显式失败 |
| Interpolation | linear/bezier/steps/hold/spring | 编译结果可预测 |
| Path | line/cubic/arc/circle | 采样在安全区 |
| Tracks | 多属性独立 easing | 采样一致 |
| Compile | MotionSpec → MGDef | 预览/渲染同源 |
| Author | LLM 合法/一次修复/失败 | 无模板回退 |
| Persistence | spec/hash/model 写回 | 重渲染不变 |
| Render | 无 LLM 调用 | 渲染进程不 import author |
| Preview | exact/approx/unsupported | 不静默白字 |
| Packs | style/asset 缺失 | 明确错误 |
| E2E | 生成→编译→渲染→抽帧 | 702s 成片 |

## 23. 版本与兼容

- `motion_spec/1` 主版本不兼容变更必须升 `motion_spec/2`；
- 新增可选字段保持向后兼容；
- 未知字段拒绝，扩展放 `ext`；
- 旧 clip 无 spec：只读 legacy 或显式缺 spec；
- compiler 版本写入 metadata；
- 插件卸载后已有 spec 不能静默重解释。

## 24. 开放问题

- spring 采样率是固定 120Hz 还是按 fps；
- 3D 深度/透视是否需要独立的 camera-aware compile；
- 路径越界是自动夹取还是判非法；
- LLM 生成 motion 的质量评分阈值；
- prompt pack 与 motion author 的职责是否合并；
- 是否需要项目级 motion 风格记忆。

## 25. 修订说明：现状、冻结子集与非目标

本节及第 26–28 节约束前述完整设计空间；原有示例（含 `requires` 列表、插件 manifest 和 `exact`）均为**目标示例**，不能用作当前支持证明。代码静态存在不等于端到端验收通过。

| 范围 | 当前代码已实现 | PRD 目标 / 尚未实现 |
|---|---|---|
| Schema/校验 | Pydantic 顶层、Layer、Easing 与部分时间/属性检查 | `tracks/effects/camera/layout/style/progress` 仍为宽松 dict；未知嵌套字段、正文与 clip 一致性、真实变化覆盖率、末段静止、安全区采样与资源约束尚未闭合 |
| 编译 | text/shape/line/image、部分 camera/stagger 与 blur/glow；基础 easing、circle/折线近似 | group 当前被丢弃，parent 不继承；camera 不处理 rotate/easing；line/range、order/seed/unit_duration 未兑现；贝塞尔路径/arc 退化为线段、orient 未用；spring 采样走线性；未知 named easing 退线性，dict easing 的参数读取不正确 |
| MGDef/预览 | MGRenderer 可输出部分 3D/blur/clip CSS；compiler 返回 MGDef | MG validator 的属性白名单缺 3D/clip/mask，且每元素要求至少两帧；compiler 不调用它而恒标 `exact`；HTML 能输出不代表 MGDef 已统一校验或 Canvas 可等价绘制 |
| Agent/持久化 | author 最多两次 JSON 解析；Agent 写 spec/hash 并调用 engine；render 不直接导入 author | 没有 structured `json_schema`、能力 prompt/错误反馈修复、完整生成元数据；Agent 不传 validator capabilities；render 只读 mg_html/mg_def，不核 spec/hash/版本或重编译；quality 仅对已有 spec 报 issue |
| 前端/接口 | 已有 mg_def Canvas 预览与通用编辑底座 | MotionSpec 编辑和 validate-spec/author/compile API 均待新增；前端可能普通文字回退，不能宣称一致 |

**冻结的 v1 可执行子集是准入门槛，不是对当前实现的背书。** 在双方 schema、validator、compiler、MG validator 和前端能力表逐项对齐并有测试前，仅将已验证的 text 单层、显式静态 style/layout 字段、2D 数值 transform/opacity、严格命名白名单及可验证的至少两帧线性插值列入候选。候选也必须先通过 MG validator 和 Canvas 原语对账才可启用。`spring`、复杂 path（含 arc、Bezier、orient）、group/parent、camera、3D、mask/clip、tracks、line/range stagger、插件效果和外部 image 均为**尚未实现的目标能力**；未对齐的已有编译分支不进入公开能力声明。不得偷偷忽略、退线性或返回 `exact`。扩展能力按单项 schema→validator→compiler→MG validator→前端→抽帧测试放行，不因后端 HTML 支持而自动开放。旧 fixed motion/ASS 只允许按来源/版本识别为 legacy clip 后只读兼容；新 MotionSpec clip 缺失/非法 spec 不准落入 legacy、默认 `fade_in` 或白字路径。

## 26. 修订契约：Schema、校验与编译

1. **唯一能力表**（目标）：宿主按 compiler 版本发布 feature ID、字段形状/范围、依赖、MG 属性及前端可绘制原语；`requires` 必填、去重、精确匹配，且与实际使用字段双向比对，隐式能力也须推导。Agent 在调用 validator 时**必须传入非空的实际能力集合**，不能以 `None`/空集合跳过检查；author request 的 `allowed_features`、`capabilities` 和 seed 从同一快照构造。当前 Agent 未传 capabilities、manifest 未列 features，`allowed_features` 默认空，均是未完成项。没有协商结果即拒绝生成/保存，不猜能力。
2. **结构约束**（目标）：顶层和每个嵌套对象（tracks/segments/effects/camera/layout/style/path/progress）均有关闭未知字段的版本化 schema；字段无实现即拒绝，不允许宽松 dict 直通 compiler。`spec_id`、`requires` 必填；duration/canvas 与 clip 对齐，text layer `content` 与 clip 正文一致。对位置/采样后路径包围盒、字幕带及资源引用做校验。质量闸基于采样后的实际位移/缩放/旋转变化时段计算主元素 ≥45% 覆盖、入场 <1.2s、末段静止 ≥0.5s；弱动效的 warning 与 hard fail 由明确的错误等级/可保存政策区分。现有“最后带运动属性的关键帧时间”与低于 0.35 仅警告，不能充当此验收。
3. **插值语义**（目标）：§5 命名清单只有在 `_NAMED` 与前端绘制/采样同时支持时才放行；expo/elastic/bounce 等当前未在 `_NAMED`，未知名必须拒绝。dict easing 必须以键读取参数并测试边界；spring 实现确定性积分/采样后才放行，不得经无 spring 分支的 `easing_progress` 退线性。§6 的 Bézier/arc 必须真实曲线采样，orient 用切线算角度，安全区按采样点及文字外框检测，否则拒绝该类型。
4. **组合与产物**（目标）：为 tracks 与 keyframes 同属性冲突定义唯一规则：同一属性同一时间窗不允许双重驱动，校验拒绝并定位 layer/property/time；不同属性按合并采样。group/parent、camera rotate/easing、stagger unit/order/seed/unit_duration、mask/3D/effects 均需确定性变换语义和属性白名单；未实现时显式 `MOTION_SPEC_UNSUPPORTED`。编译后**调用统一 MGDef validator**，对齐 3D/clip/mask 属性与最少帧约束；验证失败不持久化，不以 MGRenderer 能输出 HTML 代替校验。§7 的固定 `exact`、§15 的“同采样即 exact”均撤销：后端只声明编译原语/提示，fidelity 由前端实际可绘制原语与 MGDef/easing 所属起始帧逐项比对；默认 `approx`，未知/缺 ops/无法绘制为 `unsupported`，`exact` 仅经实测像素和时序阈值证明（常规 UI 仍提示近似预览）。

## 27. 修订契约：权威、任务、插件与 UX

**权威与写回（目标，当前未实现）：** `motion_spec` 是可编辑唯一权威，`motion_spec_hash` 是对规范化 spec（规定排序/数字精度/编码）的 SHA-256，`motion_spec_version` 与 `compiler_version` 一并入缓存键；`mg_def`、`mg_html`、`preview_ops`、fidelity 是与该 hash/版本绑定的派生产物，不允许独立改写。生成/编辑采用草稿→验证→编译→MG 验证→单次事务/条件版本替换 spec、hash、版本、产物与 provenance；失败保留旧四元组及当前可用画面，禁止半写入。render 必须读取 spec 校验 hash/版本及产物绑定；缺/旧产物按**固定版本宿主 compiler**确定性重编译并校验，不能重 author；若 compiler 版本不可用则显式失败，不能拿旧产物冒充。现有 render 只读 `mg_html/mg_def`、`apply_style` 重编译不核/更新 hash 且覆盖 `text_provider_id`、`apply_text_style` 可改 duration 不重 author，全部是代码缺口。文字、样式、时长的变更必须同步更新 layer.content/style/duration 或拒绝并提示需重新生成；样式优先级规定为“显式 spec layer.style > 样式包默认值”，clip.text 不得暗中覆盖 spec；任何重编译保持来源 ID 与审计记录。

**Author/Agent（目标，当前未实现）：** 现有链路先由 `text_generation` 调一次 LLM，再由 author 生成（至多两次 JSON 解析），Agent 外层至多三轮；因此 §2/§17 的“每段只调用一次”不成立。目标将两段调用及最大重试、300 秒级单次等待/超时、token/费用上限记录为任务预算；不把全链路成本算作一次调用。author 使用受约束 `json_schema` 输出与真实字段/能力白名单 prompt（含场景、画布、安全区、原文），第一次 validator 失败只允许**带定位错误 JSON 的一次修复**，记录 model、prompt_version、author/plugin version、attempt、seed、tokens、latency、失败原因；JSON parse 成功不等于校验成功。Agent 外层重试需总预算/幂等键和明确终态，不能无限或伪装成功。§11 的写回与 §21.2 均属待实现。

**插件与资源（目标，当前未实现）：** 将异步 `TextMotionAuthor.author` 与同步 `TextMotionProvider.supports/plan` 分注册/贡献契约；Agent 按声明的能力和优先级选择 author，禁止固定 `TextMotionRegistry.get("llm_motion")` 假定 provider 即 author。宿主 compiler 是唯一版本化实现。插件卸载/禁用只影响**新生成**；已有有效 spec 的确定性重编译不依赖 author 在线，不按 `text_provider_id` 要求活插件；若已有 spec 需要已卸载的效果/asset pack 且无可用受控资源，返回明确 unavailable，不能重解释。当前 render 检查活 `llm_motion` 会拒绝旧产物，此政策尚未落地。图片/纹理/字体仅持有 `asset_id`（含 pack/version/hash），通过宿主受控 resolver 限目录、类型、大小与权限；不允许 `style.src`、URL、路径、`..` 直接进入 compiler。当前 compiler 直接用 `style.src`，需要封闭入口。新 MotionSpec clip 失败显式终止；旧无 spec 的 ASS/fixed motion 仅明确 legacy 来源标识的**只读**分支可继续渲染，不可编辑后伪装为 v1。

**任务与前端交接（目标）：** `validate-spec`、`compile` 和“提交重新生成”的 author 任务入口是待新增 API；author 不直接由浏览器调用插件，走 Agent 编排、授权/任务归属/幂等回执。响应携带 clip/version/hash、compiler_version、能力快照、定位错误、任务状态；比较版本失败为 409，取消/超时/失败保留旧 spec 和派生物。先上线只读摘要/明确待编译或不支持状态、错误定位及真实成片抽帧对比，再开放有限参数编辑；取消、刷新和失败后允许从已提交 spec 继续手工编辑。quality 对有 MotionSpec 标识却缺 spec、hash 错误、产物不匹配及弱动效分别产出 issue；错误码统一映射任务终态、可重试性及 UI，不能只在有 spec 时检查。

## 28. 审阅意见落实矩阵与验收门槛

| 优先级 / 审阅点 | 落实章节 | 新增验收项（均未完成，须有自动化证据） |
|---|---|---|
| P0 可执行 schema/能力一致；spring/path/group/camera/stagger 不兑现；MG 白名单 | §25–26、§4–9、§13–14 | 同一能力快照贯通 author/validator/compiler/MG/前端；未知字段/能力/easing 拒绝；逐项语义与抽帧通过才开启；MG 验证失败不落盘 |
| P0 spec/hash/MGDef/HTML 权威、render 不重编译、普通文字回退 | §25、§27、§11–12、§15–16 | 修改/失败/并发冲突无半写；render 验 hash/版本且确定性重编译；新 clip 缺 MGDef 或 spec 不落 legacy/白字 |
| P1 author structured schema、能力、双 LLM/修复成本 | §26–27、§10–11、§17、§21 | 记录两段 LLM/总调用上限、validator 错误反馈与来源元数据；超时和失败有终态 |
| P1 插件 author/provider、卸载、asset_id、旧 ASS | §25、§27、§10、§16、§21、§23 | 卸载 author 后已有有效 spec 仍能渲染；不可用 pack 显错；路径/外链拒绝；legacy 只读分支独立 |
| P1 编辑 API/时间-easing 适配、质量/错误码及 fidelity | §26–27、§13、§15、§19、前端 PRD §16–18 | 三个接口及版本冲突/取消契约；错误按 layer/field 定位；前端由原语对账决定 approx/unsupported，不接受固定 exact |
| P2 长任务、编辑恢复、性能/成本与成片对照 | §27–28、§17、§19、§22 | 702s 全任务、实际 provider 费用与延迟、刷新/撤销/取消/失败恢复；多时间点预览/成片抽帧对照，容差和环境留档；现有 2 秒短片/单测不能替代 |

以上条款是**目标验收**，不表示已运行测试。先冻结并验证 v1 子集，再逐项开放扩展能力；未达到 P0 不得默认启用编辑或标注 `exact`。原 §19 的“插件禁用后明确 MISSING”仅适用于尚未生成且确需 author 的新 clip，不适用于已有有效 spec；原 §19 的全能力一口气通过改为按能力门禁验收。原 §24 未定问题不得被当作已支持语义。
