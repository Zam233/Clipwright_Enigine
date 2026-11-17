# 文字动画插件化 Coding Plan

> 对应 PRD：[text-animation-plugin-boundary-prd.md](text-animation-plugin-boundary-prd.md)  
> 实现分支：`ref/文字效果插件化`  
> 目标：Engine 只保留文字动画接口、渲染运行时、布局原语、预览/质量/时间线；fade/slide/scale/typewriter/word_by_word/highlight 等基础动效由独立插件 `text-motion-core` 提供。  
> 原则：**先做无损抽象，再搬实现；每步可回滚、可对比、不得静默降级。**

## 1. 范围

### 1.1 本期范围

- 新增 `TextAnimationEngine` / `TextMotionProvider` / `TextPrimitives` 契约；
- 新增 `TEXT_MOTION` 贡献类型（需契约评审）；
- 新建 `text-motion-core` 插件，迁移 7 类基础动效；
- `text-mg` 只保留布局/box/shadow 等原语，不再有 motion 分支；
- `text-motion-fx` 预留 glitch/neon/3D；
- 提示词、样式包、资产、静态效果卡做成插件（可分批）；
- `apply_text_style` 全部文字统一走 engine；
- 预览/质量通过 `TextPlan` + `fidelity` 收口；
- E2E：插件启用/禁用、任务 done、video/audio 702s、抽帧。

### 1.2 非目标

- 不重做 MG 图形动画；
- 不改字幕轨；
- 不把 timeline 写入、quality、任务终态下放插件；
- 不新增“LLM 失败时套固定模板”的降级路径。

## 2. 现状基线

| 模块 | 现状 |
|---|---|
| `clipwright/animation/text_style.py` | style/motion schema、归一化、校验 |
| `clipwright/animation/text_mg.py` | 文字布局 + 所有 motion 实现（fade/slide/scale/typewriter/word/highlight/glitch/neon） |
| `clipwright/animation/text_generation.py` | LLM prompt、MGDef、`text_animation` |
| `clipwright/animation/mg_renderer.py` | MGDef → HTML/CSS |
| `clipwright/animation/hyperframes_renderer.py` | HTML → 透明 MOV |
| `clipwright/agents/animation_agent.py` | `_build_text_clip_llm`、`apply_text_style_to_clip`、失败记录 |
| `clipwright/services/render.py` | text clip → `hf_ov_local` → Hyperframes |
| `clipwright/tool/text_style_apply.py` | 写回 style/motion + 重建 HTML |
| `src/features/preview/mgRenderer.ts` | 预览消费 mg_def |
| `clipwright/agents/quality_agent.py` | 样式/叠字/单调检查 |
| `clipwright/plugins/contribution.py` | 20 类贡献（`ANIMATION` 是动画定义，不是 motion） |
| `plugins/text_animations/` | 25 个独立 FFmpeg 文字工具，不参与文字轨 |

**关键代码事实**：

- `text_mg.build_text_mgdef()` 当前按 `motion_type` 分支生成 fade/slide/scale/typewriter/word/highlight/glitch/neon；
- `_build_text_clip_llm()` 生成 Clip 后调用 `apply_text_style_to_clip()`；
- 所有文字动画已是 `renderer="mg_hyperframes"`；
- 预览 `mgRenderer.ts` 直接消费 `mg_def`；
- `ContributionKind.ANIMATION` 语义是动画定义，**不应直接复用**为文字 motion。

## 3. 目标架构

```text
text prompt pack (plugin)
        │
        ▼
text_generation (engine: LLM call/parse/retry)
        │  text_animation.style/motion
        ▼
TextAnimationEngine (engine)
  ├─ normalize/validate style
  ├─ registry.resolve(motion.type) ──► TextMotionProvider (plugin)
  │                                      │
  │                                      ├─ plan(request)
  │                                      └─ preview(plan,t)
  ├─ validate MotionPlan
  ├─ TextPrimitives (engine: layout/box/shadow/safe area)
  └─ TextPlan { mg_def, fidelity, preview_ops }
        │
        ├─ MGRenderer (engine) ──► Hyperframes (engine)
        ├─ preview mgRenderer (engine)
        ├─ apply_text_style (engine)
        └─ quality/timeline/task state (engine)
```

**硬边界**：

- `text_mg.py` 不出现 `if motion_type == "fade"` 这类分支；
- 插件只返回 `MotionPlan`/MGDef，不返回可执行 HTML/JS；
- 插件不能 import `task_blackboard`、不能写 Timeline、不能改 quality；
- 所有文字 clip 必须经过 engine 的 `apply_text_style`。

## 4. 工作包

### CP-0 基线、契约评审与特性开关（0.5–1 天）

**任务**

- 固定当前 19 条文字动画的 `mg_def`/HTML/渲染抽帧为 golden 基线；
- 评审并冻结 `TEXT_MOTION` 贡献类型（新增第 21 类），确定：
  - `kind=text_motion`；
  - `TextMotionProvider` 不接收 Timeline；
  - plugin 返回 `MotionPlan`；
- 新增配置：
  - `text_animation_engine_v2`（默认 false）；
  - `text_motion_plugins_enabled`（默认 false）；
  - `text_motion_shadow_compare`（true 时旧/新 plan 对比只告警）。

**文件**

- `clipwright/config.py`
- `clipwright/plugins/contribution.py`
- `clipwright/schema/plugin.py`（manifest 校验允许 `text_motion`）
- `docs/text-animation-plugin-boundary-prd.md`

**测试**

- `test_plugin_contribution_kinds.py`：新 kind 可声明/注册/回收；
- `test_text_golden_baseline.py`：19 条 clip 的 `mg_def` 结构快照。

**退出条件**

- 新开关打开前，现有行为零变化；
- 契约评审通过。

---

### CP-1 Engine 接口与注册表（2–3 天）

**任务**

1. 新增 `clipwright/animation/text_engine.py`：
   - `TextAnimationEngine`；
   - `TextPlan`、`TextMotionRequest`、`MotionPlan`、`PreviewFrame`；
   - `TextPrimitives`（调用 engine 的测量/布局/box/shadow 原语）；
   - `plan/render_html/render_preview/apply_style/capabilities`。
2. 新增 `clipwright/animation/text_motion_registry.py`：
   - provider 注册/按 motion 解析/优先级/归属/卸载；
   - 无 provider 返回 `TEXT_MOTION_UNAVAILABLE`；
   - 禁止匿名注册。
3. `text_mg.py` 拆出原语函数（不改行为）：
   - `measure_text` / `tokenize` / `char_width`；
   - `make_text_element` / `make_box` / `shadow_css`；
   - 旧 `build_text_mgdef` 暂时保留，内部调用原语。
4. `TextAnimationEngine.apply_style()` 接管 `apply_text_style_to_clip()` 的逻辑；旧函数改为兼容 wrapper。

**文件**

- 新增：`text_engine.py`、`text_motion_registry.py`、`text_primitives.py`
- 修改：`text_mg.py`、`text_style.py`、`tool/text_style_apply.py`
- 测试：`test_text_engine_contract.py`、`test_text_motion_registry.py`

**测试要点**

- 注册 provider → resolve 成功；
- 卸载 → resolve 返回明确错误；
- 同一 motion 两个 provider → 冲突拒绝；
- `TextPlan` 字段完整，fidelity 必填；
- 旧 `build_text_mgdef` 输出与基线一致。

**退出条件**

- 引擎接口可被单测直接调用；
- 旧路径仍走原实现，行为不变。

---

### CP-2 `text-motion-core` 插件（3–4 天）

**任务**

1. 创建插件：

```text
plugins/text_motion_core/
  plugin.yaml
  main.py
  providers/fade.py
  providers/slide.py
  providers/scale.py
  providers/typewriter.py
  providers/word_by_word.py
  providers/highlight.py
```

2. 从 `text_mg.py` 迁移：
   - fade/slide/scale 的 keyframes；
   - typewriter 的 token/reveal/cursor；
   - word_by_word 的 token；
   - highlight 的 bar/shadow；
   - 删除 engine 内对应的 motion 分支（仅当 v2 开关打开）。
3. 插件 provider 使用 `TextPrimitives`，不自己算字幕带/画布。
4. `_build_text_clip_llm()` 在 v2 路径调用 `TextAnimationEngine.plan()`；
   - metadata 写 `text_provider_id`、`text_provider_version`、`fidelity`；
   - 失败记录 `TEXT_MOTION_UNAVAILABLE`/`TEXT_MOTION_PLAN_INVALID`，不生成白字。
5. `apply_text_style` 全部走 engine，所有 clip 必须带 `provider_id`。

**文件**

- 新增：`plugins/text_motion_core/*`
- 修改：`text_mg.py`、`text_engine.py`、`animation_agent.py`、`tool/text_style_apply.py`
- 测试：`test_text_motion_core.py`、`test_text_animation_v2_wiring.py`

**测试要点**

- 19 条旧 clip 在 v2 下 `mg_def`/HTML 与基线逐字段/哈希一致（允许 provider_id 元数据差异）；
- typewriter 逐帧 reveal 与旧一致；
- word_by_word token 数、时间一致；
- highlight bar 位置/阴影一致；
- 禁用 core 插件 → 重渲染该 clip 明确失败，不回退 ASS/白字。

**退出条件**

- 基础动效 100% 由插件提供；
- `rg 'motion_type == "typewriter"' text_mg.py` 为空（v2 路径）；
- golden 对比通过。

---

### CP-3 插件契约落地与隔离（2 天）

**任务**

- `contribution_dispatch` 支持 `TEXT_MOTION`；
- manifest 声明 `text_motion:fade_in` 等；
- 插件卸载/禁用：
  - registry 回收；
  - 已有 clip 引用时报告 `TEXT_PROVIDER_UNLOADED`；
- 权限边界：
  - 禁止插件 import `task_blackboard`/`render`/`quality`；
  - 插件返回的 MGDef 经 engine 校验；
  - 不允许插件写文件（资产走 `TEXT_ASSET_PACK`）。

**文件**

- `clipwright/plugins/contribution.py`
- `clipwright/plugins/contribution_dispatch.py`
- `clipwright/plugins/loader.py`
- `clipwright/schema/plugin.py`
- `tests/clipwright/test_text_plugin_isolation.py`

**测试**

- 未声明 `text_motion` 的 manifest 不能注册；
- 卸载后 provider 不在 capabilities；
- 插件返回非法 MGDef 被拒绝；
- 插件尝试写 Timeline 的 import 被静态检查/测试拒绝。

**退出条件**

- 插件启停可重复，不残留能力；
- 失败可解释。

---

### CP-4 Prompt / Style / Asset / Static Card 插件（2 天）

**任务**

- `text-prompt-pack`：
  - 把 `text_specs.TEXT_ANIM_SPECS` 变成 builtin prompt pack；
  - 插件可新增类型；
  - 不再要求“只保留提示词”。
- `text-style-pack`：
  - 允许显式 `style_pack_id`；
  - 禁止静默 fallback；
- `text-asset-pack`：
  - 字体、纸张纹理、贴图；path/sha/license。
- `text-effect-card`：
  - 从 `tool/text_effect_card.py` 拆为插件 tool + asset；
  - 静态 PNG 卡，仍不承担逐字/位移动画。
- `text-motion-fx`（可后置）：
  - glitch/neon/3D；声明 `renderer_backend`。

**文件**

- 新增：`plugins/text_prompt_pack/*`、`plugins/text_style_pack/*`、`plugins/text_asset_pack/*`、`plugins/text_effect_card/*`
- 修改：`text_specs.py`、`text_generation.py`、`tool/text_effect_card.py`、`animation_agent.py`
- 测试：`test_text_prompt_pack.py`、`test_text_style_pack.py`、`test_text_asset_pack.py`

**退出条件**

- 新增一个文字类型无需改 engine；
- 卸载 prompt pack 后，类型从 capabilities 消失；
- style preset 只能显式选择。

---

### CP-5 预览与质量收口（2 天）

**任务**

- `TextPlan.preview_ops` 与前端 `mgRenderer.ts` 对齐；
- provider 可声明 `preview: exact|approx|none`；
- `PreviewPanel` 对 unsupported 显示“不可近似预览”，不静默画白字；
- quality 增加：
  - provider 缺失/未注册 → error；
  - fidelity=unsupported 但进了成片 → error；
  - 同一 clip 多 provider → error；
  - style 字段与 provider capabilities 不一致 → error；
- `apply_text_style` 工具校验 provider。

**文件**

- `src/features/preview/PreviewPanel.tsx`
- `src/features/preview/mgRenderer.ts`
- `clipwright/agents/quality_agent.py`
- `clipwright/animation/text_engine.py`
- 测试：`test_text_preview_fidelity.py`、前端 preview tests

**退出条件**

- 预览与成片对基础 motion 一致；
- unsupported 有明确 UI/错误，不静默。

---

### CP-6 旧路径清理与兼容（1–2 天）

**任务**

- v2 默认开启后，删除 engine 内 motion 分支与 `builder` 重复逻辑；
- `build_text_mgdef` 只保留兼容 wrapper（或删除）；
- `tool/text_style_apply.py` 完全委托 engine；
- 旧 ASS/drawtext 文字路径只保留**旧时间线只读兼容**，新文字绝不进入；
- 删除/归档 dead `_fallback_generate`（如果确认无调用）；
- 更新 `docs/text-animation-style-prd.md` 与插件 PRD 状态。

**退出条件**

- `rg` 检查 text_mg 无 motion 实现；
- 无新路径 fallback；
- 全量回归通过。

---

### CP-7 E2E 与发布（2 天）

**任务**

1. 不装 `text-motion-core`：选一个 typewriter clip 重渲染 → 必须显式 `TEXT_MOTION_UNAVAILABLE`；
2. 装 core：19 条文字动画、video/audio 702s；
3. 装 `text-motion-fx`：glitch/neon 真实效果；
4. 卸载 fx：对应 clip 明确失败，不白字；
5. 预览截图 vs 成片抽帧；
6. 任务 done、quality 0 error；
7. 提交、push、PR。

**测试**

- `test_text_animation_plugin_e2e.py`
- `test_text_animation_plugin_disable.py`
- `test_text_preview_render_parity.py`
- 人工抽帧：332/340/556/632/692s。

**退出条件**

- 所有验收项通过；
- 无静默降级；
- PR 创建。

## 5. 测试矩阵

| 层级 | 测试 | 覆盖 |
|---|---|---|
| 契约 | `test_text_engine_contract` | TextPlan/fidelity/错误码 |
| 注册 | `test_text_motion_registry` | resolve/冲突/卸载 |
| 插件 | `test_text_motion_core` | 7 类基础动效逐字段 |
| Golden | `test_text_render_golden` | 旧 19 条 mg_def/HTML 对比 |
| 隔离 | `test_text_plugin_isolation` | 插件不能写 timeline/quality |
| Prompt | `test_text_prompt_pack` | 新增类型/卸载 |
| Style | `test_text_style_pack` | 显式选择/不静默 |
| Preview | `test_text_preview_fidelity` | exact/approx/none |
| E2E | `test_text_animation_plugin_e2e` | 装/卸/重渲染 |
| 回归 | 现有 text_mg/MG/quality 套件 | 不破坏旧能力 |

## 6. 迁移与回滚

- **特性开关**：`text_animation_engine_v2` 默认 false，金丝雀项目开启；
- **shadow compare**：旧/新 plan 同时生成，比较关键字段和 HTML hash，差异只告警；
- **回滚**：关闭 v2 立即回旧路径；插件禁用不影响 timeline 数据，只影响重渲染；
- **数据兼容**：旧 clip 保留 `metadata.style/motion`，新 engine 读同一份；provider_id 缺失时按 `motion.type` 解析 core；
- **版本**：provider 版本写入 clip，重渲染使用同版本或报告不兼容。

## 7. 风险

- `TEXT_MOTION` 新增贡献类型需要冻结名单评审；
- 插件 provider 输出 MGDef 的自由度可能破坏布局/安全边界 → engine 必须二次校验；
- 预览 `approx` 的标准需要可量化；
- 旧 19 条 clip 的 HTML hash 可能因 provider 重排变化 → 先做字段级 golden，再做像素级；
- 字体/纹理授权与路径安全；
- 默认 bundled plugin 的发布/升级策略需要明确；
- 插件卸载后的已有项目重渲染行为必须统一（建议 fail-closed）。

## 8. Definition of Done

- [ ] `TEXT_MOTION` 契约评审通过；
- [ ] engine 只有接口/原语，无 motion 实现；
- [ ] `text-motion-core` 独立插件提供 7 类基础动效；
- [ ] 所有文字 clip 经 `apply_text_style`，带 provider/fidelity；
- [ ] 提示词/样式/资产/静态卡可按插件扩展；
- [ ] 预览/质量对 fidelity 收口；
- [ ] 插件启停不残留能力；
- [ ] 不装插件时显式失败，不回退 ASS/白字/模板；
- [ ] E2E 任务 done，video/audio 702s，抽帧和预览对比通过；
- [ ] 代码提交、push、PR。


## 10. 实施记录（分支 `ref/文字效果插件化`）

### 已完成

- 新增 `clipwright/animation/text_engine.py`：
  - `TextAnimationEngine` / `TextPlan` / `TextMotionRequest` / `MotionPlan` / `TextMotionProvider` / `TextMotionRegistry`；
  - engine 只做 style 归一化、provider 解析、MGDef 校验、HTML 渲染、apply_style；无 motion 分支。
- 新增 `clipwright/animation/text_primitives.py`：颜色/测量/token/阴影等纯原语。
- 新增 `TEXT_MOTION` 贡献类型并接入 contribution dispatch / unregister。
- 新增 `plugins/text_motion_core`：
  - fade_in/fade_out/slide_in/scale_in/typewriter/word_by_word/highlight；
  - 兼容期同时提供 glitch/neon（后续可拆到 fx）；插件可独立加载/卸载。
- `text_mg.py` 已清空 motion 实现，只保留兼容 wrapper + 渲染原语别名；
- `_build_text_clip_llm`、`text_style_apply`、`apply_text_style_to_clip` 均通过 engine/provider；
- conftest 为测试注册 core，显式缺失 provider 时测试会失败而不是回退白字。

### 已完成点对点 E2E

- `render_a1604dca3b53`：
  - 项目 19 条文字全部由 `TextAnimationEngine` 重新生成并写回；
  - 每条 metadata 有 `text_provider_id`、`fidelity=exact`；
  - 成片 `renders/..._20261117075812.mp4`：
    - video **702.9s**
    - audio **702.4s**
  - `_cache/plugin_e2e_shots/contact_sheet.jpg` 19 格文字动画均可见。

### 未完成

- CP-4：`text-prompt-pack`、`text-style-pack`、`text-asset-pack`、`text-effect-card` 尚未插件化（当前仍由 engine 内置）；
- CP-5：`preview_ops`/fidelity 的完整前端契约、quality provider 校验尚未接完；
- CP-6：旧 ASS/drawtext 文字兼容路径未删除（旧时间线仍需只读兼容）；
- CP-7：插件禁用/卸载的真实 E2E（不是单测）未跑；
- `text-motion-fx`（3D/shader）未实现。

### 提交

- `ea62cec` 分支基线 PRD/Coding Plan
- `36b9963` engine 接口 + core 插件 + 清理 engine motion
- `01eadc9` clamp provider keyframes


### 追加实施记录（2026-11-17，branch `ref/文字效果插件化`）

- CP-1/CP-2/CP-3 基础完成：
  - `TextAnimationEngine`/`TextMotionRegistry`/`TextPrimitives` 已落地；
  - `plugins/text_motion_core` 提供 9 个 provider（7 基础 + glitch/neon 兼容）；
  - `text_mg.py` 已无 motion 实现，只保留 wrapper/原语；
  - provider 缺失/非法时 `_build_text_clip_llm` 记录 `text_motion_unavailable`/`text_motion_plan_invalid`，不回退 ASS/白字/模板；
  - 新增 `test_text_engine_plugin` 覆盖 provider 启停、9 个 motion、非法 plan。
- 点对点 E2E 已通过：
  - `render_a1604dca3b53` video 702.9s / audio 702.4s；
  - 19 条文字 metadata 均有 `text_provider_id`/`fidelity=exact`；
  - 截图 `_cache/plugin_e2e_shots/contact_sheet.jpg`。
- 仍未完成：CP-4 prompt/style/asset/card 插件、CP-5 preview_ops/quality 契约、CP-6 旧 ASS 兼容清理、CP-7 插件卸载真实 E2E。


### 插件生命周期/隔离验证（2026-11-17）

- `test_text_motion_plugin_lifecycle.py`：PluginLoader 加载 core → provider 可用；unload → `TEXT_MOTION_UNAVAILABLE`；
- 静态隔离：`plugins/text_motion_core` 不含 `task_blackboard` / `quality_agent` / `services.render` 引用；
- `test_text_engine_plugin`：9 个 provider、非法 plan 显式失败；
- 点对点 E2E：`render_a1604dca3b53`，video 702.9s / audio 702.4s，19 条文字 provider_id/fidelity=exact。


### CP-4/CP-5/CP-7 追加（2026-11-17）

- `text_effect_card` 已从 engine tool 迁到 `plugins/text_effect_card`（TOOL contribution）；
- render 在文字 clip 有 `text_provider_id` 时检查 provider；未加载时抛
  `TEXT_MOTION_UNAVAILABLE`，不回退 ASS/白字；
- quality 增加 provider 缺失 / `fidelity=unsupported` 的 error；
- `test_text_motion_plugin_lifecycle` 增加“provider 卸载后 render 显式失败”测试；
- 仍未完成：`text-prompt-pack` / `text-style-pack` / `text-asset-pack`，
  `preview_ops` 的前端完整契约、旧 ASS 兼容路径清理。


### 最终点对点 E2E（2026-11-17）

- `render_9b88102f740f` 通过 API 触发（不依赖浏览器按钮）：
  - video **702.9s**
  - audio **702.4s**
  - output `renders/render_9b88102f740f.mp4`
- 插件路径：TextAnimationEngine → `text-motion-core` → MG HTML → Hyperframes；
- 抽帧 `_cache/final2_332.png`、`final2_340.png`、`final2_556.7.png`、`final2_632.4.png`。
- 仍缺：CP-4 的 prompt/style/asset pack，CP-5 的前端 preview_ops 完整契约，CP-6 旧 ASS 兼容清理。


### CP-4 pack 插件补齐（2026-11-17）

- 新增贡献类型：`TEXT_PROMPT_PACK` / `TEXT_STYLE_PACK` / `TEXT_ASSET_PACK`；
- 新增 `plugins/text_packs` 示例：
  - prompt type `spotlight_quote`；
  - style pack `editorial_dark`；
  - asset pack `paper_assets`；
- `text_specs.get_text_spec/load_text_prompt` 优先读取 prompt pack；
- `TextAnimationEngine.capabilities()` 暴露 packs；
- `apply_style` metadata 写 `preview_ops`；
- `test_text_packs_plugin` 覆盖 load/unload。
- 仍缺：前端真正消费 `preview_ops` 的渲染分支（当前仍以 `mg_def` 为准）、style pack 的显式选择 UI。


### CP-6 收尾（2026-11-17）

- 文字 clip `renderer=mg_hyperframes` 但缺 `mg_html` 时现在抛
  `TEXT_MG_HTML_MISSING`，不再静默回退 ASS/白字；
- 旧时间线（没有 mg_html、renderer 不是 mg_hyperframes）仍走只读 ASS 兼容；
- 新文字路径已无动静混合 fallback。


### 收尾状态（2026-11-17）

- CP-4：`text_effect_card`、`text_packs`（prompt/style/asset 三类）已插件化；
- CP-5：engine metadata 写 `preview_ops`/`fidelity`；quality 检查 provider/fidelity；
  前端 `previewFidelity` 对 `fidelity=unsupported` 显示不可预览角标；
- CP-6：`renderer=mg_hyperframes` 但缺 `mg_html` 现在抛 `TEXT_MG_HTML_MISSING`，
  不再回退 ASS；旧时间线无 mg_html 仍只读兼容；
- CP-7：PluginLoader 加载/卸载、provider 卸载后 render 显式失败测试通过；
  路由验证：项目 19 条文字 clip 全部进入 `hf_ov_local`（category=text_mg），
  `ass_text` 仅 321 条字幕，不含文字动画；
- 测试：后端 25 passed；前端 preview 151 passed；`tsc --noEmit` 通过。


### 最终收尾（2026-11-18）

- CP-6：删除 `render.py` 旧 ASS 的 typewriter/word/highlight/slide/scale/glitch 实现；
  旧时间线只读时退化为静态 ASS（保留兼容），新 MG 文字缺 HTML 抛 `TEXT_MG_HTML_MISSING`；
- CP-4：style pack 已可被 `TextAnimationEngine.resolve_style_pack`/`plan(style_pack_id)` 消费；
  asset pack 提供 `resolve_asset`；内置 prompt 类型仍作为 engine bootstrap（外部 pack 优先）；
- CP-5：`preview_ops` 默认生成 `mg_def` op；前端 `previewFidelity` 消费 `preview_ops` 的 unsupported；
- CP-7：真实禁用 `text_motion_core` 后提交 render `render_3d92e6ffd5ce`，状态 failed/0%，未产生成片；
- 最新完整 E2E：`render_aec88d8c82b6`，video **702.9s** / audio **702.4s**；
- 测试：后端 20 passed；前端 preview 151 passed；`tsc --noEmit` 通过。


### Goal 轮次进展（2026-11-18）

- 内置 6 个 prompt 类型数据已物理迁到 `plugins/text_packs/builtin_types.json`；engine `text_specs` 从插件 JSON 加载，插件贡献完整 prompt；
- `apply_text_style` 支持 `style_pack_id`；`TextAnimationEngine.apply_style` 读取 `asset_pack_id/asset_id` 并写 `resolved_asset`；
- `render_3d92e6ffd5ce` 的失败原因已持久化在 Mongo `result.error`：
  `TEXT_MOTION_UNAVAILABLE ... provider slide_in`；
- 仍未完成：同步 `_handle_text_animation`/`AnimationCatalog` 模板路径（测试依赖）、asset pack 未进入像素渲染、preview_ops 未实际绘制、style pack 无 UI；PR #19/#16 保持 open。


### 同步模板路径清理（2026-11-18）

- `_handle_text_animation` 旧同步入口不再调用 `AnimationCatalog.build_full_keyframes`；
- 旧调用改为通过 TextAnimationEngine + text-motion-core 生成 MGDef；
- 对应测试改为断言 `text_provider_id`/`mg_html`，不再断言模板 keyframes；
- 受影响测试 47 passed。


### 第三轮收尾（2026-11-18）

- 内置 prompt 数据物理迁移到 `plugins/text_packs/builtin_types.json`；
- 旧同步 `_handle_text_animation` 已去掉 AnimationCatalog keyframes，改走 motion provider；
- texture asset pack 进入 MGDef（shape.background_image）并在 MGRenderer 输出 CSS；
- 新增 API：
  - `GET /api/text-animation/capabilities`
  - `GET /api/text-animation/style-packs`
  - `GET /api/text-animation/asset-packs`
- 前端按 `preview_ops.kind=mg_def` 决定是否绘制 MG；
- 仍未完成：style pack 的 PropertiesPanel UI、asset 路径安全/实际资源加载（图像路径），最新 702s 全量 E2E。
