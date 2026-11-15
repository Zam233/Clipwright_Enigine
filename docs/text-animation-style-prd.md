# 文字动画样式与 Typewriter 修复 PRD

> 状态：**部分实现，验收未通过**（任务 failed；motion/样式/底板/任务终态均有未完成项，见 §12）  
> 日期：2026-11-15  
> 范围：后端文字动画生成 / 渲染、前端预览、文字 LLM 提示词与样式契约  
> 关联现场：成品 `05:37`、`08:13` 白色文字抽帧；`renders/从_PCL_到_HMCL...mp4`

## 1. 背景与问题

当前文字动画已经由 LLM 生成（`text_generation` + `_build_text_clip_llm`），但在成片和预览里表现为“白色文字浮在画面中央、长时间不消失、没有逐字效果”，与用户提供的文字动效样例（商品 1/2、示例文字的各种描边/阴影/底板/渐变/故障风）差距明显。

### 1.1 已复现的现场

- **337s（05:37）**：TEXT 轨 `tc_eaa99e16`，文字「没有通报」，`anim_type=text_fade_in`，clip 331.64–339.64s。337s 处于 hold，白色文字可见。
- **493s（08:13）**：TEXT 轨 `tc_97af0cf6`，文字「离线下载功能」，`anim_type=typewriter`，clip 478.48–508.67s（30.19s）。483s / 490s 抽帧已经是完整文字，逐字效果没有生效。

### 1.2 根因（代码级）

1. **默认渲染器走 ASS，文字动画属性没有进入 ASS 路径**
   - `_apply_text_concat()` 默认 `_apply_text_ass()`；`_apply_text_ass()` 只输出 `Dialogue`，没有消费 `anim_type` / `keyframes`；
   - `typewriter` 的逐字逻辑只存在于 `_build_drawtext_filter()`，默认 ASS 路径永远走不到。
2. **样式只取第一条 overlay**
   - `_apply_text_ass()` 的 `style_ts` 取第一条 overlay，后续 Dialogue 即使覆盖 `\fn/\fs/\1c`，底板、阴影、描边等仍容易被首条样式带偏。
3. **文字 clip 时长等于整个视频片段**
   - 关键词动画被生成成 8s / 30s 的 clip，hold 到片段结束；关键词变成长时间挂在画面上的白字。
4. **LLM 样式没有进入 clip 样式**
   - `_mgdef_to_drawtext_keyframes()` 只提取字号、颜色、字距；MG 里的 `background`、`border_*`、`text_shadow`、`font_weight`、`align` 等没有转成 `Clip` / `metadata.style`。
5. **没有淡出策略**
   - 关键帧末尾直接 `opacity=1` 到 clip 结束，文字在切点突然消失。

## 2. 目标

让文字动画成为**可配置样式的独立视觉层**，而不是“白色 drawtext 文字”：

- 每个文字动画 clip 有完整的样式：字体、字号、字重、斜体、字距、行高、对齐、颜色、背景/底板、描边、阴影、发光、圆角/内边距；
- 每个文字动画有明确动效：淡入淡出、滑入、缩放、逐字（typewriter）、逐词、强调高亮、故障/霓虹等（按渲染能力分级）；
- typewriter **真正逐字出现**，reveal 时长独立于 clip 总时长；
- 关键词 clip 时长由内容语义决定（不设硬上限），但不得无理由默认覆盖整个视频片段；
- 样式在**预览与成片**一致，至少颜色/字号/描边/阴影/底板/位置一致；
- LLM 提示词与数据契约明确要求输出 `style` + `motion`，不再默认白色。

## 3. 非目标

- 本期不实现 3D 透视、任意 SVG 路径文字、逐字物理粒子等超出 ASS/drawtext 能力的特效；
- 本期不替换 MG 动画系统（`mg_dynamic` 的图形动画仍走 MG）；
- 本期不重做字幕系统（CAPTION 仍独立；文字动画不得与字幕叠字）。

## 4. 用户故事

- 作为编导，我希望「没有通报」以带底板的红/白关键词出现，2.5s 后淡出，而不是白色文字挂 8 秒。
- 作为编导，我希望「离线下载功能」逐字出现（约 1.2s 内打完），停留 1.5s 后淡出，而不是一次显示全文 30 秒。
- 作为编导，我希望同一支片里不同关键词可以分别用白字描边、红色底板、黄色高亮、故障风，而不是全片只有一种白色样式。
- 作为审核者，我希望预览里看到的文字位置/样式和最终 render 一致。

## 5. 功能需求

### FR-1 文字动画样式模型（P0）

新增统一的 `TextAnimationStyle` 契约，挂在 `clip.metadata.style`，并映射到 `Clip` 已有字段（颜色/描边/阴影/字重/字距等）。字段：

| 分组 | 字段 | 说明 |
|---|---|---|
| 排版 | `font` / `font_size` / `font_weight` / `font_italic` / `letter_spacing` / `line_height` / `text_align` | 字体与排版 |
| 颜色 | `fill`（纯色） | 一期不做渐变填充；渐变/故障保留扩展位 |
| 底板 | `box` / `box_color` / `box_border` / `box_radius` / `box_padding_x` / `box_padding_y` | 纯色圆角底板、条带底板 |
| 描边 | `stroke_width` / `stroke_color` | ASS `\bord` / drawtext `borderw` |
| 阴影 | `shadow_x` / `shadow_y` / `shadow_blur` / `shadow_color` | ASS 阴影/模糊 |
| 发光 | `glow_color` / `glow_width` | ASS `\blur` + `\3c` 双通道 |
| 动效 | `motion` + `motion_params` | 见 FR-2 |
| 位置 | `position` / `offset_x` / `offset_y` | center/top，禁止 bottom 字幕带 |
| 安全区 | `avoid_caption_band` | 默认 true，文字动画必须避开底部字幕带 |

样式优先级：`clip.metadata.style` > `Clip` 字段 > persona 默认 > 系统默认。**不允许**整片只取第一条 overlay 的样式。

### FR-2 动效与 Typewriter（P0）

动效类型（一期）：

| `motion` | 语义 | 渲染要求 |
|---|---|---|
| `fade_in` | 淡入 + hold | `\fad(in,out)` |
| `slide_in` | 位移入场 | `\move` 或 drawtext x/y 关键帧 |
| `scale_in` | 缩放入场 | ASS `\t(\fscx,\fscy)` 或 drawtext keyframes |
| `typewriter` | 逐字出现 | 见下方专用规则 |
| `word_by_word` | 逐词出现 | 按词拆分 Dialogue，带固定位置 |
| `highlight` | 关键词高亮/扫过 | ASS `\k` 或双色层 |
| `glitch` / `neon` | 故障/霓虹 | 二期；一期至少不冒充 |

**Typewriter 规则（必须实现）**：

- reveal 时长 `reveal_sec = clamp(0.6, char_count * 0.08, 2.0)`，不再按 clip 总时长摊开；
- 第 i 个字符在 `start + i * reveal_sec / max(1, n-1)` 出现；
- 标点 `，。！？` 后追加 0.08s 停顿；
- 每个字符的显示窗口到 `clip_end`，可选 cursor `▍` 在 reveal 完成后 0.3s 消失；
- clip 结束前 0.2s 淡出；
- 逐字必须固定整体起点（居中整行的左起点），不能每个字符各自居中重叠；
- 预览按同样的 reveal 公式逐字绘制。

### FR-3 关键词时长与位置（P0）

**时长以内容需求为准，不做硬性上限。**

- clip 时长由 LLM 依据内容决定，必须输出 `duration_sec` + `duration_reason`（为什么需要这么久：阅读时间/多行/引用/法律条文/语气停顿等）；
- 给出**建议区间**而不是硬限制：单行短词约 1.5–3s，多行/长引用可以更长；是否超过 6s 由内容判断，审核看 `duration_reason` 是否成立；
- 不允许“无理由默认整段时长”——未给 `duration_reason` 的 clip 视为样式/时长契约不完整，退回重生成；
- typewriter 的 reveal 时间与 clip 总时长解耦（见 FR-2），长 clip 不代表逐字要拖很久；
- 关键词 clip 起点对齐所在 video clip 的起点或语义句起点（由 LLM 给 `start_ratio`）；
- `position` 只能 `center` / `top`；禁止 `bottom`（字幕带）；
- 同一时间窗最多 1 条关键词文字动画，避免叠字；
- 与 CAPTION 同文的长句（>10 字）不生成文字动画，交给字幕。

### FR-4 渲染路径（P0）

默认 `caption_renderer=ass` 时必须支持文字动画：

1. `_apply_text_ass()` 为每个 overlay 计算独立 `TextStyle`；至少生成 per-overlay `Style:` 行或逐行 override；
2. `typewriter` 在 ASS 路径生成**逐字 Dialogue**：同一 `\an7` 起点 + `\pos(x,y)`，避免每个字居中重叠；
3. `fade_in/slide_in/scale_in/highlight` 生成对应 ASS override（`\fad` / `\t` / `\move` / `\k`）；
4. `box` 使用 ASS `BorderStyle=3` 或底层 `\p` 绘图区实现纯色底板；一期至少支持纯色圆角近似（方角）和条带底板；
5. 若某动效 ASS 无法表达，允许该 overlay 单独走 drawtext 路径（不是整片切换），并在 fidelity 里记录降级。

### FR-5 预览一致性（P1）

- 前端预览按同一 `TextAnimationStyle` 渲染文字动画；
- typewriter 预览与成片使用同一 reveal 公式；
- 至少保证：文字内容、字体大小、颜色、位置、底板、描边、阴影、进出场时机一致；
- 记录“预览近似”差异，不要求像素级一致。

### FR-6 LLM 提示词与契约（P0）

`text_generation` 的 system/user prompt 增加：

```json
{
  "style": {
    "font": "sans-serif",
    "font_size": 56,
    "font_weight": "bold",
    "letter_spacing": 2,
    "text_align": "center",
    "fill": "#ffffff",
    "box": false,
    "box_color": "#00000080",
    "box_radius": 6,
    "stroke_width": 3,
    "stroke_color": "#000000",
    "shadow_x": 0,
    "shadow_y": 4,
    "shadow_blur": 12,
    "shadow_color": "#000000b3",
    "motion": {"type": "typewriter", "reveal_sec": 1.2, "cursor": true}
  }
}
```

- 颜色必须来自 Persona 色板或遵守对比度；
- 禁止默认白字无描边/无阴影；
- 长句（>16 字）不许生成关键词动画，返回 `caption_only=true`；
- 输出必须通过 `validate_text_style`；不合法时修复而不是静默变白字。

### FR-7 校验、降级与质量（P1）

- 校验：文字动画必须有 `style` 和 `motion`；typewriter 必须有 `reveal_sec`；
- 降级：样式字段非法时用安全默认（白字+黑描边+阴影），并在 degradation 里记录 `text_style_invalid`；
- 质检新增：
  - 白色无描边文字 + 无底板 → warning（观众可读性）；
  - clip 时长与 `duration_reason` 不一致（例如 reason 说“短促强调”却占满整段）→ warning；
  - 同时窗多条关键词 → error；
  - typewriter 在成片帧中一次性全显 → error（抽帧验收）。

## 6. 数据契约

`clip.metadata.style`（JSON）：

```json
{
  "font": "sans-serif",
  "font_size": 56,
  "font_weight": "bold",
  "font_italic": false,
  "letter_spacing": 2,
  "line_height": 1.25,
  "text_align": "center",
  "fill": "#ffffff",
  "box": true,
  "box_color": "rgba(220,20,20,0.92)",
  "box_radius": 6,
  "box_padding_x": 18,
  "box_padding_y": 8,
  "stroke_width": 2,
  "stroke_color": "rgba(0,0,0,0.9)",
  "shadow_x": 0,
  "shadow_y": 4,
  "shadow_blur": 12,
  "shadow_color": "rgba(0,0,0,0.7)",
  "glow_color": "",
  "glow_width": 0,
  "motion": {"type": "typewriter", "reveal_sec": 1.2, "cursor": true},
  "position": "center",
  "offset_x": 0,
  "offset_y": 0,
  "avoid_caption_band": true
}
```

映射：

- `fill` → ASS `\1c` / drawtext `fontcolor`
- `stroke_*` → ASS `\bord` + `\3c`
- `shadow_*` → ASS `\shad` / `\4c`；drawtext shadowx/y
- `box` → ASS `BorderStyle=3` + `\3c`（或 `\p` 绘图）
- `motion` → ASS `\fad` / `\t` / `\move` / 逐字 Dialogue
- `position/offset` → `\an` + `\pos` / `MarginV`

## 7. 验收标准（按当前真实状态修订）

> 以下验收项**尚未全部通过**。不通过项已在 §12–§14 标注，不能再把“单测通过/有产物”当成验收通过。

1. **任务终态**：任务必须 `done`；不能是 `failed/capped`，也不能靠 accepted 收据把未修复的 motion 降级带过去。
2. **337s**：文字「没有通报」按新样式出现（颜色/描边/阴影/底板可配），时长由 `duration_reason` 支撑，结束前淡出；不与底部字幕重叠。
3. **493s**：文字「只禁离线下载」在 clip 开始后按 `reveal_sec` 逐字出现；抽帧 `+0.2s/+0.6s/+1.2s` 分别看到部分/更多/完整字符；不允许首帧全文。
4. **样式多样性**：同一支片至少 3 种可区分文字样式（如白字描边、红色底板、黄色高亮）；当前实际 19 条全白、无底板，**未通过**。
5. **预览与成片**：内容/位置/颜色/描边/阴影/底板一致；当前预览 box 圆角与 ASS 方角不一致，**未通过**。
6. **动效真实实现**：slide_in / scale_in / highlight 必须有真实位移动画/缩放/高亮；glitch/neon 未实现时必须显式 `DEPENDENCY_MISSING` 或 error，不能 fade 冒充。
7. **样式字段生效**：text_align、line_height、offset_x、box_radius、box_padding 必须在成片生效；当前 ASS 不生效。
8. **工具自主性**：LLM 必须能通过一个带 style/motion 的工具把效果写入文字轨；当前 25 个 text_* 工具都没有完整 style 参数，**未通过**。
9. **自动化**：
   - `test_text_style_contract`、`test_typewriter_reveal`、`test_ass_text_animation`、`test_text_clip_duration_policy`（已有，但只覆盖基础）；
   - 新增 motion 实现、box 几何、样式多样性、预览—成片一致、任务 done 的测试；
   - 全片抽帧断言，不只人工截图。
10. **全片**：任务 done + render 成功 + `ffprobe` 702s + 抽帧验收全部通过。

## 8. 里程碑（按完成度重标）

| 阶段 | 状态 | 产出 / 未完成 |
|---|---|---|
| M1 契约 | ✅ 已完成 | style/motion/duration_reason 契约、prompt 示例、单测 |
| M2 生成 | ✅ 已完成 | 时长/样式写入 clip、淡出、类型提示词 |
| M3 Typewriter | ✅ 已完成 | ASS 逐字、固定起点、reveal 独立；cursor/标点停顿未完成 |
| M4 样式渲染 | ⚠️ 部分完成 | per-overlay 颜色/描边/阴影、方角 box；圆角/内边距/对齐/行高/offset_x 未完成 |
| M5 验收 | ⚠️ 部分完成 | 有全片 render 与局部抽帧；任务 failed、样式单一、动效未兑现，验收未通过 |
| M6 引擎/工具 | ❌ 未完成 | slide/scale/highlight/glitch/neon/渐变/撕纸/多层描边、apply_text_style 工具 |

## 9. 风险与开放问题（真实状态）

- **ASS 的能力边界**：圆角/内边距/渐变文字/多层描边/故障/霓虹无法靠当前 ASS 实现；必须用 `\p` 背景层、MG HTML 或专门 renderer，否则只能明确不支持。
- **“二期”不能吞掉需求**：用户图片里的渐变/撕纸/故障/霓虹属于输入需求；本 PRD 未实现，验收必须标失败，不能把“二期”当完成。
- **长片段覆盖率**：`text_motion_weak` 按比例计算，内容驱动长停留会天然低覆盖；需要区分“内容要求 hold”与“动效缺失”，否则任务永远无法 done。
- **样式多样性**：LLM 默认全白；prompt/质量门禁必须强制至少 N 种样式，否则“可配置样式”只是字段存在。
- **预览与成片**：预览圆角/box 与 ASS 方角不一致；预览未实现 slide/scale/highlight/glitch，不能宣称一致。
- **工具自主性**：25 个 text_* 工具输出独立 mp4，不写文字轨；没有 apply_text_style，LLM 不能自主完成图片效果。
- **验收截图**：当前只有人工截图，没有自动化逐段断言；任务 failed 也说明“成功出片”未闭环。

## 10. 代码证据（历史诊断；部分已修，当前状态以 §12–§14 为准）

- `clipwright/agents/animation_agent.py::_build_text_clip_llm`：文字 clip 生成，样式只取 `main_style` 部分字段。
- `clipwright/services/render.py::_apply_text_ass`：默认 ASS 路径，未消费 `anim_type/keyframes`；样式取首条 overlay。
- `clipwright/services/render.py::_build_drawtext_filter`：typewriter 分支存在，但默认不走；x 从左边起排、reveal 摊满整段。
- `clipwright/tool/design.py::TextStyle`：已有描边/阴影/发光/box 字段，但 `build_ass_style` 未使用 box，且 `build_ass_dialogue` 没有逐字位置参数。
- 成片抽帧：`_cache/render_check_337.jpg`、`_cache/render_check_493.jpg`（本次分析用），新验收用逐字帧。


## 11. 实现记录（2026-11-15，只记录已落地代码，不代表验收通过）

- `e0ef24e`：`TextAnimationStyle` 契约、`text_animation` prompt/校验、时长/淡出、ASS 逐字与 per-overlay 样式、底板。
- `5725d3c`：quality 增加文字样式/时长依据/叠字检查。
- `31ead06`（前端）：预览文字样式、底板、typewriter、fade 与成片语义对齐。
- `338b4e1`：6 份文字类型提示词示例补齐 `text_animation`。
- `fc8f662`：style 的 `{accent}` 参数占位符解析。

已验证：

- `tests/clipwright/test_text_animation_style.py`：4 passed（样式归一、legacy 时长/reason、ASS 逐字 6 行且 `\pos/\an7`、多样式与 box BorderStyle=3）。
- `tests/clipwright/test_ass_style.py`：22 passed。
- 抽帧合成验证：`_cache/text_style_check/txt_0.mp4`，0.8s 只见“离线”、2.2s 完整“离线下载功能”，红底白字；4.8s “没有通报”带描边/阴影；证明逐字、底板、描边/阴影在 ASS 路径生效。
- 前端 `npx tsc --noEmit` + preview 151 tests 通过。

全片验收（2026-11-15 晚）：

- 重置旧 task 黑板为 edit 产物后只重跑 animation → audio → render；
- 新成片：`renders/从_PCL_到_HMCL..._1920x1080_20261115111153.mp4`，702.4s，127,831,470 bytes，h264+aac；
- `new_337.jpg`：「没有通报」白字+描边，字幕在底部不重叠；
- `type_478.9.jpg`：只显示「只禁离」（逐字 reveal 生效）；`new_493.jpg`：18s clip 完整显示「只禁离线下载」；
- 新文字 clip metadata 已带 `motion.reveal_sec`、`display_sec`、`duration_reason`、style。

仍属二期：底板圆角/内边距在 ASS 的精确像素对齐、渐变/故障/霓虹等复杂样式；本期 glitch/slide 等 motion 在 ASS 路径按 fade 兜底，不冒充完整特效。


## 12. 需求完成度审计（2026-11-15，忠实呈现未完成项）

> 结论：**当前不能算完成本条 PRD 的全部需求。** 代码层已完成样式契约、时长依据、ASS typewriter、per-overlay 样式；但下列 P0 需求仍未完成或只做了近似，且任务终态是 `failed`。

### 12.1 未完成 / 不达标

| # | 需求（PRD/用户原话） | 当前状态 | 证据 / 差距 |
|---|---|---|---|
| 1 | 完整跑完并输出合格成片，任务终态不能 failed | ❌ 未达成 | `task_a3e78e2fda` 最终 `failed`；animation 3 轮后仍有 8 段 `text_motion_weak` + 1 段 `mg_motion_weak`，走了 accepted 收据；quality 未作为成功门禁闭环 |
| 2 | `slide_in` / `scale_in` / `highlight` / `glitch` / `neon` 动效 | ❌ 未实现 | ASS 路径仅对 `typewriter`/`word_by_word` 做逐字；其余 motion 一律只加 `\fad`，实际是淡入淡出，**没有位移动画、缩放动画、高亮扫过、故障/霓虹** |
| 3 | 不得把未实现的特效静默降级 | ❌ 违反 | `_apply_text_ass` 对 `slide_in/scale_in/glitch` 没有 fidelity 记录，也不报错，直接按 fade 输出；预览同样只处理 typewriter/fade |
| 4 | `word_by_word` 逐词出现 | ⚠️ 名义实现、实际逐字 | 代码把 `word_by_word` 与 `typewriter` 一起按**字符**拆分 Dialogue，不是按词 |
| 5 | 底板圆角 `box_radius`、内边距 `box_padding_x/y` 精确生效 | ⚠️ 方角近似 | ASS 用 `BorderStyle=3`，只用了 `box_border` 作为框厚度；`box_radius` / `box_padding_*` 在 ASS 渲染中未使用（预览有 roundRect，但与成片不一致） |
| 6 | 渐变填充、撕纸/条带、描边示例、故障风、霓虹风等用户示例样式 | ❌ 未实现 | 仅支持纯色 fill；`fill` 只接受单一颜色；渐变/纹理/多层描边/故障/霓虹均为空 |
| 7 | Typewriter 标点停顿、cursor、结束前 0.2s 淡出 | ⚠️ 部分 | 逐字和 `\fad(80,150)` 已实现；标点额外停顿未实现；`motion.cursor` 字段未消费；结束淡出是每字 `fad` 尾，非统一 0.2s clip 淡出 |
| 8 | 同一支片至少 3 种可区分文字样式 | ❌ 未达成 | 实际 LLM 输出 19 条文字全部 `fill=#ffffff`、`box=False`，只有描边/阴影默认值；没有红底/黄高亮/故障等变化；prompt 未强制颜色/底板多样性 |
| 9 | 预览与成片样式一致 | ⚠️ 部分 | 预览支持 fill/box/stroke/shadow/typewriter/fade；但 slide/scale/glitch 预览也未实现；底板圆角预览与 ASS 方角不一致；无专门视觉回归测试 |
| 10 | 时长由内容决定、不设硬限制 | ⚠️ 部分 | `duration_reason/display_sec` 已落地；但 `normalize_display` 仍会把 display 夹到宿主片段上限（物理上避免越界），且质量门禁仍按“长片段覆盖率”判弱 |
| 11 | quality 通过或明确接受后继续 render | ⚠️ 流程可用但不等于“合格” | 本次是 accepted 收据后手动 render；quality 未真正判定成片合格，任务仍 failed |
| 12 | M5 全片抽帧验收 | ⚠️ 只验证了局部 | 已抽 478.9s 逐字、493s 全文、337s 白字+描边；但这不是“合格成片”验收：白字样式单一、弱动效仍存在、任务 failed |

### 12.2 已完成（可复核）

- `text_animation` 数据契约 + `TextAnimationStyle` 归一化/校验；
- display_sec + duration_reason 生成链路；
- ASS per-overlay 样式表（颜色/字号/描边/阴影/字体）；
- typewriter 逐字 Dialogue、固定左起点 `\an7\pos`、reveal_sec 独立于 clip；
- 预览 fill/box/stroke/shadow/typewriter/fade；
- quality 对纯白无装饰、缺 duration_reason、同窗叠字的检查；
- 6 份提示词示例补齐契约；
- 合成渲染抽帧与全片渲染（702.4s，h264+aac）。

### 12.3 下一步必须做（按优先级）

1. **任务终态**：把 animation 的 `text_motion_weak/mg_motion_weak` 做成可修复闭环（或调整“长片段按比例覆盖”判定），让任务最终 `done` 而不是 accepted failed；quality 必须真正检查成片。
2. **motion 实现**：ASS 实现 slide（`\move`）、scale（`\t(\fscx,\fscy)`）、highlight（`\k`/双色层）；不支持的 glitch/neon 明确报错/记录 fidelity，禁止静默 fade。
3. **word_by_word** 改为按词拆分；cursor/标点停顿按 FR-2 实现。
4. **底板**：ASS 用 `\p` 绘图或独立背景层实现圆角/内边距；与预览 roundRect 对齐。
5. **渐变/故障/霓虹**：要么实现，要么从一期验收移除并明确标注“未实现”，不能列为已完成。
6. **样式多样性**：prompt 强制至少 N 种样式/底板，质量门禁检查全片样式单调；重跑 animation 后抽帧验证 3 种以上可区分样式。
7. **验收**：任务 `done` + render + 337/478.9/493/其他样式点抽帧，全部通过后才可把本 PRD 标为完成。


## 13. 第二轮审计：仍未完成 / 只在代码里“半实现”的项（2026-11-15）

> 这一节是对 §12 的补充。**不能因为字段存在、单测通过，就认为需求完成**；以下逐项对照用户需求与真实渲染行为。

### 13.1 样式字段“有名无实”清单

| 需求字段 | 契约/预览 | ASS 成片实际 | 差距 |
|---|---|---|---|
| `text_align`（left/center/right） | 预览使用 | ASS 只按 `position` 设 `\an`，没有用 `text_align` | 左/右对齐在成片不生效 |
| `line_height` | 字段存在 | ASS/Drawtext 均未使用；换行行距固定 | 多行行距不可控 |
| `offset_x` | 字段存在 | ASS 只用 `MarginV` 处理 `offset_y`，`offset_x` 未使用；预览也未按 style offset 定位 | 水平偏移在成片/预览都不生效 |
| `offset_y` | 字段存在 | ASS 用行级 MarginV 近似；`position=center` 时语义不稳 | 不同对齐下偏移会漂移 |
| `box_radius` | 预览 roundRect | ASS `BorderStyle=3` 只有方框 | 圆角不生效 |
| `box_padding_x/y` | 预览有绘制 | ASS 只用 `box_border` 作为框厚度，padding 未映射 | 内边距不生效 |
| `background`（渐变/纹理） | 未实现 | 未实现 | 用户示例里的渐变/撕纸/条带没有 |
| `font` 字体族 | 字段存在 | `_ass_fontname` 只清洗，不保证系统存在；缺字体静默回退 MSYH | 字体选择不可验证 |
| `stroke` 多层/描边风格 | 单层 | ASS 单层 `\bord` | 示例里的描边+外发光组合只部分生效 |
| `glow` | 字段存在 | ASS 用 `\blur+\3c` 近似；是否作用于阴影/描边未验证 | 发光语义不精确 |
| `fill` 渐变 | 非目标 | 未实现 | 用户示例中的渐变字未实现 |

### 13.2 动效“静默降级”清单

- `slide_in`：ASS 里没有 `\move`/关键帧位移；实际只加 `\fad`，**位置不动**；
- `scale_in`：ASS 里没有 `\t(\fscx,\fscy)`；实际只淡入，**不缩放**；
- `highlight`：没有 `\k` / 双色层；实际只淡入，**不高亮**；
- `glitch` / `neon`：未实现，仍被归入 fade；
- `word_by_word`：与 typewriter 共用逐**字符** Dialogue，按词效果不存在；
- `typewriter`：无标点停顿、无 cursor、无统一 0.2s 结尾淡出（每个字符各自 `\fad`）；
- 预览：只实现 typewriter/fade；slide/scale/highlight/glitch/neon 在预览同样不可见；
- 预览文字不消费 `clip.keyframes`（只有 `motion`），所以 LLM 产出的关键帧缩放入场在预览里也不会体现。

### 13.3 生成与契约

- `ensure_text_animation` 在 LLM 未给 `text_animation` 时会自动补 legacy `duration_reason`，**导致“缺少 reason 必须退回重生成”的验收被绕过**；
- `normalize_display` 会把 `display_sec` 夹到宿主片段时长；用户要求“不硬限制”，当前只做到“不额外造上限”，仍受物理片段限制；
- 实际生成 19 条文字全部 `fill=#ffffff`、`box=False`；prompt 没有强制“至少 N 种样式/必须使用底板或强调色”，LLM 可以全部输出白字；
- 样式多样性没有质量门禁，只有“纯白无描边/阴影/底板”warning；因为默认有描边，warning 也不会触发；
- `TextStyle` 默认 `stroke_width=2`、`shadow` 默认值使“纯白裸字”检查永远通过，无法发现单调。

### 13.4 质量与任务终态

- `task_a3e78e2fda` 最终 **failed**，不是 `done`；成片是 accepted 收据后手动 render；
- quality 仍以 frame_validator 为主，没有对文字样式多样性、motion 是否真正实现、底板几何做硬检查；
- `text_motion_weak` 只看关键帧覆盖，“长片段 + 内容驱动时长”天然容易低覆盖；没有区分“内容需要长停留”和“动效缺失”；
- 任务没有“样式验收通过 → render → done”的闭环测试。

### 13.5 预览与验收

- 预览底板圆角/padding 与 ASS 方角不一致，不能声称“预览与成片一致”；
- 预览没有 slide/scale/highlight/glitch/neon；
- 没有针对 337s / 493s 的自动化抽帧断言（目前只有人工截图）；
- 全片抽帧只覆盖少数时间点，没有逐段检查文字 clip 的 duration_reason、样式、motion 是否兑现。

### 13.6 PRD 自身需要修正的地方

- 目标写“每个文字动画有明确动效……按渲染能力分级”，但一期验收却把 glitch/neon 列为“至少不冒充”；实际代码**静默 fade 冒充**，PRD 必须把“未实现”标为失败项，而不是可用降级；
- §12 的“全片验收”只能证明 typewriter 和局部白字，不能证明“样式配置”与“预览一致”；
- 用户示例中的渐变、撕纸、故障、霓虹属于本期需求输入，不能简单归入二期后宣称完成；要么排期实现，要么在 PRD 明确“本 PRD 不交付这些示例样式”。

### 13.7 结论

当前 PRD 的**代码骨架**已完成，但按“用户原始需求”验收，未完成项至少包括：

**任务成功闭环、slide/scale/highlight/glitch/neon、word_by_word、cursor/标点停顿、box 圆角/内边距、text_align/line_height/offset_x、渐变/撕纸/故障/霓虹、样式多样性、预览—成片一致、自动化抽帧验收。**

这些已写入本节；在以上项全部关闭前，PRD 状态保持“部分实现，验收未通过”。


## 14. 工具与引擎能力核查：图片示例能否由 LLM 自主实现？

> 结论：**不能全部自主实现。** 当前 LLM 能调用的文字动画工具是“独立 FFmpeg 视频生成器”，不是“可配置样式的文字层”；ASS/MG 引擎也只覆盖其中一部分。用户图片里的效果大多没有对应的工具参数或渲染能力。

### 14.1 LLM 可见的文字工具（text_animations 插件）

| 工具 | 能力 | 关键限制 |
|---|---|---|
| `text_fade_in` / `text_slide_*` / `text_zoom_*` / `text_scale_bounce` / `text_rotate_in` / `text_blur_in` / `text_wave` / `text_shake` | 对输入视频做 drawtext + FFmpeg 滤镜，输出**新 mp4** | 只接受 `text/font_size/font_color/position/start/duration`；没有底板/渐变/描边/阴影/圆角参数 |
| `text_typewriter` | drawtext 静态叠加 | 不是逐字；无 reveal/光标 |
| `text_letter_reveal` | 逐字符 drawtext | x 坐标硬编码 `8+i*22`，不居中；无样式 |
| `text_glow` / `text_rainbow` / `text_neon` | 单条 FFmpeg 滤镜（hue/发光/闪烁） | 只是独立视频；不是 ASS/MG 文字样式；无底板/渐变填充/故障 |
| `text_typing_cursor` | drawtext + 光标 | 光标坐标简单近似；无样式 |
| `text_perspective` / `text_flip_in` / `text_elastic` / `text_pulse` / `text_morph` | drawtext 旋转/字体大小/替换 | 不是 3D/真变形；效果有限 |
| `text_custom_json` | 自定义 JSON → drawtext | style 只支持 font_size/font_color/position/rotation/opacity/stroke_width/stroke_color/shadow(bool)；没有背景/渐变/圆角/故障/霓虹；输出 mp4，不写入时间线文字样式 |
| `text_gradient` | FFmpeg hue 循环 | 不是渐变填充；只是色相变化 |
| `typewriter_animation`（内置） | 静态 drawtext | 无逐字 |
| `tracking_text`（内置） | 直接返回 `DEPENDENCY_MISSING` | Manim 未接入，占位 |

这些工具是**独立视频渲染**，不是 `clip.metadata.style` 的文字层；即使主 Agent 调用，也不能自动把“样式化文字”写进动画/文字轨。当前 animation agent 的文字路径（`_build_text_clip_llm` + ASS）**不调用这些工具**。

### 14.2 引擎能力矩阵

| 效果 | ASS/drawtext | MG | 插件工具 | 结论 |
|---|---|---|---|---|
| 纯色字、字号、字重、字距 | ✅ | ✅ | ✅ | 支持 |
| 描边 | ✅ 单层 | ✅ `border` | ✅ 基础 | 单层可用；多层描边无 |
| 阴影 | ✅ 偏移/颜色；blur 近似 | ✅ `text_shadow` | ✅ 基础 | 可用，非精确 |
| 纯色方角底板 | ✅ `BorderStyle=3` | ✅ shape 背景 | ❌ | 方角可用 |
| 圆角/内边距底板 | ❌ | ✅ HTML | ❌ | ASS 不行；预览圆角但成片方角 |
| 渐变填充 | ❌ | ⚠️ 仅元素背景渐变，文字填充无 | ⚠️ hue 近似 | **未真正支持** |
| 撕纸/纹理/纸张底板 | ❌ | ⚠️ 需要图片素材 | ❌ | **需素材+合成，当前无自动化工具** |
| 故障/Glitch | ❌ | ⚠️ 理论上 CSS 可实现，无契约/验证 | ❌ | **未支持** |
| 霓虹 | ❌ | ⚠️ 发光可近似，闪烁无契约 | ⚠️ `text_neon` 独立视频 | 文字层不完整 |
| 逐字/光标 | ✅ 逐字；光标无 | ⚠️ | ✅ 独立视频 | 文字层光标未实现 |
| 滑动/缩放/旋转 | ❌（当前全 fade） | ✅ | ✅ 独立视频 | 文字层 ASS 未实现 |
| 文字对齐/水平偏移/行高 | ❌ | ✅ | 部分 | 成片 ASS 不支持 |
| 多层描边/3D/透视 | ❌ | ⚠️ | ⚠️ 旋转近似 | 未完整支持 |

### 14.3 用户图片逐项判断

| 图片示例 | 能否由 LLM 调工具自主实现 | 说明 |
|---|---|---|
| 商品 1/2：白字 + 红色底板/条 | ⚠️ 部分 | ASS 方角 box 可近似；圆角/内边距/精确条宽不行；MG 可由 LLM 生成 shape+text，但没有样式工具保证 |
| 黄色“示例文字”高亮 | ⚠️ 部分 | 无 `highlight` 文本层工具；只能用 box/shape 近似，非高亮扫过 |
| 蓝色下划线/底线 | ⚠️ 部分 | MG line/shape 可以，ASS box 不能只画下划线；无文字样式工具 |
| 撕纸/白纸底板 + 黑字 | ❌ | 需要撕纸纹理图片 + 合成；当前没有“底板素材/纹理”文字工具，LLM 不能自主保证 |
| 渐变字（紫/蓝） | ❌ | 文字 fill 不支持渐变；`text_gradient` 只是 hue 循环 |
| 故障/glitch 字 | ❌ | 无工具、无 ASS/文字层契约；MG 理论可做但未实现/未验收 |
| 霓虹发光字 | ⚠️ 部分 | `text_neon` 可产出独立视频，但不会进入文字层/ASS；文字层只有发光近似 |
| 多层描边/白色描边+蓝光 | ⚠️ 部分 | 单层描边+glow 近似；多层描边无 |
| 3D/透视字 | ⚠️ 部分 | `text_perspective` 旋转近似，不是真正 3D |

### 14.4 根因

1. **工具参数没有样式模型**：25 个 `text_*` 工具只暴露 text/font_size/font_color/position；没有 background/stroke/shadow/box/radius/gradient/motion 参数。
2. **工具输出不是时间线文字层**：工具返回 mp4，不写 `clip.metadata.style`；animation agent 也不调用它们。
3. **ASS 渲染器只有基础样式**；MG 有更丰富的 HTML/CSS，但文字样式契约没有把 MG 的 style 暴露给 LLM，且没有 glitch/neon/gradient 的 schema/验收。
4. **没有 `text_style` / `apply_text_effect` 工具**：LLM 无法声明“给这段文字加红色圆角底板+黄色描边+故障入场”，然后由渲染层执行。
5. **预览与成片能力不一致**；预览的圆角/内边距不是成片能力。

### 14.5 要让 LLM 自主实现图片效果，需要补的工具/引擎

1. 新增 `apply_text_style`（或 `text_effect`）工具：
   - 入参：`timeline_id/clip_id` 或 `text + start/duration`，`style`（FR-1 全字段），`motion`（FR-2），可选 `background_asset`；
   - 输出：写入文字轨的 clip（metadata.style），或返回可插入的 styled text spec；
   - 不接受“只给 mp4”的旁路。
2. ASS 渲染器补齐：slide/scale/highlight、text_align/line_height/offset、圆角/内边距（`\p` 背景层）、gradient 或明确拒绝。
3. MG 文字样式 schema：把 MG 的 background/text_shadow/border/渐变/故障/霓虹能力收敛为可校验的 style；LLM 用 `mg_dynamic` 生成时也能写同一套 style。
4. 为 glitch/neon/gradient/torn paper 定义明确的 fidelity：支持就渲染，不支持就 `DEPENDENCY_MISSING`/error，不能静默 fade/方角。
5. 质量门禁检查“声明样式 == 实际渲染”，并建立图片效果样例的视觉回归。

### 14.6 结论

- **LLM 现在不能仅靠工具自主实现这些图片效果**；只能对纯色/描边/阴影/方角底板做有限近似。
- **引擎也没有全部支持**：渐变文字、撕纸纹理、glitch、真正的霓虹/多层描边、圆角底板、滑动/缩放文字层均缺失。
- 图片示例属于**需求但未实现**，不是“已有能力”；PRD 状态仍为“部分实现，验收未通过”。


## 15. 覆盖核对：用户提出的问题是否都已进入 PRD

| 用户关注点 | PRD 位置 | 是否忠实记录 |
|---|---|---|
| 任务 `task_a3e78e2fda` 最终 failed | §12.1、§7.1、§13.4 | ✅ |
| typewriter 是否真正逐字 | §13.2、§7.3、§11 | ✅ 已实现但 cursor/标点未完成 |
| slide/scale/highlight/glitch/neon 只 fade | §13.2、§7.6、§14.2 | ✅ 标为未实现/静默降级 |
| word_by_word 实际逐字 | §13.2、§7.6 | ✅ |
| 底板圆角/内边距在 ASS 是方角 | §13.1、§7.7、§8 M4 | ✅ |
| text_align/line_height/offset_x 不生效 | §13.1、§7.7 | ✅ |
| 渐变/撕纸/故障/霓虹缺失 | §3、§13.1、§14.3 | ✅ 标为未实现，不再归“二期=完成” |
| 实际 19 条文字全白/无底板 | §13.3、§7.4 | ✅ |
| 预览与成片不一致 | §13.5、§7.5 | ✅ |
| 25 个 text_* 工具不能自主做图片效果 | §14.1、§14.3、§14.4 | ✅ |
| 引擎是否全部支持 | §14.2 矩阵、§14.6 | ✅ 明确“没有全部支持” |
| 缺 apply_text_style/text_effect 工具 | §14.5、§7.8 | ✅ |
| quality 未做样式/motion/几何验收 | §13.4、§7.9 | ✅ |
| 只做了局部抽帧，不是合格成片验收 | §12.1 #12、§13.5 | ✅ |
| PRD 状态是否可标完成 | 文档头、§12.7、§14.6、§15 | ✅ 保持“部分实现，验收未通过” |

> 结论：截至本版，**用户在本轮对话中提出的问题均已映射到 PRD 的具体章节**；PRD 不再把未实现项写成已完成，也不再以“二期/近似/收据”替代验收。


## 16. 第三轮实现与 E2E 记录（2026-11-15 晚，进行中）

### 16.1 本轮已关闭的缺口

| 缺口 | 状态 | 证据 / 代码 |
|---|---|---|
| ASS `slide_in` 只 fade、不位移 | ✅ 已修 | `\move` 写入；E2E 抽帧断言上移（`test_text_animation_e2e.py`） |
| ASS `scale_in` 不缩放 | ✅ 已修 | `\t(\fscx,\fscy)`；E2E 宽度增长断言 |
| `word_by_word` 实际逐字 | ✅ 已修 | 按 token 拆分（中文按字/英文按词），独立 Dialogue |
| typewriter cursor / 标点停顿 | ✅ 已修 | `cursor` + 标点 0.08s 停顿；E2E 检查 `▍` |
| `highlight` 只 fade | ✅ 已修 | karaoke `\k` + 高亮色；ASS `raw_text` 支持 |
| 底板方角 | ✅ 已修 | `\p1` 矢量圆角 + `box_radius/box_padding`；抽帧 `_cache/box_round.jpg` |
| `text_align` / `offset_x` | ✅ 基本修复 | `TextStyle.text_align` 映射 `\an`；`offset_x` 走 `\pos`；预览同步 |
| LLM 无样式工具 | ✅ 新增 | `apply_text_style` 工具（`text_style` 能力）写入 `clip.metadata.style/motion` |
| 长 hold 被误判 `text_motion_weak` | ✅ 已修 | 改为“运动窗口 <0.30s”判定，不再按整段时长比例 |
| 样式单调无门禁 | ✅ 提示+告警 | prompt 增加样式变化要求；quality 增加 `text_style` 单调 warning |

### 16.2 本轮 E2E

- `tests/clipwright/test_text_animation_e2e.py`：3 passed
  - typewriter：0.45s 白像素/宽度 < 1.5s，证明逐字；
  - slide：早期 y > 后期 y，证明上移；
  - scale：早期宽度 < 后期宽度，证明缩放；
  - rounded box：红色像素 >1000，证明矢量底板。
- 真实前端 render（`render_30f9c53a4793`）：
  - 成片 `renders/..._20261115131502.mp4`，702.4s，127,800,158 bytes；
  - `_cache/e2e_478.9.jpg`：typewriter 只显示「只禁离」，红底圆角；
  - `_cache/e2e_493.jpg`：完整「只禁离线下载」+ 红底圆角；
  - `_cache/e2e_337.jpg`：「没有通报」+ 黄色圆角底板。
  - 说明：该 render 是现有时间线上改两条文字 clip 的样式后重渲，不是全新 animation→audio 任务终态；任务终态仍未 done。

### 16.3 仍未完成（按验收口径）

- **任务终态仍 failed**：本次没有重新跑到 done；accepted 收据不能替代。
- **渐变文字、撕纸/纹理、故障/glitch、真霓虹**：ASS 仍不支持；`apply_text_style` 对 glitch/neon 明确拒绝，但 render 对旧数据的 glitch/neon 仍只记 warning + fade。
- **line_height**：ASS/drawtext 仍不生效（预览也未实现）。
- **text_align/offset 精度**：已按近似坐标实现，多行/不同字体的精确对齐未验收。
- **`apply_text_style` 尚未被主 Agent/动画 Agent 自动调用**：工具存在，但生成链路仍走文本 clip 自动样式；LLM 尚不能自主选择“给这条加红底/渐变/故障”。
- **style 多样性**：只有 prompt 引导 + warning，未强制；实际全白仍可能。
- **E2E 覆盖**：目前是 synthetic pytest + 已有时间线重渲；缺“新 requirements 任务 animation→audio→render→done”的完整链路。

### 16.4 结论

本轮把 slide/scale/highlight/word/cursor/圆角底板/工具链补上了，但 PRD 仍**未全部完成**；状态继续保持“部分实现，验收未通过”。下一轮优先：任务 done 闭环、渐变/故障/霓虹/撕纸、line_height、LLM 自动调用 `apply_text_style`、完整任务 E2E。
