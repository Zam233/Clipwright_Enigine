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
4. Agent 细化：草稿 → 研究/素材 → 结构 spec（不是一次成型）
5. 逐镜头落地 mg_def
6. 自动布局 + 文本安全区 + 文本阅读时间
7. AnimationAgent 自检 QA：bbox/重叠/字号/阅读时间/空拍
8. 技术 QA：contact sheet / phone / strip / motion / determinism
9. 工作区预览（可 scrub + 音频） → 用户确认
10. 导出 / 归档 brief + style + QA
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

### 5.12 Agentic 动画生产循环（不要求一次生成）

动画生成不应是“一个 step 里直接吐 mg_def”。应把 AnimationAgent 做成可多轮、可调用工具的 agent，至少分六个阶段：

```text
A. 规划        读 brief / 场景 / 上下文，确定这一镜要说明什么
B. 细化规划    选风格、主体、变化、承接、seams、beats、禁止项
C. 研究        需要时调用 web_search / web_fetch / kb / 图片素材，
               数据必须带 source；不得凭空造数字
D. 草稿        只生成“结构草稿”：元素角色、层级、相对布局、运动意图，
               不要求一次写全关键帧
E. 落地        按草稿生成 mg_def，调用自动布局/字号/安全区/阅读时间
F. 质检        AnimationAgent 自己用 QA 工具检查：
               文本 bbox、重叠、字号、阅读时间、空拍、元素数、配色
               → 不通过则带着具体 fail 项回到 D/E，而不是盲重试
G. 终审        通过后交主 Agent / 人工审片；记录 brief、草稿、QA、修复轮次
```

硬约束：

- 规划/细化/研究/质检都必须是**真实阶段**，不能只在 prompt 里写一句“请先思考”；
- 每个阶段有结构化产物：`AnimationBrief`、`ShotPlan`、`DraftSpec`、`mg_def`、`QAReport`；
- 质检不通过必须回到具体阶段（布局失败回 E，叙事失败回 B，事实缺失回 C），禁止整段重生成；
- 最多 3 轮修复，仍失败则降级并留下 `unfinished`，不伪装成完成；
- 研究阶段的来源写入 `brief.sources`，数据型动画没有来源不允许进入 D；
- 最终 trace 必须包含阶段耗时、工具调用、QA fail/pass、修复次数。

这对应 `motion-showreel` 的 brief/风格帧/制作/审片，以及 `web-video-presentation` 的“首章验收后再批量”和 `beautiful-article` 的 reviewer/repair 流程。

## 6. 优先级与里程碑

### P0（先做，收益最大）

1. `AnimationBrief` + forbidden + signature；
2. 3 个风格帧与用户确认；
3. 禁止清单 + 2+1 配色 + 文本可读性硬约束；
4. 连续叙事字段；
5. contact sheet/phone/text/motion QA；
6. Agent 六阶段循环：规划/细化/研究/草稿/落地/自检，带修复回退。

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
