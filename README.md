## 目录

- 项目结构
- 项目定位
- 为什么是帧艺
- 架构总览
- 快速开始
- 前端编辑器
- 后端引擎
- Persona 配置
- 视频类型
- 特化三级跳
- 路线图
- 开源协议
- 贡献

## 项目结构：两大子系统

帧艺 ClipWright 由两大子系统构成：

### 帧艺 ClipWright 内容视频编排引擎（纯后端）

> 核心文档：[架构设计](docs/structure.md) · [Persona 系统](docs/Persona.md) · [Agent 工作流](docs/workflow.md)

纯后端引擎，负责 AI 驱动的视频内容编排。包含五层架构——原子能力层、Agent 编排层、类型层、Persona 配置层、用户接口层——以及完整的 Agent 管线（结构/素材/剪辑/动画/音效/质检）。对外暴露 REST API，不依赖任何前端，可独立运行。

### 帧艺 ClipWright AI 辅助视频创作系统（全栈）

> 项目总览（本文档）

前后端分离的 AI 辅助视频创作系统 = 内容视频编排引擎（后端）+ Web 视频编辑器（前端）。前端基于 React 19 + TypeScript + Canvas 自研时间轴，对标 PR 的多轨编辑器。后端引擎以 Agent 管线驱动，Persona 系统实现风格化自动剪辑。

## 项目定位

**帧艺 ClipWright 是一个前后端分离的 AI 辅助视频创作系统。**

- **后端（内容视频编排引擎）**：Persona 驱动的 Agent 引擎。负责选题分析、脚本骨架生成、素材智能匹配、自动粗剪、动画合成、风格质检。对外暴露 REST API，不依赖任何前端。
- **前端**：一个完整的 Web 视频编辑器。它的基础能力对标 PR—— 多轨时间轴、素材拖拽替换、动画参数面板、关键帧编辑。在此基础上，AI Agent 作为 "副驾驶" 嵌入编辑器：你可以接受 Agent 生成的粗剪时间线，然后手动微调每一帧，也可以在任何环节让 Agent 介入。

不是 "一键生成视频然后你只能接受或放弃"。是 "Agent 先生成一个初稿，你拿过来在时间轴上改，改完不满意再让 Agent 处理局部"。循环迭代，人在回路。

------

## 为什么是帧艺

市面上的 AI 视频工具有两种：

1. **一句话生成**：输入 prompt，等几分钟，得到一个不太对但你也不知道从哪改起的视频。
2. **PR/AE + 插件**：传统剪辑软件 + AI 抠图 / 降噪之类的点状增强。创作逻辑还是纯手工。

帧艺走第三条路：**AI 负责结构化和体力活，人负责审美判断和微调。**

核心洞察很简单 —— 创作者的隐性知识分布在三个层面：

- **语言层**：措辞密度、句式节奏、论证推进方式
- **视听层**：剪切手感、动画偏好、配色直觉、BGM 情绪匹配
- **结构层**：如何从选题切入、如何抛出论证、如何在关键处停顿

帧艺把这三层全部参数化为 Persona 配置。Agent 按你的 Persona 出初稿，你在编辑器里审视、调整、确认。这个过程重复得越多，Persona 越贴合你 —— 它是在用的过程中被 "驯化" 的。

------

## 架构总览

```plaintext
┌──────────────────────────────────────────────────────┐
│                    前端 (clipwright-web)               │
│                  React 19 · TypeScript                │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │              视频编辑器主界面                    │  │
│  │                                                │  │
│  │  ┌──────────┐  ┌────────────┐  ┌────────────┐  │  │
│  │  │ 素材面板  │  │  预览窗口   │  │ 属性/参数   │  │  │
│  │  │          │  │            │  │ 面板        │  │  │
│  │  │ AI匹配   │  │  Canvas    │  │            │  │  │
│  │  │ 手动导入 │  │  实时预览   │  │ 动画参数   │  │  │
│  │  │ 历史素材 │  │            │  │ 转场选择   │  │  │
│  │  └──────────┘  └────────────┘  │ Persona微调 │  │  │
│  │                                └────────────┘  │  │
│  │  ┌──────────────────────────────────────────┐   │  │
│  │  │           多轨时间轴                       │   │  │
│  │  │  V1: [====素材A====][==素材B==][==C==]    │   │  │
│  │  │  V2:     [==文字动画==]    [==文字==]     │   │  │
│  │  │  A1: [============BGM==============]      │   │  │
│  │  │  A2:    [旁白TTS==============]           │   │  │
│  │  └──────────────────────────────────────────┘   │  │
│  │                                                │  │
│  │  ┌─────────────────────────────────────────┐    │  │
│  │  │         Agent 副驾驶面板                  │    │  │
│  │  │  "素材A和素材B之间需要过渡镜头，建议:…"    │    │  │
│  │  │  [接受] [替换候选] [忽略]                 │    │  │
│  │  └─────────────────────────────────────────┘    │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌─────────────────────┐  ┌──────────────────────┐   │
│  │   Persona 管理面板   │  │     导出面板         │   │
│  │   可视化配置Persona  │  │   格式/分辨率/码率   │   │
│  │   实时风格预览       │  │   渲染队列           │   │
│  └─────────────────────┘  └──────────────────────┘   │
│                                                      │
└──────────────────────┬───────────────────────────────┘
                       │ REST API
                       │ POST /api/pipeline/run
                       │ POST /api/tool/execute
                       │ POST /api/skill/execute
                       │ POST /api/material/search
                       │ POST /api/animation/list
                       │ POST /api/stt/transcribe
                       │ POST /api/plugin/load-all
┌──────────────────────┴───────────────────────────────┐
│                    后端 (clipwright)                   │
│                  Python · FastAPI · LangGraph          │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │             Persona 引擎                        │  │
│  │  解析YAML → 参数验证 → 注入Pipeline             │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │             Agent 编排层 (LangGraph)            │  │
│  │                                                │  │
│  │  RequirementsAgent → StructureAgent →          │  │
│  │  MaterialAgent → EditAgent → AnimationAgent     │  │
│  │  → AudioAgent → QualityAgent                   │  │
│  │                                                │  │
│  │  支持: 全流程执行 / 单Agent执行 / 局部重执行   │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │             视频类型系统                     │  │
│  │  knowledge_longform │ kichiku_fastcut │ ...    │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌────────────────────────────────────────────────┐  │
│  │        Tool 层 / Skill 层 / Material 层          │  │
│  │  40 工具 · 12 技能 · 3 素材源 · 37+ 动画          │  │
│  │  FFmpeg · CLIP · Whisper · JSON规范             │  │
│  └────────────────────────────────────────────────┘  │
│                                                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ 插件系统  │  │ 渲染服务  │  │  动画系统         │   │
│  │ importlib │  │ 异步队列  │  │  onscreen/text   │   │
│  │ 热加载    │  │          │  │  transition       │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│                                                      │
└──────────────────────────────────────────────────────┘
```

### 前后端职责边界

| 职责                                 | 前端 | 后端 |
| ------------------------------------ | ---- | ---- |
| 时间轴交互（拖拽、裁剪、轨道管理）   | ✓    |      |
| 素材库浏览、手动导入、标签管理       | ✓    |      |
| 动画参数面板（关键帧编辑、样式切换） | ✓    |      |
| 视频预览（Canvas + WebCodecs）       | ✓    |      |
| Persona 可视化配置                   | ✓    |      |
| 导出参数设置                         | ✓    |      |
| Agent 调用触发                       | ✓    |      |
| Persona 解析与验证                   |      | ✓    |
| Agent Pipeline 编排与执行            |      | ✓    |
| 素材语义检索与匹配                   |      | ✓    |
| 时间线生成（粗剪）                   |      | ✓    |
| 动画合成、音效匹配                   |      | ✓    |
| 渲染转码                             |      | ✓    |
| 素材库索引与存储                     |      | ✓    |


## 前端编辑器

帧艺前端不是 Agent 的输出窗口。它是一个独立完整的视频编辑器，Agent 只是其中一个功能模块。

### 核心功能

#### 多轨时间轴

- 不限轨道数量（视频轨、音频轨、文字轨、特效轨）
- 拖拽裁剪、分割、合并、调整时长
- 帧级精度定位（键盘方向键逐帧移动）
- 吸附对齐、标记点、区域循环播放
- 支持导入 Agent 生成的初始时间线，完全可手动调整

#### 素材面板

- 左侧固定面板，三栏切换：
  - **AI 匹配**：根据当前光标所在位置，Agent 推荐候选素材（显示匹配理由、相似度分数）
  - **素材库**：本地素材浏览，按标签 / 文件夹筛选，缩略图预览
  - **历史使用**：该项目中已使用的素材，快速复用
- 双击或拖拽素材到时间轴以添加 / 替换

#### 预览窗口

- 基于 WebCodecs API 的实时 Canvas 渲染
- 时间轴光标位置实时预览
- 支持全屏、分辨率切换、安全框显示
- 渲染结果即时反馈

#### 属性面板

- 选中时间轴上的任意元素后，右侧显示其属性：
  - **视频素材**：位置、缩放、旋转、透明度、时长、速度
  - **文字动画**：字体、大小、颜色、动画类型、入场 / 出场时长
  - **转场**：类型、时长、方向
  - **音频**：音量、淡入淡出、音频闪避参数

#### Agent 副驾驶面板

这是帧艺最有辨识度的模块。它不是全自动的，是按需介入的。

- **全局 Agent 调用**：输入选题 → Agent 生成完整时间线初稿 → 导入编辑器
- **局部 Agent 调用**：
  - 在时间轴上选中一段空白区域 → "Agent，这里加过渡镜头"
  - 选中某个素材 → "Agent，推荐风格匹配的替代素材"
  - 选中一段文字动画 → "Agent，换成更克制的动画风格"
- **建议模式**：Agent 持续分析当前时间线，在副驾驶面板中列出优化建议（"第 3 分 12 秒到第 3 分 18 秒节奏过慢，建议压缩 2 秒或增加信息叠加"）

#### Persona 管理面板

- 可视化编辑 Persona 配置文件
- 每个参数有实时预览（例如调整`cut_density`时，右侧小窗展示该参数对应的剪辑风格变化）
- 支持多 Persona 切换、对比
- 与后端同步，修改即时生效

#### 导出面板

- 分辨率、帧率、码率、编码器选择
- 渲染队列管理（多任务并行渲染由后端处理）
- 渲染进度实时推送（WebSocket）
- 渲染完成后自动下载或保存到指定路径

### 前端技术栈

| 层         | 选型                       | 理由                           |
| ---------- | -------------------------- | ------------------------------ |
| 框架       | React 19 + TypeScript      | 生态成熟，状态管理灵活         |
| 状态管理   | Zustand                    | 轻量，适合编辑器复杂状态       |
| 时间轴     | 自研 Canvas + DnD Kit      | 需要帧级精度，现有开源方案不够 |
| 视频预览   | WebCodecs API + Canvas     | 无需后端转码即可实时预览       |
| UI 组件    | Radix UI + Tailwind        | 无样式锁定的基础组件           |
| 与后端通信 | TanStack Query + WebSocket | REST 数据同步 + 实时状态推送   |

------

## 后端引擎

### API 设计（核心端点）

```plaintext
# Pipeline 执行
POST   /api/pipeline/run           # 全流程执行，返回时间线JSON
POST   /api/pipeline/step/{agent}  # 单Agent执行

# 原子能力工具
GET    /api/tool/list              # 列出所有工具
POST   /api/tool/execute           # 执行单个工具
POST   /api/tool/batch             # 批量执行工具

# 技能
GET    /api/skill/list             # 列出所有技能
POST   /api/skill/execute          # 执行技能

# 素材库
GET    /api/material/sources       # 列出素材源
POST   /api/material/search        # 跨源搜索

# 动画
GET    /api/animation/list         # 列出所有动画定义
GET    /api/animation/get/{id}     # 查看动画详情

# 语音转文字
POST   /api/stt/transcribe         # 音频→带时间戳文字
POST   /api/stt/align              # 文案→音频对齐

# 插件管理
GET    /api/plugin/list            # 已加载插件
GET    /api/plugin/discover        # 发现可用插件
POST   /api/plugin/load/{id}       # 加载插件
POST   /api/plugin/unload/{id}     # 卸载插件
GET    /api/plugin/capabilities    # 系统能力概览

# Persona 管理
GET    /api/persona/list
GET    /api/persona/{id}
POST   /api/persona/create
PUT    /api/persona/{id}

# PersonaForge
POST   /api/persona/forge/from-prompt   # 自然语言→Persona
POST   /api/persona/forge/from-script   # 脚本分析→Persona
POST   /api/persona/forge/refine        # 迭代优化
POST   /api/persona/forge/dialogue      # 对话引导

# RAG 知识库
POST   /api/persona/{id}/rag/index     # 建立向量索引
POST   /api/persona/{id}/rag/query     # 语义检索

# 渲染
POST   /api/render/start          # 提交渲染任务
GET    /api/render/status/{id}    # 查询进度

# 需求分析
POST   /api/requirements/analyze  # 分析用户需求
POST   /api/requirements/extract  # 提取创作约束

# 声音克隆与 TTS
POST   /api/voice/upload          # 上传音频文件
POST   /api/voice/clone           # 克隆音色
GET    /api/voice/list            # 列出已克隆音色
DELETE /api/voice/{db_id}         # 删除音色
POST   /api/voice/synthesize      # 文字→语音
POST   /api/voice/dub             # 文案切分+逐段配音

# 波形可视化
GET    /api/waveform/{id}         # 获取音频波形数据

# 视觉分析
POST   /api/vision/analyze        # 视频/图像分析

# 字体管理
GET    /api/font/list             # 列出可用字体
POST   /api/font/upload           # 上传字体

# EDL 导入导出
POST   /api/edl/import            # 导入 EDL
GET    /api/edl/export/{id}       # 导出 EDL

# 项目管理
POST   /api/project/create        # 创建项目
GET    /api/project/{id}          # 获取项目
PUT    /api/project/{id}          # 更新项目
DELETE /api/project/{id}          # 删除项目
GET    /api/project/list          # 项目列表

# Chat Forge
POST   /api/chat_forge/generate   # 对话式内容生成

# 学习/反馈
POST   /api/learning/feedback     # 提交反馈
GET    /api/learning/stats        # 学习统计

# 媒体预处理
POST   /api/preprocess/validate   # 素材验证
POST   /api/preprocess/transcode  # 素材转码

# 配音脚本
POST   /api/dub_script/split      # 文案切分
POST   /api/dub_script/sync       # 配音同步
```

### 时间线 JSON 格式

前后端之间传递的时间线是统一 JSON 格式，确保前端编辑器和后端 Agent 可以互操作：

前端编辑器加载这个 JSON 并渲染为可视化时间轴。用户在编辑器里的每个操作，最终也是修改这个 JSON 结构。Agent 的输出和用户的修改在同一个数据模型上工作 —— 这就是互操作性的基础。


## Persona 配置

（此部分与上一版 README 基本一致，保留核心内容，省略字段细节表以控制篇幅。完整字段说明见 [docs/persona-schema.md](https://docs/persona-schema.md)）

### 最小配置示例

```yaml
persona_id: "my_first_persona"
persona_name: "我的第一个人格"
version: "1.0.0"
tone: "warm_storyteller"
knowledge_domains:
  - "digital_culture"
language:
  max_sentence_length: 25
  sentence_variance_target: 0.6
rhythm:
  cut_density_tier: "medium"
visual:
  color_palette:
    primary: "#1a1a2e"
    accent: "#e94560"
  animation_style: "smooth_fade"
audio:
  voice_clone_model_id: null
```

------

## 视频类型

| ID                   | 类型       | 平均镜头时长 | 转场特征        | 动画密度 |
| -------------------- | ---------- | ------------ | --------------- | -------- |
| `knowledge_longform` | 知识区长片 | 5-15s        | 硬切为主        | 中       |
| `kichiku_fastcut`    | 鬼畜快剪   | 0.3-2s       | 闪白 / Jump Cut | 极高     |
| `digital_review`     | 数码评测   | 3-8s         | 缓入缓出        | 中       |
| `vlog_daily`         | Vlog 日常  | 3-10s        | 混合            | 低       |


------

## 特化三级跳

```plaintext
Level 0 ─── 零配置 ──────── 选Plugin用默认参数，Agent出初稿后手动调整
         │                   适用场景：临时项目、快速试剪
         │
Level 1 ─── Persona配置 ──── 填写YAML，Agent按你的风格出初稿
         │                   适用场景：常规创作
         │
Level 2 ─── LoRA微调 ────── 上传50+历史视频，微调Agent底层模型
                             适用场景：头部UP主、MCN
```

------

## 路线图

| 阶段   | 内容                                      | 状态   |
| ------ | ----------------------------------------- | ------ |
| 阶段一 | 7-Agent 管线 + API；Tool/Skill/Animation 系统 | ✅ 完成 |
| 阶段二 | 素材库 + 插件系统；前端编辑器初步          | ✅ 完成 |
| 阶段三 | 动画系统 + STT + 语音转文字               | ✅ 完成 |
| 阶段四 | AudioAgent + QualityAgent 增强；渲染队列  | 🔄 进行中 |
| 阶段五 | 前端完整时间轴编辑器；LoRA 微调管线       | 📅 计划中 |

------
