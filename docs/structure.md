# 帧艺 ClipWright 内容视频编排引擎 — 架构设计文档

> **所属系统**：[帧艺 ClipWright AI 辅助视频创作系统](../README.md)
>
> 本文档描述**内容视频编排引擎**（纯后端）的五层架构设计。该引擎作为 AI 辅助视频创作系统的后端核心，可独立运行并通过 REST API 提供服务。

---

## 一、设计原则

在展开架构之前，先定三条铁律：

1. **通用层只做 "视频创作" 的抽象，不做 "视频风格" 的假设。** 一个转场 API 不应该内嵌 "扎姆喜欢硬切" 的预设。
2. **Persona 是驱动核心，不是外层皮肤。** 换 UP 主不是换个配色方案，是从剪辑节奏到论证逻辑的全面接管。
3. **插件即类型。** 鬼畜区、知识区、数码区、Vlog—— 它们的差异大到不能用参数覆盖，必须用独立插件。

------

## 二、总体架构：五层分离

```plaintext
┌─────────────────────────────────────────────────┐
│              用户接口层 (CLI / Web / API)           │
├─────────────────────────────────────────────────┤
│              Persona配置层 (YAML/JSON)             │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│   │扎姆·知识区│  │何同学·数码│  │影视飓风· │  ...  │
│   │批判型    │  │创意型    │  │工业型    │       │
│   └──────────┘  └──────────┘  └──────────┘       │
├─────────────────────────────────────────────────┤
│              类型插件层 (Video Type Plugins)       │
│   ┌────┐ ┌────┐ ┌────┐ ┌────┐ ┌────┐           │
│   │知识│ │鬼畜│ │数码│ │Vlog│ │纪录│  ...        │
│   │长片│ │快剪│ │评测│ │日常│ │短片│           │
│   └────┘ └────┘ └────┘ └────┘ └────┘           │
├─────────────────────────────────────────────────┤
│              Agent编排层 (LangGraph Orchestrator)  │
│   ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐          │
│   │结构  │ │素材  │ │剪辑  │ │质检  │  ...       │
│   │Agent │ │Agent │ │Agent │ │Agent │          │
│   └──────┘ └──────┘ └──────┘ └──────┘          │
├─────────────────────────────────────────────────┤
│              原子能力层 (Atomic Capabilities)      │
│   FFmpeg │ OpenCV │ Manim │ Whisper │ TTS │ ... │
└─────────────────────────────────────────────────┘
```

关键点：**Persona 配置层不直接调用原子能力，它必须经过类型插件层的翻译。** 也就是说，"扎姆・知识区批判型" 的 Persona 文件里写的`cut_density: high`，会被知识区长片插件翻译成具体的剪辑参数，而不是被直接执行。

------

## 三、五层逐一拆解

### 3.1 原子能力层 —— 只做 "动词"

这一层不包含任何创作逻辑，只提供原子化的视频处理 API：

| 能力模块   | 技术选型                 | 暴露接口                             |
| ---------- | ------------------------ | ------------------------------------ |
| 时间轴操作 | FFmpeg + MoviePy         | `trim()`, `concat()`, `overlay()`    |
| 画面分析   | OpenCV + CLIP            | `scene_detect()`, `semantic_match()` |
| 文字动画   | Manim / Motion Canvas    | `typewriter()`, `tracking_text()`    |
| 语音合成   | GPT-SoVITS / Fish-Speech | `tts_with_timbre(clone_source)`      |
| 语音识别   | WhisperX                 | `transcribe_with_timestamps()`       |
| 音频处理   | librosa + FFmpeg         | `bpm_detect()`, `audio_replace()`    |
| 渲染导出   | FFmpeg                   | `render(preset)`                     |

设计约束：**所有 API 的入参必须是纯数值或纯路径，不接受 "风格描述" 字符串。** `cut_density: high`是上层逻辑，这层只认`min_cut_interval_ms: 800`。

### 3.1.1 Skill 层（在原子能力之上的组合层）

Skill 是比 Tool 更高层级的可组合能力，编排多个 Tool 完成一个业务目标：

```
Skill: analyze_video_structure
  ├── Tool: scene_detect       → 检测场景切换点
  ├── Tool: audio_extract      → 提取音频
  └── Tool: bpm_detect         → 分析 BPM/节奏
```

Skill 与 Tool 统一注册机制：
- 都通过 `Registry`（ToolRegistry / SkillRegistry）注册
- 都支持 `to_llm_tool()` 生成 LLM tool schema
- 第三方 Plugin 可在 `initialize()` 中同时注册 Tool 和 Skill

### 3.1.2 Animation 层（基于 JSON 规范的动画引擎）

Animation 是独立于 Tool/Skill 的声明式动画系统，分三种类型：

```
Onscreen（10 个）      Text（5 个）           Transition（10 个）
fade_in               typewriter             crossfade
slide_up_in           char_by_char           push_left/right
scale_in              text_fade_in           wipe_left
blur_in               text_slide_up          zoom_in
rotate_in             highlight_flash        glitch
pulse                                     pixel_dissolve
...                                       ...
```

每种动画通过 JSON 关键帧定义：
```json
{
  "animation_id": "fade_in",
  "type": "onscreen",
  "duration_sec": 0.5,
  "easing": "ease-out",
  "keyframes": [
    {"time": 0.0, "properties": {"opacity": 0}},
    {"time": 1.0, "properties": {"opacity": 1}}
  ]
}
```

```
Skill: analyze_video_structure
  ├── Tool: scene_detect       → 检测场景切换点
  ├── Tool: audio_extract      → 提取音频
  └── Tool: bpm_detect         → 分析 BPM/节奏
```

Skill 与 Tool 统一注册机制：
- 都通过 `Registry`（ToolRegistry / SkillRegistry）注册
- 都支持 `to_llm_tool()` 生成 LLM tool schema
- 第三方 Plugin 可在 `initialize()` 中同时注册 Tool 和 Skill

------

### 3.2 Agent 编排层 —— 脑回路

用 LangGraph 做 DAG 编排。每个 Agent 是图中的一个节点，边代表数据流。

```plaintext
                    ┌──────────┐
                    │ 触发信号  │
                    └────┬─────┘
                         ↓
              ┌──────────────────┐
              │ ① 结构Agent       │
              │ 输入：选题/热点    │
              │ 输出：脚本骨架     │
              └──────┬───────────┘
                     ↓
         ┌───────────────────────┐
         │ ② 素材Agent           │
         │ 输入：脚本骨架+素材库  │
         │ 输出：候选素材集合     │
         └──────┬────────────────┘
                ↓
    ┌───────────────────────────┐
    │ ③ 剪辑Agent               │ ← Persona参数在此注入
    │ 输入：素材+时间轴模板     │
    │ 输出：粗剪时间线          │
    └──────┬────────────────────┘
           ↓
    ┌───────────────────────────┐
    │ ④ 动画Agent               │
    │ 输入：粗剪+动画参数       │
    │ 输出：带动画的时间线      │
    └──────┬────────────────────┘
           ↓
    ┌───────────────────────────┐
    │ ⑤ 音效Agent               │
    │ 输入：时间线+音频参数     │
    │ 输出：混音后的时间线      │
    └──────┬────────────────────┘
           ↓
    ┌───────────────────────────┐
    │ ⑥ 质检Agent               │
    │ 输入：完整时间线+规则集   │
    │ 输出：pass / fail + 修正  │
    └──────┬────────────────────┘
           ↓
       ┌───────┐
       │ 渲染  │
       └───────┘
```

每个 Agent 内部有一个**策略注册表**，根据 Persona 配置和视频类型插件来动态选择策略。Agent 不写死逻辑，它只是 "调度器"。

------

### 3.3 类型插件层 —— 这个设计是整个系统的灵魂

不同的视频类型，其底层剪辑逻辑差异巨大。举例：

| 维度         | 知识区长片       | 鬼畜区快剪       | Vlog 日常      |
| ------------ | ---------------- | ---------------- | -------------- |
| 平均镜头时长 | 5-15 秒          | 0.3-2 秒         | 3-8 秒         |
| 转场类型     | 硬切为主         | 闪白 / Jump Cut  | 缓入缓出       |
| BGM 角色     | 环境铺垫         | 节奏骨架         | 情绪引导       |
| 动画密度     | 中（关键词标注） | 极高（全屏特效） | 低（字幕为主） |
| 停顿设计     | 有（留白思考）   | 无（不能停）     | 有（情绪呼吸） |


这意味着：**同一个 Persona 配置（比如 "扎姆"），套用不同的类型插件，会产生不同但风格一致的视频。** 扎姆做知识区长片和扎姆做鬼畜二创，剪辑参数不同，但背后的 "扎姆感" 由 Persona 配置保证。

------

### 3.4 Persona 配置层 —— UP 主的数字灵魂

这是整个系统最核心的创新点。Persona 不是一个简单的 "风格预设"，而是一个**可训练、可迁移的创作者模型**。

Persona详见Persona.md

------

### 3.5 用户接口层

提供交互方式：

1. **用户应用程序前端**
2. **用户Web前端**

### 3.6 第三方插件层（已实现）

同时本项目支持使用第三方插件进行扩展。主要支持的扩展功能包括：

1. 支持增加视频、图片、动画、音效、音乐素材库/素材源。
2. 编辑器插件，编辑器插件在前端运行。它们可以新增 UI 组件、修改现有面板、添加新的交互工具。
3. Agent 插件。Agent 插件在后端运行，可以新增 Agent 节点、替换 Agent 策略、添加质检规则。
4. 能力及工具插件。能力/工具插件封装对特定工具 / 服务 / 模型的调用。它们让帧艺的能力层可替换、可扩展。

**实现状态**：

| 组件 | 状态 | 说明 |
|------|------|------|
| `PluginLoader` | ✅ 已实现 | 支持 `plugin.yaml` 清单解析 + `importlib` 动态导入 + 抽象基类跳过 |
| `HookRegistry` | ✅ 已实现 | 7 个 HookPoint：PRE/POST Pipeline、Agent、Render、ON_ERROR |
| REST API | ✅ 已实现 | list/discover/load/unload/load-all/capabilities |
| 自动发现 | ✅ 已实现 | `main.py` lifespan 中自动扫描 `plugins/` 目录，启动时加载 |
| 插件 ID 追踪 | ✅ 已实现 | PluginLoader 自动标记插件注册的 Tool/Skill/MaterialSource |
| `BasePlugin` | ✅ 已实现 | `initialize()` 可注册 Tool + Skill + MaterialSource |
| `MaterialSourcePlugin` | ✅ 接口已定义 | 扩展素材库来源 |
| `AgentStrategyPlugin` | ✅ 接口已定义 | 替换或增强 Agent 执行策略 |
| `CapabilityPlugin` | ✅ 接口已定义 | 封装外部工具/服务调用 |
| 前端编辑器插件 | ❌ 未实现 | 计划在 Phase 3 |

**使用方式**：

1. 在项目根目录 `plugins/` 下创建 `{your_plugin_id}/` 目录
2. 包含 `plugin.yaml`（清单）和 `main.py`（入口模块）
3. 入口模块的 `__all__` 中 export 继承 `BasePlugin` 的类
4. 重启服务后自动加载，或通过 API 手动加载

------

## 四、核心工作流：一次完整的视频生产

以一个具体场景走一遍完整链路：

**输入**： `persona=zam, type=knowledge_longform, topic="年轻人盲盒消费"`

### 阶段 1：结构 Agent

- 读取`zam` Persona 配置中的`identity.position`和`knowledge_domains`

- 识别到这是 "消费批判" 主题，调取鲍德里亚符号消费理论的引用权重

- 生成脚本骨架：

  ```plaintext
  ① 反问破题："你以为这是自由选择？"
  ② 拆解：商品拜物教 → 符号价值 → 身份焦虑
  ③ 传播结构：算法推送 → 种草笔记 → 社交货币
  ④ 哲学收束：景观社会的个体迷失
  ⑤ 回到现实：你买的不是盲盒，是资本给你造的临时身份
  ```

  

### 阶段 2：素材 Agent

- 根据脚本骨架中的关键词（"商品拜物教"、"盲盒"、"身份焦虑"），在素材库中检索
- CLIP 视觉匹配：关键词 "商品拜物教"→ 超市整齐排列的盲盒货架、泡泡玛特门店排队画面
- 弹幕素材匹配：检索 B 站相关视频的高频弹幕做视觉元素

### 阶段 3：剪辑 Agent（Persona 参数注入点）

- `knowledge_longform`插件读取 Persona 的`rhythm`配置
- 翻译：`cut_density_tier: high` → 知识区长片语境下 = 平均镜头时长 7 秒
- 翻译：`acceleration_trigger: deep_theory` → 脚本中第四节 "哲学收束" 部分加速至 4 秒 / 镜头
- 生成粗剪时间线

### 阶段 4：动画 Agent

- 读取`visual.animation_style: typewriter_glitch`
- 关键词出现时生成打字机效果文字
- 读取`visual.color_palette: cold_industrial` → 调色参数注入

### 阶段 5：音效 Agent

- 读取`audio.bgm_pool`按脚本段落匹配 BGM
- 第四节 "哲学收束" 段：匹配`theory_section` → Kraftwerk 风格工业电子
- 第五节 "回到现实" 段：`critical_climax` → 7 秒静默，纯人声

### 阶段 6：质检 Agent

- 对脚本文本跑正则：`forbidden_patterns`
- 对时间线跑方差分析：`sentence_variance`低于阈值则标记
- 检查视频时长：超过`max_video_duration_sec`则打回

------

## 五、特化机制：从通用到专属的三级跳

这是回答你 "如何针对特定 UP 主特化" 的核心。

```plaintext
Level 0: 通用默认 → 任何用户开箱即用
    ↓
Level 1: 类型插件特化 → 选择视频类型后自动适配
    ↓
Level 2: Persona特化 → 加载UP主专属配置文件
    ↓
Level 3: 微调特化 → 对底层模型做LoRA微调
```

**Level 0-1**不需要任何 UP 主数据，纯靠插件内置的通用策略。

**Level 2**是主战场。一个新 UP 主只需要三个动作：

1. 录制 15 分钟的风格采样视频
2. 填写 Persona 配置问卷（或者让系统自动分析采样视频生成初版配置）
3. 人工校正 3-5 个参数

**Level 3**是深度定制。对特定 Agent 的底层模型做微调：

- 剪辑 Agent：用 UP 主的 50 + 个历史视频做时间轴标注，训练个性化的剪切决策模型
- 动画 Agent：提取 UP 主常用的 Keyframe 动画参数分布，训练生成模型
- 声音 Agent：用 GPT-SoVITS 克隆声线

Level 3 的微调产物以 LoRA 权重发布，其他用户可以直接加载。这就是开源社区的力量 —— 影视飓风的转场风格 LoRA、扎姆的节奏控制 LoRA、手工耿的硬核手工感 LoRA，都可以在社区流通。

------

## 六、技术栈选型

| 层           | 选型               | 理由                                 |
| ------------ | ------------------ | ------------------------------------ |
| Agent 编排   | LangGraph          | DAG 表达力强，支持条件分支和人机协作 |
| 视频处理     | FFmpeg + MoviePy   | 工业标准，API 稳定                   |
| 视觉理解     | CLIP + SigLIP      | 开源，能做语义级素材匹配             |
| 动画生成     | Manim Community    | 程序化动画，比 AE 脚本更可控         |
| 语音合成     | GPT-SoVITS         | 开源，Few-shot 声音克隆              |
| 模型推理     | vLLM / llama.cpp   | 本地部署，数据不出创作者机器         |
| Persona 存储 | YAML + SQLite      | YAML 人可读，SQLite 做版本管理       |
| 插件分发     | Python 包 + Docker | 即装即用                             |

------

## 七、落地路线

这不是画饼。按现在的技术成熟度，可以这样推进：

**Phase 1（已完成）**：6-Agent 管线全线贯通。Structure（LLM+tool calling）→ Material（MaterialRegistry）→ Edit（真实时间线）→ Animation（JSON 规范动画引擎）。Tool/Skill/Animation/Material/Plugin 五大系统就绪。

**Phase 2（已完成）**：素材库系统（JSON 目录 / URL / RAG 知识库）+ 第三方插件系统（importlib 动态加载）+ 语音转文字（Whisper 转录 + 文案对齐）。测试前端覆盖全部功能。

**Phase 3（进行中）**：AudioAgent / QualityAgent 增强，FFmpeg 渲染管线接入。

**Phase 4（计划中）**：LoRA 微调管线 + 社区生态。开放插件市场和 Persona 市场，创作者上传自己的微调权重，形成开源循环。

------

## 八、Workflow 完整流程

```plaintext
┌─────────────────────────────────────────────────────┐
│ STEP 0: 选择/创建 Persona                            │
│   • CLI: upforge persona init                       │
│   • 上传历史视频 → 系统自动分析生成初始Persona      │
│   • 手动编辑 YAML 微调                              │
└───────────────────────┬─────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ STEP 1: 选择视频类型插件                             │
│   • upforge plugin list                             │
│   • → knowledge_longform / kichiku_fastcut / ...    │
└───────────────────────┬─────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ STEP 2: 输入选题                                     │
│   • upforge run persona=zam plugin=knowledge \      │
│       topic="年轻人的盲盒消费批判"                   │
└───────────────────────┬─────────────────────────────┘
                        ↓
┌─────────────────────────────────────────────────────┐
│ STEP 3: Pipeline自动执行                             │
│   StructureAgent.execute()                          │
│     → 读取Persona.tone + knowledge_domains           │
│     → LLM生成脚本骨架                                │
│     → 输出: script_skeleton (dict)                   │
│                                                     │
│   MaterialAgent.execute()                           │
│     → 对脚本中每个scene做CLIP语义检索               │
│     → 候选素材排序 + 去重                           │
│     → 输出: candidate_clips (list[Clip])             │
│                                                     │
│   EditAgent.execute()  ← Persona参数注入点          │
│     → plugin.translate_persona(persona) → ClipParams│
│     → plugin.get_timeline_template() → 时间轴骨架   │
│     → 候选素材填入时间轴骨架                        │
│     → 输出: raw_timeline (Timeline)                  │
│                                                     │
│   AnimationAgent.execute()                          │
│     → 读取 VisualConfig                             │
│     → 为文字层生成动画(Manim)                       │
│     → 输出: animated_timeline (Timeline)             │
│                                                     │
│   AudioAgent.execute()                              │
│     → 读取 AudioConfig                              │
│     → BGM匹配 + 混音 + TTS生成                      │
│     → 输出: mixed_timeline (Timeline)                │
│                                                     │
│   QualityAgent.execute()                            │
│     → 正则过滤 forbidden_regex                      │
│     → 节奏方差分析                                  │
│     → 时长/格式校验                                 │
│     → 输出: final_timeline (Timeline)                │
│                                                     │
│   pass → 渲染导出                                    │
│   fail → 打回EditAgent重剪（带修正指令）             │
└─────────────────────────────────────────────────────┘
```

------

## 九、特化机制：三级跳

```plaintext
                    Level 0: 零配置
        ┌───────────────────────────────────┐
        │  选择一个视频类型插件              │
        │  使用该插件的默认参数              │
        │  → 得到一个"还行"的通用视频       │
        └───────────────┬───────────────────┘
                        ↓
                    Level 1: Persona配置
        ┌───────────────────────────────────┐
        │  填写/上传生成 Persona YAML        │
        │  系统用Persona参数覆盖插件默认值   │
        │  → 得到有风格的视频                │
        └───────────────┬───────────────────┘
                        ↓
                    Level 2: LoRA微调
        ┌───────────────────────────────────┐
        │  上传50+历史视频                   │
        │  对EditAgent/AnimationAgent微调    │
        │  → LoRA权重发布到社区              │
        │  → 得到高度特化的视频              │
        └───────────────────────────────────┘
```

Level 0 到 Level 1 的跃迁只需要一个 YAML 文件。Level 1 到 Level 2 需要数据和算力 —— 但一旦完成，LoRA 权重就是可交易的数字资产。

------

## 十、一个完整的新 UP 主接入流程（实操）

假设有个新 UP 主叫 "老陈"，做数码评测的，视频风格是毒舌吐槽 + 硬核拆机。

**第 1 步：初始化**

```bash
upforge persona init --name "老陈·数码毒舌" --tone tech_enthusiast
# 生成 personas/laochen_tech_sarcastic.yaml 模板
```

**第 2 步：上传采样视频**

```bash
upforge persona analyze --video-dir ./laochen_samples/
# 系统自动分析：
#   - 平均镜头时长: 4.2s → cut_density_tier: high
#   - 转场偏好: 硬切82% → transition_weights.hard_cut: 0.82
#   - 色彩倾向: 高对比、偏暖 → color_palette调整
#   - 口头禅检测: "这玩意纯纯的智商税" → preferred_openers
# 自动写入 Persona YAML
```

**第 3 步：人工微调**

```bash
vim personas/laochen_tech_sarcastic.yaml
# 手动调整：加几个毒舌专用BGM、调整停顿策略、补充知识域
```

**第 4 步：试运行**

```bash
upforge run \
  persona=laochen_tech_sarcastic \
  plugin=digital_review \
  topic="某品牌新手机发热问题深度分析" \
  --dry-run          # 只生成时间线预览，不渲染
```

**第 5 步：满意后正式渲染**

```bash
upforge run ... --render --output ./laochen_ep01.mp4
```

**第 6 步（可选）：深度特化**

```bash
upforge finetune \
  persona=laochen_tech_sarcastic \
  agent=edit_agent \
  --videos ./laochen_all_videos/ \
  --output-lora ./loras/laochen_edit_v1.safetensors
```
