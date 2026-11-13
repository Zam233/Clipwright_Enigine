# 帧艺 ClipWright — Agent 工作流设计

> Agent 编排引擎从选题到成片的完整工作流。

---

## 一、Agent 工作流总览

当前生产链路是 **1 个主 Agent + 阶段子 Agent**：

```plaintext
用户指令 → 主 Agent（任务内核：规划 / 工具 / 收尾）
                │
                ├─ delegate_agent(stage="structure")   → 场景 / 分镜
                ├─ delegate_agent(stage="material")    → 候选素材
                ├─ delegate_agent(stage="edit")        → 时间线草稿
                ├─ delegate_agent(stage="animation")   → 动画 / MG
                ├─ delegate_agent(stage="audio")       → 配音 / 混音 / final_timeline
                └─ delegate_agent(stage="quality")     → 质检；问题回流主 Agent
                          │
                          ▼
                    RenderService → MP4 + Webhook

阶段依赖：structure → material → edit → animation → audio；quality 在时间线成形后复查。
主 Agent 依据 delegate_agent 返回的 degraded / failure.category / needs 决定重派、跳过还是接受，
不是固定路由表。
```

> 历史：v1 固定序列 / v2 DAG + 自愈循环已于 2026-10 起被主 Agent + 阶段子 Agent 取代，
> `pipeline.py` / `pipeline_v2.py` 已删除、`run_pipeline` 能力下线。下面「二、Agent 职责」
> 中的阶段职责仍是各**阶段子 Agent** 的契约来源。

---

## 二、Agent 职责

### Requirements Agent
> F4 对齐注记（2026-09）：本 Agent 类**保留实现但不在阶段派发中实例化**——生产链路的需求
> 分析由 `services/requirements_service.py` 承担（自持提示词，经 /api/requirements/* 暴露），
> 主 Agent 的默认首派阶段是 structure。animation_intents 经 requirements_service →
> /proceed 的 extra_params → StructureAgent（C2 已接通）。

- **输入**: 用户选题 + 文稿 + 参考素材
- **输出**: 创作方案 (creative_brief) + 动画需求意图 (animation_intents)
- **动画识别**: 当用户提到数据展示、对比、流程、图表时，自动识别并输出 animation_intents
  - `type: "mg"` — 动态图形（数据图表、进度条、对比图等）
  - `type: "text"` — 文字入场动画
  - `type: "logic"` — 逻辑关系图解
- animation_intents 经 `requirements_service` → `StructureAgent.extra_params` → 注入 LLM prompt

### Structure Agent
- **输入**: 选题 + Persona 配置 + 完整文稿 + animation_intents（来自 RequirementsAgent）
- **输出**: 带时间结构的场景列表（含动画标记）
- **模式**:
  - `voiceover` — 根据口播文稿按段落生成场景
  - `visual` — 每行场景描述作为一个独立场景
- 通过 `structured_output()` 强制 LLM 返回 JSON
- `_build_anim_guide()` 向 LLM 暴露可用动画类型，包括 `mg_dynamic` 标记格式

### Material Agent
- **输入**: 场景列表
- **输出**: 每个场景的候选素材列表
- **流程**:
  1. LLM 生成具体视觉搜索词（非抽象关键词）
  2. 多组关键词分别搜索、去重
  3. `FrameValidatorTool` 帧验证（过滤全黑/全白）
  4. 按方向/分辨率排序

### Edit Agent
- **输入**: 场景 + 候选素材
- **输出**: 粗剪 Timeline（3 轨：视频/文字/音频）
- **特性**:
  - 多段素材循环填充场景时长（避免素材不足）
  - 素材不可用时自动跳过尝试下一个
  - 所有素材不可用 → 文字占位视频

### Animation Agent
- **输入**: 粗剪时间线 + Persona 视觉参数 (visual_config)
- **输出**: 编排好的动画序列（含 generated_mg_count）
- **文字动画**: 在文字轨创建 text clip，生成完整 keyframes（入场+保持+出场），走 FFmpeg drawtext
- **逻辑动画**: 在动画轨创建独立 clip（Hyperframes SVG / drawtext 降级），展示箭头/对比/流程等关系
- **MG 动画（静态模板）**: ID 以 mg_ 开头 → MGRenderer 加载预定义 JSON 模板 → HTML/CSS 动画 → hyperframes 渲染
- **MG 动画（LLM 动态生成）**: `[逻辑动画]mg_dynamic:{...}` 标记 → `_handle_llm_mg()` → `llm_mg` 插件生成完整 MG JSON → MGRenderer → Hyperframes
  - 成功生成时 `generated_mg_count` 递增
  - 失败时自动降级: LLM → 模板匹配 → drawtext 纯文字
- **过渡动画**: `[过渡动画]xxx` 标记 → 设置 clip.transition_in 字段，供 xfade filter

### Audio Agent
- **输入**: 时间线 + Persona 音频参数
- **输出**: 混音后的时间线（音量包络 + BGM 建议）

### Quality Agent
- **输入**: 完整时间线 + 约束条件
- **输出**: 质检报告 + 自愈建议（`redo_agent` 字段）
- **检查项**: 时长/轨道/节奏方差/动画覆盖率/音量
- **自愈**: 发现问题时给出 `redo_agent` 建议 → 结果回主 Agent，由它用 `delegate_agent` 重派对应阶段

---

## 三、AgentBus 上下文总线

阶段子 Agent 之间**不直接通信**：输入由主 Agent 通过 `delegate_agent` 显式传入，结果写回黑板
（`task_blackboard`）/产物，失败与降级回主 Agent。`AgentBus` 目前只承担事件与诊断通道：

| 方法 | 说明 |
|------|------|
| `publish(agent, topic, data)` | 发布消息（诊断/追踪用） |
| `get_events(topic)` | 按主题获取事件 |
| `set_artifact / get_artifact` | 时间线等共享产物的读写 |
| `add_demand / get_demands` | 需求通道（⚠ F6: 无消费确认机制，管线已停止注入 `_demands`，保留事件流） |
| `route_decision` | ⚠ 未实现——编排由主 Agent 决策，不再有静态 DAG 路由 |

---

## 四、镜头意图系统

每个 clip 标注 `ShotIntent`，指导剪辑决策：

> ⚠ 对账注记（2026-08）：ShotIntent 镜头意图系统**尚未实现**（代码中仅 shot_params 时长参数），本章为规划稿。

| 类型 | 说明 |
|------|------|
| `main` | 主镜头 |
| `reaction` | 反应镜头 |
| `broll` | B-roll 辅助画面 |
| `transition` | 过渡镜头 |
| `establishing` | 定场镜头 |
| `detail` | 特写 |
| `pip` | 画中画 |
| `text` | 文字/标题 |

---

## 五、对话式编辑

> ⚠ 对账注记（2026-08）：对话式编辑（EditSession + `/api/edit/session/{id}/chat`）**已删除**
> （死代码 + 依赖 video_filter/text_design 占位工具）。能力由「时间轴手动编辑 + Agent 选片段返工」替代。

通过自然语言修改已生成的视频：

```
用户: "把字幕改成金色粗体加发光"
  → LLM 解析意图 → action: change_text_style
  → 调用 TextDesignTool → 更新渲染参数

用户: "调亮一些，加暗角效果"
  → LLM 解析意图 → action: apply_video_filter + apply_effect
  → 调用 VideoFilterTool + EffectVignetteTool
```

详见 `POST /api/edit/session/{id}/chat`。

---

## 六、老 V2 管线的可靠性机制（历史参考）

> 以下机制属于已下线的 V2 管线；主 Agent 侧等价能力为：熔断（子 Agent 连续失败）、
> 墙钟/回合/预算硬闸、`failure.category` 分类与收口（soft landing）。保留此节供对照。

### 熔断器 (Circuit Breaker)
- 连续 3 次失败后熔断指定 Agent
- 熔断后跳过该 Agent 60 秒（恢复期）
- Agent 成功后自动重置熔断计数器

### 全局超时
- 默认 1800 秒（30 分钟），通过 `extra_params.pipeline_timeout_sec` 覆盖（对账注记：原文档写 900s，实际实现为 1800s，已对齐）
- 超时后管线标记为 FAILED，错误分类为 `transient`

### 错误分类
| 类别 | 匹配模式 | 示例 |
|------|---------|------|
| `transient` | 超时/连接/Rate Limit | LLM 请求超时 |
| `permanent` | 未找到/无效/类型错误 | Persona 不存在 |
| `fatal` | 系统级（OOM/DB 断连） | MongoDB 连接失败 |

### 持久化
- Pipeline 状态持久化到 MongoDB（含截断保护，单字段 5000 字符）
- LLM token 用量追踪（`record_llm_call`）
- SpanTracer 全调用树可观测性

### 任务队列
- `TaskQueue` 信号量控制并发（默认 3）——⚠ 对账注记：队列当前未接线到管线执行（规划 P8 接线），仅用于健康/指标统计
- 每个任务内置超时（默认 900s）
- 支持取消 pending 任务

---

> 完整 API 参考见 [api_reference.md](api_reference.md)
