# 帧艺 ClipWright — Agent 工作流设计

> Agent 编排引擎从选题到成片的完整工作流。

---

## 一、Agent 工作流总览

### v1（固定序列）

```plaintext
Structure → Material → Edit → Animation → Audio → Quality → Render
```

### v2（动态路由 + DAG 并行执行）

```plaintext
执行组 [0] ─→ structure
执行组 [1] ─→ material
执行组 [2] ─→ edit
执行组 [3] ─→ animation ─┐
              audio ──────┤ → 并行 (asyncio.gather)
执行组 [4] ─→ quality
                │
                ├→ PASS ─→ Render
                └→ FAIL ─→ 自愈循环
                     │
                     ├→ 重做指定 redo_agent
                     ├→ 联动重做下游 Agent
                     └→ 返回 quality 复查 (最多 3 次)
```

---

## 二、Agent 职责

### Requirements Agent
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
- **自愈**: 发现问题时设置 `redo_agent` → 编排器自动回退重做

---

## 三、AgentBus 上下文总线

Agent 之间通过 `AgentBus` 交换信息：

| 方法 | 说明 |
|------|------|
| `publish(agent, topic, data)` | 发布消息 |
| `get_messages(topic)` | 按主题获取消息 |
| `set_demand(agent, demand)` | 声明需求 |
| `get_demands()` | 获取所有 Agent 的需求 |
| `route_decision(agent, status)` | 动态路由决策 |

---

## 四、镜头意图系统

每个 clip 标注 `ShotIntent`，指导剪辑决策：

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

## 六、V2 管线可靠性机制

### 熔断器 (Circuit Breaker)
- 连续 3 次失败后熔断指定 Agent
- 熔断后跳过该 Agent 60 秒（恢复期）
- Agent 成功后自动重置熔断计数器

### 全局超时
- 默认 900 秒（15 分钟），通过 `extra_params.pipeline_timeout_sec` 覆盖
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
- `TaskQueue` 信号量控制并发（默认 3）
- 每个任务内置超时（默认 900s）
- 支持取消 pending 任务

---

> 完整 API 参考见 [api_reference.md](api_reference.md)
