# Clipwright LLM 动画质量修复 PRD

> 状态：可执行方案；待按 M1 基线与 fixture 实测校准阈值。  
> 范围：后端 MG 动画生成、合成质量分析、AnimationAgent 修复闭环及插件扩展。  
> 约束：Timeline / AgentBus / SSE 事件契约不变；Hyperframes、FFmpeg 与既有渲染选型不变；保留现有降级顺序；所有质量阈值可配置。

## 一页速览

| 维度 | 当前已知状态 | 本期交付 | 首轮建议门槛（全部可配置） | 失败处理 | 总回滚开关 |
|---|---|---|---|---|---|
| 动画生成 | LLM MG 有提示词、配方、Validator、lint/check、自批判 | 生成前 Beat Plan + 结构检查 | beat 时间有效；每 beat 至多 1 个 primary | 修复一次，继而走原回退链 | `CLIPWRIGHT_ANIMATION_QUALITY_ENABLED` |
| 运动量 | 无已确认的 motion energy 硬门禁 | 结构运动量 + 渲染帧差分 | `motion_energy_mean >= 0.015`；活动帧比例 `>= 0.20`，按镜头类型豁免 | 先 warning，校准后可配置为修复门禁 | `CLIPWRIGHT_ANIMATION_MOTION_GATE` |
| 动效节奏 | shotcraft 已要求错峰 0.2–0.5s、核心 hold 约 1s、尾部静止约 0.5s | 可计算 coverage、peak、hold、主角并发 | stagger `>=0.20s`；默认 hold `>=1.0s`；尾静止 `>=0.5s` | warning / 结构化修复 | 同上 |
| 可读性 | Hyperframes check 可选；visual_eval 默认关闭且 best-effort | 文本框、遮挡、对比度、安全区采样 | 普通文本对比度 `>=4.5:1`；遮挡 `<=5%`；画布外 `0` | error / warning 分级；采样失败不阻断 | `CLIPWRIGHT_ANIMATION_READABILITY_GATE` |
| 合成层级 | MG JSON 元素渲染已有顺序，未见统一 beat 层级模型 | 背景、主体、辅助、强调、文字保护的逻辑层级 | 一个主角动效；装饰不得遮盖受保护文本 | 反馈修复；无法修复则原回退链 | `CLIPWRIGHT_ANIMATION_BEAT_PLAN` |
| 采样与缓存 | 已有 MG 生成缓存；visual_eval 三点采样 | 独立质量采样缓存及 cache metadata | 键包含内容、画布、FPS、采样与阈值版本 | 缓存失效按 miss，分析失败 best-effort | `CLIPWRIGHT_ANIMATION_QUALITY_CACHE` |
| 插件 | 插件及语义能力解析已存在 | 质量分析器/采样器可选扩展 | 内置核心门禁不可被插件跳过 | provider 不可用时回内置或跳过新增指标 | `CLIPWRIGHT_ANIMATION_QUALITY_PLUGINS` |
| 回退 | LLM 动态 MG → 模板 MG → drawtext | 失败原因和指标进入现有 trace detail | 顺序、事件名与事件语义保持不变 | 原有回退链 | 总开关及各分项开关 |

## 1. 结论先行

动画质量问题不能只靠继续加长 prompt 解决。现有系统已经有 shotcraft 动效约束、结构化动效配方、MG Validator、Hyperframes lint/check、自批判、可选视觉自评和多级降级；缺少的是把“是否真的动了、主次是否清楚、文字是否可读、元素是否越界”变成可复现的确定性报告，并把报告用于受预算约束的修复。

本期采用两道质量保险：

1. **生成期结构门禁**：AnimationAgent 为镜头生成可校验的 beat plan；MG Validator 检查时间、主次、错峰、hold 及元素角色；现有 lint/check 按现状运行。
2. **渲染后视觉门禁**：对 Hyperframes snapshot 或已有可用渲染帧计算运动量、文本可读性、遮挡和安全区。新门禁初期以 warning-only 运行，fixture 校准后按配置开启修复/阻断级别。

任何新增检查均 best-effort，不改变渲染成功判定的既有语义，除非对应 gate 明确开启。阈值均从配置 profile 读取，环境变量仅提供总开关和紧急回滚。

## 2. 症状与证据

| 症状 | 已有项目证据 | 证据解释 / 当前缺口 |
|---|---|---|
| 成片里有动画标记但视觉近似静止 | `docs/animation_system.md` 记载 LLM MG→MGRenderer→Hyperframes；QualityAgent 当前检查降级、缺 `mg_html` 与时间越界 | 现有检查证明“路径执行/产物存在”，不量化关键帧或像素是否产生有效运动 |
| 多元素同时抢注意力 | shotcraft 提示词已经要求单镜头单主角、元素错峰 0.2–0.5s | 当前文档未记录 beat 级主角并发计算与自动验收 |
| 关键信息未留阅读时间 | 已有核心信息落定后约 1s hold 的设计要求 | 尚无统一 hold 时长计算及不足反馈 |
| 文字与装饰重叠、边缘裁切 | visual_eval 评分维度包含可读性、层级、构图安全区；QualityAgent 目前的越界检查是时间范围 | 缺稳定的几何 bbox、遮挡率、画布安全区报告；视觉 LLM 默认关闭且不是确定性硬指标 |
| 背景/透明覆盖异常 | 已有真实 Chrome 覆盖率测试；曾发现 bg 平移后覆盖率仅 25%，修复后目标阈值为 97%、实测 100% | 说明合成覆盖率可作为 fixture 型确定性回归指标，不应误诊为截图裁切 |
| 低对比度未进入默认硬门禁 | Hyperframes `check` 的 `warn`/`strict` 可报告 WCAG 对比度，`generation.check_gate` 默认 `off` | 新功能应复用已有检查结果，不能重复实现或默认强制启动浏览器 |
| 重试原因难定位 | MGGenerator 已有修复、批判、缓存和 trace；visual_eval 失败可静默跳过 | 缺统一质量指标摘要、cache hit 标记、结构化 retry reason 的明确契约 |

### 2.1 本次 E2E fixture 的实测数据（2026-10-29，702s 成片）

在同一份 timeline（32 段动画，全部 `renderer=mg_hyperframes`）上直接量化，结论是
**"像素在变"与"有动效"是两件事**：

| 指标 | 实测值 | 参考阈值 | 判读 |
|---|---|---|---|
| 单段最大位移（占画布宽） | 中位数 **1.56%**（≈30px @1920），最小 0.52%，最大 103.65% | ≥ 4% | **27 / 32 段不达标**（84%） |
| 元素数量 | 中位数 **9.5** | 3–12 | 元素不少，但几乎都不怎么动 |
| 静止尾部（末次运动后的时长占比） | 仅 1 段 >35% | ≤ 35% | 不是"停太久"，而是"动太小" |
| 逐帧哈希 distinct ratio（旧判据） | **1.0**（24/24 帧不同） | ≥0.25 | 旧判据无法区分"设计动效"与"1px 抖动" |
| 文字对比度（WCAG，84.9s 右栏，浅色木块背景） | **1.32 : 1** | ≥ 4.5 : 1 | 严重不可读 |
| 文字对比度（WCAG，35.1s 左栏，灌木/天空背景） | **2.25 : 1** | ≥ 4.5 : 1 | 不达标 |

复现命令（脚本已随仓库提供）：

```powershell
# 每段动效强度（位移/缩放/静止尾部/元素数）
python scripts/measure_animation_quality.py motion `
  --timeline workspaces/proj_b3a9abc2c818-c2c818/artifacts/art_llm_anim.json `
  --out _cache/anim_quality_motion.json

# 文字对比度（归一化区域 x0,y0,x1,y1）
python scripts/measure_animation_quality.py contrast `
  --frame _cache/llm_anim_verify/final_at_84.9.png `
  --region 0.56,0.33,0.86,0.42 --label "对比右栏"
```

**由此得出 PRD 的核心判断**：必须把「动效强度」「对比度」「层级/安全区」变成
可计算的硬门禁，而不是继续用"有没有不同帧"或 LLM 自评分（实测 55–58 分即通过）。

## 3. 目标、边界与成功标准

### 3.1 目标

- 对 MG 运动量、运动覆盖、峰值、hold、主角并发形成可解释指标。
- 在实际输出尺寸下检测文字可读性、遮挡与安全区越界。
- 让 AnimationAgent 基于同一 beat plan 生成、校验、修复并记录 trace detail。
- 以 fixture 证明修复前后差异，令每项结果可重复运行。
- 复用现有 Validator、Hyperframes lint/check、snapshot、生成缓存和既有回退链。
- 质量分析可关闭、失败可降级，不增加公共事件或改变已有事件字段语义。

### 3.2 成功标准（M5 验收）

- 低能量、过度并发、文本越界、低对比、背景覆盖异常 fixture 均得到预期等级与定位信息。
- 同一输入、配置、渲染参数重复运行得到相同采样时间和一致指标（容许像素采样配置定义的误差）。
- 质量采样或缓存故障不导致主渲染链失败。
- 质量修复达到既有重试预算上限后，确定地进入原有模板 MG，再到 drawtext 回退链。
- 全局开关关闭时新增质量分析为 no-op；事件契约、Timeline schema 和渲染后端不变。
- 所有新阈值均能通过 YAML/profile 配置；不得将 1920×1080、固定 FPS 或单一镜头类型写死。

## 4. 根因分层与定位方案

| 层 | 可能成因 | 检测/归属 | 处理边界 |
|---|---|---|---|
| 镜头意图 | 没有明确主角、能量档、阅读 hold；文案长度与镜头时长冲突 | AnimationAgent 输入归一化、beat planner | 不改上游事件契约；内部 plan 可从现有字段推导 |
| 提示与配方 | prompt 有原则但缺结构化落点；修复时丢失初始节奏 | MGGenerator / 已有 knowledge pack | 首次生成、schema 修复、批判、视觉修复共享同一 plan |
| MG 定义 | 关键帧变化太小、时间重叠、多个 primary、无运动峰 | 现有 MG Validator 扩展的内部规则 | 输出结构化 finding；由 gate 决定 warning/error |
| 合成几何 | anchor、shadow、glow、transform 使 bbox 超界或遮字 | MGRenderer 已有元素定义 + 拟新增 bbox 计算 | 不改 CSS/渲染选型；分析逻辑独立可测 |
| 渲染结果 | 结构有变化但视觉无变化，或背景/透明覆盖不正确 | Hyperframes snapshot/既有帧与像素差 | 采样 best-effort；帧不可得时保留结构指标并标 `sampling_unavailable` |
| 可读性 | 文本对比度不足、遮挡、字号/行高不适合实际画布 | 优先复用 `hyperframes check` findings，辅以拟新增 bbox 和采样 | 不重复启动昂贵浏览器；采样预算可配 |
| 编排与重试 | 质量 findings 未转为可操作修复指令或 trace | AnimationAgent / QualityAgent 现有路径 | 复用当前重试预算和事件；不引入新的 AgentBus 事件 |
| 运行环境 | Hyperframes CLI、Chrome 或视觉模型不可用 | 现有 await_available、lint/check 与 snapshot 结果 | 按已有 best-effort / fallback 语义处理，不反向阻断 |

## 5. 量化 Motion Energy

### 5.1 指标定义

质量报告分开保留**结构运动量**与**渲染运动量**，避免仅凭 CSS 关键帧判断成片效果。

- `structural_delta(e,t)`: 元素 `e` 在相邻关键帧之间归一化的 translate、scale、rotate、opacity、width/height 变化；按元素可见面积与角色权重聚合。
- `pixel_delta(t)`: 相邻采样帧中差异像素占有效画布像素比例。透明区、已知背景噪声与抗锯齿差异按配置处理。
- `motion_energy(t)`: 配置化融合结构运动与像素差异；当像素帧不可用时只返回结构值，并标记来源，不伪造完整渲染测量。
- `motion_energy_mean` / `motion_energy_p95`: 逐窗口能量均值 / P95。
- `active_frame_ratio`: 能量高于 `pixel_noise_floor` 的有效窗口比例。
- `motion_coverage`: 含有效运动窗口的时长 / 镜头时长。
- `peak_count`: 超过局部峰阈值的运动峰数。
- `primary_overlap_ratio`: 同时处于 active 状态的 primary 元素时间比例。
- `hold_sec`: 主信息首次落定后连续稳定的时长。
- `tail_still_sec`: 末尾连续低运动时长。

运动维度权重按 profile 配置，建议初值：translate 0.35、scale 0.25、opacity 0.15、rotate 0.10、size 0.15。归一化坐标基于实际画布宽高；旋转需按配置的最大有效角度归一化。指标公式版本纳入报告与缓存键。

### 5.2 首轮阈值建议

以下均为**待 fixture 校准的推荐初值，不是现网实测基线**：

| 参数 | 建议初值 | 用途 |
|---|---:|---|
| `pixel_noise_floor` | 0.005 | 将抗锯齿/微小像素波动视为噪声的下限 |
| `mean_low` | 0.015 | 低能量告警初值 |
| `mean_medium` | 0.035 | 中等能量参考档，不作为所有镜头下限 |
| `mean_high` | 0.060 | 高能量参考档，用于识别冲击型镜头目标 |
| `p95_spike` | 0.180 | 瞬时突变告警初值 |
| `active_frame_ratio_min` | 0.20 | 有效运动窗口最低比例 |
| `coverage.low/medium/high` | 0.25 / 0.35 / 0.45 | 不同能量 profile 的运动时长覆盖建议值 |
| `stagger_min_sec` | 0.20 | 与现有 0.2–0.5 秒错峰原则对齐 |
| `hold_min_sec` | 1.0 | 与现有约 1 秒核心信息 hold 原则对齐 |
| `tail_still_min_sec` | 0.5 | 与现有约 0.5 秒收尾留白原则对齐 |

判定必须考虑 `shot_type` / `energy_profile` 豁免：引语卡、静态知识卡、caption、刻意定格镜头不得机械套用高能量阈值。首期建议仅低能量、突变、主角重叠产生 warning；在 fixture 与实际样本验证后才允许将指定规则配置为 `repair` 或 `error`。

## 6. 合成层级模型与实现

### 6.1 逻辑层级

为质量分析建立 MG 内部的语义层，不改变现有元素渲染器的选型与 Timeline 契约：

| 层级 | 名称 | 典型内容 | 约束 |
|---|---|---|---|
| L0 | 背景/底图 | `bg`、底色、背景装饰 | 背景覆盖按画布实际尺寸验证；不得意外遮住上层 |
| L1 | 主体信息 | 主标题、核心数字、主要图形 | 每个 beat 至多一个 primary 主运动 |
| L2 | 关系辅助 | 连接线、对比项、次要说明 | 不穿过文本主体；运动优先错峰 |
| L3 | 强调效果 | ring、arc、glow、粒子、强调线 | 只围绕单一焦点；受面积/数量配置约束 |
| L4 | 文字保护 | 标题、标签、字幕及其保护区 | 逻辑上置顶；禁止装饰层压盖受保护文本 |

### 6.2 拟新增内部字段

在 MG 定义的可选 metadata 中拟增加 `role`、`priority`、`beat_id`、`reading_order`、`protect_text`。字段只用于生成、分析和修复；需先验证 MG 定义允许的扩展位置，不能假定现有 schema 已支持。若不能安全扩展定义，则由 analyzer 外部映射生成，不修改公开 Timeline schema。

### 6.3 规则与确定性

- `primary=true` 的元素按 beat 时间区间检测并发；多主角重叠超阈值生成 finding。
- 次要元素进入、强调效果触发均相对主角错峰；错峰最小值可配。
- z-order 以显式优先级或实际 DOM/元素顺序解析；无法可靠解析时报告 `unknown`，不推断通过。
- 对文本保护区域扩展实际 bbox（可配置是否计入 stroke、shadow、glow padding）。
- 检查器只产出 finding 和建议，不自行改变绘制顺序或渲染输出。

## 7. 自动可读性、采样缓存与双保险

### 7.1 采样策略

复用现有 visual_eval 的三点采样作为低成本基线，并在可取得 beat plan 时追加 beat-aware 点：入场完成、主要 beat 结束、hold 中段、运动峰附近、退场前。每镜头默认最多 5 个唯一采样点；数量、时间偏移和预算均可配置。采样时刻裁剪到 `[0, duration_sec]`，合并重复点。优先复用已有 snapshot，不为每个规则重复启动浏览器。

### 7.2 可读性指标与等级

| 指标 | 首轮建议阈值 | 级别 |
|---|---:|---|
| 普通文字对比度 | `>= 4.5:1` | 未达标 warning；启用严格 gate 时 error |
| 大字号文字对比度 | `>= 3:1` | 按文本类别配置 |
| 文本 bbox 遮挡率 | `<= 5%` | 5%–15% warning，`>15%` error（可配置） |
| 画布外 bbox | `0` | error |
| 安全区外但仍在画布内 | `0` 为目标 | warning |
| 文本完整可见采样比例 | `>= 0.98` | 低于门槛 warning/error 按配置 |
| 未解析 `{placeholder}` | `0` | error；复用现有变量防线 |
| 单帧文本块数 | 建议 `<= 6` | warning，按片型配置 |

中文字体 bbox 的量度必须纳入 font family、字号、字重、行高、letter spacing、描边、阴影、发光、锚点和 transform。P0 实现可复用 Hyperframes `check` 产生的 contrast finding；自算 bbox 需通过字体与效果 fixture 校准后再设为严格门禁。

### 7.3 安全区

按实际 `width` / `height` 的比例计算；推荐初值：左右 8%、顶部 8%、底部 12%；caption 底部安全区 15%，标题横向 10%。这些是 profile 建议，可按平台、字幕和镜头类型配置。阴影与 glow padding 默认纳入 bbox。超出画布属于 error；仅越过安全区属于 warning。不得将 1920×1080 作为固定尺寸假设。

### 7.4 双保险流程

```text
LLM MG 生成 + beat_plan
  -> 现有 JSON Validator / 确定性结构规则
  -> 现有 lint/check（按配置）
  -> 渲染/快路径或 Hyperframes 既有路径
  -> 关键帧 snapshot（best-effort）
  -> motion/readability/safe-zone 分析
  -> findings 回注同一 beat_plan 的一次质量修复
  -> 仍失败时走模板 MG -> drawtext 原回退链
```

结构门禁和渲染后门禁分别报告结果。分析工具缺失、CLI 超时或帧不可用时记录 `sampling_unavailable` / `analysis_skipped`，不得把未知写成通过，也不得因新增分析意外阻断原有渲染。

### 7.5 采样缓存

拟新增独立目录 `_cache/mg_quality/`，与已有 MG 生成缓存隔离。缓存键为规范化哈希，至少包含：`mg_html_hash`、`mg_def_hash`、duration、FPS、width、height、sample profile、指标公式版本、threshold profile 版本。缓存值包含版本、采样时间、各项报告、创建时间及来源。损坏按 miss 处理；只缓存成功完成的报告；阈值/算法版本改变必须失效；cache hit 写入 trace detail。缓存开关默认可控且关闭时不影响生成缓存。

## 8. AnimationAgent Beat Plan、重试与 Trace

### 8.1 Beat Plan

拟新增 MG 内部结构（不是 Timeline 或 AgentBus 公共契约）：

| 字段 | 含义 |
|---|---|
| `id` | 稳定 beat 标识 |
| `start_sec`, `end_sec` | 镜头相对时间区间 |
| `role` | primary / support / background / text 等语义角色 |
| `action` | 可读的动作意图，如 reveal、count、hold、exit |
| `primary` | 是否为该 beat 的唯一主运动 |
| `energy_target` | profile 目标，不替代最终分析结果 |
| `hold_sec` | 关键信息最短阅读保持时间 |

Beat Plan 可由现有 `animation_intents`、shot type、时长和 knowledge recipe 推导；缺少信息时用确定性默认值，避免新增上游必填参数。prompt、schema 修复、批判和视觉修复必须传递相同 plan，防止重试改变镜头意图。

### 8.2 重试预算

沿用 `mg/config.yaml` 的现有 `generation.max_retries`（文档已记录默认 2）与既有生成/修复路径，不额外叠加无限循环。建议各次尝试顺序：初始生成；已有 schema/lint 修复；如质量门禁启用则最多一次带质量 findings 的修复。实际 attempt 总数由现有预算控制，超预算立即进入现有 fallback。

拟新增 finding reason 枚举（内部 metadata）：`schema_invalid`、`lint_error`、`check_error`、`motion_energy_low`、`motion_peak_spike`、`readability_low`、`safe_zone_violation`、`layer_overlap`、`render_error`。未识别 reason 仍可作为文本 detail 留存。

### 8.3 Trace metadata

沿用现有 trace event 与 `detail` 承载方式，不新增事件名，不改变必需字段。拟新增可选 detail：`generation_id`、`attempt`、`cache_hit`、`method`、`fallback`、`beat_plan_hash`、`sample_hash`、`motion_energy_mean`、`motion_energy_p95`、`motion_coverage`、`active_frame_ratio`、`readability_score`、`safe_zone_violation_count`、`retry_reason`、`threshold_profile`、`analysis_status`。只有实际计算出的指标才写数值；不可用时记录状态，不填假值。

## 9. 插件扩展及边界

### 9.1 扩展方式

优先沿用 `clipwright/tool/capabilities.py` 的语义能力解析和现有插件治理，不直接在 Agent 中写死具体插件工具名。能力名建议先评估并在实现时通过 `CAPABILITY_ALIASES` 与 `TIER_MAP` 登记：`motion_quality_check`、`readability_sampler`、`safe_zone_check`、`beat_plan_provider`。插件能力为可选增强；核心内置检查始终保留。

### 9.2 插件允许与禁止

允许插件：提供额外 shot profile、替代/增强分析器、snapshot 采样实现或报告导出器；返回统一内部报告与可追踪来源。

插件不得：修改 Timeline schema、AgentBus/SSE 语义、Hyperframes/FFmpeg 选型或 fallback 顺序；覆盖核心错误等级、绕过质量 findings 记录、伪造内置门禁通过、绕过生成重试预算或将安全区阈值下调至内核下限以下。插件缺失、冲突或异常时按现有插件错误通道记录，使用内置实现或跳过该扩展能力，不阻断管线。

## 10. 文件级实施计划 M1–M5

> 文件存在性和具体锚点以每阶段开工时的当前工作树复核为准。以下标注“现有”依据项目地图与动画系统文档；新增路径均为拟新增，不代表已存在。

### M1：基线、契约和 fixture（先行）

| 文件 | 变更 |
|---|---|
| `docs/animation_system.md`（现有） | 补充质量指标与新门禁说明；不重复改写已落地的渲染事实 |
| `clipwright/animation/mg/config.yaml`（现有） | 新增 `quality` profile 的配置草案，默认 warning-only / 功能开关关闭 |
| `clipwright/animation/mg/validator.py`（现有，开工核对） | 核实扩展点；先固定外部行为与报告 shape |
| `clipwright/agents/animation_agent.py`（现有） | 核对现有 trace、回退和重试路径，不先改公共事件 |
| `clipwright/agents/quality_agent.py`（现有） | 核对当前动画 warning 归属与 redo 路径 |
| `tests/fixtures/animation_quality/`（拟新增） | 放置低能量、重叠、对比不足、越界、覆盖异常、回退 fixture |
| `tests/clipwright/test_animation_quality_fixtures.py`（拟新增） | 冻结修复前观测和报告格式 |

**出口条件：** 确认质量报告的输入、输出、阈值 profile、失败语义；所有推荐阈值均标识待校准。

### M2：独立测量模块

| 文件 | 变更 |
|---|---|
| `clipwright/services/animation_quality.py`（拟新增） | 汇总运动、可读性、bbox、遮挡与安全区分析；纯输入/输出接口，可单测 |
| `clipwright/animation/mg/beat_plan.py`（拟新增） | Beat / BeatPlan 内部结构、规范化、hash、时间合法性 |
| `clipwright/animation/mg/config.yaml`（现有） | 配置阈值、采样预算、安全区 profile、缓存版本 |
| `clipwright/animation/mg/generation_cache.py`（现有） | 仅在需要复用缓存封装时参考；质量缓存逻辑与生成缓存数据隔离 |
| `tests/clipwright/test_animation_quality_metrics.py`（拟新增） | 纯结构指标、安全区、重叠、归一化边界测试 |
| `tests/clipwright/test_animation_quality_cache.py`（拟新增） | key 变化、损坏 miss、版本失效、只缓存成功报告 |

### M3：生成与修复闭环

| 文件 | 变更 |
|---|---|
| `clipwright/animation/mg/generator.py`（现有） | 贯穿现有生成/修复路径传 beat plan；追加质量 finding；不破坏现有 critique/cache |
| `clipwright/agents/animation_agent.py`（现有） | 编排内部 plan、分析调用和受预算修复；保留 fallback 次序 |
| `clipwright/agents/quality_agent.py`（现有） | 将质量报告映射到现有 warning/error 与 redo 语义，不新增事件 |
| `clipwright/animation/mg/visual_eval.py`（现有） | 在可复用采样处暴露采样结果/时间，不改变其默认关闭及 best-effort |
| `tests/clipwright/test_animation_retry_trace.py`（拟新增） | 验证重试原因、attempt 上限、trace detail、fallback 链 |
| `tests/clipwright/test_animation_quality_agent.py`（拟新增） | 外部行为测试：异常分析器不阻断、启停 gate 有效 |

### M4：插件与配置治理

| 文件 | 变更 |
|---|---|
| `clipwright/tool/capabilities.py`（现有） | 仅在确认能力确由 tool/provider 消费时新增 alias 与 tier 映射 |
| `clipwright/plugins/` 现有加载/契约模块 | 复用声明、权限、错误通道、启停与 reload 回滚机制；避免创建第二套插件注册表 |
| `clipwright/services/animation_quality.py`（拟新增） | 注入可选 analyzer provider，内置分析器始终作为核心基线 |
| `tests/clipwright/test_animation_quality_provider.py`（拟新增） | 验证优先级、不可用回退、异常隔离、核心规则不可覆盖 |
| `docs/pluginization-plan-2026-09-10.md`（现有） | 如确需新增扩展 kind/provider，再记录边界与契约；未经验证不扩展插件总契约 |

### M5：命令验收、阈值校准和文档

| 文件 | 变更 |
|---|---|
| `scripts/verify_animation_quality.py`（拟新增） | 对 fixture 运行分析，输出可机器比对的摘要与失败码 |
| `scripts/verify_animation_quality_fixtures.py`（拟新增） | 对 before/after 样本生成对比表/报告 |
| `tests/fixtures/animation_quality/before/`、`after/`（拟新增） | 保留独立原件与修复件，不覆盖原始样本 |
| `docs/animation-quality-prd.md`（本文件） | 实测后更新阈值、验收记录、待验证项 |
| `docs/animation_system.md`（现有） | 功能落地后补充实际接口与配置，不先宣称未实现能力存在 |

### 10.1 拟新增 API 签名（非现有实现）

以下均为设计签名，返回类型可在实现时按项目现有数据模型校准；所有阈值由 `profile` 注入。

```python
class MotionQualityAnalyzer:
    def analyze(
        self, *, mg_def: dict, frames: list[bytes] | None,
        width: int, height: int, fps: float, duration_sec: float,
        profile: dict,
    ) -> dict:
        """拟新增：返回结构/像素运动指标及 analysis_status。"""
        ...
```

```python
class ReadabilitySampler:
    async def sample(
        self, *, html: str, sample_times: list[float],
        width: int, height: int, profile: dict,
    ) -> dict:
        """拟新增：复用可用 snapshot，返回 bbox、对比与遮挡 findings。"""
        ...
```

```python
class SafeZonePolicy:
    def check(
        self, *, boxes: list[dict], canvas: dict,
        profile: dict,
    ) -> list[dict]:
        """拟新增：按实际画布和 profile 返回可定位的违规项。"""
        ...
```

```python
class BeatPlan:
    def normalize(self, *, duration_sec: float, profile: dict) -> "BeatPlan":
        """拟新增：裁剪/校验 beat 时间，不改变 Timeline。"""
        ...

    def stable_hash(self) -> str:
        """拟新增：规范化序列化后生成缓存与 trace 用摘要。"""
        ...
```

（每段伪代码不超过 40 行。）

### 10.2 回滚开关

拟新增配置项，名称与默认值在 M1 确认：

| 开关 | 作用 |
|---|---|
| `CLIPWRIGHT_ANIMATION_QUALITY_ENABLED` | 新质量分析总开关；关闭即不运行新增分析 |
| `CLIPWRIGHT_ANIMATION_MOTION_GATE` | 运动指标从观测升级为修复/门禁的开关 |
| `CLIPWRIGHT_ANIMATION_READABILITY_GATE` | 可读性和安全区严格级别开关 |
| `CLIPWRIGHT_ANIMATION_QUALITY_CACHE` | 质量报告独立缓存开关 |
| `CLIPWRIGHT_ANIMATION_BEAT_PLAN` | 内部 beat plan 生成功能开关 |
| `CLIPWRIGHT_ANIMATION_QUALITY_PLUGINS` | 可选第三方分析器开关 |

现有 `CLIPWRIGHT_HF_LINT_GATE`、`CLIPWRIGHT_HF_QUALITY`、`HYPERFRAMES_BIN`、`CLIPWRIGHT_MG_FASTPATH`、`CLIPWRIGHT_HF_STRICT_VARIABLES`、`generation.check_gate` 与 `critique.visual_eval.enabled` 的既有语义必须保留。总开关关闭不清理缓存、不改旧配置、不改变 fallback 与 trace 基础事件。

## 11. 命令级验收与 Fix 前后 Fixtures

### 11.1 Fixture 目录建议

每项 fixture 同时保存原始输入、配置 profile、修复结果（若有）与预期报告；原始与衍生文件分开。禁止只保留修复后样本。

| ID | Fixture 场景 | Fix 前预期 | Fix 后/最终验收 |
|---|---|---|---|
| F01 | 关键帧有微小变化但近似静态 | `motion_energy_mean < mean_low` 或 `active_frame_ratio` 低，定位到镜头与窗口 | 生成有效主运动或标记为合法静态豁免；报告有来源与状态 |
| F02 | 三个主体同一时刻同等强度入场 | `primary_overlap` 超限，报告元素和 beat | 修复后每 beat 至多一个 primary；次要元素错峰达到配置阈值 |
| F03 | 核心数字落定后立即切走 | `hold_sec < hold_min_sec` | hold 达标或镜头 profile 明确豁免 |
| F04 | 长标题超画布/安全区 | 画布外为 error；安全区外为 warning | 不裁字；缩放、换行或调整构图后进入配置安全区 |
| F05 | 文字与 glow/装饰线重叠 | 遮挡率 > 配置阈值，识别文本与遮挡元素 | 保护区内遮挡达标；采样不可用时明确报告 skipped |
| F06 | 普通文字前景/背景对比不足 | Hyperframes check finding 或测得对比度低于配置阈值 | 对比度达标；在 strict gate 开启时低对比触发预期回退 |
| F07 | 全幅 bg 锚点变换异常 | 复现背景覆盖不足；历史已知缺陷修复前为 25% | 复用/参考 `verify_mg_bg_coverage.py` 行为，当前基线目标 `>=97%`；原文档记录修复后实测 100% |
| F08 | Hyperframes/Chrome 不可用 | sampler unavailable，不得假报通过 | 分析 best-effort；模板 MG→drawtext 的既有 fallback 顺序不变 |
| F09 | 重复质量分析 | 首次 miss | 输入、版本、profile 相同则 hit；改变任一影响因素即 miss |
| F10 | quality 总开关关闭 | 无新增质量分析调用 | 原有生成、渲染、trace 与降级行为一致 |

### 11.2 验收命令

在 `J:\Clipwright` 后端仓库执行。专项测试文件以实际工作树为准；拟新增文件完成后纳入命令：

```powershell
python -m pytest tests/clipwright/test_mg_renderer_contract.py tests/clipwright/test_mg_validator.py tests/clipwright/test_animation_degrade.py -q
```

```powershell
python -m pytest tests/clipwright/test_mg_fastpath.py tests/clipwright/test_mg_fastpath_wiring.py tests/clipwright/test_mg_hf_adoption.py -q
```

```powershell
python -m pytest tests/clipwright/test_llm_mg_critique.py tests/clipwright/test_mg_generator_critique.py tests/clipwright/test_mg_knowledge_pack.py -q
```

```powershell
python -m pytest tests/clipwright/test_quality_agent_content.py tests/clipwright/test_quality_conformance.py -q
```

```powershell
python -m pytest tests/clipwright/test_animation_quality_metrics.py tests/clipwright/test_animation_quality_fixtures.py tests/clipwright/test_animation_quality_cache.py tests/clipwright/test_animation_retry_trace.py -q
```

```powershell
python scripts/verify_mg_bg_coverage.py
python scripts/verify_mg_fastpath.py
python scripts/verify_animation_quality.py
python scripts/verify_animation_quality_fixtures.py
```

```powershell
npx hyperframes lint <fixture-dir>
npx hyperframes check <fixture-dir>
```

```powershell
python -m pytest tests -q
python -c "import clipwright.main"
```

仓库维护说明给出的全量回归历史期望约为 `1154 passed`；执行时记录真实用例数和失败，不以该历史数字替代当前结果。若变更仅在后端，本 PRD 不要求运行前端构建；事件契约/Timeline 不变应由契约测试验证。

### 11.3 必须验证的断言

- 每个数值 finding 包含指标名、观察值、阈值、profile、来源及样本/元素定位。
- 不同画布尺寸下归一化结果一致；画布大小变化不会改变比例安全区定义。
- 采样失败不改变既有渲染成功/失败结果。
- retry attempt 不超过现有预算，错误反馈不会删除 Persona 样式或 beat plan。
- quality failure 之后回退顺序严格保持 LLM → 模板 → drawtext。
- `CLIPWRIGHT_ANIMATION_QUALITY_ENABLED=0` 时没有新增 snapshot/分析器调用。
- 不新增 AgentBus/SSE 事件名，不修改公共事件必需字段，不修改 Timeline schema。

## 12. 不做事项

- 不更换 Hyperframes、FFmpeg、PIL 快路径或既有渲染后端选择。
- 不修改 Timeline schema、AgentBus/SSE 契约、公开 API 响应或事件语义。
- 不改变 `LLM 动态 MG → 模板 MG → drawtext` 回退链及其顺序。
- 不把视觉 LLM、自评或新质量采样设为默认硬阻断依赖。
- 不以扩大 prompt 文本作为唯一修复。
- 不将推荐阈值描述成现网测量结论；不在缺少样本时过拟合阈值。
- 不写死分辨率、FPS、字体或单一平台 safe zone。
- 不把未知 bbox / 采样失败判作通过。
- 不新增第二套插件注册表，不允许插件绕过内核质量 finding。
- 不重构整个 AnimationAgent、Pipeline DAG 或 QualityAgent 生命周期。
- 不因新增检测改变素材、音频或其它 Agent 的行为。

## 13. 风险、依赖与待验证项

| 项目 | 风险/未知 | 关闭方式 |
|---|---|---|
| 阈值外部有效性 | 推荐 motion energy 数值尚无项目样本分布校准 | M1 建立 fixture；M5 用实际 MG 样本分布复核，先 warning-only |
| 中文字体 bbox | font fallback、stroke、shadow、glow、旋转带来误差 | 建字体 fixture；对照 Chrome snapshot；不达精度时采用 Hyperframes check + warning |
| 像素差指标 | 背景闪烁、抗锯齿、透明像素会造成虚高 | 过滤透明区、配置噪声底限，记录有效区域和计算来源 |
| snapshot 成本 | Chrome 启动可能影响管线时延 | 复用现有 snapshot 与缓存；限制每镜头采样点数；独立开关 |
| MG 扩展字段 | 现有 JSON Validator 是否允许 metadata 需开工核实 | 优先外部映射；确需扩展时仅限内部 MG schema 并加兼容测试 |
| 质量修复提示 | LLM 可能为过指标而增加无意义运动 | 保留 shot profile、语义 beat 与可读性优先级；对高能量设上限和 spike 告警 |
| 插件优先级 | 当前能力解析/贡献契约需核对实际适用入口 | M4 先复用现有 capability，不提前创建新 registry |

## 14. 实施决策记录

1. 质量报告是内部分析结果；第一版不新增公共 API。
2. `beat_plan` 是内部生成/修复中间结构；不向 Timeline 或 AgentBus 暴露为新契约。
3. 指标拆分结构测量和像素测量；任何单一来源不可用均要在报告中显式标注。
4. 现有 Hyperframes check 的 WCAG finding 优先复用；新增 bbox 计算仅补充其未覆盖的遮挡、边界和合成关系。
5. 首阶段 warning-only，阈值经 fixture 校准后由 YAML gate 决定升级；不默认把启发式误差升级为失败。
6. 新增分析器以可独立单测的 service 深模块实现，输入为 MG 定义/可选采样帧/画布/profile，输出稳定报告，不持有渲染生命周期。
7. 缓存按内容与指标版本隔离，复用生成缓存实现经验但不混写生成产物。
8. 插件用于扩展，不是质量内核的唯一实现；插件失败不改变内置安全边界或主链可用性。

## 15. 参考的项目内既有事实

- `docs/animation_system.md`：MG 路径、降级顺序、shotcraft 规则、Hyperframes 门禁、visual_eval、质量检查、快路径和背景覆盖率实测。
- `clipwright/animation/mg/config.yaml`：生成重试、lint/check、批判与视觉评估配置（具体默认值以工作树为准）。
- `clipwright/animation/mg/generator.py`、`validator.py`、`visual_eval.py`、`generation_cache.py`：MG 生成、校验、采样与缓存实现边界（开工时复核文件）。
- `clipwright/agents/animation_agent.py`、`quality_agent.py`：现有编排、回退、质量 findings 与 trace 边界（开工时复核文件）。
- `clipwright/tool/capabilities.py` 与 `docs/pluginization-plan-2026-09-10.md`：语义能力解析与插件扩展约束。

本 PRD 不引入外部学术文献；所有技术阈值属于可验证的工程初值，不以文献引用替代项目 fixture 验收。

---

## 16. persona 与视觉上下文断链：完整修复方案（P0，2026-10-29 新增）

> 本节解释了一个比"动效弱"更根本的问题：**动画从来没有按 creator 的 persona 生成过**。
> 上一轮把 32 段动画改成 LLM 生成后，只有手工脚本 `_cache/reanimation.py` 传了 persona；
> 真实 pipeline 里 `persona_prompt` 恒为空串、`visual_config` 恒为空 dict。

### 16.1 事实认定（代码 + 运行态双向核实）

**拓扑上到处都是入口，链路上没有生产者。**

| 环节 | 位置 | 现状 |
|---|---|---|
| 输入字段 | `schema/agent.py`（`StructureInput` / `AnimationInput`） | `persona_prompt` / `persona_config` / `visual_config` / `vision_prompt` 四个字段都在 |
| 装配读取 | `services/stage_inputs.py:142,190-192` | 全部 `data.get(...)`（只从**黑板**取） |
| 黑板写入 | `services/stage_merge.py:24-31` | `MERGE_KEYS` 只有 `script_skeleton/scenes/candidate_clips/edit_notes/animation_plan/generated_mg_count/audio_notes/issues/redo_agent` —— **四个 persona 键都不在** |
| 请求透传 | `api/requirements.py:561` | `PipelineRequest.extra_params` 只有 script/audio/video_mode/split_mode/subtitle/voice/dub_segments/material_source_ids/animation_intents |
| Agent 兜底 | `animation_agent.py:1511-1513` | `ctx.pop("_persona_config")` / `ctx.pop("_identity")`，**全仓没有写入这两个键的代码**（死兜底） |

运行态证据（本轮 E2E `task_ed4f577aa8.state.json`）：

```
blackboard keys = [animation_plan, audio_notes, candidate_clips, edit_notes,
                   final_timeline, generated_mg_count, scenes, script_skeleton, timeline]
animation 阶段 inputs = {}
```

即主链路（`delegate_agent → run_stage → stage_inputs.assemble`）拿到的 `persona_prompt = ""`、
`visual_config = {}`。只有两条**旁路**有值：
`requirements_service.py:1930`（内联 structure 显式传 persona_prompt/persona_config）、
`requirements_service.py:1201`（「重做动画」接口显式传 visual_config）。

### 16.2 断点清单

| 编号 | 位置 | 现象 | 影响 |
|---|---|---|---|
| D1 | `stage_merge.MERGE_KEYS` | 四个 persona 键没有白名单 | 黑板永远不含它们，下游一切读取都空 |
| D2 | `api/requirements.py:561` | `PipelineRequest.extra_params` 不含 persona | 即使想从 `ctx.extra_params` 兜底也没有源头 |
| D3 | `stage_inputs.assemble` | 只读 `data`，不读 `ctx.extra_params`（`brief`/`plan` 却有 extra 兜底） | 双轨不一致：创意简报能兜底，persona 不能 |
| D4 | `animation_agent.py:1536-1541` | 无条件注入 `#000000 / #FFFFFF / #FF0000`，并与"以 Persona 注入值为最终准绳"一起写进 LLM 上下文 | **任何 persona 的动画配色都会变成黑白红**，且以 persona 的名义强制 |
| D5 | `animation_agent.py:1511-1513` | `_persona_config` / `_identity` 兜底无生产者 | 死代码，给人"已经兜底"的错觉 |
| D6 | `animation_agent.py:589`、`type_specs.py:138`、`animation_agent.py:1056` | persona 提示词被 400 / 600 / 2000 字三重截断 | 同一份 persona，在 mg_dynamic 与类型化两条路径拿到的长度不同，风格不一致 |

### 16.3 修复设计（六步，互不依赖，可分批合入）

**R1 新增统一装配层** `clipwright/services/visual_context.py`：

```python
@dataclass(frozen=True)
class VisualContext:
    persona_id: str
    persona_prompt: str          # persona prompt.md 全文（截断到 PERSONA_PROMPT_MAX）
    persona_config: dict         # manifest.parameter（identity/constraints…）
    visual_config: dict          # visual/palette（含 animation_styles）
    vision_prompt: str
    source: str                  # "board" | "extra" | "persona_store" | "fallback"
    fingerprint: str             # 进生成缓存键

def load(persona_id: str, *, board: dict, extra: dict) -> VisualContext: ...
```

优先级固定为 **board → extra → persona 仓库（按 id 读 prompt.md + parameter.visual）→ 空**，
并把 `source` 写进 trace，便于"为什么这次没风格"一眼定位。

**R2 透传**：`api/requirements.py` 只在 `extra_params` 里加 `persona_id`（已有）与
`vision_prompt`（若前端提供）；**不要把整份 prompt 塞进 request**——由 R1 按 id 读盘，避免请求体膨胀与陈旧副本。

**R3 装配改造**：`stage_inputs.assemble` 里四个字段改为单一入口：

```python
vc = visual_context.load(ctx.persona_id, board=data, extra=ctx.extra_params)
# structure / animation 共用
persona_prompt=vc.persona_prompt,
persona_config=vc.persona_config,
visual_config=vc.visual_config,
vision_prompt=vc.vision_prompt,
```

**R4 删掉硬编码兜底**（D4）：`_resolve_style` 的 `#000000/#FFFFFF/#FF0000` 改为
"`VisualContext.visual_config` 有则用；没有则用 `DiagramStyle()` 默认（#4f8cff 系）"，
且 `_build_context_section` 的「最终约束」段显式标注来源：
`色板来源：persona(Zam) | fallback(default)`。**禁止**把 fallback 色标成 persona 色。

**R5 截断统一**：`settings.persona_prompt_max_chars`（默认 2000），类型化与 mg_dynamic 共用；
`type_generation` 侧不再二次截到 600。若 prompt 超长，截断处写一条 trace（说明损失）。

**R6 插件契约**：`_handle_mg_animation` 把 `style_hint`（`PersonaAnimationContext.to_prompt()` 的 1200 字摘要）
放进 `mg_params`；插件在 `plugin.yaml` 声明 `accepts_persona: true` 才注入，未声明者行为不变（向后兼容）。
`mg_animations` / `logic_animations` / `diagram_style` 三个内置插件先接。

### 16.4 验收

```powershell
# 1) 契约：装配后字段必须非空且来源可解释
python -m pytest tests/clipwright/test_visual_context_plumbing.py -q
#    断言：persona_id=Zam 时 persona_prompt 长度 ≥ 200 且 source ∈ {board, extra, persona_store}
#    断言：visual_config.palette 非空；vision_prompt 与 persona 配置一致（未配置则为空且 source=fallback）

# 2) 运行态：重排一次动画阶段，检查 metadata 与 trace
python _cache/reanimation.py --timeline <artifact> --out-artifact <new>
#    每段动画 metadata 应含 persona_fingerprint / persona_source / persona_prompt_chars
#    trace 里应出现 "persona=Zam source=persona_store chars=NNNN"
```

**通过标准**：32 段动画中 `persona_source=persona_store` ≥ 90%；`persona_prompt_chars` 的
中位数 ≥ 800；不再出现 `#000000/#FFFFFF/#FF0000` 三条兜底同时生效且标注为 persona 的情况。

### 16.5 回滚

R4/R5 各自有开关（`settings.persona_fallback_palette`、`settings.persona_prompt_max_chars`）；
R1/R3 是纯装配层，回滚只需把 `stage_inputs` 的四处赋值改回 `data.get(...)`（保留 `visual_context` 模块不影响运行）。

---

## 17. 同源缺陷审计（P1–P2，2026-10-29 新增）

persona 断链不是孤例，它的模式是：**字段/参数声明了，读取点也在，但没有写入者；或者被一个硬编码兜底覆盖**。
本轮把这种模式写成脚本扫了一遍。

### 17.1 方法与工具

`scripts/audit_stage_plumbing.py`（已随仓库提交，AST 实现、不执行业务代码）：

1. `*Input` 每个字段 → `stage_inputs.assemble` 的取值表达式（含一层局部变量回溯）；
2. 该字段是否有显式构造传入点（全仓 `XInput(...)` 的关键字）；
3. 字段名是否在 `stage_merge.MERGE_KEYS`；
4. `extra_params` 的读取键 vs 写入点；
5. prompt 装配里的截断，以及 persona 路径上的硬编码颜色。

```powershell
python scripts/audit_stage_plumbing.py --json _cache/plumbing_report.json --strict
# --strict：存在「死入口 / 未接线 persona 通道」时退出码 1，可直接进 CI
```

当前实测输出（`fields_total = 36`）：

```
[strict] 死入口 6 个，未接线 persona 通道: ['vision_prompt']
```

### 17.2 已确认缺陷清单

| 编号 | 字段 | 位置 | 现象 | 影响与修复 |
|---|---|---|---|---|
| D7 | `StructureInput.vision_prompt` / `AnimationInput.vision_prompt` | `stage_inputs.py:143,192` | 从黑板取，无任何写入者，也无显式传入点 | **`vision_prompt` 永远为空** → `MGGenerator._ensure_no_background` 永远认为"创作者没要求背景"，把所有动画背景清成透明；同时 `_build_context_section` 的「视觉需求（优先级最高）」段永不出现。**这直接解释了"文字平铺、没有底板、背景一变就看不清"**。修复：走 R1 的 `vision_prompt` 通道；`_ensure_no_background` 增加"允许半透明底板"的独立开关（`allow_scrim`），不再与 vision_prompt 绑定 |
| D8 | `MaterialInput.material_plugin_config` | `stage_inputs.py:166` | 黑板无写入者 | 素材插件配置（源权重/过滤/语言）恒空 → 永远用全部源与默认参数。修复：并入 R1 的同款装配 + `MERGE_KEYS` 增加该键（由 requirements 阶段写一次） |
| D9 | `AudioInput.audio_config` | `stage_inputs.py:204` | 黑板无写入者 | BGM 槽位/音量/ducking 配置恒空（与本轮 E2E 里"BGM 槽位为空"互相印证）。修复：同 D8 |
| D10 | `QualityInput.constraints` | `stage_inputs.py`（未赋值） | `quality_agent.py:68` 读 `input_data.constraints`，但 assemble 里没有 `constraints=` 这一行 | 质检自定义约束恒 `{}`。修复：补装配 + 单测（新增字段必须配契约测试，见 17.5） |
| D11 | 模板/插件路径 | `animation_agent._handle_mg_animation` | 只把 `persona_style["primary_color"]` 覆盖成 `mg_params["accent"]` | 插件生成动画时拿不到 persona prompt / 字体 / 节奏 / 留白偏好。修复：R6 的 `accepts_persona` + `style_hint` |
| D12 | `_build_llm_params` | `mg/generator.py:1249-1297` | 按 `param_keys` 位置顺序填 `parts[i]`，与语义键无关 | 曾导致 `{label}` 与 `{q1}` 拿到同一句话（300s 处重影）。已加 `_dedupe_duplicate_text` 守卫，但根因（位置填充）仍在。修复：改为"按语义键名映射 + 位置兜底"，并在 validator 里拒绝"多个文本元素解析后同文" |

### 17.3 `extra_params` 通道：读取但主链路无写入（P2，待逐个确认）

以下键在 Agent 里有读取点，但 `clipwright/` 内找不到写入点（可能由前端 `request.extra` 透传，需逐个确认；
确认后要么接线，要么删除读取点）：

```
cut_profile / transition_weights / enable_color_match / lut_path / enable_pv_llm_tools /
orientation / glossary / visual_config / dry_run / task_id / shot_params
```

其中 `cut_profile`（`edit_agent.py:380`）与 `transition_weights`（`edit_agent.py:381`）最值得优先查：
它们本该由"视频类型"驱动剪辑节奏，若恒空则所有类型都用同一套切点策略。

### 17.4 截断与硬编码（P2）

| 类型 | 位置 | 值 | 处理 |
|---|---|---|---|
| 截断 | `animation_agent.py:589`（类型化 persona） | `guide[:400]` | 并入 `settings.persona_prompt_max_chars`（R5） |
| 截断 | `type_specs.py:138` | `style_description[:600]` | 同上，去掉二次截断 |
| 截断 | `animation_agent.py:1056`（mg_dynamic） | `persona_guide[:2000]` | 统一到同一常量 |
| 截断 | `structure_agent.py:1034` / `style_interpreter.py:179` | `guide/prompt[:1500]` | 统一常量；超长写 trace |
| 硬编码 | `animation_agent.py:1537,1539,1541` | `#000000 / #FFFFFF / #FF0000` | R4 删除 |

### 17.5 防复发：契约测试 + CI

新增 `tests/clipwright/test_stage_input_contract.py`：

1. 复用 `scripts/audit_stage_plumbing.py` 的 AST 结果，断言 `dead_entries == []`；
2. 断言四个 persona 通道在 `persona_id` 非空时装配后非空（`test_visual_context_plumbing.py`）；
3. 新增 `*Input` 字段时，若既不在黑板白名单、也没有显式构造点与 extra 兜底，
   必须在 `ALLOW_UNWIRED` 里写明理由，否则测试失败。

CI 命令：

```powershell
python -m pytest tests/clipwright/test_stage_input_contract.py tests/clipwright/test_visual_context_plumbing.py -q
python scripts/audit_stage_plumbing.py --strict
```

### 17.6 与本文档其它章节的关系

- **D7 是 §6/§7 的前提**：层级与可读性方案要落地，必须先有 `vision_prompt` / `allow_scrim` 通道，
  否则"加底板"会被 `_ensure_no_background` 一律清掉。
- **D4 与 §5 的 persona 相关阈值互相影响**：色板若恒为黑白红，任何"风格一致性"验收都失去意义。
- **D12 与 §8 的 Beat Plan 有关**：Beat Plan 依赖"元素语义键"，位置填充会让 beat 映射错位。

---

## 18. 实施记录：§16/§17 修复落地与验证（2026-10-29）

本节记录本轮实际改动、验证数据，以及仍未闭环的部分（供下一轮直接接着做）。

### 18.1 改动清单（全部已提交）

| 编号 | 内容 | 位置 |
|---|---|---|
| R1 | 统一视觉上下文装配层：board → extra → persona_store 三级兜底，含 prompt 截断、指纹、rhythm 补齐、trace | `clipwright/services/visual_context.py`（新增） |
| R3 | 四个 persona 字段改由装配层注入（structure/animation 共用）；material/audio/quality 的配置键补 extra 兜底 | `services/stage_inputs.py` |
| R3b | Agent 侧双保险：`execute` 里再按 `context.persona_id` 装配一次，老路径/直接调用也能拿到 persona | `agents/animation_agent.py` |
| R4 | 删除 `_resolve_style` 无条件注入的 `#000000/#FFFFFF/#FF0000`；改标注 `palette_source=persona\|fallback` | `agents/animation_agent.py` |
| R4b | 无结构化色板时，用 palette 文本顶替硬约束（"配色以 palette 描述为最终准绳"） | `animation/mg/generator.py::_build_context_section` |
| R5 | persona 提示词统一上限 `settings.persona_prompt_max_chars`（默认 2000），去掉 400/600 二次截断 | `config.py`、`agents/animation_agent.py` |
| R6 | 模板/插件路径注入 `mg_params["style_hint"]`（配色/palette/动效风格/节奏摘要）+ `persona_fingerprint` | `agents/animation_agent.py` |
| D7 | 背景守卫改为「禁不透明全幅、允许半透明底板」：`_ensure_no_background(..., allow_scrim=)` + `settings.animation_allow_scrim / animation_scrim_max_alpha` | `animation/mg/generator.py`、`config.py` |
| D7b | 提示词侧放行并鼓励半透明底板（rgba 0.25–0.55、高度 ≤45%），并要求文字自带 `text_shadow`/`text_stroke` | `animation/mg/type_generation.py`、`animation/mg/generator.py` |
| D7c | 可读性启发式：文本无描边/投影且无底板 → 记 `可读性风险` issue（不阻断，进 metadata/trace） | `animation/mg/type_generation.py::validate_for_type` |
| D8/D9/D10 | `material_plugin_config` / `audio_config` / `QualityInput.constraints` 接线（黑板 → extra → persona constraints） | `services/stage_inputs.py` |
| D12 | `_build_llm_params` 改「语义别名优先 + 未占用位置兜底」，避免 `{label}`/`{q1}` 同文 | `animation/mg/generator.py` |
| 工具 | 接线审计脚本（AST，`--strict` 可进 CI） | `scripts/audit_stage_plumbing.py`（新增） |
| 测试 | 视觉上下文/装配/背景守卫/参数映射/可读性 14 条；更新 3 条旧契约测试 | `tests/clipwright/test_visual_context_plumbing.py`、`test_mg_persona_style.py`、`test_animation_llm_typing.py` |

### 18.2 验证结果

**审计**：`python scripts/audit_stage_plumbing.py --strict` → 退出码 0（修复前 6 个死入口 + 1 个未接线 persona 通道）。

**单片段重跑**（只给 `persona_id=Zam`，`extra_params={}`、`visual_config={}`，刻意不传任何 persona 内容）：

```
persona_ctx: source=persona_store, prompt_chars=1713, palette="冷色调+高对比度，黑白为主色调…",
             fingerprint=68fd838c7332
clip: comparison / mg_hyperframes / method=llm / issues=[]
motion: moving_count=15 / 15, max_shift=1920px（整屏级）
MOV 逐帧验收: 24/24 distinct frames → ratio 1.0（修复前同类批次 3/115 = 0.026）
可读性: 6 个文本元素带 text_shadow；抽帧可见灰色半透明卡片 + 白字投影，配色为黑白+蓝/红点缀
```

结论：**persona 从"完全没传"变成"按 persona_id 自动装配并进入提示词"**；动效与可读性同时改善。

**回归**：后端 `pytest tests -q` = **4636 passed / 4 skipped / 1 xfailed**。

### 18.3 仍未闭环（下一轮）

| 项 | 现状 | 建议 |
|---|---|---|
| 底板/描边仍是"提示词要求"而非硬门禁 | 本次靠 `text_shadow` 通过可读性启发式，`has_scrim=false` | 把 §7 的「背景采样 + 自动底板」做成渲染期兜底；生成期把"无描边且无底板"升级为阻断（配一次自动修复） |
| 动效强度不均 | 本次 max_shift 1920px，但历史片段中位数仅 1.56% | 按 §5 的 motion energy 阈值进 critique；`motion_report` 增加 `coverage`（关键帧覆盖时长比） |
| 插件 `accepts_persona` 声明 | 只做了 `style_hint` 注入，插件尚无声明位 | 在 `plugin.yaml` 增加 `accepts_persona: true`，`_handle_mg_animation` 据此传 `persona` 字典 |
| `extra_params` 其余 10 个无写入键 | `cut_profile`/`cut_density_tier` 已由 persona rhythm 兜底 | 其余（`orientation`/`glossary`/`shot_params`/`enable_color_match` 等）逐个确认：接线或删读取点 |
| 视觉上下文指纹进生成缓存 | 已产出 `persona_fingerprint` 并按 description 影响缓存，但未直接拼进 `_mg_cache_key` | 下一轮把 fingerprint 显式加入缓存键，避免不同 persona 命中同一缓存 |

