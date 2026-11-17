# LLM MotionSpec 文字动效 PRD

> 状态：方案评审稿  
> 范围：仅文字动画（不含 MG 图形动画、转场、字幕样式）  
> 目标分支：`ref/文字效果插件化`  
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

- LLM 在 **animation agent 阶段**调用一次，输出 `MotionSpec`；
- `MotionSpec` 写入文字 clip，render 阶段只编译 spec，**绝不调用 LLM**；
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
