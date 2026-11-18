# 动画质量提升方案

- 状态：规划稿 / 待实施
- 输入：`garden-skills` 六个 Skill 的公开 SKILL/README 与当前项目动画链路
- 当前分支：`feat_zam_animation_improvements`
- 目标：不再只靠堆提示词，而是补齐 **Brief → 风格帧 → 连续叙事 → 自动布局 → QA → 审片** 的闭环，使 LLM 生成的动画从“能动”提升到“可交付”。

## 1. 结论先行

当前动画的问题不是“模型不会写 JSON”，而是缺少三层约束：

1. **创作前**：没有对标、风格方向、禁止清单、记忆点；
2. **创作中**：逐镜头生成，没有对象承接、连续轨迹和统一运动性格；
3. **创作后**：没有文本安全区、阅读时间、contact sheet、手机审片、空拍/跳变检测。

`garden-skills` 最值得借鉴的是它的**制作方法**，不是它的运行时：

```text
需求确认 → brief / 对标 → 风格帧 → 分镜/连续叙事
→ 制作 → 工作区预览 → 技术验收 → 用户审片 → 导出
```

本项目已经有：

- HyperFrames + MG JSON 的确定性渲染；
- 严格 JSON validator；
- `AnimationPlan` / 规则库 / 模块化 prompt；
- Persona 视觉令牌；
- 文本宽度估算、安全区回退、布局重叠守卫；
- 预览 API 与浏览器帧检查。

当前最缺的是：

- `AnimationBrief`（对标、风格、记忆点、禁止清单）；
- 风格库与风格帧审批；
- 跨镜头对象承接和音画事件；
- 完整 QA/审片闭环。

## 2. 六个 Garden Skill 的适用性

| Skill | 能力 | 对本项目价值 | 采用方式 |
|---|---|---|---|
| `motion-showreel` | 代码动画短片、brief、风格帧、连续叙事、QA | **最高** | 采用方法论与验收流程，不引入其 render/editor 运行时 |
| `web-video-presentation` | 口播稿→章节网页、主题 token、每步一屏、narrations 单一真相源 | 高 | 借用“章节/节拍/单一真相源”和主题 token，不做点击演示 |
| `web-design-engineer` | 设计方向、风格配方、反 AI 味、critique rubric | **高** | 借用 Design Direction 与 critique，不生成网页 |
| `gpt-image-2` | 18 类、80+ 图像提示词模板与图像编辑 | 中高 | Mode B/C 用于 MG 参考图/插图/风格帧；不依赖其生图 API |
| `beautiful-article` | 源材料→规划→首屏→审阅→修复 | 中 | 借用 source/plan/checkpoint/review 方法，不生成文章 |
| `kb-retriever` | 本地知识库分层检索与溯源 | 中 | 为数据型动画提供事实与来源，不参与动效设计 |

### 2.1 不建议直接采用的部分

- 不引入 `motion-showreel` 的 `render.mjs` / `editor.mjs` / `score.mjs`；
- 不引入 `web-video-presentation` 的 Vite/React 点击演示；
- 不引入 `beautiful-article` 的 reacticle 文章栈；
- 不把 `gpt-image-2` 的 API Key 写进项目；
- 不复制 Skill 的模板 CSS/主题资产，只提炼字段与规则；
- 不把 43 个风格全部引入，先做 6–8 个。

## 3. 当前动画链路的主要缺口

| 缺口 | 当前表现 | 目标 |
|---|---|---|
| 没有 Brief | 直接从场景描述生成 MG | 先定对标/风格/禁止项/记忆点 |
| 没有风格帧 | 直接生成成片 | 3 个方向风格帧，用户选 1 个 |
| 逐镜头独立 | 03 卡片/04 节点/05 文本各说各话 | 对象承接、连续轨迹、统一运动性格 |
| 风格默认味 | 深色底 + 居中大字 + 蓝紫发光 | 2+1 配色、明确风格、禁止清单 |
| 文字只保不重叠 | 有安全区但无阅读时间/手机检查 | 阅读时间、bbox、手机审片 |
| 无完整 QA | 有布局守卫但无 contact sheet/motion report | 技术验收 + 审美审片 |
| 音画分离 | 动画和音频各自生成 | shared beat/events |
| 数据无溯源 | 数字靠模型编 | 数据动画接知识库/事实检索 |

## 4. 目标工作流

```text
0. 需求 / 平台 / 时长 / 画幅确认
1. Reel Brief：对标 + 风格 + 禁止清单 + 记忆点
2. 3 个风格帧（或 3 个设计方向） → 用户选 1
3. 分镜 / AnimationPlan：对象、变化、承接、seams、节拍
4. Agent 自主决定：研究/草稿/工具/生成顺序（不是固定流水线）
5. 生成 mg_def（可多轮、可局部修复）
6. 自动布局 + 文本安全区 + 文本阅读时间
7. AnimationAgent 自检 QA：bbox/重叠/字号/阅读时间/空拍
8. 技术 QA：contact sheet / phone / strip / motion / determinism
9. 前端活动流 / 可选风格帧 / QA 面板 → 工作区预览 → 用户确认（如需要）
10. 前端闸门通过后导出 / 归档 brief + style + QA
```

## 5. 逐项改进设计

### 5.1 新增 `AnimationBrief`

在 `AnimationPlan` 之前增加一份 brief，参考 `motion-showreel` 的 Reel Read。

```json
{
  "topic": "一句话主题",
  "audience": "受众",
  "platform": "B站/抖音/YouTube",
  "duration_sec": 10,
  "aspect": "16:9",
  "benchmark": {
    "reference": "参考片名/链接",
    "learn": ["节奏", "字重", "转场"],
    "avoid": ["紫色渐变", "粒子爆炸"]
  },
  "structure": ["Hook", "建立", "加速", "签名", "落版"],
  "style": {
    "school": "swiss-editorial",
    "signals": ["网格", "大留白", "单色强调"],
    "motion_character": "snap / glide，无弹簧"
  },
  "takeaway": "观众记住的一句话",
  "signature": "全片唯一记忆点",
  "palette": ["#背景", "#主色", "#强调色"],
  "forbidden": ["深色居中大字", "所有元素同时淡入", "无目的粒子"]
}
```

实现位置：

```text
clipwright/animation/brief.py
clipwright/schemas/animation_brief.py
```

验收：

- brief 不完整不允许进入 MG；
- 前端可展示/编辑 brief；
- brief 写入 `AnimationPlan` 或 trace。

### 5.2 风格库（先 6–8 个）

参考 `web-design-engineer` 的 Design Direction 与 `motion-showreel` 的风格库，但只保留适合本项目的结构化风格：

- `swiss-editorial`：网格、左对齐、巨大字号、snap；
- `cinematic-title`：大留白、慢推、强字重、少装饰；
- `soft-3d`：柔和体积、轻弹簧、低对比；
- `blueprint`：线框、标注、工程感；
- `paper-ink`：纸色、手写、扫描质感；
- `data-story`：图表、数字、节拍、结论先出；
- `comic-panel`：分格、硬切、拟声；
- `loop-minimal`：可循环、固定机位、精确首尾。

每个风格只写：

```json
{
  "id": "swiss-editorial",
  "signals": ["左对齐网格", "大留白", "单色强调"],
  "palette": ["#F7F3EA", "#111111", "#D7263D"],
  "type": {"display": "heavy sans", "body": "neutral sans", "scale": [56, 120, 220]},
  "motion": {"easing": "snap", "entry": 0.35, "exit": 0.2, "stagger": 0.04},
  "forbidden": ["居中大标题", "紫色渐变", "发光粒子"]
}
```

不要照搬 43 个风格；先 6–8 个，后续按需扩展。

### 5.3 三个风格帧 + 审批

在生成完整 MG 前：

1. 根据 brief 生成 3 张 **静态风格帧**（同一句话、同一数据，三种风格）；
2. 每张标注 school / palette / type / motion；
3. 用户选 1 个；
4. 选中的风格帧作为后续每个镜头的视觉锚点。

对应 Skill：

- `motion-showreel` 第 3 步；
- `web-design-engineer` Design Direction Advisor；
- `gpt-image-2` Mode B/C 可生成参考图/风格图。

验收：

- 没有确认风格帧，不进入批量 MG；
- 风格帧与最终成片的 palette/type/motion 可追溯。

### 5.4 连续叙事字段

每个镜头增加：

```json
{
  "inherits": {"from_shot": 1, "object": "title-word", "position": [960, 540]},
  "before": "上一镜结束状态",
  "after": "本镜结束状态",
  "change": "观众要理解的变化",
  "handoff": {"vector": "left", "carrier": "title-word", "speed": "matched"}
}
```

规则：

- 下一镜必须继承至少一个对象/位置/方向；
- 不允许每镜清空舞台重摆；
- 转场必须写 carrier；
- 接触/释放类动作检查接触点；
- 伪运动（粒子/呼吸光/推镜）不能替代信息变化。

### 5.5 禁止清单与 2+1 配色

将 `craft.md` 的 AI 默认味转成项目 prompt 约束：

```text
- 禁止默认深色 + 居中大字 + 紫蓝渐变 + 到处发光；
- 配色不超过 2 主色 + 1 强调色；
- 一个镜头只有一个主角；
- 禁止无目的粒子/光斑/光晕；
- 禁止所有元素同时淡入；
- 禁止匀速平移作为主运动；
- 一屏一句，中文约 12 字；
- 主体字号 ≥ 画面高度 4%；
- 入场 0.35–0.6s，离场 0.2–0.3s；
- 同类元素同一套 easing/spring。
```

这些必须是 **brief 的 forbidden 字段 + prompt 硬约束 + validator** 三层，不只是提示词。

### 5.6 文本可读性与阅读时间

当前 `layout.py` 已有：

- 宽度估算；
- 安全区回退；
- 同组字号统一；
- 重叠守卫。

继续补：

- 阅读时间：

```text
英文字母数 / 15 + 1.5s
中文汉字数 / 4.5 + 1.5s
```

- 文字完全显示后至少停留该时间；
- 手机审片：1280×720 / 390×844 缩略；
- 真实字体 bbox 测量（浏览器 `getBoundingClientRect`）而非纯字符估算；
- 遮挡/框景检查：文字是否被图形/图片挡住。

### 5.7 运动质感默认值

参考 `craft.md`：

- 入场 0.35–0.6s，离场 0.2–0.3s；
- 同组错峰 30–60ms；
- 大动作前有预备动作；
- 弹簧参数按“运动性格”固定，不要每镜随机；
- 同类元素同一套缓动；
- 快速运动 `--subframes 4` 运动模糊；
- 细线/小字 `--dpr 2` 超采样；
- 颗粒在导出层加，不在页面画噪点。

### 5.8 共享音画时间线

短期：

- `AnimationPlan` 增加 `beat_grid`、`events[]`；
- MG prompt 注入关键变化秒数；
- 转场/冲击/关键帧对齐同一组事件。

中期：

- 音频/配音/音效和 MG 使用同一 `timeline.json`；
- 事件 `at_sec` 一致；
- 预览工作区能同时 scrub 画面和音频。

### 5.9 QA 与审片

技术验收：

- 尺寸/帧率/时长；
- 帧数/黑帧/空拍；
- 首尾 loop seam；
- determinism：同一输入两次渲染 hash；
- 响度/真峰值（有音频时）；
- 文本 bbox/overflow；
- motion 报告：按镜头变化比例、静止帧步、跳变。

审美审片：

- contact sheet（全片）；
- phone sheet（手机尺寸）；
- strip（切点连续帧）；
- 每镜运动热图；
- 独立 reviewer / 人工看片。

### 5.10 参考片拆解

参考 `motion-showreel` 的 `inspect sheet/grid/heat`：

- 用户给参考视频 → 抽 contact sheet；
- 识别剪点与节拍；
- 写“学什么/不学什么”；
- 不复制素材、角色、文案、配色。

### 5.11 图像资产与事实

- `gpt-image-2` Mode B/C：为风格帧/MG 插图/图标生成高质量 prompt；
- 不把 API key 写进项目；
- `kb-retriever`：数据型 MG 先查知识库/来源，禁止模型编数字；
- 在 `AnimationBrief` 增加 `sources[]`，进入 trace/交付物。

### 5.12 Agent 自主编排（不是固定流水线）

动画生成应交给一个**有工具、有提示词目标、有完成标准**的 AnimationAgent，而不是一个固定状态的 pipeline。用户的要求是：

> Agent 根据提示词中的需求，自主判断先做什么、是否需要研究、要不要先出草稿、何时生成、何时自检、失败后回到哪里。

因此不强制：

```text
规划 → 细化 → 研究 → 草稿 → 落地 → 自检 → 待审片
```

这条顺序只是**可选的工作方式**，不是状态机。Agent 可以：

- 简单标题直接生成，跳过研究；
- 数据镜头先 `kb_search` / `web_search` 再出草稿；
- 复杂镜头先写 `ShotPlan` 或 `DraftSpec`，简单镜头直接落 MG；
- 并行调用素材/事实/字体检查；
- 自检失败后只重做布局、只补来源、只改一句文案，而不是整段重生成；
- 根据用户反馈回到任意一步；
- 不需要为了“走完流程”而假装完成某个阶段。

#### 5.12.1 系统提示词给目标，不给步骤

AnimationAgent 的系统提示词应写：

- 角色：动画导演 / motion designer；
- 目标：在给定时长、画幅、brief、风格和事实约束下，做出可交付动画；
- 完成标准：内容覆盖、叙事连续、文字可读、布局不重叠、QA 通过、来源可追溯；
- 可用工具：brief、research、kb、draft、generate、layout、qa、preview、repair；
- 预算：最多 3 轮修复、工具调用上限、时长上限；
- 自主权：可跳过、并行、回退、询问用户；
- 禁止：为了走流程而输出空阶段；无来源编数字；QA 未过声称完成。

不要写“必须先规划再研究再草稿”的硬顺序。

#### 5.12.2 工具与产物是能力，不是阶段

提供以下**可被 Agent 自主调用**的能力：

| 能力 | 作用 | 是否必须 |
|---|---|---|
| `build_animation_brief` | 生成/更新 brief | 可选 |
| `research_web` / `kb_search` | 事实、数据、参考 | 按内容 |
| `build_shot_plan` | 镜头意图/承接/seams | 复杂镜头 |
| `build_draft_spec` | 元素角色/层级/相对布局 | 可选 |
| `generate_mg` | 生成 mg_def | 必须 |
| `normalize_layout` | 安全区/字号/阅读时间 | 生成后自动 |
| `qa_animation` | text/phone/motion/determinism | 必须（生成后） |
| `repair_animation` | 按 fail 项局部修复 | 按需 |
| `request_user_review` | 风格帧/画面确认 | 按用户需求 |

产物：

- `AnimationBrief`；
- `ShotPlan`；
- `DraftSpec`；
- `mg_def`；
- `QAReport`；
- `repair-log`。

产物用于**可追溯**，不是必须逐个填满的表单。

#### 5.12.2a Tool 与 Skill 都是 Agent 的能力

除了原子 Tool，Agent 还必须能调用更高层的 **Skill**。区别：

| 层 | 例子 | 适合做什么 |
|---|---|---|
| Tool | `web_search`、`kb_search`、`generate_mg`、`qa_animation` | 单一动作、确定性输入输出 |
| Skill | `motion-showreel`、`web-design-engineer`、`data-story`、`text-animation` | 一套方法、多个步骤、可再调用 Tool |

Agent 自主选择：

- 简单标题：直接 `generate_mg`，不必加载 Skill；
- 需要完整短片方法论：`load_skill("motion-showreel")`，按 Skill 的 brief/风格帧/审片流程执行；
- 需要设计方向/反 AI 味：`load_skill("web-design-engineer")`；
- 数据型动画：`load_skill("data-story")` + `kb_search`；
- 文字动画：`load_skill("text-animation")`；
- 需要图像资产：`load_skill("gpt-image-2")`（Mode B/C，只出 prompt 或委托宿主工具）；
- 需要事实/知识库：`load_skill("kb-retriever")`。

Skill 调用契约：

```json
{
  "type": "animation_activity",
  "kind": "skill_call",
  "name": "motion-showreel",
  "status": "success",
  "summary": "已加载 brief/风格帧/审片方法",
  "artifact_ref": "skills/motion-showreel/SKILL.md",
  "children": ["build_animation_brief", "qa_animation"]
}
```

规则：

- Skill 必须来自 `SkillRegistry`，按能力名解析，不写死实现；
- Agent 可加载完整 Skill，也可只读取其 references；
- Skill 可以继续调用 Tool 或其他 Skill；
- Skill 不可用时如实降级，不假装已执行；
- Skill 产物（brief/风格帧/QA 清单）进入 trace/工作区；
- 不允许把 Skill 的说明文本直接当 HTML/脚本执行；
- 不允许为了“用上 Skill”而套一个不相关的方法论。

系统提示词中的能力面写成：

```text
可用 Tool：brief/research/kb/draft/generate/layout/qa/repair
可用 Skill：motion-showreel / web-design-engineer / web-video-presentation /
           gpt-image-2 / beautiful-article / kb-retriever（按 SkillRegistry 实际注册）
选择原则：先判断任务需要的方法层；简单任务直接用 Tool，
         复杂任务加载 Skill；Skill/Tool 都可跳过、并行、回退。
```

#### 5.12.3 完成标准与失败处理

Agent 自己判断是否完成，但必须满足：

- text_content 每段都落到画面；
- 同镜一个主角；
- 文字不重叠/不溢出/可读；
- 数据有来源；
- QA 技术项通过；
- 用户确认（如果用户要求确认）。

失败时 Agent 自己决定修复粒度：

```text
布局失败 → 只调 normalize_layout / 局部改坐标
字号失败 → 只改字号/分组
事实缺失 → 只补 research
叙事不连贯 → 只改 ShotPlan / 承接
生成失败 → 换 prompt 重试或降级模板
```

最多 3 轮修复；仍失败则如实标记 `unfinished`，不伪装完成。

#### 5.12.4 可观测性

不要求固定阶段，但每次工具调用/关键决策要有 trace：

```json
{
  "type": "animation_activity",
  "kind": "tool_call|skill_call|decision|artifact|qa|repair|user_review",
  "name": "qa_animation",
  "summary": "text #2 字号 < 4% 屏高",
  "artifact_ref": "qa/qa-report.json",
  "at": "2026-11-19T..."
}
```

前端展示的是**活动流**，不是“必须走完七步”的进度条。

### 5.13 前端与预览适配（Agent 自主编排）

前端不把 Agent 画成固定 pipeline，而是让用户看到“它现在在做什么、调用了什么、产出了什么、哪里失败”。

#### 5.13.1 活动流而非步骤条

动画任务卡/预览面板显示：

- 当前动作（研究 / 草稿 / 生成 / 布局 / 自检 / 修复 / 等待确认）；
- 最近 Tool / Skill 调用与结果；
- 产物（brief / shot plan / draft / mg_def / QA）；
- 失败项与 Agent 选择的修复动作；
- 修复轮次。

不显示“规划 1/6、细化 2/6”这种强制进度。

SSE 事件：

```json
{
  "type": "animation_activity",
  "kind": "tool_call",
  "name": "qa_animation",
  "status": "failed",
  "summary": "text #2 字号 < 4% 屏高；line 穿过文字",
  "retry_action": "normalize_layout",
  "artifact_ref": "qa/qa-report.json"
}
```

前端用 `asText` / `textList` 渲染，禁止把对象当 React child。

#### 5.13.2 Brief 与风格帧是可选工具

- `AnimationBriefCard`：展示/编辑 brief；
- `StyleFramePicker`：用户要求风格确认时出现 3 张风格帧；
- 用户没有要求确认时，Agent 可以直接按 brief 生成；
- 不把 brief/风格帧做成硬闸门；
- 用户选择结果写入 plan，后续可追溯。

#### 5.13.3 Draft / QA 的可见性

预览侧栏可折叠展示：

- ShotPlan：inherits / before / after / change / handoff；
- DraftSpec：元素角色/层级/相对布局/意图；
- QA：text / phone / motion / determinism；
- repair-log：每次失败与修复。

QA 未通过：

- 显示 fail 项；
- 提供“按失败项修复”；
- Agent 可选择局部修复或重新生成；
- 保留上一版 QA 对比。

#### 5.13.4 预览契约

前端预览继续只消费 `clip.metadata.mg_def`：

- 禁止执行 `metadata.mg_html`；
- brief/style/draft/qa 是元数据，不进入 canvas；
- 旧 mg_def 没有新字段必须正常渲染；
- 后端自动布局后的坐标/字号必须反映到预览；
- `mgFidelity.ts` 标记前后端差异；
- `width/height` 缺省使用 `mg_def` 尺寸。

#### 5.13.5 导出与确认

- QA 技术项未通过：导出禁用；
- QA 通过但用户要求确认：等待用户确认；
- 用户未要求确认且 QA 通过：允许导出；
- 不允许静默导出未通过 QA 的成片；
- QA 文件写入 `qa/` 并进入 trace。

#### 5.13.6 前端文件与测试

```text
src/features/preview/PreviewPanel.tsx
src/features/preview/mgRenderer.ts
src/features/preview/mgFidelity.ts
src/features/animation/AnimationBriefCard.tsx
src/features/animation/StyleFramePicker.tsx
src/features/animation/AnimationActivityFeed.tsx
src/features/animation/QAReportPanel.tsx
src/features/animation/api.ts
src/features/animation/types.ts
```

测试：

- 活动流事件渲染（含 skill_call）；
- Skill 不可用时降级提示；
- brief/style 可选，不阻断直接生成；
- QA fail 导出禁用；
- 旧 mg_def 兼容；
- 不执行 mg_html；
- 三种宽度下侧栏不挤压画布；
- 缺失 artifact 不崩。

## 6. 优先级与里程碑

### P0（先做，收益最大）

1. `AnimationBrief` + forbidden + signature；
2. 3 个风格帧与用户确认；
3. 禁止清单 + 2+1 配色 + 文本可读性硬约束；
4. 连续叙事字段；
5. contact sheet/phone/text/motion QA；
6. Agent 自主编排 Tool + Skill（brief/research/draft/generate/qa/repair；motion-showreel/web-design-engineer 等），非固定阶段；
7. 前端活动流 / 可选 brief / 风格帧 / QA 面板 / 导出闸门。

### P1（第二阶段）

6. 6–8 个风格库；
7. 共享 beat/event 时间线；
8. 参考片拆解；
9. 真实字体 bbox + 自动修复；
10. 独立 reviewer。

### P2（第三阶段）

11. 长片章节/缓存；
12. 音频混音与 loop seam；
13. 图像资产生成与资产库；
14. 数据知识库溯源。

## 7. 验收指标

| 指标 | 基线 | 目标 |
|---|---|---|
| 人工视觉评分 | 当前 5 个样本 | +0.8/5 |
| 连续性/转场评分 | 当前样本 | +0.8/5 |
| 文本溢出/重叠 | 有 | 0 |
| 同组字号不一致 | 有 | 0 |
| 空拍/跳变 | 未统计 | 报告并下降 50% |
| 渲染失败率 | 当前 | 不高于基线 |
| 首版可用率 | 低 | +30% |
| QA 通过率 | 无 | 100% 技术项 |

## 8. 风险

| 风险 | 对策 |
|---|---|
| 风格库变成模板库 | 每个风格只给 signals/禁止项，给改造空间 |
| Brief 过重导致流程慢 | 短视频只要求对标/风格/记忆点/禁止项四项 |
| 风格帧成本高 | 3 张静态图，复用同一文案/数据 |
| 自动布局过度 | 只处理安全区/同组/flow，不重写创意 |
| 参考片版权 | 只学节奏/结构，不复制素材/文案 |
| Skill 版本漂移 | 记录引用 commit，规则入库 |
| 模型能力不足 | 短规则 + JSON schema + QA；不一次塞全量文档 |

## 9. 不做什么

- 不替换 HyperFrames；
- 不引入 Garden 的运行时；
- 不直接使用其 43 个风格；
- 不把图像生成 API key 写进仓库；
- 不把“参考片”当素材复制；
- 不用更多 prompt 替代 QA。

## 10. 参考

- [garden-skills README](https://github.com/ConardLi/garden-skills)
- [motion-showreel SKILL](https://raw.githubusercontent.com/ConardLi/garden-skills/main/skills/motion-showreel/SKILL.md)
- [motion-showreel craft.md](https://raw.githubusercontent.com/ConardLi/garden-skills/main/skills/motion-showreel/references/craft.md)
- [web-video-presentation SKILL](https://raw.githubusercontent.com/ConardLi/garden-skills/main/skills/web-video-presentation/SKILL.md)
- [web-design-engineer SKILL](https://raw.githubusercontent.com/ConardLi/garden-skills/main/skills/web-design-engineer/SKILL.md)
- [gpt-image-2 SKILL](https://raw.githubusercontent.com/ConardLi/garden-skills/main/skills/gpt-image-2/SKILL.md)
- [beautiful-article SKILL](https://raw.githubusercontent.com/ConardLi/garden-skills/main/skills/beautiful-article/SKILL.md)
- [kb-retriever SKILL](https://raw.githubusercontent.com/ConardLi/garden-skills/main/skills/kb-retriever/SKILL.md)
