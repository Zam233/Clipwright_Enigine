# 深读：三部分细说 + 前端与 OpenCut 差距

- **日期**：2026-09-13
- **配套文件**：[opencut-video-use-hyperframes-adoption-2026-09-13.md](opencut-video-use-hyperframes-adoption-2026-09-13.md)（总报告）、`_research/findings-*.md`（分项原始材料）
- **本文范围**：① 借用清单逐条细说（含落地设计/验收/风险）；② MPL-2.0 合规的条款级判定与现成模板；③ HyperFrames 增强的代码级改法与实测；④ 前端与 OpenCut classic 的能力差距矩阵。

---

# 第一部分：能借的东西，逐条细说

## 1.1 从 video-use 借（MIT；价值在"规则 + 编排"，不在代码）

### V1 十二条生产正确性硬规则 → 变成 `services/render.py` 的断言与顺序

**它为什么值钱**：这 12 条是"偏离即静默失败"的正确性条款（`SKILL.md:18-33`），不是审美偏好。把它固化成代码里的顺序与断言，比写进文档有效得多。

**我们现状 vs 它**（逐条核对，✅=已有 / ⚠️=部分 / ❌=缺）：

| # | 规则 | 我们现状 | 证据 |
|---|---|---|---|
| 1 | 字幕在合成链**最后** | ✅ **已修正**（2026-09-13）：新增 `_compose_video_stages` 固定顺序 = 图解/MG → 画中画 → 文本/字幕，并修正"缓存命中时重复叠加 MG/PIP"的隐患 | `services/render.py`（`_compose_video_stages`）；实测命令序 `concat → mg_overlay → text_ass → audio`（`scripts/verify_render_order.py`） |
| 2 | 逐段提取 + lossless `-c copy` concat | ✅ **已落地**（2026-09-13）：裁剪阶段已把每段编码成最终参数，无转场时改用 `concat` demuxer + `-c copy` 拼接（`_try_concat_copy`），省掉一次全片重编码；参数不一致/失败/`CLIPWRIGHT_CONCAT_COPY=0` 自动回退 filter 拼接 | `services/render.py`（`_video_signature` / `_try_concat_copy` / `_concat_segments`）；A/B 实测（`scripts/verify_concat_copy.py`）：编码调用 9 vs 11、两路 PSNR 61.7dB、时长一致 |
| 3 | 每个切点 30ms afade | ✅ **已修正**：`_apply_default_cut_fades` 给未显式设置淡化的音频片段补 30ms（同轨首尾相接的连续段不补），显式值（含 0）不被覆盖 | `services/render.py`（`_CUT_FADE_DEFAULT_SEC` / `_apply_default_cut_fades`）；实测 `afade` 值集 `{0.03, 1.0}` |
| 4 | overlay `setpts=PTS-STARTPTS+T/TB` | ✅ 已实现并可注入窗口 | `animation/hyperframes_renderer.py:382-397` |
| 5 | 字幕用**输出时间轴**偏移 | ✅ 有 `_r1_seg_shifts` 漂移校正（转场累计时长） | `services/render.py`（漂移校正块） |
| 6/7 | 切点落字边界 + 30–200ms padding | ❌ 取决于 EditAgent 的 prompt，无强制 | 需在 `agents/edit_agent.py` 落约束 |
| 8 | 字级 verbatim ASR | ⚠️ 取决于转写源 | 与 `services/transcribe` 相关 |
| 9 | 转写按源缓存 | ✅ 素材 sha256 去重已有 | AGENTS.md「资产治理」 |
| 10 | 多动画并行 | ⚠️ 管线上是并发（Semaphore 6），但"每动画一个自治单元"没有 | `services/render.py:1944-1958` |
| 11 | 执行前先确认策略 | ✅ 需求确认流 + dry-run | AGENTS.md |
| 12 | 产物只写输出目录 | ✅ 有 work_dir / 产物清理白名单 | `docs/fix-report-2-render-tool-plugin.md` |

**落地设计**（最小改动、最大收益）：

```python
# services/render.py —— 合成顺序常量 + 断言（把"顺序"变成可测事实）
_RENDER_STAGES = ("trim", "concat", "overlay_mg", "pip", "text_subtitle", "audio")
# 规则 1：文本/字幕必须晚于 MG/图解 overlay
assert _RENDER_STAGES.index("text_subtitle") > _RENDER_STAGES.index("overlay_mg")
```

- 改变实际顺序时，把 `_apply_text_concat`（`render.py:957`）移到 `_apply_all_hyperframes`（`render.py:980`）**之后**；注意 `_apply_text_concat` 目前与 concat 合并在同一 filtergraph，拆分需先做（否则要两遍编码，参考规则 2 的取舍）。
- 规则 3：在音频段合成处，当 `fade_in/fade_out` 未显式给出时注入 `0.03` 默认值（`render.py:2435` 附近），并加单测断言「每个段首/段尾都有 ≥30ms 淡化」。

**验收**：新增单测 `test_render_stage_order`（顺序断言）+ `test_cut_fade_default`（默认 30ms）；E2E 用 `scripts/e2e_smoke.py` 跑一遍确认字幕不再被 MG 遮住。

**风险**：改变合成顺序会让 `_apply_text_concat` 的缓存键（`render.py:903` 的 pre-audio stage cache）失效一次；字幕与 overlay 同帧时可能出现层级争议（需产品确认"字幕永远最上"）。

### V2 每动画一个并行 sub-agent + 10 项自包含 brief

**它的做法**：每个动画 slot 一个 sub-agent，一次 spawn N 个（`SKILL.md:249-262`）；brief 固定 10 项（目标/绝对路径/技术规格/调色板/字体/逐帧时间轴/反清单/代码模式/交付检查/"不许提问"）；"一个 sub-agent 一个文件"避免互相覆盖。

**我们现状**：`AnimationAgent._handle_llm_mg`（`agents/animation_agent.py:646-802`）是**串行单线程**逐镜头生成 + 自批判，渲染端才有并发（Semaphore 6）。

**落地设计**（分层，先做便宜的）：

1. **批量并行生成**：把 `mg_dynamic` 的生成任务收集成一个列表，用 `asyncio.gather` + 并发上限（复用 `settings.pipeline_concurrency`）批量跑 `mg_gen.generate`，每个任务写独立 `generation_id` 目录（`mg/generations/gen_*.json` 已经是独立文件，天然隔离）。
2. **brief 结构下沉到 prompt**：把 10 项 brief 模板化进 `mg/config.yaml`，字段来自 `scene_context`（已有 `title/keywords/description`）+ Persona 令牌 + 时间线 fps/宽高。
3. **产物契约**：每动画产出 `{html, mg_def, duration_sec, palette, font, reasons}`，缺字段即校验失败（复用现有 `validator.py`）。

**验收**：N=6 个 mg_dynamic 时，生成阶段墙钟 ≈ 最慢一个（±20%）；每个产物目录互不覆盖。

**风险**：LLM 并发触发限流；建议并发上限与 Provider 配置联动，失败降级为串行重试。

### V3 引擎按 slot 选型（不是所有动画都上 HyperFrames）

**它的做法**：HyperFrames / Remotion / Manim / PIL+ffmpeg 按动画类型选（`SKILL.md:203-214`），并明确"不要因为动画是 web 向就默认 Remotion"。

**我们现状**：MG 只有一条 HTML→HyperFrames 路线；文字动画走 drawtext、逻辑动画走 SVG/diagram。

**落地设计**：在 `animation/` 里加一个轻量"渲染器路由"，按镜头类型分流：

| 镜头类型 | 建议渲染器 | 理由 |
|---|---|---|
| 数字滚动/打字机/单条进度条/简单卡片 | **PIL + PNG 序列 + ffmpeg**（新，成本极低） | 无需 Chrome，单帧 20–50ms，迭代快；video-use 的 launch video 就是 PIL |
| 复杂排版/图表/多阶段动画 | HyperFrames（现状） | 已有链路 |
| 公式推导/状态机/图变换 | Manim（MIT，可选） | 明显强于 HTML 路线 |
| 3D/空间运动 | HyperFrames + three adapter | 见第三部分 |

**验收**：同一"数字滚动"镜头，PIL 路径的渲染耗时 < 现路径的 1/3 且视觉达标。

**风险**：多引擎 = 多套预览/自评路径；建议先只加 PIL（零新依赖：Pillow 已在依赖里）。

### V4 渲染后自评闭环（最被低估的一条）

**它的做法**：在**渲染输出**上跑 `timeline_view`，对每个切点 ±1.5s 逐张看图查 4 类问题（跳变/爆音/字幕被遮/overlay 错位），另抽首 2s/尾 2s/中点查连贯；`ffprobe` 校验时长；最多 3 轮，之后透传用户（`SKILL.md:91-99`）。

**我们现状**：QualityAgent 有**渲染前**的内容级校验（空镜头、动画是否降级、越界），**没有渲染后的看图自评**（`docs/animation_system.md:117-123`）。

**落地设计**：
1. 新 Tool `timeline_view`（ffmpeg 抽帧 + PIL 合成：filmstrip + 波形 + 静音着色 + 词标签），参考实现 `_research/video-use/helpers/timeline_view.py`（MIT，需保留版权头）。
2. QualityAgent 增加 `post_render_review`：切点来自时间线（转场点/剪辑点/MG 起止），逐点生成合成图 → 交视觉模型或人工 → 输出 `{切点, 问题类型, 证据图路径}`。
3. 失败路由：`redo_agent=edit`（切点问题）/`animation`（overlay 问题）/`audio`（爆音），复用现有 Agent 重做机制。

**验收**：故意把 MG 起始时间偏移 0.5s，自评应报"overlay 错位"；故意去掉 30ms 淡化，自评应报"边界爆音"。

**风险**：视觉模型成本与延迟；建议只在"切点数量 ≤ 上限"时抽样，并缓存证据图。

### V5 动画时长/节奏规则（零成本、立刻见效）

直接把下面规则写进 `animation/mg/config.yaml`（现已有 easing/错峰指引，缺这几条）：

- 旁白同步：下限 3s，简单卡 5–7s，复杂图 8–14s；overlay 时长 ≥ 旁白 +1s。
- 末帧 hold ≥1s；不同时并行揭示两个独立元素。
- **payoff 同步**：overlay 提前 `reveal_duration` 秒入场，使"落点帧"正对旁白关键词（`SKILL.md:224`）。需要旁白字级时间戳（详见 V6）。
- easing 禁用 linear（我们已有 22 种 Penner easing，规则上可直接约束）。
- 数字用平滑计数器而不是跳变（对应我们的 counter_up 模板）。

**验收**：抽检 10 个 MG，入场落点与旁白关键词的偏差 < 300ms。

### V6 短语级转写打包（控 token）

`[start-end] S{spk} text` 短语视图（静音 ≥0.5s 或换人断句），约原始逐词 JSON 的 1/10 token（`helpers/pack_transcripts.py`；README 称 12KB 承载一小时素材）。

**落地**：新增 `services/transcript_pack.py`，在 EditAgent/StructureAgent 组装 prompt 时用短语视图；保留静音区间作切点候选。**验收**：同一素材，EditAgent prompt token 下降 ≥60%，切点质量不降。

### V7 / V8 / V9（简述）

- **V7 `timeline_view`**：与 V4 复用同一 Tool，也可给编辑器做"切点复核"按钮。
- **V8 `project.md` 四段式**（Strategy/Decisions/Reasoning/Outstanding）：与现有 Persona 学习、项目版本历史（`services/api/project.ts` 的 `versionApi`）互补，做"人可读的决策日志"。
- **V9 Manim 参考的通用质量指导**：透明度分层 1.0/0.4/0.15、旁白-first 拆 beat、**同屏 ≤6 元素硬上限**、相邻镜头 variety audit、tempo 曲线、图表最低要求。落进 `mg/config.yaml` + `generator.py` 的校验项（现在是"LLM 自批判打分"，可加程序化硬校验：元素数、对比度、调色板一致性）。

## 1.2 从 OpenCut classic 借（MIT；价值在"数据模型 + 编辑器工程范式"）

### O1 整数时间 + 有理数帧率（根治对齐问题）

- **做法**：`MediaTime` = 整数 tick（120000/s，能整除所有标准帧率分母），`FrameRate{numerator, denominator}`；Rust 侧定义、`tsify-next` 生成 TS 类型（classic 单一来源）。
- **我们的问题**：时间线是浮点秒 + 整数 fps（`src/types/timeline.ts`），与后端 Pydantic 模型一致；29.97/23.976 素材在"秒 ↔ 帧"换算里会累积误差（渲染端已有多处 `_fmt_sec` 与 1e-6 容差）。
- **落地设计**：先在**后端 schema 层**引入 `FrameRate` 与整数 tick 的等价物（Pydantic：`int` ticks + `width/height/fps_num/fps_den`），API 兼容层保留 float 秒字段（过渡期双写）；前端 TS 用 branded type `type MediaTime = number & { __mediaTime: true }`，统一时间工具函数（不要散落 `* fps`）。
- **验收**：`30000/1001` 时间线上切 100 次，累计漂移 = 0 帧；既有测试（后端 1154、前端 349）全绿。
- **风险**：改动面最大的一项，建议排在 P2，且先做只读兼容层。
- **执行进度（2026-09-13，第一阶段完成）**：后端整数时间基座已落地，**API 契约不变**（浮点秒字段仍是唯一序列化来源，新属性不进 JSON）：
  - `clipwright/utils/timebase.py`：`TICKS_PER_SECOND=120000`、`FrameRate`（有理数帧率 + 标准帧率吸附 + drop-frame 时间码 + 时间码往返）、tick 换算（`seconds_to_ticks`/`ticks_to_seconds`/`frame_to_ticks`/`ticks_to_frame`）、帧边界吸附（`snap_ticks` 三种模式）与 `format_ticks`。
  - `schema/timeline.py`：`Timeline.frame_rate` / `total_duration_ticks` / `frame_count` / `frame_aligned_duration_sec()` / `timecode()`，全部是**派生属性**（不改变 JSON 形状）。
  - `services/render.py`：转场漂移校正改为纯函数 + tick 整数（`transition_shift_ticks` / `shift_ticks_for` / `corrected_start_sec`）；视觉 overlay 校正后**吸附渲染帧边界**（`CLIPWRIGHT_FRAME_ALIGN_OVERLAY=0` 可关），**音频不吸附**（只做 tick 精确减法，避免半帧以内的 A/V 相位偏移）；`_apply_default_cut_fades` 的「相邻」判定改为 tick 比较，50ms 窗口变成具名常量并写明它与 30ms 淡化的关系。
  - `services/timeline_view.py`：切点用 tick 判重（去掉毫秒取整，避免把吸附好的帧边界又挪走），可选按帧率吸附，post-render hook 传入 fps。
  - **实测证据**（`python scripts/verify_timebase.py`，VERIFY PASS）：29.97fps 的 200000 个帧边界里，浮点 `frame*(k-1)+frame != frame*k` 有 **41202 个（20.6%）**，tick 下 **0 个**；`int(sec*120000)` 在毫秒刻度 651/19999（3.3%）、帧刻度 569/19999（2.8%）上少 1 tick（1.007s → 120839，应为 120840）；10000 次 15 帧累加浮点得 5005.000000000789、tick 得 600600000 精确落在帧边界；12 段 29.97 时间线漂移校正后 11/11 个 overlay 起点落在整帧，吸附位移最大 5.00ms（< 半帧 16.68ms）。测试 `tests/clipwright/test_timebase.py` **114 条**；`python scripts/e2e_smoke.py` 仍 SMOKE PASS（14.00s、fallback=0、luma 124.7）。
  - **未做（第二阶段）**：Pydantic 侧 `int` tick 字段 + `fps_num/fps_den` 双写、前端 branded type `MediaTime` 与统一换算工具、命令式撤销（O3 联动）。

### O2 轨道模型：判别联合 + 主轨单例

- **做法**：`SceneTracks{overlay: OverlayTrack[], main: VideoTrack(单例), audio: AudioTrack[]}`，从类型上杜绝多主轨；`TrackType = video|text|audio|graphic|effect`；元素是判别联合（Audio/Video/Image/Text/Sticker/Graphic/Effect），`BaseTimelineElement` 带 `trimStart/trimEnd` 显式建模裁剪。
- **我们现状**：`Track{kind, clips[]}` 通用结构，有 `addTrack/removeTrack/reorder/lock/mute/hidden`；裁剪靠 clip 的 start/duration（无独立 trim 字段）。
- **落地**：Pydantic `discriminated union` + 前端 `satisfies` 校验；主轨单例约束写进类型与 store 校验（`addTrack` 拒绝第二个 main）。
- **收益**：AI Agent 生成的时间线更容易被静态校验，减少"轨道错位"类线上问题。
- **风险**：与现有 7 个 Agent 的产出格式要对齐，避免双份真值（建议后端 schema 为唯一真值，前端类型从 OpenAPI 生成）。

### O3 撤销重做：从"快照式"升级为"命令式"

- **它的做法**：`Command` 抽象（execute/undo/redo）、`BatchCommand`（逆序 undo）、`CommandManager` 维护 history/redoStack + **每次执行记录选择快照** + `registerReactor` 修剪空轨 + **ripple 在命令后统一 diff 计算**。
- **我们现状**：`stores/historyStore.ts` 是**时间线整体快照**（引用存储 O(1)、maxSize 200、`jumpTo`、去重），`pushState` 调用点分散在 AssetPanel/DubView/TimelineDiffView 与各操作里；**没有选择恢复**，没有"这一步做了什么"的语义标签体系（有 label 字符串但未用于 UI），没有历史面板。
- **落地设计（不推倒重来）**：
  1. 保留快照存储（简单可靠），**补三件事**：① 每个 pushState 附带 `selection` 快照并在 undo/redo 后恢复；② `label` 结构化（`{action, target, params}`）+ 前端历史面板（可跳到任意步，`jumpTo` 已具备）；③ 把分散的 pushState 收口到 store 内部（避免"有的操作没记录"）。
  2. 若后续要做"协作/审计/Agent 操作回放"，再引入命令对象（命令 = 可序列化 diff，比快照更适合跨端）。
- **验收**：任意编辑后 undo，选择集与滚动位置回到操作前；历史面板可跳回第 20 步。
- **风险**：选择快照需要序列化稳定 id（我们现在用 clip id，OK）。

### O4 声明式参数/特效/遮罩契约（对插件系统直接有用）

- **它的做法**：`ParamDefinition` 6 型（number/boolean/color/select/text/font）+ `ParamValues = Record<string, ParamValue>`；`EffectDefinition{type, name, keywords, params, renderer:{passes, buildPasses}}`；`MaskDefinition{type, features, params, renderer, interaction}`。
- **我们现状**：插件已有 manifest 签名/权限白名单/UI 控件集（input/select/checkbox/slider，见 AGENTS.md「插件治理」），但参数描述未统一成"声明式 schema → 自动渲染控件"。
- **落地**：定义 `ParamDefinition` 等价的 JSON Schema 片段（6 型对上现有控件集），前端按 schema 渲染；插件 UI 预览页（`PluginPage`）直接消费。
- **风险**：不要把 OpenCut 的 shader 直通搬过来（插件可注入任意 GPU 代码 = 权限面扩大）；我们的插件参数只描述数据，不描述执行。

### O5 持久化：本地草稿 + 版本化迁移链

- **它的做法**：IndexedDB（项目元数据）+ OPFS（媒体文件）+ 每项目独立媒体库；`navigator.storage.estimate()` 配额预警 + 50MB 预留；**v0→v31 每步一个可测迁移器**。
- **我们现状**：项目/时间线在**后端**（Mongo）+ 前端 `localStorage` 存偏好（settings/keybindings）+ `mediaManager` 用 blob URL（刷新即失效）+ `tabSync` 多标签页同步；**没有本地离线草稿**，刷新时媒体要重新解析。
- **落地**：① 前端加 IndexedDB 缓存"最近打开的媒体句柄 + 缩略图/波形"，减少重复解析（对 `mediaManager` 是纯增强）；② 后端 schema 演进采用"每步迁移器 + 单测 + runner"范式（写进 `docs/`）。
- **风险**：配额与跨标签页一致性（已有 `tabSync` 基础）。

### O6 预览性能：纹理缓存 + 分段计时

- **它的做法**：`contentHash` 纹理去重 + OffscreenCanvas 复用 + GPU 合成；`render-perf.ts` 分段计时（resolve/buildFrame/syncTextures/renderFrame）+ 缓存命中计数。
- **我们现状**：`PreviewPanel.tsx`（48KB）是 Canvas 合成器 + rAF + `<video>` 源，有字幕布局/几何/合成顺序/音量/变速测试；**无法确认有纹理缓存与性能诊断**。
- **落地（TS 版，不引 Rust）**：① 帧级纹理缓存（同一 clip、同一时间点不重复 drawImage；图片/文字层按内容哈希缓存到 OffscreenCanvas）；② 加 `perf` 诊断（每帧 resolve/composite 耗时 + 缓存命中率）暴露在开发面板。
- **验收**：1080p 三轨时间线（2 视频 + 字幕 + MG 预览）拖动播放头时，合成耗时应下降 ≥30%（用诊断面板读数）。

### O7 / O8（简述）

- **O7 导出 UI 对齐**：我们后端渲染更强（预设：码率/容器/ProRes422HQ/水印/特效/dry-run），可对齐的是"预设字段结构 + 进度/取消 + 预估体积"这类 UI 契约（`pages/exportPreset.ts` 已有容器解析）。
- **O8 工程规范**：把 Ultracite 的 a11y/TS 规则清单真正落成 eslint/biome 配置（上游自己都还只是 Copilot 指令），并给 CI 加 Windows/macOS 矩阵（我们依赖 ffmpeg/Chrome，跨平台问题比 OpenCut 更现实）。

## 1.3 优先顺序（含依赖）

| 序 | 事项 | 依赖 | 工期 | 说明 |
|---|---|---|---|---|
| 1 | HyperFrames 时序/门禁修复（第三部分 P0） | 无 | 1–2d | 唯一"立刻省钱省时间"的一项 |
| 2 | V1 合成顺序与默认淡化 | 无 | 1d | 影响所有成片正确性 |
| 3 | V5 + V9 提示词与硬校验 | 无 | 1d | 动画观感立刻提升 |
| 4 | V4 自评闭环（+V7 工具） | 需 timeline_view Tool | 3–5d | 质量闭环的地基 |
| 5 | O3 撤销增强（选择快照 + 历史面板） | 无 | 2–3d | 编辑器体验 · ✅ **2026-09-13 完成**（见 §4.2 第 1 项执行进度） |
| 6 | V2 并行动画生成 + 多方案择优 | 无 | 3d | ✅ **2026-09-13 核对**：并行执行**早已存在**（`AnimationAgent` 收集 `logic_jobs` 后用 `Semaphore(pipeline_concurrency)` + `asyncio.gather` 并发跑，见 `agents/animation_agent.py`「串行是动画阶段的最大瓶颈」注释）——本报告此前把它列为"未做"是**误判**；本轮补的是**多方案生成 + 择优**（`MGGenerator.generate_best`，`generation.variants` 1-4，视觉分优先/批判分次之） |
| 7 | O4 声明式参数契约 | 插件系统 | 3d | 插件生态 |
| 8 | O6 预览纹理缓存 + 诊断 | 无 | 3–5d | 大时间线流畅度 · ✅ **2026-09-13 完成**（见 §4.2 第 6 项执行进度） |
| 9 | O1 时间模型整数化 | 需 schema 迁移 | 5–10d | 根治对齐误差 · **后端第一阶段已完成（2026-09-13）**：`utils/timebase.py` + `Timeline` 派生属性 + 漂移校正/切点走 tick，API 契约不变；剩余第二阶段（schema 双写 + 前端 branded type） |
| 10 | O5 本地草稿 + 迁移链 | 无 | 3d | 离线与稳健性 · ✅ **2026-09-13 完成**（见 §4.2 第 5 项执行进度） |

---

# 第二部分：MPL-2.0 合规细说

## 2.1 条款级判定

| MPL 条款 | 内容要点 | 对本项目的含义 |
|---|---|---|
| §1.4 Covered Software | 仅指挂了 MPL 声明的文件（本仓库自己的源码） | 第三方 MIT/Apache 文件**不**自动变成 Covered Software |
| §1.7 Larger Work | 把 Covered Software 与其它文件组合成的更大作品 | 我们把 MIT 代码放进本仓库 = 典型 Larger Work，**被明确允许** |
| §3.1 源码分发 | 分发源码形式时，Covered Software 必须继续以 MPL 提供、保留声明 | 公开仓库已满足；改过的 MPL 文件要保留许可与版权声明 |
| §3.2 可执行体分发 | 分发二进制/镜像时需告知接收者如何取得源码 | **只在分发**（Docker 镜像、桌面端安装包）时触发；SaaS 不触发 |
| §3.3 二次许可 | 若未标 Exhibit B，可将 Larger Work 按 GPL/LGPL/AGPL 分发 | 我们的 LICENSE **无 Exhibit B**，通道开放；与 GPL 系组合不受阻 |
| §3.4 通知义务 | 必须告知接收者 Source Code Form 的获取方式 | 仓库根 README/LICENSE 已有 |
| §5 专利 | 贡献者默示专利许可 | 无需动作 |
| §6 商标 | 不授予商标权 | 不得用 "Clipwright" 之外的商标；反向：不得暗示 OpenCut/HeyGen 背书 |
| Exhibit A | 建议在每个源文件加 MPL 声明头 | 现状：未见统一文件头（**可选**，但建议至少在新文件加） |

## 2.2 决策表：四种用法 × 三种场景

| 用法 | 自用/内部 | SaaS 对外服务 | 分发可执行体（镜像/客户端） |
|---|---|---|---|
| 借鉴思路（架构/提示词/规则） | 自由 | 自由 | 自由（建议注明来源） |
| 复制 MIT 代码 | 保留 MIT 声明即可 | 同上 | 同上（在 NOTICES 里给全文） |
| 复制 Apache-2.0 代码（HyperFrames 片段/模板） | 保留 LICENSE/NOTICE + 标注修改 | 同上 | 同上，且需提供 Apache 许可副本 |
| 通过 subprocess 调用外部 CLI（npx hyperframes） | 无义务 | 无义务 | 若把该包一起打包，需带其许可与 NOTICE |
| 打包其资产（字体/图标/模板） | 逐项核许可（见 2.4） | 同 | 同，且 OFL 字体的 OFL 文本必须随附 |

## 2.3 现成模板

**`THIRD_PARTY_NOTICES.md`（两仓库各放一份）**

```markdown
# 第三方软件声明 / Third-Party Notices

本产品包含以下第三方开源软件。各组件的版权归其作者所有，并按其自身许可条款使用。

## opencut-app/opencut 与 opencut-app/opencut-classic
License: MIT — Copyright 2026 OpenCut / Copyright 2025-2026 OpenCut
使用范围：时间数据模型（MediaTime/FrameRate）、轨道类型定义、命令与波纹编辑设计、参数/特效/遮罩契约的学习与部分移植。
<附 MIT 全文>

## browser-use/video-use
License: MIT — Copyright (c) 2026 Browser Use
使用范围：渲染管线规则、动画分派 brief、自评闭环、timeline_view 工具（移植文件保留原版权头）。
<附 MIT 全文>

## heygen-com/hyperframes（Apache License 2.0）
Copyright 2026 HeyGen, Inc.
使用范围：作为外部渲染器通过 CLI/进程调用；部分模板与提示词结构的借鉴（见各文件头注明）。
本产品未使用其商标，也不代表 HeyGen 的背书。
<附 Apache-2.0 全文 + 上游 NOTICE（如有）>

## 其它
FFmpeg（构建许可：LGPL/GPL，以实际构建为准）…
```

**移植文件头模板**

```python
# Portions adapted from browser-use/video-use (MIT).
# Copyright (c) 2026 Browser Use — see THIRD_PARTY_NOTICES.md.
```

**CI 检查（可选，防漏）**

```bash
# 新增文件中若出现已知上游特征串，则要求同步更新 NOTICES
! git diff --name-only HEAD~1 | xargs grep -l "video-use\|hyperframes\|opencut" | grep -v THIRD_PARTY_NOTICES \
  || echo "记得更新 THIRD_PARTY_NOTICES.md"
```

## 2.4 风险点处置

| 风险 | 事实 | 处置 |
|---|---|---|
| 上游混有**商业字体** | `hyperframes/.claude/skills/changelog-video/assets/fonts/TT_Norms_Pro_*.woff2`、`ABCSolarDisplay-Bold.woff2` | **禁止整仓拷贝**；只按文件白名单取用 |
| registry 条目自带许可 | 已见 `"license": "OFL-1.1"`（`registry/blocks/weight-wave/registry-item.json`）与 `"license": "MIT"`（`registry/components/kinetic-center-build/registry-item.json`） | 采用前逐条读 `registry-item.json` 的 `license` 字段并登记 |
| OFL 字体 | `frame-presets/*/fonts/*.woff2` 附 `OFL-*.txt` | 随附 OFL 文本；不改字体名（Reserved Font Name） |
| OpenCut 素材 | `apps/web/public/{flags,shapes,stickers,icons,logos}` 来源未核实 | 不用；或逐个溯源后登记 |
| 商标 | Apache-2.0 §6 | 文档可写"使用 HyperFrames 渲染"，但不得用于产品命名/宣传暗示 |
| 其它引擎 | Remotion 为专有（规模门槛）；GSAP 为自有免费许可（非 OSI） | 引入前单独评审；本项目当前不需要 |
| FFmpeg 构建 | GPL 构建会带来分发时的 GPL 义务 | 分发镜像前确认构建类型与源码可得性 |

## 2.5 常见误解 FAQ

- **"把 MIT 代码并进来，整个仓库是不是要变 MIT？"** 不用。文件级：那些文件仍是 MIT，其余仍是 MPL；分发时两者义务并行满足即可。
- **"MPL 是不是像 AGPL 一样，SaaS 也要开源？"** 不是。MPL 没有网络条款；只有分发可执行体才触发 §3.2。
- **"能不能把 MIT 文件的头改成 MPL？"** 不能——必须保留原版权与许可声明；可另加一行"本文件随本项目以 Larger Work 形式分发"的说明。
- **"用了 npx 调 HyperFrames，算不算衍生作品？"** 不算（进程边界，非链接/非改编）；只有再分发它的包/二进制时才承担 Apache-2.0 的通知义务。

---

# 第三部分：HyperFrames 增强细说

## 3.1 现状调用链（代码级）

```
AnimationAgent._handle_mg_animation / _handle_llm_mg      agents/animation_agent.py:526-802
   └─ MGGenerator.generate（LLM → 校验 → 自批判 → 模板降级）  animation/mg/generator.py
        └─ MGRenderer.render → html                        animation/mg_renderer.py:190-207
             · 根：<html data-fps data-width data-height>
             · <div id="root" data-composition-id="main" data-width data-height data-start data-duration>
             · window.__timelines['main'] = { paused: true }   ← 空对象桩，非 GSAP timeline
   └─ clip.metadata["mg_html"] 存进时间线                    animation_agent.py:770-791

RenderService（视频阶段）
   · _concat_segments → _apply_text_concat（文本/字幕）      services/render.py:943-958
   · _apply_all_hyperframes                                 services/render.py:1918-1996
        Phase1: 每个 MG 一个 npx 进程（Semaphore 6）
                → render_mg_html_to_mov                      hyperframes_renderer.py:130-177
                   cmd = npx hyperframes render <mg_dir> -o <mov> --format mov -f <fps> --quiet
        Phase2: 图解 overlay → render_overlays（单 HTML 合成）  hyperframes_renderer.py:196-243
                   → _build_html：.hf-el + data-start/duration + 自写 setTimeout 时序 JS（:337-360）
        Phase3: 链式 overlay 到主视频（单次 filter_complex）    services/render.py:1969-1982
   · 画中画 → 音频（loudnorm 目标响度）
```

**代码级问题清单**

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| 1 | overlay 合成 HTML 无 timeline 注册 | `hyperframes_renderer.py:268-285` | 每次渲染触发 45s+ 就绪轮询（实测总损耗 90–106s） |
| 2 | 时序用 `setTimeout` + 直接写 `visibility` | `hyperframes_renderer.py:337-360` | 非 seek-safe；并行抓帧下入场/退场可能错帧 |
| 3 | MG 模板的时间轴是空对象桩 | `mg_renderer.py:207` | 依赖上游宽容行为；上游收紧即失效 |
| 4 | 无 lint/check 门禁 | `hyperframes_renderer.py:173-177` | 布局溢出/对比度问题进成片 |
| 5 | 字体恒 `sans-serif` | `mg_renderer.py:172,193`、`mg/config.yaml:268,395` | Persona 字体与片头质感丢失 |
| 6 | 只用 `-f/--format/--quiet` | `hyperframes_renderer.py:156-158` | 无质量分级/并行/缓存控制 |
| 7 | `npx` 每次冷启动 | `hyperframes_renderer.py:27-43` | 首次渲染 15–90s 额外开销 |

## 3.2 实测数据（复现命令见总报告附录 A）

| 样本 | 帧数 | 原样 | 加 `data-no-timeline` |
|---|---|---|---|
| 历史 MG 产物（无注册） | 135 | 129.1s | 22.9s |
| 生产 overlay 合成 HTML（`_build_html`） | 180 | 108.2s | 18.0s |
| 当前模板 MG（有桩注册） | 120 | 22.8s | — |

`hyperframes check` 对当前模板 MG：Lint 0 error/1 warning、Runtime 0、Layout 0（9 个采样）、Motion 0、**Contrast ✗ 1.6:1（需 3:1）**。

## 3.3 上游能力全表（0.8.36）

**命令**：`init / add / capture / catalog / preview / present / publish / render / lint / check / validate（弃用）/ beats / inspect（弃用）/ keyframes / snapshot / media-treatment / normalize-audio / grade-compare / compare / info / compositions / docs / benchmark / browser / doctor / upgrade / cloud / lambda / cloudrun / skills / transcribe / tts / remove-background / auth / feedback / telemetry`。

**`render` 关键参数**（本地全未用）：`-q/--quality(draft|standard|high)`、`-w/--workers`、`--crf/--video-bitrate`、`--resolution`（DPR 超采样到 4K/竖屏/方形）、`--variables/--variables-file/--strict-variables`、`--batch/--batch-concurrency/--batch-fail-fast/--json`、`--format(mp4|webm|mov|gif|png-sequence)`、`--strict/--strict-all`、`--debug`、`--gpu/--browser-gpu`、`--low-memory-mode`、`--frames-cache-dir`、`--browser-timeout/--protocol-timeout/--player-ready-timeout`、`--page-side-compositing`。

**HTML 契约**：根 `data-composition-id`（必填）/`data-width`+`data-height`（必填）/`data-duration`/`data-fps`/`data-composition-variables`；clip 用 `data-start`（有它即成 clip）/`data-duration`/`data-track-index`；`class="clip"` 是约定（Studio 与 lint 用）；**每个合成注册唯一 paused GSAP timeline 到 `window.__timelines[<id>]`**；子合成用 `<template>` + `data-composition-src`；变量绑定 `data-var-text`/`data-var-src`；7 种 runtime adapter。

**资产**：`registry/` **180 blocks + 219 components + 13 examples**（条目自带 `license` 字段）；`skills/` 20 个（`hyperframes-core/animation/keyframes/creative/cli/registry/audio`、`motion-graphics`、`media-use`、`figma` + 10 个创作工作流）；`frame-presets/` 20+ 套设计系统 + palettes；`transitions/` 转场注册表；`shader-transitions` 包；Studio + `<hyperframes-player>`；Lambda/Cloud Run 分布式渲染。

## 3.4 改法（P0/P1/P2 逐条）

### P0-1 overlay 时序 seek-safe 化 + 时间轴注册（1 天）

```python
# animation/hyperframes_renderer.py —— _build_html 根元素
<div id="root" data-composition-id="main" data-width="{w}" data-height="{h}"
     data-duration="{total:.2f}" {'' if use_timeline else 'data-no-timeline'}>
```
```javascript
// 首选：声明式 + 单条 paused GSAP timeline（gsap 由上游产物/本地依赖提供）
const tl = gsap.timeline({ paused: true });
document.querySelectorAll('.hf-el.clip').forEach(el => {
  const s = +el.dataset.start, d = +el.dataset.duration, ad = +el.dataset.animDur || 0.4;
  tl.fromTo(el, { autoAlpha: 0, y: 16 }, { autoAlpha: 1, y: 0, duration: ad, ease: 'power2.out' }, s);
  if (d > 0.5) tl.to(el, { autoAlpha: 0, duration: Math.min(0.3, d * 0.15) }, s + d - Math.min(0.3, d * 0.15));
  el.classList.add('clip');          // 让框架接管可见性
});
window.__timelines['main'] = tl;      // 构建完成后注册（不要提前）
```
降级路径：若不便引入 GSAP，先补 `data-no-timeline` + 纯 CSS `animation-delay`（把每个元素的入场用 `animation-delay: {start}s` 与 `data-start` 对齐），也能拿到 5–6× 提速与基本确定性。

**验收**：`npx hyperframes render` 计时下降 ≥5×；`--workers auto` 与 `-w 1` 产出的 `snapshot` 帧一致；不再出现 `sub_timeline_readiness_timeout`。

### P0-2 lint 门禁（0.5 天）

在 `render_mg_html_to_mov` 写盘后、render 前插入：
```python
r = await run_cmd([npx, "hyperframes", "lint", str(mg_dir), "--json"])
if r.returncode != 0:  # error 级 → 走既有降级链
    logger.warning("MG HTML 未通过 lint: %s", r.stdout[:500]); return None
```
**验收**：投喂缺 `data-width` 或超画布坐标的 mg_html，应被拦截并降级；正常模板 0 error。

### P0-3 本地固定 hyperframes 版本（0.5 天）

`npm i -D hyperframes@0.8.36`（或部署镜像内预装），`_npx_cmd()` 优先 `node_modules/.bin/hyperframes`，探针缓存 TTL 可缩短。**验收**：首次渲染前的冷启动耗时下降。

### P1-1 渲染后视觉自评（2–3 天）

```bash
npx hyperframes snapshot <mg_dir> --at <reveal>,<mid>,<exit> -o <out>
npx hyperframes compare <vA> <vB> --at 1.5 --labels a,b --out cmp.png
```
把 PNG 交视觉模型打分（或 `snapshot --describe`），分数回灌 `mg/generator.py` 的 `critique.min_score` 闭环；`compare` 用于多方案择优。

### P1-2 `check` 进生成期（1 天）

生成阶段（非每次渲染）跑 `hyperframes check --json`（含 lint + 运行时 + 布局 + 动效 + WCAG 对比度），失败即触发"修 → 重生成一次"。实测当前模板 MG 就会命中对比度问题。

### P1-3 质量分级与并行（0.5 天）

预览端点用 `-q draft`；正式渲染 `-q standard|high`；`-w auto` 交给上游并行（替代"每 MG 一进程"）。同源多 MG 场景加 `--frames-cache-dir`（迁出系统盘）。

### P1-4 Persona 字体落进 MG（1 天）

MG 目录内置 `fonts/*.woff2` + `@font-face { src: url('fonts/x.woff2') }`，`mg_renderer` 的 font-family 用 Persona 字体名（中文需子集化）；上游 `injectDeterministicFontFaces` 是同一思路的参考实现。

### P1-5 变量参数化替代占位符（2 天）

模板/生成产物声明 `data-composition-variables`，`render` 时传 `--variables '{"accent":"#fbbf24",...}'`；`_RESIDUAL_PLACEHOLDER_RE` 那套兜底可逐版退役。附带收益：`--batch` 可一次出多语言/多主色版本。

### P2-1 上游资产蒸馏成 MG 知识库（3–5 天）

```bash
npx hyperframes catalog --json > catalog.json     # 180 blocks + 219 components 索引
npx hyperframes add <name>                        # 按需取用，条目 license 字段登记
```
按 `shot_type`（title_reveal / data_impact / comparison / flow / quote / mindmap）给 registry 条目分类，精选每类 3–5 个作为 few-shot（**只取结构，不整段复制**，并在 `mg/config.yaml` 头注明 Apache-2.0 来源）；`frame-presets/*/FRAME.md` 直接作为"设计令牌"参考（调色板/字体/留白规则）。

### P2-2 producer 常驻 / 云端渲染（架构级）

`@hyperframes/producer` 的 `executeRenderJob` 放进一个 Node sidecar（FastAPI 通过 HTTP 调用），彻底消除 npx 冷启动；或走 `hyperframes cloud/lambda/cloudrun`（与 `docs/remote_render.md` 的远端渲染规划对齐，注意服务条款与费用）。

### P2-3 Studio / player 接入前端

`<hyperframes-player>` 做 MG 即时预览（替代当前"渲染 MOV 再播"），Studio 供人工微调；对 `MgPreviewPage.tsx` 的 2–3s 预览定位是直接升级。

## 3.5 可引入的其它项目（含许可）

| 项目 | 许可 | 用途 | 建议 |
|---|---|---|---|
| heygen-com/hyperframes | Apache-2.0 | 渲染器 + registry + skills + 设计系统 | 主依赖，按 §3.4 推进 |
| browser-use/video-use | MIT | 编排方法论 + timeline_view 实现 | 移植并在文件头保留 MIT |
| Manim Community | MIT | 公式/状态机/图变换 | 作为"逻辑/公式"slot 备选 |
| PIL + ffmpeg（自研） | Pillow 许可（宽松） | 简单卡片/计数器/打字机 | **优先新增**（零新依赖、快） |
| GSAP | GSAP 自有免费许可（非 OSI） | HyperFrames 首选适配器 | 已在依赖链内；注意条款 |
| Lottie / dotLottie | MIT（运行时） | 图标/插画动效 | 有 HF adapter，按需 |
| Three.js / Anime.js | MIT | 3D / 轻量时间轴 | 有 HF adapter，按需 |
| Motion Canvas | MIT | 备选引擎 | 观察 |
| Remotion | 专有（规模门槛） | React 合成 | 不建议进主管线 |

---

# 第四部分：前端与 OpenCut classic 的差距

> 结论先说：**我们缺的是"通用剪辑器的纵深"（命令式撤销、关键帧曲线、遮罩、整数时间、本地持久化、预览性能工程），而 OpenCut 缺的是"AI 工作流与产品化"（Agent 面板、评审/差异、Persona、插件、i18n、服务端渲染）**。两者不是同一类产品，逐项对齐没有意义；应对齐的是"用户已经习惯的剪辑器手感"。

## 4.1 能力矩阵

| 领域 | 我们（J:\Clipweight-Client） | OpenCut classic | 判定 |
|---|---|---|---|
| 技术栈 | React 19 + Vite + Tailwind4 + Radix + zustand + i18next + TanStack Router | Next.js + Tailwind + shadcn + zustand(UI) + Rust/wgpu(WASM) | 各有取向；我们无 WASM 构建链 |
| 时间模型 | 秒（float）+ 整数 fps | **MediaTime 整数 tick(120000/s) + FrameRate 有理数** | 🔻**缺**（23.976/29.97 对齐） |
| 轨道模型 | `Track{kind, clips[]}`，可增删/排序/锁定/静音/隐藏/重命名 | `SceneTracks{overlay[], main(单例), audio[]}` 判别联合 + 5 类轨道强类型 | 🔻类型安全稍弱（我们有 nested sequence，它没有） |
| 编辑器布局 | `EditorLayout.tsx`：四面板（素材/预览/属性/Agent）+ `workspaceStore` 的 `panelWidths`/`timelineHeight`/面板显隐 + 移动端面板切换（`mobilePanel`） | `app/editor/[project_id]/page.tsx:81-208` 四面板 ResizablePanelGroup 嵌套拖拽 + `panel-store` 持久化尺寸 + 顶栏（导出/主题/快捷键/内联改名）+ `MobileGate` 拦截移动端 | 🔸能力相当（我们移动端不拦截，改为面板切换） |
| 时间线渲染 | **自研 Canvas 引擎**（缩略图缓存、滚动条、缩放预设、markers、拖放反馈、trim ghost、命中测试、marquee） | React 组件 + 控制器族（drag-drop/resize/seek/zoom/keyframe-drag） | 🔸取向不同，能力相当 |
| 编辑操作 | split/trim 起止/**rollingTrim/slip/slide/rippleDelete/rippleInsert**/group/ungroup（拖动同组联动）/nested sequence/多选/**框选 marquee** | 拖拽/裁剪/切割/复制粘贴/多选/框选/吸附/缩放/波纹/群组移动缩放 + `placement/` 层解析插入位置（可单测） | 🔹**能力相当，我们操作集更全**（marquee `renderers.ts:735`、`TimelineEngine.ts:436`；组拖拽 `TimelineEngine.ts:423`） |
| 吸附 | 元素边缘/播放头/标记/**零点/网格**（`engine/snap.ts:1-30`） | 元素边/播放头/**书签/关键帧**（**无网格**）+ 阈值可配 + 预览区吸附参考线 | 🔸各有缺项：我们缺"预览区参考线 + 关键帧吸附"，它缺网格 |
| **画布内直接变换** | ❌ 预览画布只做 **scrub 拖动 + 双击改文字**（`PreviewPanel.tsx:50,207-266`）；变换只能靠属性面板滑杆 | ✅ 预览区直接拖拽/缩放/旋转（带变换手柄、命中测试、吸附参考线） | 🔻**明显差距（剪辑器手感的核心）** |
| 撤销重做 | **快照式**（引用存储、maxSize 200、jumpTo、label、`historyCoalesce` 合并） | **命令式**（30 个 action / 8 类 / 21 个默认快捷键，Command/BatchCommand、选择快照、reactor 修空轨、ripple diff 后置） | 🔻**缺语义与选择恢复**，无历史面板 |
| 关键帧 | value keyframes + **22 种命名 easing** + 动画预设 + 播放头增删 + 钻饰条 | 14 条类型化属性路径 + **graphic/effect 参数路径可动画**；插值 linear/**hold**/bezier + **tangentMode(auto/aligned/broken/flat)** + 外推；贝塞尔手柄在**图编辑器弹窗** | 🔻**缺曲线编辑与参数级动画** |
| 特效 | 后端起效（ffmpeg 滤镜）+ 工具接线（水印/特效） | shader pass 定义 + GPU 管线 + 逐 clip 特效栈 | 🔸架构不同；我们预览端特效还原度需核对 |
| 遮罩 | `mask_type: rect|ellipse` + `mask_rect` | **9 种内置 + freeform**，feather/inverted/stroke（JFA 羽化） | 🔻**明显偏弱** |
| 预览 | Canvas 合成器 + rAF + video 源 + 字幕布局/合成顺序/音量/变速测试 | Rust/wgpu WASM 合成器 + 纹理缓存 + WebGPU/WebGL 双路径 + 分段性能诊断 | 🔻缺纹理缓存与性能诊断 |
| 媒体/持久化 | 后端项目存储 + autosave/pagehide flush + `tabSync` 多标签 + 服务端版本历史 + blob URL 媒体；**无 IndexedDB**（`EditorPage.tsx:30` 明写 "strict: no IndexedDB fallback"，`localPrefs.ts:3` 提到的 projectCache 实际不存在） | **IndexedDB(项目元数据)+OPFS(媒体文件)** 分离 + 50MB 配额余量检测 + **存储迁移链 v0→v31** + 自动保存 800ms debounce | 🔻**缺本地离线草稿与配额管理**；我们多标签/服务端版本是其没有的 |
| 字幕 | 后端转写 + 字幕工具页 + ASS/drawtext 双渲染 + captionLayout + 11 项样式字段（描边/发光/阴影） | **SRT/ASS 导入**（跳过畸形 cue 并告警）→ 批插入 text 轨 + **客户端 Whisper ONNX（Web Worker，tiny/small/medium/large-v3-turbo）** + 样式覆盖项齐全 | 🔸各有侧重：我们渲染侧更强、转写走服务端；它胜在**导入体验与浏览器内离线转写** |
| 字体 | 后端字体管理页 + 系统字体 | **1000+ Google Fonts 栅格化 avif 图集** + 系统字体枚举（不打包字体二进制） | 🔻字库规模与加载策略 |
| 导出 | 后端 ffmpeg 渲染队列 + **平台化预设**（Bilibili/YouTube/抖音/微博/自定义/ProRes422HQ/H.265 10bit/720p/480p）+ SSE 进度 + dry-run | 客户端 WebCodecs（mediabunny）：MP4(H.264)/WebM(VP9) + **四档质量** + 含音频 + 进度/取消/错误重试 | 🔹**我们更强**（格式与预设更贴国内平台）；可从它借"错误重试" |
| 项目管理 | 项目列表（搜索/按更新或名称排序/缩略图）+ 后端版本历史 + 归档 zip | 新建/打开/重命名/删除/最近项目 + 本地 IndexedDB | 🔹各有侧重（我们服务端、它本地） |
| 快捷键 | **44+ action、分类、可自定义并持久化、速查表、J/K/L、markers、V/C/R 工具** | 30 action（8 类，21 个默认快捷键）+ 可录制改键 + **冲突校验** + **键位迁移 v2→v7** + 严格导入校验 + 输入框内单字符键自动禁用 | 🔹数量与功能面我们更全；**它胜在"改键的健壮性"**（冲突校验/迁移） |
| i18n / 主题 | i18next 中英 + 设置页主题 | 无 i18n | 🔹**我们领先** |
| 测试 | 71 个测试文件（引擎/裁剪历史/吸附/快捷键/属性/历史/store） | bun test + vitest；CI 里 "No tests implemented yet" | 🔹**我们领先** |
| AI 工作流 | AgentPanel(68KB)/ReviewPanel/TimelineDiffView/需求确认/Persona/素材库/插件（14 文件） | 无 | 🔹**我们独有** |
| 账号/云 | 登录页 + 账号/市场服务（独立 8090） | db 仅存 auth/feedback | 🔹我们更完整 |

## 4.2 该补的 11 项（按性价比）

| # | 缺口 | 补法 | 工期 |
|---|---|---|---|
| 1 | 撤销缺"选择恢复 + 历史面板" | `historyStore.pushState` 附带 `selection`；undo/redo 后恢复；新增历史面板消费 `jumpTo` | 2–3d · ✅ **2026-09-13 完成**：`pushState` 自动抓取选择（40 处调用点零改动）；`pruneSelection` 裁掉已不存在的片段；因 `setTimeline` 默认 `deselectAll()`，改由 historyStore 内部 `swapTimeline()` 用 `preserveSelection` 切换并写回选择；`jumpTo` 把未来状态转入可重做栈 + 新增 `redoTo`；`features/history/HistoryPanel.tsx` 面板（中文动作名映射层 + 片段数/选中数/相对时间/可重做段）。前端 586 passed / 76 文件 |
| 2 | 关键帧无曲线编辑 | 数据层加 `bezier?: {h1,h2}`（`Keyframe.easing` 已可扩展）；属性面板加曲线图（Canvas 小图即可）；属性路径扩展成白名单（含特效参数） | 4–6d |
| 3 | 遮罩太弱 | `mask_type` 扩到 polygon/star/heart 等 + feather/inverted；预览端 Canvas `clip()` + 模糊羽化 | 3–5d |
| 4 | 时间模型浮点 | 后端 schema 引入整数 tick + 有理数帧率，API 兼容层双写；前端 branded type + 统一换算工具 | 5–10d（P2）· **后端第一阶段已做**（见 §1.2 O1 执行进度） |
| 5 | 无本地离线草稿 | IndexedDB 缓存媒体句柄/缩略图/波形 + 未保存改动；配额提示 | 3d · ✅ **2026-09-13 完成**：草稿（时间线快照）+ 媒体派生数据快照（缩略图/波形/时长，刷新后立即显示）+ 恢复横幅 + 配额淘汰重试 + 偏好迁移链（版本号/备份/幂等步骤）。媒体**文件**句柄仍不持久化（blob URL 刷新即失效）——这是边界，不是缺陷 |
| 6 | 预览无纹理缓存/诊断 | OffscreenCanvas 内容哈希缓存 + 每帧分段耗时与命中率面板 | 3–5d · ✅ **2026-09-13 完成**：`features/preview/layerCache.ts`（内容键 LRU 48 层 + 占位不入缓存 + 上下文复位）+ `perf.ts`/`PreviewPerfOverlay.tsx`（resolve/composite 分段、p95、绘制帧率、命中率，store 推送节流 250ms）；视频按源帧号量化（复用 O1 换算）、MG 动画不入缓存。**实测操作次数口径**：1080p 四轨拖动 120 步，内容光栅化 3360→1200（**−64.3%**）、seek 240→120（**−50%**）、命中率 33.3%、每帧层数不变。墙钟毫秒需真机手动验收（见执行轮次 117） |
| 7 | 吸附源少 | 增加"关键帧/书签"吸附源 + 预览区吸附参考线（OpenCut 做法：`preview/components/snap-guides.tsx`） | 2d |
| 8 | 特效预览还原度 | 预览端为 `filter_id` 提供近似 CSS/canvas 效果，或明确标注"仅渲染后可见" | 2–3d |
| 9 | **预览画布不能直接操作画面** | 预览区加变换手柄（拖拽移动/四角缩放/旋转）+ 命中测试 + 吸附参考线；写回 `metadata.transform`（属性面板已有该字段与滑杆，数据层无需改） | 4–6d |
| 10 | 改键缺健壮性 | `keybindingStore` 加**冲突校验**（同 combo 重复告警）+ **键位版本迁移**（导入旧配置时补默认值）+ 严格导入校验（非法 combo 拒绝）；输入框聚焦时禁用单字符键 | 1–2d |
| 11 | 字幕导入体验 | 加 SRT/ASS 导入（复用后端 `services/subtitle.py`；前端做 cue 预览与批量插入 text 轨，畸形 cue 告警） | 2–3d |

## 4.3 不要丢掉我们的优势

- **Agent 工作流**（AgentPanel/ReviewPanel/TimelineDiffView + 需求确认 + 时间线 diff 接受/合并）——OpenCut 完全没有，这是产品差异化的核心。
- **后端 ffmpeg 渲染**：格式覆盖（ProRes/水印/发光字幕/色彩匹配）远超客户端 WebCodecs，别为了"快"迁到浏览器。
- **插件化 + Persona + 素材治理**：OpenCut 无对应物。
- **快捷键体系与 i18n**：比 OpenCut 完整，别在对齐中降级。
- **服务端版本历史 + 多标签同步**：OpenCut 靠本地 IndexedDB 也没有这个。

## 4.4 值得学的 8 个"工程习惯"（与产品功能无关，但决定长期可维护性）

来自 OpenCut classic 的实现方式（细节见 `_research/findings-opencut-features.md`）：

1. **核心与 UI 状态分离**：单例 `EditorCore` 持有 12 个 manager（command/timeline/playback/scenes/project/media/renderer/save/audio/selection/clipboard/diagnostics），zustand 只管 UI。对照我们：`timelineStore`（32.9KB）同时承担数据与命令，`historyStore`/`selectionStore` 分离——可以再抽一层"编辑内核"。
2. **写操作全部命令化**：30 个 action 覆盖 8 类操作，`BatchCommand` 保证原子性，ripple 作为**命令后处理**而不侵入每个命令。对照我们：`pushState` 散落在各组件里，容易漏记。
3. **可插拔吸附源**：吸附目标来自"源"接口而非硬编码（元素/播放头/书签/关键帧各一个源）。对照我们：`snap.ts` 里是集中式 `collectSnapTargets`，加源要改核心函数。
4. **独立的 placement 层**：拖放"插到哪一格"的解析（重叠/兼容/主轨约束/插入索引）单独成模块并有单测。对照我们：逻辑散在 `TimelineEngine` 的拖拽分支里。
5. **参数 schema 驱动属性面板**：`ParamDefinition` 自动渲染控件 + 每个参数自带关键帧开关 + 数值可拖拽 scrub。对照我们：属性面板 59.7KB 手写分区（`sectionsForKind.ts`）。
6. **关键帧语义完整**：hold/贝塞尔/切线模式/外推，而不只是"命名 easing 列表"。对照我们：22 种命名 easing 覆盖了 90% 场景，缺"可手调的曲线"。
7. **存储分层与配额**：媒体（OPFS）与元数据（IndexedDB）分开，写前查配额余量。对照我们：媒体是 blob URL，刷新即失效。
8. **长期演进纪律**：存储迁移 v0→v31、键位迁移 v2→v7，每一步都有迁移器与测试。对照我们：后端 schema 演进可套用同一范式。

## 4.5 前端与 HyperFrames 的交叉机会（两问的交汇点）

1. **MG 预览升级**：`MgPreviewPage` 现在"提交 → 后端渲染 MOV → 播放"。可用 `<hyperframes-player>` 在浏览器内直接预览 MG HTML（秒级、可拖时间轴），渲染只用于最终成片。这一项同时解决"动画 Agent 效果难验证"与"预览慢"。
2. **时间线里直接看 MG**：编辑器预览面板对 MG clip 显示 HTML 预览帧（而非占位形状），配合 `snapshot --at` 取首/中/尾三帧做缩略图。
3. **视觉自评的证据图回传前端**：QualityAgent 的 `timeline_view` 证据图 + MG snapshot 作为"质检报告"在 ReviewPanel 展示，用户可直接看到"哪里有问题"并一键重做。
