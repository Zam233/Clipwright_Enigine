# 文字动画 LLM 化重构 PRD

- 项目：Clipwright / 帧艺（后端 `J:\Clipwright`，分支 `feat/master-agent`）
- 日期：2026-10-30
- 关联：`docs/modern-agent-review-prd-2026-10-30.md`、`docs/web-search-fetch-prd-2026-10-30.md`
- 触发：成片实测「视频下方除字幕还多了其他的字」+ 用户裁定「文字动画也使用 prompt 实现，完全重构文字动画插件，不用模板，由 LLM 动态生成渲染」

## 1. 现状（代码事实）

| 环节 | 现状 | 证据 |
|---|---|---|
| 类型目录 | 文字动效是**固定枚举**（`fade_in`/`slide_*`/`highlight_flash`/`typewriter`…），关键帧来自查表 | `animation/catalog.py`（`AnimationCatalog.build_full_keyframes`） |
| 生成 | `AnimationAgent._handle_text_animation` 直接查表 → `Clip(kind=TEXT)` + `keyframes=full_kfs` | `agents/animation_agent.py`（`_handle_text_animation`） |
| 样式 | `DEFAULT_TEXT_STYLE`（继承字幕样式）+ persona 覆盖 | `services/subtitle.py` |
| 位置 | `metadata.position` 取自 persona，默认 `bottom` → **与字幕同区** | 同上 |
| 文本 | `_extract_text_with_source` 常取到旁白整句 → **与字幕同文** | 同上 |
| 渲染 | FFmpeg drawtext（固定滤镜形态），不支持结构化动效（逐字、强调块、描边动画） | `services/render.py::_build_drawtext_filter` |

后果（实测）：
1. 底部两行字（文字动画 + 字幕）；
2. 动效语言贫乏（查表关键帧 vs MG 的 LLM 设计）；
3. 与 38 个类型化 MG 的能力差距越拉越大。

## 2. 目标与非目标

**目标**
1. 文字动画改由 **prompt + LLM 生成**：类型语义、动效语言、构图、避让规则写进提示词，模型输出结构化 `text_def`（元素/关键帧/样式/锚点）。
2. 渲染层支持 `text_def`：优先走 MG 快路径（文字元素已在白名单），必要时回退 drawtext 参数化（不再是固定表）。
3. 与字幕**明确分工**：字幕只说"说出来的话"，文字动画只做**关键词/短语强调**；位置默认避开字幕区（center/upper），可配置安全区。
4. 动效与可读性门禁复用 MG 现有指标（coverage/位移比例/scrim/对比度）。
5. 失败与降级如实上报（`text_llm_fallback_template`、`text_motion_weak`），主 agent 可重派。

**非目标**
- 不改字幕（caption）的生成与对齐链路（已由 audio 阶段 + speech_alignment 负责）。
- 不删除 `AnimationCatalog`：保留为 **fallback**（LLM 不可用时）。
- 不做逐字卡拉 OK（未来扩展）。

## 3. 设计

### 3.1 契约：`TextDef`

```json
{
  "text_anim_id": "emphasis_keyword|reveal_line|quote_block|...",
  "duration_sec": 3.0,
  "anchor": {"position": "center|upper|lower", "safe_area": {"bottom_px": 160}},
  "elements": [
    {"id": "t0", "text": "PCL 被封", "style": {"font_size": 64, "weight": "bold",
      "color": "#F5C518", "letter_spacing": 2, "text_shadow": "0 2px 8px rgba(0,0,0,.6)"},
     "keyframes": [{"time": 0.0, "opacity": 0, "scale": 0.9},
                   {"time": 0.35, "opacity": 1, "scale": 1.0, "easing": "back-out"}]}
  ],
  "avoid": {"regions": ["caption_band"], "max_elements": 3}
}
```

- 与 `MGDef` 保持同构（elements/keyframes/easing/params），复用 `mg_fastpath` 与 `validate/repair`。
- `avoid.regions` 由 audio 阶段回填的「字幕带」bbox 提供；渲染期若越界，按 `position` 上移并记降级。

### 3.2 提示词族（`animation/prompts/text/*.md`）

`## Intent` / `## Semantic constraints` / `## Composition` / `## Motion` / `## Anti-patterns` / `## Example` / `## Style reference`（沿用 38 类型化 MG 的六段 + 风格参考）。

首批类型（对齐现有目录，语义化命名）：
`emphasis_keyword`（关键词强调）、`reveal_line`（整句揭示）、`quote_block`（引语块）、`label_callout`（标注条）、`number_punch`（数字冲屏）、`title_stack`（标题堆叠）。

提示词硬约束（写进每份 md 的 `Semantic constraints`）：
- 文本必须来自旁白，**允许摘要到 ≤12 字**，禁止编造；
- 不得与同时间窗字幕**逐字相同**（同文时只做一次"关键词抽取"）；
- 元素 ≤3、单行 ≤12 字；
- 关键帧覆盖 ≥60% 时长，入场 <1.2s，末段留 0.5s 静止；
- 位置默认 center/upper，禁止落入字幕带（bottom 160px）；
- 必须带 `text_shadow` 或半透明底板（可读性）。

### 3.3 生成与渲染

```
AnimationAgent._handle_text_animation
  ├─ 语义抽取：narration → 关键词/短语（LLM，可缓存）
  ├─ type 选择：由 structure 的 marker 或 LLM 依语义选（text_anim_id）
  ├─ 生成：text_generation.generate_for_type(prompt + persona + 字幕带 bbox)
  │     └─ 失败 → repair → 再失败 → AnimationCatalog 模板（fallback，登记降级）
  ├─ 门禁：coverage/位移/可读性（复用 mg 指标）+ 越界检查
  └─ 渲染：mg_fastpath（文字元素）→ 不行则 drawtext 参数化（新形态：
           font_size/letter_spacing/描边/阴影/位移均可由 keyframes 驱动）
```

### 3.4 降级与可观测
- `text_llm_failed` / `text_llm_fallback_template` / `text_motion_weak` / `text_overlap_avoided`（上移次数）
- 全部走 `note_degradation` → `StageResult.degradations` → 主 agent 可见（已有链路）。

## 4. 里程碑

| 里程碑 | 交付 | 验收（可测断言） |
|---|---|---|
| **M1 契约与骨架** | `TextDef` schema + `animation/prompts/text/*.md`（6 类）+ `text_generation.py`（生成/校验/修复）+ 单测 | 6 类各自能产出合法 TextDef；非法输入走 repair；prompt 含六段 + 风格参考 |
| **M2 渲染与避让** | mg_fastpath 支持 text_def；drawtext 参数化；字幕带 bbox 注入 + 越界上移 | 渲染出 30fps MOV；动画区像素变化 ≥15%；不落入 bottom 160px；与字幕同文时只保留一次 |
| **M3 接线与降级** | `_handle_text_animation` 改走 LLM（catalog 作 fallback）；降级四种；前端可见 | 真实管线里 method=`text_llm`/`text_fallback` 可查；降级出现在任务事件流；主 agent 收到后可按守门重派 |

## 5. 风险与回滚
- **风险**：LLM 生成的文字动效可能过花/不可读 → 用现有可读性门禁 + 元素数/字数上限强约束。
- **风险**：drawtext 表达力有限 → M2 优先走 MG 快路径，drawtext 只作兜底。
- **回滚**：`settings.text_animation_llm = False` → 全部走 AnimationCatalog（行为与今天一致）。


## 执行记录（2026-10-30 · M3 收口）

本轮把三份 PRD 的 M3 全部落地，并跑通后端全量回归（4840 passed / 4 skipped / 1 xfailed）
与前端全量回归（128 files / 1107 tests，`tsc` 干净）。要点：

- **归一化兜底**：内容键 `content/text/label/title/value/caption/words` 全收，
  没文字的 text 元素**丢掉**而不是整段退模板；关键帧时间键
  `time/t/keyframes/frames/...` 全收，缺时间时**均匀铺到 [0,duration]**
  （旧行为全落 0 → 动效覆盖 0% → 判弱动效 → 回退模板）。
- **位置必须进 style**：`_extract_text_overlay` 把 `position/offset_y/font_size/font_color`
  写进 style dict——ASS 路径只认 style，旧实现让 `position=center` 被默认 `bottom`
  顶掉，于是文字动画**全画进字幕带**（成片里看不见，与字幕叠住）。这是本轮实测到的
  真实缺陷，修复后取帧可见：文字居中、字幕在底部、互不侵占。
- **字号下限**：文字动画（画面主体）在 1080p 基准下 ≥56px（LLM 给 28 这种字幕级字号时兜底）。
- **真实渲染核对**：新增 `render_qa`（横带墨迹率 + 帧间运动量）与
  `tests/clipwright/test_text_render_real.py`（真跑 ffmpeg 渲染 + 取帧比对：
  有/无文字整帧差异 >1%、中心带墨迹显著增加、字幕带几乎不变、入场动画运动量
  ≥ 静止窗口 1.5 倍）。


### 第 8 轮 · 真实 E2E 里的文字动画核对

- 成片 `机械键盘为什么会发出声音_1920x1080_20261030052243.mp4`（60.0s / 1080p30）里，
  文字动画 clip（`start=51s, 9s, font_size=96, position=center, type=reveal_line`）**画在画面正中**，
  底部字幕独立成行，互不侵占（取帧 `e2e/artifacts/final/final-frame-54.png`）。
- `render_qa` 对 51–53s 窗口：center 墨迹 0.470、caption 0.032（文字没进字幕带），
  窗口内帧间运动 1.65（入场动画在动）。
- 时间线里文字层只有 1 条（该片的动画阶段只产出 1 条文字动画）；数量由上游场景规划决定，
  渲染与位置已按上述验证通过。
