## Persona 的类型体系

帧匠的 Persona 是一个**分层复合的数字人格包**。一个完整的 Persona 由四种类型的表达层叠加构成：

```plaintext
┌─────────────────────────────────────────┐
│              Persona 复合体              │
│                                         │
│  ┌─────────────────────────────────┐    │
│  │  类型一：参数层 (Parameter)      │    │
│  │  YAML / JSON                    │    │
│  │  规则的、可读的、人可编辑的       │    │
│  └─────────────────────────────────┘    │
│              ↓ 叠加                     │
│  ┌─────────────────────────────────┐    │
│  │  类型二：示例层 (Exemplar)       │    │
│  │  视频片段 + 标注                │    │
│  │  "这种地方我会这样剪"            │    │
│  └─────────────────────────────────┘    │
│              ↓ 叠加                     │
│  ┌─────────────────────────────────┐    │
│  │  类型三：嵌入层 (Embedding)      │    │
│  │  向量 + 特征分布                │    │
│  │  不可读、可计算、可比较           │    │
│  └─────────────────────────────────┘    │
│              ↓ 叠加                     │
│  ┌─────────────────────────────────┐    │
│  │  类型四：模型层 (Model)          │    │
│  │  LoRA / Adapter 权重            │    │
│  │  深度特化、可直接推理             │    │
│  └─────────────────────────────────┘    │
│                                         │
│  层与层之间：继承关系、覆盖规则、冲突消解 │
└─────────────────────────────────────────┘
```

四种类型不是互斥的，而是**由浅入深的叠加**。一个新手 UP 主的 Persona 可能只有参数层；一个头部 UP 主的 Persona 四层齐全，且模型层权重持续迭代。

------

## 类型一：参数层（Parameter Layer）

上一版 YAML 的核心保留在这里。但它不再代表 Persona 的全部，只是四层之一。

参数层负责定义**显式的、人可读的风格约束**：

```yaml
persona_id: "zam_knowledge_critical"
layer_type: "parameter"
version: "2.0.0"

# 身份
identity:
  tone: "critical_intellectual"
  position: [0.72, -0.15]       # 政治光谱二维坐标（经济轴, 文化轴）
  class_perspective: "vocational_ascending"

# 语言
language:
  academic_density: 0.12
  slang_ratio: 0.08
  max_sentence_len: 30
  variance_target: 0.7
  forbidden_patterns:
    - "不是.*而是.*"
    - "值得一提的是.*"
    - "掰开了揉碎了"

# 剪辑
rhythm:
  cut_profile: "surge_pause"    # 命名节奏配置：蓄力→爆发→留白→再蓄力
  surge_sections: ["hook", "theoretical_climax"]
  pause_sections: ["real_world_return", "conclusion"]
  base_shot_duration_ms: 7000

# 视觉
visual:
  palette: "cold_industrial"
  animation_styles:
    text_intro: "typewriter_glitch"
    emphasis: "red_flash"
    transition: ["hard_cut:0.7", "pixel_dissolve:0.2", "glitch:0.1"]

# 音频
audio:
  bgm_slots:
    theory_backing: ["kraftwerk_pool"]
    climax: ["silence_7s"]
    resolution: ["minimal_piano_single_note"]
  voice: "zam_v3.sovits"

# 约束
constraints:
  max_duration_sec: 900
  require_source_citation: true
```

参数层的特点：**可读、可编辑、可版本控制、可 diff。** 两个 Persona 之间的参数层可以直接用 `git diff` 比较差异。

------

## 类型二：示例层（Exemplar Layer）

参数层的问题在于：很多创作判断无法用规则描述。比如「第 3 分 12 秒那个停顿，多留 3 帧刚好，多了就拖」—— 你没法把它写成 YAML 字段。

示例层解决这个问题。它由**带标注的视频片段**组成，作为风格参考样本：

```yaml
persona_id: "zam_knowledge_critical"
layer_type: "exemplar"
version: "1.2.0"

exemplars:
  - exemplar_id: "ex_001"
    source_video: "/videos/zam_ep12.mp4"
    time_range: [180000, 215000]    # 3:00 - 3:35
    annotation:
      what: "理论推导加速段"
      cut_count: 14                  # 35秒内剪切14次
      avg_shot_ms: 2500
      audio_treatment: "bgm_fade_to_silence"
      text_overlay_style: "rapid_typewriter"
      note: "从鲍德里亚符号消费过渡到景观社会的关键桥段。加速制造压迫感。"
    
  - exemplar_id: "ex_002"
    source_video: "/videos/zam_ep08.mp4"
    time_range: [420000, 450000]    # 7:00 - 7:30
    annotation:
      what: "回到现实的减速段"
      cut_count: 3                   # 30秒内只剪3次
      avg_shot_ms: 10000
      audio_treatment: "complete_silence_first_7s"
      text_overlay_style: "none"
      note: "关键论点后留白。让观众消化。不用任何视觉干扰。"

  - exemplar_id: "ex_003"
    source_video: "/videos/zam_ep15.mp4"
    time_range: [10000, 25000]       # 开场15秒
    annotation:
      what: "标准开场句式"
      script_pattern: "先别急着.*"
      visual_treatment: "single_static_shot_no_animation"
      note: "开场永远不做动画。直接怼脸。制造不适感。"
```

示例层的工作方式不是「按规则剪」，而是 **「参考类似场景的标注来决策」**。当 Agent 在某个脚本段落发现它被标注为 "理论推导加速段"，它会检索示例层中同类标注的 exemplar，提取它们的剪辑参数分布，作为当前决策的参考。

技术实现上，示例层的 embedding 会被预计算并存入向量库，供 Agent 做 few-shot 检索。

------

## 类型三：嵌入层（Embedding Layer）

前两层都需要人工标注。嵌入层走另一条路：**用模型自动提取创作者的隐性特征向量**。

这一层不包含任何人可读的字段。它是一组高维向量和统计分布：

```yaml
persona_id: "zam_knowledge_critical"
layer_type: "embedding"
version: "3.1.0"
source_videos_count: 67              # 从67个视频中提取
extraction_model: "clipwright-embed-v2"

embeddings:
  # 剪辑节奏向量（128维）—— 从67个视频的时间轴自动提取
  rhythm_embedding: "rhythm_zam_v3.npy"
  rhythm_stats:
    shot_duration_distribution: "log_normal"
    shot_duration_mu_ms: 6200
    shot_duration_sigma_ms: 2400
    pacing_variance_per_minute: 0.41  # 每分钟内剪切间隔的方差
    
  # 视觉风格向量（512维）—— CLIP视觉特征在时间轴上的均值
  visual_embedding: "visual_zam_v3.npy"
  visual_stats:
    dominant_color_cluster: [[18,18,26], [220,20,20], [245,245,245]]
    saturation_median: 0.32
    contrast_median: 0.78
    motion_magnitude_median: 0.15     # 运动幅度低 = 偏好静态镜头
    
  # 语言风格向量（768维）—— 从脚本和口播提取
  language_embedding: "lang_zam_v3.npy"
  language_stats:
    avg_sentence_complexity: 0.68
    rhetorical_question_ratio: 0.12
    first_person_ratio: 0.04          # 极少用"我"
    second_person_ratio: 0.09         # 适度用"你"
    imperative_ratio: 0.03             # 极少祈使句
    
  # 论证结构向量（384维）
  argument_embedding: "arg_zam_v3.npy"
  argument_stats:
    hook_to_body_ratio: 0.12          # 破题段占12%
    theory_density_peak_position: 0.55 # 理论密度峰值在55%位置
    real_world_return_position: 0.78   # 回到现实在78%位置
```

嵌入层的使用方式：Agent 在决策时，不仅读取参数层的规则，还读取嵌入层的向量，计算当前决策与历史风格分布的 KL 散度。如果偏差过大，质检 Agent 标记警告。

具体场景：EditAgent 产出了一个平均镜头时长 3.2 秒的段落，但嵌入层记录的历史分布均值是 6.2 秒、标准差 2.4 秒 —— 偏离超过一个标准差，触发复核。

------

## 类型四：模型层（Model Layer）

前三层都是「描述」风格，模型层是「复现」风格。它包含微调过的模型权重，可以直接替代通用模型参与推理。

```yaml
persona_id: "zam_knowledge_critical"
layer_type: "model"
version: "2.0.0"
base_models:
  llm: "qwen-3-32b"
  vision: "clip-vit-l-14"
  tts: "gpt-sovits-v3"

weights:
  # 脚本生成模型的 LoRA 权重
  structure_agent_lora: "loras/zam_structure_v2.safetensors"
  training_samples: 89               # 用89个脚本微调
  
  # 剪辑决策模型的 Adapter 权重
  edit_agent_adapter: "adapters/zam_edit_v3.pt"
  training_samples: 67               # 用67个视频的时间轴微调
  
  # 动画风格生成模型
  animation_agent_lora: "loras/zam_animation_v1.safetensors"
  training_samples: 45
  
  # 声音克隆
  voice_clone: "sovits/zam_v3"
  
  # 素材选择偏好模型（学习"什么画面配什么论点"的映射）
  material_selection_adapter: "adapters/zam_material_v2.pt"
```

模型层加载后，Agent 的推理不再依赖通用模型 + 参数约束，而是直接用微调过的模型。微调模型已经内化了创作者的风格，不需要外部参数做引导。

------

## 四层叠加的执行逻辑

Agent 运行时，Persona 的四层按以下优先级叠加：

```plaintext
        高优先级
          ↑
    ┌──────────┐
    │ 参数层    │  ← 显式规则，硬约束（如 forbidden_patterns）
    ├──────────┤     违反即打回，不可被其他层覆盖
    │ 示例层    │  ← Few-shot 参考，影响策略选择
    ├──────────┤
    │ 嵌入层    │  ← 统计分布，软约束（偏离过大时警告）
    ├──────────┤
    │ 模型层    │  ← 微调权重，替代通用推理
    └──────────┘
          ↓
        低优先级（但最深层的推理基础）
```

具体执行流程：

```plaintext
Agent决策点
    │
    ├──→ 1. 加载模型层权重（如果有）
    │       替代通用模型做基础推理
    │
    ├──→ 2. 查询嵌入层
    │       计算当前决策与历史分布的距离
    │       距离 > 2σ → 标记为"风格偏离"
    │
    ├──→ 3. 检索示例层
    │       匹配同类标注的 exemplar
    │       提取其参数作为 few-shot 参考
    │
    └──→ 4. 执行参数层硬约束
            检查是否违反 forbidden_patterns
            检查是否超出 max_duration
            违反 → 拒绝输出，生成修正指令
```

------

## Persona 的继承与组合

四种类型支撑起另一个关键能力：**Persona 不是孤立的，可以继承和组合。**

```yaml
persona_id: "zam_variant_experimental"
persona_name: "扎姆·实验版·温和向"
version: "1.0.0"

# 继承
inherits: "zam_knowledge_critical"    # 继承全部四层

# 覆盖参数层的特定字段
overrides:
  parameter:
    language:
      academic_density: 0.06          # 降低学术密度
      slang_ratio: 0.15               # 增加网络用语
    rhythm:
      cut_profile: "gentle_flow"      # 换一个节奏配置
    constraints:
      max_duration_sec: 600           # 缩短到10分钟

# 组合：引入另一个Persona的示例层
compose:
  - persona: "he_tech_creative"
    layers: ["exemplar"]              # 借用何同学的示例层
    weight: 0.3                        # 以30%权重混合
  - persona: "film_hurricane"
    layers: ["model.animation_agent_lora"]
    weight: 0.2
```

继承 + 覆盖 + 组合 = Persona 的谱系树。一个 MCN 可以维护一个 "机构基础 Persona"，旗下每个 UP 主继承它并覆盖个人特征。一个 UP 主可以做多个变体：激进版、温和版、实验版。

------

## Persona 的存储结构

从单个 YAML 变成目录结构：

```plaintext
personas/
└── zam_knowledge_critical/
    ├── persona.yaml              # 元信息 + 继承声明 + 四层引用
    ├── parameter.yaml            # 参数层
    ├── exemplar/                 # 示例层
    │   ├── index.yaml            # 标注索引
    │   └── clips/                # 参考片段（只存引用，不存原片）
    ├── embeddings/               # 嵌入层
    │   ├── rhythm_zam_v3.npy
    │   ├── visual_zam_v3.npy
    │   ├── lang_zam_v3.npy
    │   └── stats.yaml            # 统计摘要
    ├── models/                   # 模型层
    │   ├── structure_agent_lora/
    │   ├── edit_agent_adapter/
    │   ├── animation_agent_lora/
    │   └── voice_clone/
    └── versions/                 # 版本历史
        ├── v1.0.0/
        ├── v2.0.0/
        └── v3.0.0.yaml           # 当前版本快照
```

------

## Persona 的创建路径

不同类型的创作者，进入路径不同：

| 路径     | 起始层 | 适合谁               | 投入                      |
| -------- | ------ | -------------------- | ------------------------- |
| 快速起步 | 参数层 | 新 UP 主             | 15 分钟填 YAML            |
| 风格分析 | 嵌入层 | 有历史视频的 UP 主   | 上传视频 → 自动提取       |
| 深度定制 | 示例层 | 对风格有自觉的 UP 主 | 人工标注 20 + 片段        |
| 完全特化 | 模型层 | 头部 UP 主、MCN      | 上传 50 + 视频 + GPU 微调 |

四条路径产出的 Persona 可以随时补全缺失的层。今天只有参数层，明天上传视频后自动生成嵌入层，后天手动标注补充示例层。

## Persona 生成agent

### 一、输入源

PersonaForge 支持五种输入，可以任意组合：

| 输入类型        | 格式                                | 能提取什么                               |
| --------------- | ----------------------------------- | ---------------------------------------- |
| 历史视频        | `.mp4` / `.mov`                     | 嵌入层全部 + 模型层训练数据 + 示例层片段 |
| 视频工程文件    | `.fcpxml` / `.prproj` / 时间线 JSON | 剪辑节奏参数（比分析成品视频更精确）     |
| 脚本 / 口播文本 | `.txt` / `.md` / `.srt`             | 语言层参数 + 论证结构向量                |
| 声纹采样        | `.wav` (5-15 分钟纯净人声)          | 声音克隆模型                             |
| 自然语言描述    | 纯文本                              | 参数层的初始值 + 示例层的文字描述        |

第五种输入最关键 —— 它允许一个连视频都没做过的创作者，通过对话来定义自己的风格意图。

------

### 二、分析 Pipeline

PersonaForge 内部有自己的子 Pipeline，独立于主视频生产的 Pipeline：

```plaintext
[输入接收]
    ↓
[模态分流] ──→ 视频流 ──→ [视觉分析] ──→ [视觉向量 + 统计]
    │                    [节奏分析] ──→ [节奏向量 + 统计]
    │                    [音频分析] ──→ [BGM偏好 + 声纹]
    │
    ├──→ 文本流 ──→ [语言分析] ──→ [语言向量 + 规则]
    │               [结构分析] ──→ [论证结构向量]
    │
    ├──→ 工程流 ──→ [时间轴解析] ──→ [精确剪切参数]
    │
    └──→ 描述流 ──→ [意图理解] ──→ [参数初始值]
                         ↓
              ┌─────────────────────┐
              │   [融合与消歧]       │
              │   多源数据加权合并   │
              │   冲突检测与提示     │
              └─────────┬───────────┘
                        ↓
              ┌─────────────────────┐
              │   [层生成]           │
              │   参数层 ← 统计 + 规则│
              │   示例层 ← 标注片段   │
              │   嵌入层 ← 向量计算   │
              │   模型层 ← 训练数据   │
              └─────────┬───────────┘
                        ↓
              ┌─────────────────────┐
              │   [质量评估]         │
              │   层完整性评分       │
              │   置信度标注         │
              │   不确定性标记       │
              └─────────┬───────────┘
                        ↓
                   [输出 Persona]
```

------

### 三、逐层生成细节

#### 3.1 参数层的自动生成

参数层是唯一必须 100% 生成的层 —— 即使没有视频输入，也能从对话描述中推导。

**从视频分析生成**：

```python
# 伪代码示意
def generate_parameter_layer_from_videos(videos: list[Video]) -> ParameterLayer:
    # 视觉
    palette = extract_dominant_palette(videos)        # K-means聚类取前3色
    animation_styles = classify_text_animations(videos)  # CNN分类：typewriter/smooth/glitch
    transition_weights = count_transitions(videos)    # 硬切/溶解/闪白 计数归一化
    
    # 节奏
    shot_durations = extract_shot_boundaries_all(videos)
    cut_profile = classify_rhythm_pattern(shot_durations)  # surge_pause/even_flow/rapid_fire
    base_shot_duration_ms = median(shot_durations)
    
    # 语言（需要对应视频的字幕/口播文本）
    academic_density = count_academic_terms(scripts) / total_words
    slang_ratio = count_slang_terms(scripts) / total_words
    max_sentence_len = percentile_95(sentence_lengths)
    forbidden_patterns = None  # 视频中无法自动提取，置空等人工补充
    
    # 音频
    bgm_slots = classify_bgm_by_context(videos)  # 按场景分类BGM
    voice_clone_available = bool(audio_samples)
    
    return ParameterLayer(...)
```

**从对话描述生成**：

用户说：「我想做那种冷峻风格的科技评论，说话比较直接，不搞煽情。画面喜欢黑白为主，文字用打字机效果。节奏偏快但不乱。」

LLM 将其映射为参数：

```yaml
identity:
  tone: "tech_enthusiast"          # "冷峻科技评论" → tech_enthusiast
language:
  academic_density: 0.08           # "比较直接" → 低学术密度
  slang_ratio: 0.12                # 非学术但口语化
  allowed_patterns: ["直接犀利的反问句"]
visual:
  palette: "monochrome_high_contrast"  # "黑白为主"
  animation_styles:
    text_intro: "typewriter"       # "打字机效果"
rhythm:
  cut_profile: "fast_but_controlled"  # "节奏偏快但不乱"
```

参数层生成时，每个字段附带一个 `confidence` 值（0-1），标记该参数是从数据提取的（0.9+）还是从描述推断的（0.5-0.7）。前端 Persona 管理面板用不同颜色高亮这些置信度，引导用户优先检查低置信度字段。

------

#### 3.2 示例层的自动标注

示例层的生成依赖视频输入。没有视频就跳过这一层。

流程：

```plaintext
视频 → [场景切割] → [段落分类] → [匹配标注模板] → [生成示例]
```

**场景切割**：用 TransNetV2 做镜头边界检测，然后按时间间隔和视觉连续性聚合成逻辑段落。

**段落分类**：用预训练的分类器将每个段落打上标签。标签体系来自所有已注册的 VideoTypePlugin 的标注模板：

| 标签                  | 来源                         | 示例            |
| --------------------- | ---------------------------- | --------------- |
| `hook`                | 所有类型                     | 视频开场破题段  |
| `theory_acceleration` | knowledge_longform           | 理论推导加速段  |
| `real_world_return`   | knowledge_longform           | 回到现实减速段  |
| `product_broll`       | digital_review               | 产品特写 B-Roll |
| `reaction_shot`       | vlog_daily / kichiku_fastcut | 反应镜头        |
| `climax_silence`      | 跨类型                       | 关键论点静默段  |
| `quick_cut_sequence`  | kichiku_fastcut              | 高速快剪序列    |

**生成示例**：分类完成后，对每个标签选取 3-5 个最具代表性的片段，自动填充标注字段：

```yaml
exemplars:
  - exemplar_id: "auto_001"
    source_video: "/input/video_ep05.mp4"
    time_range: [125000, 158000]
    annotation:
      what: "theory_acceleration"
      cut_count: 11
      avg_shot_ms: 3000
      audio_treatment: "bgm_fade_to_silence"
      note: "auto-generated from ep05. confidence: 0.82"
      auto_generated: true        # 标记为自动生成，提示用户复核
```

> 自动标注的 exemplar 带有 `auto_generated: true` 标记和置信度。人工复核后去掉标记。

------

#### 3.3 嵌入层的自动提取

嵌入层完全自动化，不需要人工介入。

```plaintext
视频集合 → 逐帧特征提取 → 时序聚合 → 统计建模 → 向量产出
```

**视觉嵌入**：用 CLIP ViT-L/14 逐帧提取视觉特征，在时间轴上做滑动窗口均值 → 512 维视觉嵌入向量。

**节奏嵌入**：提取每个镜头的时长序列 → 计算一阶差分（剪切加速度）、二阶差分（加速度变化率）、每 30 秒窗口内的方差 → 128 维节奏嵌入向量。

**语言嵌入**：用 BGE-M3 对脚本全文做嵌入 → 768 维。附加统计特征（句长分布、修辞问句比例、第一 / 第二人称比例）→ 降维后拼接。

**论证结构嵌入**：用预训练的分类器标记每个段落的功能（hook /body_theory/body_evidence /body_counterargument/conclusion /real_world_return）→ 功能标签序列做 LSTM 编码 → 384 维论证结构向量。

所有向量保存为 `.npy` 文件，统计摘要写入 `stats.yaml`。

------

#### 3.4 模型层的训练数据准备

PersonaForge 不直接做微调训练（那需要 GPU 集群和数小时的计算），但它负责**准备训练数据**：

1. **结构 Agent 训练数据**：从视频提取对应的脚本 → 组成「选题 + 脚本」的配对数据集
2. **剪辑 Agent 训练数据**：从工程文件或时间轴分析提取「脚本段落 + 上下文 + 剪辑决策」的三元组
3. **素材选择训练数据**：从视频中提取「脚本论点 + 使用的画面」的配对

产出的训练数据写入 `models/training_data/` 目录。用户后续可以运行 `clipwright finetune` 命令触发实际训练。

------

### 四、交互模式

PersonaForge 支持三种交互模式，对应用户的不同状态：

### 模式 A：全自动


输入：视频文件夹 + 脚本文本文件夹

输出：完整四层 Persona（模型层仅生成训练数据）

耗时：取决于视频数量，典型 67 个视频约 15-30 分钟

后续：用户打开前端 Persona 管理面板，复核自动生成的参数和示例

### 模式 B：对话引导

进入对话式引导流程。Agent 通过阅读其过往的视频风格，并通过一系列问题引导用户：

对话引导产出的 Persona 只有参数层（全部字段），置信度在 0.5-0.7。其他三层为空。用户后续可以通过模式 A 补齐。

### 模式 C：迭代优化


输入：已有 Persona + 自然语言反馈

输出：调整后的 Persona

迭代逻辑：


| 反馈示例                | 分类             | 操作                                    |
| ----------------------- | ---------------- | --------------------------------------- |
| "节奏太慢了"            | rhythm.global    | base_shot_duration_ms *= 0.8            |
| "开场不够冲击力"        | exemplar.hook    | 检索更强的 hook 示例替换                |
| "文字动画太花"          | visual.animation | animation_styles.text_intro → "minimal" |
| "学术味太重，听不懂"    | language         | academic_density *= 0.6                 |
| "BGM 第 5 分钟那段不搭" | audio.slot       | 在 bgm_slots 中替换对应槽位             |

每次迭代保存为新版本。用户可以随时回退。

前端编辑器中，PersonaForge 对应的是 Persona 管理面板里的「智能创建」按钮。点击后弹出一个两栏界面 —— 左栏是对话引导，右栏是实时预览正在生成的 Persona 参数。