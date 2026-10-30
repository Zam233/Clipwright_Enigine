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

## 执行记录（2026-10-30 · 第 9 轮 · M4 前端可见性 + M5c 需求会话问答 + 导出重试快照）

- **思考流与工具全部可见（§13 / M4 前端）**
  - `AgentPanel` 现在真的消费 SSE `reasoning` 帧（旧实现声明了 `liveReasoning` 却从不赋值，
    折叠条永远是空的）：逐块累积、每轮提问清空、正文已在流式时仍然可见（上限 20 万字）。
  - `ThinkingDisclosure` 改增量累积（旧实现每帧把全部片段重新 join 一遍，长任务 O(n²) 拖住主线程），
    首帧指纹变化或 seq 后退时整体重建（不会把上一轮思考挂到新任务上），到 40 万字上限如实说明停止追加。
  - `ToolCallRows` 三路合并（`tool_call`/`tool_result` 按 causation 配对 + `subagent_tool` + Skill），
    并**删除 `slice(-8)`**：主 agent / 子 agent / 技能各有来源筛选与计数；`source=kernel`（运行时补派）、
    `deduped`（复用上次结果）、`remaining_turns` 逐行标注；窗口化 60 行 + 「加载更早的」逐段翻到底，
    既不截断也不把上千行一次塞进 DOM。`ModelTurnRows` 同样取消 `slice(-6)`。
  - 后端 `subagent_tool` 补 `kind` / `run_id` / `seq_in_run`，前端 schema 同步（`ToolDetail.seq_in_run`）。
- **需求会话 ask_user（§15.5 / M5c）**——需求对话没有工具轮，因此复用同一个 `ask_user` 服务与同一套
  `question`/`answer` 事件语义，只把调用点放在 service：
  - LLM 结构化输出带 `ask_user` 指令 → `RequirementsService._ask_from_llm` 建问题
    （owner=`requirements`，事件流键 `requirements:<session_id>`，与任务事件按前缀隔离）→ 会话进
    `waiting_user`，本轮不再推进（不猜、不替用户选）；
  - `POST /api/requirements/{sid}/answer` 回答问题 → 答案还原成一句人话喂回对话 → agent 继续；
    `GET /{sid}/questions?status=pending|all`、`GET /{sid}/events?since_seq=` 供刷新/重连重建；
  - `waiting_user` 下用户直接打字 = 自由文本回答（自动应答后回到 gathering），不留死胡同；
  - 错误码对齐任务侧：404（跨会话答别人的问题）/ 409（已答、已取消）/ 410（超时或服务重启）/ 400；
  - 前端 `AgentPanel` 挂 `TaskQuestionCard`（`askerLabel="需求 agent"`），SSE `question` 帧到达即出卡，
    回答后回显 agent 续跑结果并刷新待答列表。需求会话**不访问任务事件 API**。
- **导出重试拆分（§20 M3 尾巴）**：提交时冻结时间线快照 + FNV-1a 指纹；失败卡片只给「重试本次提交」
  （跑快照），时间线改过才额外给「用当前时间线重新提交」，并把两侧指纹显示出来。旧实现永远跑
  "当前时间线"却叫重试，改完时间线再点重试会让人以为在重试、其实跑的是新内容。
- **回归**：后端 `pytest` **4924 passed / 4 skipped / 1 xfailed**（新增
  `test_requirements_ask_user.py` 9 例、`test_requirements_ask_user_api.py` 7 例）；
  前端 `tsc` 干净、`vitest` **136 文件 / 1171 例**（新增 `CopilotToolTransparency` 8 例、
  `exportSnapshot` 7 例、`AgentPanel` +5 例）；`stage_tool_failures` 失败率门禁仍绿。
- **顺手修的环境脆弱点**：`test_dead_extra_keys.py::test_plumbing_audit_passes_strict` 用 `text=True`
  按 locale（Windows 中文机 cp936）解码子进程的 UTF-8 输出，报告里一有中文就 `UnicodeDecodeError`
  → `stdout=None` → `json.loads(None)`；改为父进程显式按 UTF-8 解码。
- **未完成**：M6（动画提示词 v2：九段式模板、S1–S12 风格模块、lint、逐类改写）尚未开始；
  `TaskToolTrace` 已有后端 `limit/offset` 分页，本轮未再加虚拟滚动。

## 执行记录（2026-10-30 · 第 10 轮 · M6 动画提示词 v2）

- **九段式模板**：`prompts/text/_base.md`、`prompts/types/_base.md`（角色与交付物 / 硬约束 /
  常量表 / 分段关键帧公式与 easing 词典 / 风格模块引用 / 不要清单 / 自检清单 / 输出格式 /
  失败回退）。类型提示词保留七段旧标题（`text_specs.load_text_prompt` 与
  `validate_text_registry` 按这七段切分），另补 Constants / Output / Self-check / Fallback 四段。
- **风格模块 S1–S12**：`prompts/style_catalog.json`（id/name/series/keywords_zh/keywords_en/
  palette_hex/motion_tags/composition_tags/suitable_types/avoid_types/forbidden/
  renderer_paths/reference_source/prompt_version/status）+ `prompts/styles/S1–S12.md` 正文
  （视觉语言 / 关键参数倾向 / 适用与回避 / 禁止项 / 使用纪律）。
  `scripts/styles.py` 提供 `search` / `show` / `match`：默认只给 ≤3 条摘要、排序确定
  （分数→编号）、`avoid_types` 过滤、`show` 才读正文——不把 12 份模块正文一次拼进 prompt。
- **逐类改写（10 份）**：六类文字（emphasis_keyword / reveal_line / quote_block /
  label_callout / number_punch / title_stack）+ 四类 MG（bar_chart / line_chart /
  comparison / flow_chart）。要点：
  - drawtext 提示词只写 `keyframes_expr.EASING_EXPR` 的 21 个名字；`back-out`/`bounce`/
    `elastic-out` 明确标注为「MG 名字，drawtext 不认」，且只出现在反例行里；
  - 粒子 / 模糊 / 遮罩 / 扫光 / 独立卡片层一律标注「MG HTML 路径」或「本期不落」，
    并在 `docs/animation_system.md` 新增「渲染侧需求登记」表逐条列出所需能力；
  - Example 换成**可渲染 JSON**：扁平关键帧（不再用 `properties` 包裹，`repair_mg_json`
    会把它当未知 props 删掉）、每帧声明完整动画属性、带 `time=0` 起始帧；
  - 每份提示词声明 `style_modules: [S…]`，并给了"收束"阶段让动画覆盖率 ≥45% 且末段静止 ≥0.6s。
- **lint 进 CI**：`python -m clipwright.animation.prompts.lint_prompts --strict`
  （`.github/workflows/ci.yml` 新增一步）。检查段落齐全、反例行豁免（"不要写 X"不算违规）、
  占位符残留、duration/元素/字数上限、按渲染路径的 easing 白名单、drawtext 不支持能力词
  必须标注、Example 必须过 `validate_mg_json`、风格模块引用必须存在。
  现状 **0 error / 136 warning**：未迁移的老 MG 类型只报 warning，迁移后自动升级为 error
  （`MIGRATED_TYPES`）。
- **指标回归** `tests/clipwright/test_prompt_metrics.py`：把每份提示词自己的 Example 喂真实
  校验链路——motion coverage ≥45%、末段静止 ≥0.6s、`position` 不落字幕带（底边 ≤920px）、
  单行 ≤14 字、元素 ≤3、关键帧升序且在片段内、同输入两次结果一致、
  `repair_mg_json` 不得改动结构（修复率 = 0）。
- **顺手修不稳定测试**：`test_render_parallel::test_failed_mov_does_not_abort_others` 的
  2.5s 绝对阈值在 Windows 上连跑三次就随机红（实测 2.76s），改成相对串行基线的 80%
  （4×1.0×0.8=3.2s），仍能抓"串行化 / gather 悬挂"。
- **回归**：后端 `pytest` **4966 passed / 4 skipped / 1 xfailed**（+41：lint 29、指标 12）；
  提示词 lint strict 0 error；前端 `tsc` 干净、`vitest` 136 文件 / 1171 例。
