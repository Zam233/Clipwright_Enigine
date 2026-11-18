# 动画 Agent 提示词 Skill 化改造 PRD

- 状态：MVP 已实施（规则库 + plan + modular prompt + 测试）
- 范围：`clipwright/agents/animation_agent.py`、`clipwright/prompts/engine/mg.py`、`clipwright/prompts/agents.py`、`clipwright/animation/mg/generator.py`
- 分支：`feat_zam_animation_improvements`
- 目标：用 HyperFrames 的动画 Skill 方法论重构动画 Agent 的提示词组织方式，提升 MG 动画的 **视觉质量、镜头连续性、风格一致性、JSON 合格率**，同时降低提示词冗余与无效约束。

## 1. 背景与问题

### 1.1 当前提示词现状

| 位置 | 当前内容 | 问题 |
|---|---|---|
| `prompts/engine/mg.py::MG_DESIGNER_SYSTEM` | 13,702 字符；11 条设计原则、设计令牌、关键帧 easing、元素类型、属性白名单、2 个完整 JSON 示例、华丽技巧、镜头卡、质量红线 | 过长；规则与示例重复；约束互相冲突；每次生成都全量注入 |
| `prompts/agents.py::STRUCTURE_SYSTEM_PROMPT_TPL` | 每个场景必须带动画标记；优先 mg_dynamic；开场禁逻辑动画 | 与 MG 指南存在优先级冲突；没有“整片运动方向/转场预算” |
| `prompts/engine/mg.py::MG_CRITIQUE_*` | 复用完整设计提示 + 构图/层级/方向/编排检查 | 只审单镜头，没有 seam、carrier、idle wobble、全片连续性检查 |
| `prompts/__init__.py::build_animation_system_prompt` | 标记指南 + 动态 MG 能力拼接 | 结构提示与 MG 提示各自为政，缺少统一计划层 |
| `animation/mg/generator.py` | 直接生成完整 MG JSON；失败走 critique/repair | 没有“先计划后生成”的中间表示，修错成本高 |

### 1.2 具体冲突

1. `MG_DESIGNER_SYSTEM` 推荐 `bounce` / `elastic-out`；HyperFrames `motion-doctrine` 明确禁止 `bounce.out` / `elastic.out`（入场 overshoot 允许 `back.out`）。
2. 提示词要求“粒子 8–15 个”“华丽技巧”“发光”，但没有区分“氛围装饰”和“承担叙事的主体”；容易产生 idle wobble 和同屏噪音。
3. 提示词要求每个元素至少 3 个关键帧、第一个隐藏，但没有“入场必须 mid-motion / 退出必须还在动”的 seam 概念。
4. MG 是逐镜头生成；没有全局 `current`、vector ledger、carrier、转场预算，镜头之间天然容易“PPT 化”。
5. “每个场景必须有动画标记”与“第一个场景禁止动画标记”没有明确优先级，模型可能二选一随机执行。
6. 两个完整 JSON 示例占大量 token，且示例中的元素/技巧会诱导模型复制样式，抑制类型差异化。
7. critique 继承完整设计提示，评审时又被同一套长规则锚定，难以发现“规则之外”的构图/连续性问题。

### 1.3 现有基础

已具备：

- Prompt 单一来源 `clipwright/prompts/`；
- 运行时覆盖 `_mg_constraint`；
- `mg_def` 结构化输出与 validator；
- MG preview 只消费结构化 spec；
- HTML/JSON 渲染与质量门禁。

因此本次不应引入 HyperFrames 运行时依赖，而是 **把 Skill 当作知识与规则源，翻译成我们自己的 Prompt 模块和校验器**。

## 2. 目标与非目标

### 2.1 目标

1. 把动画提示词拆成 **计划层 / 镜头层 / 评审层 / 校验层**，按需注入。
2. 引入 `motion-doctrine` 的全局运动法则：current、seam、carrier、causal motion、no idle wobble。
3. 引入 `hyperframes-animation` 的“选 2–4 条 atomic rule”思路，替代全量规则堆叠。
4. 引入 `cut-the-curve` 的转场参数化与 `motion-graphics` 的品类选择。
5. 让 MG 设计提示从约 13.7k 字符降到可配置的 3–5k 字符级模块，示例只保留一个精简样例。
6. 用机器可读的 `AnimationPlan` 约束跨镜头连续性，并接入现有 validator/评审。
7. 对 DeepSeek V4.1 Flash 这类模型，减少长上下文干扰，提升 JSON 合格率与首版可用率。

### 2.2 非目标

- 不引入 GSAP/Lottie/Three.js 运行时；
- 不直接执行 HyperFrames HTML；
- 不重写现有 MG renderer；
- 不一次性支持所有 HyperFrames 品类/适配器；
- 不把完整 Skill 文档塞进系统提示；
- 不改变现有 `mg_def` 对外契约。

## 3. Skill 到 Prompt 的映射

| Skill | 在本项目中的用途 | 注入形式 | 不做什么 |
|---|---|---|---|
| `motion-doctrine` | 全片 current、seam、carrier、causal motion、no idle wobble、stillness | `MOTION_CONTRACT` 模块 + `AnimationPlan` 字段 + seam 校验 | 不注入完整法则文档 |
| `cut-the-curve` | 转场类型、方向、速度匹配、waterfall/nudge | `TRANSITION_RULES` 模块，最多 2–3 条 | 不生成 HyperFrames 代码 |
| `hyperframes-animation` | atomic rules、blueprints、adapter 选择 | `rule_id` 检索 + 2–4 条 snippet | 不复制 24 种效果全文 |
| `motion-graphics` | 品类选择、镜头计划、素材策略 | `AnimationPlan.category` + 品类片段 | 不引入其 source/render 工作流 |
| `marker-highlight` | 关键词高亮/圈选 | 可选 `HIGHLIGHT_RULE` | 不默认每镜使用 |
| HyperFrames Catalog | 预置效果参考（Headline Slam/RGB Glitch 等） | `preset_ref` 白名单 | 不直接依赖 catalog 运行时 |

## 4. 目标提示词架构

```text
AnimationAgent
  ├─ Plan Prompt（全片/多镜）
  │    ├─ 角色与不可违背约束
  │    ├─ 品类判断（motion-graphics）
  │    ├─ 全局运动法则（motion-doctrine）
  │    ├─ 转场预算（cut-the-curve）
  │    └─ 输出 AnimationPlan JSON
  │
  ├─ Shot Prompt（单镜 MG）
  │    ├─ 镜头意图
  │    ├─ 2–4 条选定 rules（hyperframes-animation）
  │    ├─ Persona/category 视觉令牌
  │    ├─ 渲染器能力白名单
  │    └─ 输出 mg_def JSON
  │
  ├─ Critique Prompt（单镜 + 连续性）
  │    ├─ 视觉/构图/层级
  │    ├─ 运动因果/节奏
  │    ├─ seam/carrier 检查
  │    └─ 输出可执行修复项
  │
  └─ Validator
       ├─ JSON schema / 属性白名单
       ├─ 关键帧时间/easing
       ├─ 全局 plan 与镜头一致性
       └─ 可选视觉指标
```

### 4.1 分层原则

1. **Plan before pixels**：先生成 `AnimationPlan`，再生成每个镜头，禁止直接一次性生成整片 JSON。
2. **Rule selection over rule dump**：每次只注入 2–4 条 atomic rules；完整规则库放在检索层。
3. **Skill as data**：Skill 规则以 `AnimationRule` 数据对象保存，不把 Skill 文档原文写进 prompt。
4. **Renderer-aware**：只注入当前 renderer 支持的属性/效果；不支持的规则不进入 prompt。
5. **One example max**：每类任务最多一个精简正例；反例用“禁止项”列表表达。
6. **Model-adaptive**：DeepSeek V4.1 Flash 走“短规则 + 明确 JSON schema + 一轮 critique”；更强模型可增加规则/候选数。

## 5. 核心数据契约

### 5.1 `AnimationPlan`

```json
{
  "category": "kinetic-type | stat | charts | logo-reveal | lower-thirds | maps | webpage | news | tweet | asset-fusion",
  "intent": "一句话说明这支片子要让观众记住什么",
  "energy": "low | medium | high",
  "duration_sec": 8,
  "global_current": "left | right | up | down | z_forward | z_backward",
  "seams": [
    {"at_sec": 3.2, "from_shot": 1, "to_shot": 2, "vector": "left", "carrier": "title-word"}
  ],
  "shots": [
    {"index": 1, "role": "hook", "rule_ids": ["headline-slam"], "carrier": "title-word"}
  ],
  "transition_budget": 2,
  "style_tokens": {"primary": "{primary}", "accent": "{accent}"}
}
```

### 5.2 `AnimationRule`

```json
{
  "id": "headline-slam",
  "skill": "hyperframes-animation",
  "category": "kinetic-type",
  "phase": "entry",
  "prompt_snippet": "标题从 1.15 倍回落，单次 overshoot；落定后 hold ≥1s",
  "required_capabilities": ["text", "scale", "opacity", "back-out"],
  "conflicts": ["bounce", "elastic"],
  "validator": "entry_mid_motion"
}
```

### 5.3 `Skinny MG output`

继续使用现有 `mg_def`，但：

- 顶层必须有 `shot_type`、`energy`、`animation`、`duration_sec`；
- 每个镜头最多 6 个可见元素；
- 不允许 idle 循环；
- 退场必须仍有速度；
- 所有 easing/属性必须在 renderer 白名单内。

## 6. 提示词模块设计

### 6.1 `MOTION_CONTRACT`（全片级，建议 300–500 字）

```text
全片只选一个 current（默认 left）。每个 seam 必须写 vector 和 carrier；
退出与进入同轴、同方向、速度匹配，cut 在运动中。
禁止 crossfade、禁止 ping-pong、禁止 idle wobble（呼吸/漂浮/脉冲填充时间）。
一个镜头只让一个主角承担主要动作；关键信息落定后 hold ≥1s。
转场总数 2–3 个，重复使用同一套转场语言。
```

### 6.2 `SHOT_RULESET`（镜头级，2–4 条）

每条只包含：

- 触发条件；
- 动作参数范围；
- 禁止项；
- 与 renderer 的映射。

不注入完整 Skill 文档、不注入多个长示例。

### 6.3 `STYLE_TOKEN_BLOCK`

只注入：

- Persona 色板 3–5 个；
- 字体；
- category 的气质关键词；
- 当前镜头 energy。

### 6.4 `OUTPUT_CONTRACT`

保留现有 `mg_def` JSON schema，但：

- 只保留一个精简示例；
- 明确“未列出的属性一律禁止”；
- 明确“关键帧只允许 translate/scale/opacity/…”。

### 6.5 `SELF_CHECK`

生成前自检 8–10 条，不再把 20+ 条规则散落在正文：

1. 是否有 idle 循环？
2. 退出时仍在运动？
3. 是否有 carrier？
4. 同屏可见元素是否 ≤6？
5. 是否只有一个主角？
6. 关键信息是否 hold ≥1s？
7. 颜色是否来自 Persona？
8. 是否使用了不支持的属性？
9. 转场是否超过预算？
10. 是否出现方向无因翻转？

## 7. 当前提示词的具体改造

### 7.1 `MG_DESIGNER_SYSTEM`

从“单体 13.7k 字符”拆为：

```text
MG_DESIGNER_BASE          # 角色 + 输出契约 + 硬约束（约 1.5k）
MG_MOTION_QUALITY         # 动效质量/节奏（约 1k）
MG_STYLE_RULES            # Persona/category 令牌（约 600）
MG_RENDERER_CAPABILITIES  # 当前支持的属性/元素（约 800）
MG_SHOT_RULES             # 运行时选中的 2–4 条规则（约 800）
MG_EXAMPLE                # 一个精简示例（约 800）
```

目标：稳定在 4–6k 字符以内，按镜头注入。

### 7.2 `STRUCTURE_SYSTEM_PROMPT_TPL`

- 保留“动画标记选择”；
- 增加“全片 current / 转场预算”上下文；
- 明确优先级：**开场禁用 > 每场景标记要求**；
- 禁止“每个场景都必须动画”这种绝对表述，改为“需要视觉信息承载的场景才标记”。

### 7.3 `MG_CRITIQUE_*`

增加：

- seam 连续性；
- carrier 是否跨 cut；
- 是否存在 idle wobble；
- 是否一镜一主角；
- 是否超出转场预算；
- 是否 lane 方向无因翻转。

把“修复建议”限制为可执行 JSON 变更，不要求模型重写整片。

## 8. 与 DeepSeek V4.1 Flash 的适配

官方能力：

- 552B MoE，输入/输出激活约 8B/16B；
- 原生多模态；
- 官方称 Agentic Benchmark 超过 V4 Pro；
- 1017 版本强化从零项目开发/3D/游戏，并针对 Harness 优化。

对本项目的含义：

| 方面 | 判断 |
|---|---|
| 规则遵循 | 好，适合短规则 + 明确 schema |
| JSON 生成 | 好，但需要 validator 兜底 |
| 工具/多阶段 | 好，适合 plan→build→critique |
| 动效审美 | 中等，需要候选选择和人工抽帧 |
| 长上下文 | 应避免 13.7k 全量注入；模块化更稳 |
| 多场景连续性 | 需要 plan/ledger 显式约束，不能靠模型自觉 |

建议：

- 默认使用 `temperature` 较低值（现有参数保持）；
- Plan 阶段和 Shot 阶段分开调用，不要求一次生成整片；
- 每次只让模型改一个镜头；
- critique 只输出结构化 issues，不直接重写；
- 保留 `mg_def` validator 与 visual eval。

## 9. 验收标准

### 功能

- [ ] `AnimationPlan` schema 与校验器落地；
- [ ] Prompt composer 能按 category/energy/duration 选择模块；
- [ ] 每次 MG 生成只注入 2–4 条规则；
- [ ] critique 能检查 seam/carrier/idle wobble；
- [ ] 不支持的规则被 capability 拒绝，而不是静默生成。

### 质量

- [ ] 同一批 30 个场景，JSON 有效率不低于基线；
- [ ] render 失败率不高于基线；
- [ ] 人工盲评视觉质量平均提升 ≥0.5/5；
- [ ] 连续性/转场评分提升 ≥0.5/5；
- [ ] Prompt 字符数较基线下降 ≥40%；
- [ ] 首版可用率提升（减少重写次数）。

### 兼容

- [ ] 现有 `mg_def` 消费方不改；
- [ ] 现有 Persona/category 覆盖机制不改；
- [ ] 现有 `MG_DESIGNER_SYSTEM` 可通过 feature flag 回退；
- [ ] 插件 Prompt 覆盖仍生效。

## 10. 实施里程碑

### M0 盘点与基线

- 抽取 30 个真实场景；
- 记录当前 prompt 字符数、token、JSON 失败率、render 失败率、人工评分；
- 标注当前提示词冲突项。

### M1 规则库与 Schema

- 定义 `AnimationRule`、`AnimationPlan`；
- 建立 20–30 条规则的最小集；
- 标注 renderer capability 与冲突关系。

### M2 Prompt Composer

- 把 `MG_DESIGNER_SYSTEM` 拆成模块；
- 实现按 category/energy/规则选择；
- 保留旧 prompt 作为 fallback。

### M3 Plan 阶段

- AnimationAgent 先输出 `AnimationPlan`；
- 校验 current/seams/carrier/transition budget；
- 再逐镜头生成 `mg_def`。

### M4 Critique 与 Validator

- 增加 seam/carrier/idle 检查；
- 增加 plan-shot 一致性检查；
- 把修复限制为结构化 patch。

### M5 实验与灰度

- 新旧 prompt A/B；
- 按 category 灰度；
- 记录质量、失败率、token、延迟；
- 达标后设默认。

## 11. 风险与对策

| 风险 | 对策 |
|---|---|
| Skill 版本漂移 | 规则库锁定 skill 版本，记录来源 commit |
| Prompt 过度约束 | 每次只注入 2–4 条规则，保留 fallback |
| Renderer 不支持 | capability manifest 先过滤规则 |
| 模型只会套模板 | 规则 + category + Persona 三者组合；A/B 检查多样性 |
| 连续性校验误报 | seam 检查分 warn/error，先观察再收紧 |
| 长上下文退化 | 模块化 + 单镜头调用 + 精简示例 |
| 版权/许可 | 只借鉴方法论，不复制代码/模板；保留来源标注 |

## 12. 开放问题

1. `AnimationPlan` 是放在 AnimationAgent 内部，还是独立 `AnimationDirector` 子 Agent？
2. 首期只支持 `kinetic-type` / `stat` / `charts` 三类，还是一次覆盖全部？
3. 是否需要在渲染前做一次低分辨率抽帧视觉评审？
4. `motion-doctrine` 的 seam 校验能否直接映射到现有时间线 transition 字段？
5. Prompt 规则库由谁维护：`prompts/engine/mg.py` 还是独立 JSON/YAML？
6. 是否保留“粒子/光效”作为可选风格包，而不是默认规则？

## 13. 参考

- [HyperFrames animation skill](https://raw.githubusercontent.com/heygen-com/hyperframes/3518abe7aaa8d26740a333a43791741d0959ca43/skills/hyperframes-animation/SKILL.md)
- [HyperFrames motion-graphics skill](https://raw.githubusercontent.com/heygen-com/hyperframes/3518abe7aaa8d26740a333a43791741d0959ca43/skills/motion-graphics/SKILL.md)
- [HyperFrames motion-doctrine](https://raw.githubusercontent.com/heygen-com/hyperframes/63574a7c7f9562508769938e92e35ee7f92d9cea/.claude/skills/motion-doctrine/SKILL.md)
- [HyperFrames skills](https://hyperframes.heygen.com/guides/skills)
- [DeepSeek V4.1 Flash](https://api-docs.deepseek.com/zh-cn/news/news260910/)
- [DeepSeek V4.1 Flash 1017](https://api-docs.deepseek.com/zh-cn/news/news261017/)


## 14. MVP 实施记录（2026-11-18）

已落地：

- `clipwright/animation/prompting.py`：`AnimationRule`、`AnimationPlan`、规则目录、计划构建、计划校验、模块化 prompt composer；
- `clipwright/prompts/engine/animation_prompt.py`：运动契约、规则选择、风格、能力边界、自检、最小示例；
- `MGGenerator`：默认 `prompt.modular=true`，镜头生成走模块化提示；保留旧 `MG_DESIGNER_SYSTEM` 作为 `modular=false` fallback；
- `AnimationAgent`：在 MG 生成前构建 `_active_plan`，由生成器按计划注入规则；
- critique：模块化模式下注入 motion contract + continuity check items（seam/carrier/idle/hold/budget）；
- 新增测试 `tests/clipwright/test_animation_prompt_plan.py`；
- 实测同一场景模块化 prompt 约 3.5k 字符，基线 `MG_DESIGNER_SYSTEM` 为 13,702 字符。

后续迭代仍按 M3–M5：多镜头 plan/shot 拆解、A/B 指标、视觉抽帧评审。
