# LLM MotionSpec Coding Plan

> 对应 PRD：[text-motion-llm-spec-prd.md](text-motion-llm-spec-prd.md)  
> 目标分支：`feat/zam_text_motion_llm_spec`  
> 前提：`ref/文字效果插件化` 的 TEXT_MOTION 插件化已完成  
> 原则：**MotionSpec 在 Animation Agent 阶段生成；render 只编译 spec；无 LLM、无模板回退。**

## 1. 范围

### 1.1 本期范围

- MotionSpec v1 schema / validator；
- 插值编译器：linear、cubic-bezier、steps、hold、spring；
- 路径运动：line/polyline/cubic-bezier/arc/circle；
- 分层、group、camera、mask、text unit/stagger；
- `MotionSpec → TextPlan/MGDef` compiler；
- `text-motion-llm` authoring 插件；
- AnimationAgent 生成阶段调用、写回 clip；
- preview_ops/fidelity、quality、错误码；
- 持久化、hash、缓存、迁移、测试、E2E。

### 1.2 非目标

- render 阶段调用 LLM；
- 插件执行任意 HTML/JS/Shader；
- 无限参数/任意 CSS；
- 本期不做完整 3D 引擎（只支持 transform3d + 受限 camera）。

## 2. 目标架构

```text
AnimationAgent
  → text_generation（内容/style/motion intent）
  → text-motion-llm.author()（异步，唯一 LLM 点）
  → MotionSpecValidator
  → 写回 clip.metadata.motion_spec/hash

TextAnimationEngine
  → MotionCompiler.compile(spec) → TextPlan/MGDef
  → MGRenderer / preview

Render
  → 读 metadata.motion_spec
  → compile，绝不 author
```

## 3. 工作包

### CP-0：Schema 与基础类型（1–2 天）

**任务**

- 新增 `clipwright/animation/motion_spec.py`：
  - `MotionSpec`
  - `Layer`
  - `Keyframe`
  - `Easing`
  - `MotionPath`
  - `Track`
  - `CameraSpec`
  - `EffectSpec`
  - `TextMotionSpec`
- 使用 Pydantic v2，`extra="forbid"`，`ext` 允许扩展。
- 定义枚举：
  - `LayerType`
  - `EasingType`
  - `PathType`
  - `TextUnit`
  - `StaggerOrder`
- 定义 `MotionSpecVersion = "motion_spec/1"`。

**文件**

```text
clipwright/animation/motion_spec.py
tests/clipwright/test_motion_spec_schema.py
```

**测试**

- 合法 spec 通过；
- 未知字段拒绝；
- 非法 easing/path/time 拒绝；
- 序列化/反序列化 round-trip。

**退出条件**

- schema 可独立 import；
- 不依赖 LLM/MGRenderer。

---

### CP-1：Interpolation / 采样（2–3 天）

**任务**

新增 `clipwright/animation/motion_interpolation.py`：

- `linear`
- `named easing` 白名单
- `cubic-bezier`（含 overshoot）
- `steps`（start/end/both）
- `hold`
- `spring`（stiffness/damping/mass/velocity）
- 采样函数 `sample_segment(...)`
- 时间归一化、边界钳制

新增 `clipwright/animation/motion_path.py`：

- line / polyline / cubic-bezier / arc / circle；
- `point_at(progress)`；
- `tangent_at(progress)`；
- 安全区越界检测。

**文件**

```text
clipwright/animation/motion_interpolation.py
clipwright/animation/motion_path.py
tests/clipwright/test_motion_interpolation.py
tests/clipwright/test_motion_path.py
```

**测试**

- linear 中点 = 0.5；
- cubic-bezier 端点/中段；
- steps 离散值；
- hold 不变；
- spring 收敛/不过冲上限；
- path 端点/切线/圆；
- 越界检测。

**退出条件**

- 纯函数、无渲染依赖；
- 预览/编译可复用同一采样。

---

### CP-2：MotionSpec Validator（2 天）

**任务**

新增 `clipwright/animation/motion_spec_validator.py`：

- schema 已由 Pydantic 保证；
- 校验：
  - duration/time；
  - 层数/关键帧/文本单元上限；
  - 属性白名单；
  - 路径点/控制点；
  - easing 参数范围；
  - 字幕带/安全区采样；
  - `requires` 与 capabilities；
  - 文本内容不改；
  - 运动质量 floor。
- 输出结构化 `ValidationResult`：
  - `ok`
  - `errors[]`
  - `warnings[]`
  - `metrics`（coverage、max_shift 等）。

**文件**

```text
clipwright/animation/motion_spec_validator.py
tests/clipwright/test_motion_spec_validator.py
```

**退出条件**

- 非法 spec 不进入 compiler；
- 错误码稳定；
- 质量指标可作为 repair prompt 输入。

---

### CP-3：MotionCompiler（3–4 天）

**任务**

新增 `clipwright/animation/motion_compiler.py`：

- 输入 `MotionSpec + CompileContext`；
- 合并 `keyframes` / `tracks`；
- 按 `max(60, fps*2)` 采样 spring/path/多 easing；
- 生成：
  - layers → MGDef elements；
  - group/parent transform；
  - camera；
  - mask/clip；
  - effects；
- 输出 `TextPlan`（复用现有类型）；
- 生成 `preview_ops`；
- 校验 MGDef。

**编译规则**

- linear/cubic-bezier/steps/hold：
  - 支持直接映射 CSS 的部分保留 easing；
  - 多通道不同 easing 时统一采样；
- spring/path：
  - 采样为离散 keyframes；
- 所有采样结果固定写入 `mg_def`，preview/render 同源。

**文件**

```text
clipwright/animation/motion_compiler.py
tests/clipwright/test_motion_compiler.py
tests/clipwright/test_motion_compiler_golden.py
```

**测试**

- 单层 text；
- 多层 group；
- 逐字 stagger；
- camera；
- path + easing；
- spring 采样；
- 预览 ops 一致；
- golden MGDef。

**退出条件**

- `MotionSpec` 可编译为合法 MGDef；
- 不依赖 LLM；
- 同一 spec 输出稳定。

---

### CP-4：MGRenderer 能力补齐（2–3 天）

**任务**

根据 MotionSpec 需要补：

- 3D transform：
  - `rotate_x/y/z`
  - `perspective`
  - `transform_origin`
- `blur`
- `clip_inset`
- `mask`（至少矩形/羽化）
- gradient `gradient_stops`
- shadow 数组
- group 层级/z-index
- camera（全局 transform）

修改：

```text
clipwright/animation/mg_renderer.py
clipwright/animation/mg/validator.py
```

**测试**

- HTML/CSS 包含对应属性；
- 不支持的字段被 validator 拒绝；
- 预览端能力对齐（后续 CP-6）。

**退出条件**

- 编译器输出的字段全部可渲染；
- 不出现“声明支持、成片没有”。

---

### CP-5：`text-motion-llm` 插件（3–4 天）

**任务**

新增：

```text
plugins/text_motion_llm/
  plugin.yaml
  main.py
  prompt.py
  author.py
```

- `TextMotionAuthor.author()`：
  - 构造 system/user prompt；
  - 调 LLM；
  - JSON schema/structured output；
  - 解析/校验；
  - 失败 repair 1 次。
- 不使用 render 进程；
- 不在 author 中写 timeline；
- 只返回 `MotionSpec`。

**Prompt 必须包含**

- MotionSpec schema；
- capabilities；
- 文字内容/时长/安全区；
- 不允许 HTML/JS/CSS；
- 不改变文字；
- 至少一个主运动；
- 风格/场景上下文。

**manifest**

```yaml
id: text_motion_llm
kind: capability
api_version: "2"
contributions:
  - { kind: text_motion, id: llm_motion }
```

**文件**

```text
plugins/text_motion_llm/*
tests/clipwright/test_text_motion_llm_plugin.py
```

**测试**

- fake LLM 合法 spec；
- 一次 repair；
- 二次失败 → `MOTION_SPEC_INVALID`；
- 无 provider → explicit error；
- 不 import render。

**退出条件**

- 插件可加载/卸载；
- author 只在 agent 调用；
- 无模板回退。

---

### CP-6：AnimationAgent 接入与写回（2–3 天）

**任务**

- 在 `_build_text_clip_llm` 后新增 author 阶段：
  - 构造 `MotionAuthorRequest`；
  - 调 `TextMotionAuthor.author()`；
  - validate；
  - 写 metadata；
  - compile 生成 clip。
- 新增 `motion_spec` 相关 metadata：
  - `motion_spec`
  - `motion_spec_hash`
  - `motion_spec_version`
  - `motion_author`
  - `fidelity`
- `apply_text_style`：
  - 默认保留 spec；
  - 显式 `regenerate_motion` 才重新 author；
  - 工具不直接调 LLM。
- 无 spec/provider 时显式失败，不生成白字。

**文件**

```text
clipwright/agents/animation_agent.py
clipwright/tool/text_style_apply.py
clipwright/animation/text_engine.py
tests/clipwright/test_agent_motion_authoring.py
```

**退出条件**

- clip 创建后 metadata 有 spec/hash；
- render 路径只读 spec；
- agent 失败可重派，不静默。

---

### CP-7：Engine 编译接入与渲染隔离（1–2 天）

**任务**

- `TextAnimationEngine.plan()`：
  - 有 `motion_spec` → compiler；
  - 无 spec → 明确错误或 legacy 策略；
- 禁止 render 路径 import author provider；
- 增加静态检查/测试；
- feature flag：
  - `text_motion_llm_enabled`
  - `text_motion_llm_shadow`（只生成不启用）。

**文件**

```text
clipwright/animation/text_engine.py
clipwright/services/render.py
tests/clipwright/test_render_never_calls_llm.py
```

**退出条件**

- render 服务不引用 `plugins.text_motion_llm`；
- spec 缺失/非法显式失败。

---

### CP-8：Preview / Quality / 错误码（2 天）

**任务**

- preview 消费 compiler 的 `preview_ops`/`mg_def`；
- `fidelity`：
  - exact/approx/unsupported；
- quality：
  - spec 缺失；
  - capability 不支持；
  - 运动覆盖；
  - 字幕带/安全区；
- 错误码：
  - `MOTION_SPEC_INVALID`
  - `MOTION_SPEC_UNSUPPORTED`
  - `MOTION_SPEC_WEAK`
  - `MOTION_SPEC_MISSING`
  - `MOTION_EFFECT_UNAVAILABLE`

**文件**

```text
src/features/preview/PreviewPanel.tsx
src/features/preview/previewFidelity.ts
clipwright/agents/quality_agent.py
```

**退出条件**

- 预览不支持不静默；
- quality 能阻止非法 spec 出片。

---

### CP-9：测试、迁移、E2E（2–3 天）

**测试矩阵**

| 层级 | 测试 |
|---|---|
| schema | `test_motion_spec_schema` |
| interpolation | `test_motion_interpolation` |
| path | `test_motion_path` |
| validator | `test_motion_spec_validator` |
| compiler | `test_motion_compiler*` |
| plugin | `test_text_motion_llm_plugin` |
| agent | `test_agent_motion_authoring` |
| render isolation | `test_render_never_calls_llm` |
| preview | `test_preview_motion_spec` |
| E2E | 生成→编译→渲染→抽帧 |

**迁移**

1. 新 clip 默认 LLM spec，feature flag 控制；
2. 旧 clip 无 spec 时 legacy 只读；
3. 不用固定 motion 冒充 LLM spec；
4. 失败码入 task finish；
5. 截图对比。

**E2E**

- 生成 2–3 条文字，含：
  - cubic-bezier 入场；
  - spring 回弹；
  - path 运动；
  - camera；
- video/audio 时长一致；
- preview vs render 抽帧；
- LLM 只出现一次/片段。

**退出条件**

- 全部测试通过；
- 702s/目标片段 E2E；
- 无 LLM render 调用；
- 无模板回退。

## 4. 依赖顺序

```text
CP-0 Schema
  → CP-1 interpolation/path
  → CP-2 validator
  → CP-3 compiler
  → CP-4 MGRenderer
  → CP-5 LLM plugin
  → CP-6 Agent writeback
  → CP-7 Engine isolation
  → CP-8 Preview/quality
  → CP-9 E2E
```

CP-4 与 CP-5 可在 CP-3 后并行。

## 5. Feature flags

```text
text_motion_llm_enabled=false
text_motion_llm_shadow=true
text_motion_llm_max_layers=8
text_motion_llm_max_keyframes=16
text_motion_llm_max_output_tokens=4096
```

## 6. 风险与回滚

- LLM 输出复杂 → schema + repair + 上限；
- spring/path 采样体积 → 限层/帧；
- 3D/MGRenderer 不支持 → CP-4 先补；
- 预览差异 → fidelity；
- 性能/成本 → agent 阶段一次 + cache；
- 回滚：关闭 feature flag，旧 clip 只读 legacy。

## 7. Definition of Done

- [ ] MotionSpec schema/validator/compiler 完成；
- [ ] linear/bezier/steps/hold/spring/path 全部可编译；
- [ ] text-motion-llm 插件在 agent 阶段 author；
- [ ] clip 写回 spec/hash/model；
- [ ] render 不调用 LLM；
- [ ] preview/quality/fidelity 对齐；
- [ ] 失败显式，无模板回退；
- [ ] 测试矩阵通过；
- [ ] E2E 通过；
- [ ] 提交、push。


## 8. 实施记录（分支 `feat/zam_text_motion_llm_spec`）

- CP-0：`motion_spec.py` schema 已落地；
- CP-1：`motion_interpolation.py` 支持 linear/named/cubic-bezier/steps/hold/spring、line/polyline/arc/circle；
- CP-2：`motion_spec_validator.py` 已落地；
- CP-3：`motion_compiler.py` 已支持 layers/keyframes/tracks/path/camera/stagger；
- CP-4：MGRenderer 增加 rotate_x/y/z、scale_x/y、translate_z、perspective、blur、clip_inset/filter；
- CP-5：`plugins/text_motion_llm` author 插件、fake LLM 测试通过；
- CP-6：Agent 写回 `motion_spec/hash/motion_author` 测试通过；
- CP-7：render 不 import LLM author；测试通过；
- 测试：`test_motion_spec.py` 10 passed（含 agent writeback、short Hyperframes E2E）；
- 提交：`8718747`、`c7dc694`、`fd9341c`、`052c29b`、后续测试提交。
- 仍缺：full task E2E、真实 provider LLM 调用、preview 独立解释、mask 完整语义。
