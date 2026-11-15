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

## 7. 验收标准

1. **337s**：文字「没有通报」按新样式出现（颜色/描边/阴影可配），hold ≤ 3s，结束前 0.2s 淡出；不与底部字幕重叠。
2. **493s**：文字「离线下载功能」在 clip 开始后 1.2s 内逐字出现；抽帧 `+0.2s/+0.6s/+1.2s` 分别看到 1–2 / 3–4 / 全部字符；不允许首帧全文。
3. 同一支片至少 3 种不同文字样式（白字描边、红色底板、黄色高亮）在成片里可区分。
4. 预览与成片在 05:37 / 08:13 的文字内容、位置、颜色、底板一致。
5. 自动化：
   - `test_text_style_contract`：style 字段完整、非法字段降级；
   - `test_typewriter_reveal`：逐字时间公式；
   - `test_ass_text_animation`：ASS dialogue 含 `\fad` / 逐字行/`\pos`；
   - `test_text_clip_duration_policy`：`duration_sec` 与 `duration_reason` 必须同时存在，且时长不得无理由默认等于宿主片段（不设硬上限）；
   - 前端预览快照/时间推进测试。
6. 回归：重跑 render，`renders/` 出片，`ffprobe` 时长 702s，抽帧验收通过。

## 8. 里程碑

| 阶段 | 内容 | 产出 |
|---|---|---|
| M1 契约 | `TextAnimationStyle` + 校验 + prompt | 数据契约、prompt 更新、单测 |
| M2 生成 | `_mgdef_to_drawtext_keyframes` 提取样式；按内容决定 clip 时长并记录 reason；淡出 | 文字 clip 带样式、时长有内容依据 |
| M3 Typewriter | ASS 逐字 Dialogue + reveal 公式 + 预览同步 | 493s 逐字抽帧通过 |
| M4 样式渲染 | per-overlay ASS 样式/底板/描边/阴影；drawtext 兜底 | 3 种样式成片可区分 |
| M5 验收 | render 抽帧、预览对比、质量规则 | 验收报告 + 成片 |

## 9. 风险与开放问题

- ASS 的底板圆角、渐变、故障风能力有限；一期做纯色方角/条带，复杂样式二期或 drawtext/Canvas 兜底。
- 逐字 Dialogue 在长文本上事件数爆炸：限制 ≤40 字，超长走 `caption_only` 或逐词。
- 预览与 ASS 的字体度量差异：以成片为准，预览标注“近似”。
- 既有 `drawtext` 路径的 typewriter 是按整段时长摊开且从左边起排，需按本 PRD 修正（reveal 独立、整行居中）。
- 质量门禁此前因主观语义项 fail 12 次，已在 `abdd0b4` 将 semantic/brief/type 降为 warning；本 PRD 的文字样式检查应作为新的硬/软门禁，不重复阻塞。

## 10. 代码证据（现状）

- `clipwright/agents/animation_agent.py::_build_text_clip_llm`：文字 clip 生成，样式只取 `main_style` 部分字段。
- `clipwright/services/render.py::_apply_text_ass`：默认 ASS 路径，未消费 `anim_type/keyframes`；样式取首条 overlay。
- `clipwright/services/render.py::_build_drawtext_filter`：typewriter 分支存在，但默认不走；x 从左边起排、reveal 摊满整段。
- `clipwright/tool/design.py::TextStyle`：已有描边/阴影/发光/box 字段，但 `build_ass_style` 未使用 box，且 `build_ass_dialogue` 没有逐字位置参数。
- 成片抽帧：`_cache/render_check_337.jpg`、`_cache/render_check_493.jpg`（本次分析用），新验收用逐字帧。


## 11. 实现记录（2026-11-15，部分完成）

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
