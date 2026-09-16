## 目录

- 项目结构
- 项目定位
- 为什么是帧艺
- 架构总览
- 快速开始
- 前端编辑器
- 后端引擎
  - 素材内容理解
  - 时间线正确性
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
│  │  49 工具 · 4 技能 · 3 素材源 · 37+ 动画          │  │
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


------

## 快速开始（后端 + 独立前端仓库）

> 本仓库为**后端**（Python FastAPI）；**前端为独立仓库** `J:\Clipweight-Client`（React 19 + Vite）。
> 账号/市场服务为独立仓库 `K:\Clipwright Server`（可选，端口 8090）。

```powershell
# 一键启动（检查依赖 → 启动 MongoDB → 后端 8080 → 前端 5173）
scripts\start.ps1

# 停止
scripts\stop.ps1
```

或手动分步启动：

```bash
# 后端（需要 MongoDB 127.0.0.1:27017，配置见 .env.example）
python -m uvicorn clipwright.main:app --port 8080

# 前端（独立仓库）
cd J:\Clipweight-Client
npm install --cache "D:\.npm-cache"
npm run dev            # http://localhost:5173
```

**端口约定**：后端 8080（唯一标准）· 前端 5173 · 账号/市场服务 8090 · MongoDB 27017。
**前端代理**：`/api` → 8080，`/srv` → 8090（账号服务）。

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
POST   /api/pipeline/run              # 全流程执行，返回时间线JSON
POST   /api/pipeline/run-async        # 异步启动管线（SSE 追踪）
POST   /api/pipeline/step/{agent}     # 单Agent执行
GET    /api/pipeline/runs             # 最近管线运行记录
GET    /api/pipeline/status/{id}      # 管线状态
GET    /api/pipeline/trace/stream/{id} # SSE 管线事件流

# 原子能力工具
GET    /api/tool/list                 # 列出所有工具
POST   /api/tool/execute              # 执行单个工具
POST   /api/tool/batch                # 批量执行工具

# 技能
GET    /api/skill/list                # 列出所有技能
POST   /api/skill/execute             # 执行技能

# 素材库
GET    /api/material/sources          # 列出素材源
POST   /api/material/search           # 跨源搜索

# 动画
GET    /api/animation/list            # 列出所有动画定义
GET    /api/animation/get/{id}        # 查看动画详情

# 语音转文字 / 字幕
POST   /api/stt/transcribe            # 音频→带时间戳文字
POST   /api/stt/align                 # 文案→音频对齐
POST   /api/subtitle/import           # 导入字幕
POST   /api/subtitle/export           # 导出字幕

# 插件管理
GET    /api/plugin/list               # 已加载插件
GET    /api/plugin/discover           # 发现可用插件
POST   /api/plugin/load/{id}          # 加载插件
POST   /api/plugin/unload/{id}        # 卸载插件
GET    /api/plugin/capabilities       # 系统能力概览

# Persona 管理
GET    /api/persona/list
GET    /api/persona/{id}
POST   /api/persona/create
PUT    /api/persona/{id}

# PersonaForge
POST   /api/persona/forge/from-prompt # 自然语言→Persona
POST   /api/persona/forge/from-script # 脚本分析→Persona
POST   /api/persona/forge/refine      # 迭代优化
POST   /api/persona/forge/dialogue/build # 对话引导

# Chat Forge
POST   /api/persona/forge/chat/start  # 对话式 Persona 生成
POST   /api/persona/forge/chat/commit # 提交生成 PersonaManifest

# RAG 知识库
POST   /api/persona/{id}/rag/index    # 建立向量索引
POST   /api/persona/{id}/rag/query    # 语义检索

# 渲染
POST   /api/render/queue              # 加入渲染队列
GET    /api/render/queue/{id}         # 队列任务状态
GET    /api/render/status/{id}        # 查询进度
GET    /api/render/download/{name}    # 下载渲染结果

# 需求分析
POST   /api/requirements/init         # 初始化需求会话
POST   /api/requirements/chat         # 发送需求消息（SSE 流式见 chat/stream）
GET    /api/requirements/plan/{id}    # 获取制作规划书
POST   /api/requirements/proceed      # 确认并启动管线
POST   /api/requirements/edit         # 对选中片段下达自然语言编辑指令
POST   /api/requirements/{sid}/intent # 一句话 → 结构化剪辑意图（含歧义反问）
POST   /api/requirements/{sid}/intent/apply # dry-run / 执行补丁（硬错误零执行）

# 素材内容理解（也可经 /api/tool/execute 调用）
#   content_query  自然语言问素材内容，返回带 ShotRef 的区间
#   semantic_match 画面语义检索（CLIP 向量）
#   event_spot     音频事件定位    scene_detect  镜头切分
#   timeline_view  切点复核图 / 全片联络表（人工核对"剪得对不对"）

# 声音克隆与 TTS
POST   /api/voice/upload              # 上传音频文件
POST   /api/voice/clone               # 克隆音色
GET    /api/voice/list                # 列出已克隆音色
DELETE /api/voice/{db_id}             # 删除音色
POST   /api/voice/synthesize          # 文字→语音
POST   /api/voice/dub                 # 文案切分+逐段配音

# 波形可视化
POST   /api/waveform/generate         # 生成音频波形

# 视觉分析
POST   /api/vision/analyze            # 视频/图像分析

# 字体管理
GET    /api/fonts/list                # 列出可用字体
GET    /api/fonts/default             # 默认字体
POST   /api/fonts/clear-cache         # 清空字体缓存

# EDL 导入导出
POST   /api/edl/import/edl            # 导入 EDL
POST   /api/edl/import/fcpxml         # 导入 FCPXML
POST   /api/edl/export/edl            # 导出 EDL

# 项目管理
POST   /api/project                   # 创建项目
GET    /api/project                   # 项目列表
GET    /api/project/{id}              # 获取项目
PUT    /api/project/{id}              # 更新项目
DELETE /api/project/{id}              # 删除项目
POST   /api/project/{id}/duplicate    # 复制项目

# 视频编辑器（运维控制台）
GET    /api/video-editor/projects     # 编辑器项目列表
POST   /api/video-editor/projects/create # 创建编辑器项目
POST   /api/video-editor/projects/{id}/undo # 撤销

# 学习/微调
GET    /api/learning/datasets         # 数据集列表
GET    /api/learning/jobs             # 训练任务列表

# 媒体预处理
GET    /api/preprocess/operations     # 预处理操作列表
POST   /api/preprocess/submit         # 提交预处理任务
GET    /api/preprocess/task/{id}      # 任务状态
```

### 时间线 JSON 格式

前后端之间传递的时间线是统一 JSON 格式，确保前端编辑器和后端 Agent 可以互操作：

前端编辑器加载这个 JSON 并渲染为可视化时间轴。用户在编辑器里的每个操作，最终也是修改这个 JSON 结构。Agent 的输出和用户的修改在同一个数据模型上工作 —— 这就是互操作性的基础。

### 素材内容理解：让 Agent 知道自己在剪什么

管线要"剪得好"，前提是 Agent **看得见素材里有什么**。这条线（R1–R10）把素材从"文件名 + 时长"变成**可寻址、可检索、可被自然语言改动的镜头级内容**。

**四层能力**：

1. **镜头级内容索引**——每个镜头有唯一地址 `asset:{sha256[:12]}#s{n}`（`ShotRef`），索引里存画面维度（CLIP 零样本标签 / 景别 / 明暗 / 运镜 / 质量）、音频维度（音频事件 + 转写对齐）、以及两个向量（`clip` 视觉、`text` 文字）。索引落盘并可镜像到 MongoDB，按素材 sha 缓存与失效。
2. **内容查询工具 `content_query`**——让 Agent（和用户）用自然语言问"哪几段有人在说话""最亮的镜头是哪几个"，返回**带 `ShotRef` 的区间**而不是一段模型自由发挥的文字。混合检索 = 结构化条件 + 文字 + 向量 + 音频事件。
3. **自然语言剪辑意图 `EditIntent`**——把一句话解析成**受约束的结构化操作**（`keep/remove/reorder/trim/set/add/transition` + `target` + `constraints`），未知 op/字段/取值一律拒绝，不自由发挥。规则解析器零 token 覆盖常见说法，LLM 解析走 schema 约束。
4. **落位 → 预览 → 执行**——抽象 target（"所有黑场""第 2 个片段"）落到**具体 clip**；歧义/零命中时**反问并给选项**而不是猜；执行走 dry-run → apply 两步，**硬错误一条都不执行**。

**实测数字**（复跑：`python scripts/verify_all_metrics.py`，9/9 全绿）：

| 能力 | 指标 | 实测 | 门槛 |
| --- | --- | --- | --- |
| 镜头切分 | F1（真实素材像素级） | **1.000**（3/3 真切点，0 误报） | ≥0.85 |
| 图文检索 | 区间命中（`content_query`） | **32/32 = 100%** | ≥80% |
| 画面语义检索 | recall@10 | **100%** vs 关键词 16.7% | ≥80% |
| 意图解析 | 正确解析 | **50/50 = 100%**；幻觉拦截 **20/20** | ≥90% |
| 引用落位 | 引用不存在的素材占比 | **0**（18/18 引用全部可验证） | =0 |
| 一句话→成片 | 端到端 | **25/25**；dry-run 逐字节只读 | 全通过 |
| 建索引成本 | 1080p | **214 ms/s**（10 分钟素材 ≈ 2.1 分钟） | ≤3 分钟 |

**验证边界（必须一起读，否则上面的数字会被误读成"真实素材效果"）**：

- 切分/检索的**合成素材**测试测的是颜色与图案属性，真实画面召回率另做补测（Pexels 8 段，top-1 75%、top-3 82.1%）；
- 全部 30 条问答、50 条指令、18 个场景为**自写夹具**；LLM 解析路径仅打桩测试；
- 本机**未装 STT 后端**，转写路径从未真正跑过；
- 云端多模态（真实画面理解）**默认关闭**，从未在评测中实际调用；`visual.description` 目前是**用已测字段拼出来的描述**（`evidence=composed:stats+clip+audio`，置信度 0.5），不是模型级视觉理解；
- 4K 的耗时数字是 1080p **上采样**得到的，不是真 4K 素材实测。

计划与逐轮记录见 [docs/content-understanding-plan-2026-09-14.md](docs/content-understanding-plan-2026-09-14.md)，基准集与门槛对照见 [docs/content-understanding-benchmark.md](docs/content-understanding-benchmark.md)。一键复跑全部指标：`python scripts/verify_all_metrics.py`。

### 时间线正确性：改时长统一收拢

时间线是前后端之间**唯一的真相**，所以它的不变量有专门的回归用例守着：

- **改时长的所有入口共用一条收拢规则**（`reflow_after_duration_change`）：变短 → 后续片段左移，变长 → 后续片段右推，**绝不产生重叠**；并且**只抽掉真正被改掉的那段时间**，片段之间刻意留的呼吸/黑场前的一拍不会被压掉。
- 三个入口（`trim` / 变速 / `set duration_sec`）与需求服务的数值调整路径走**同一份实现**——此前它们是各自实现，实测出现"同一句『把这句改到 10 秒』走两条路得到两种结果"，这类不一致已经变成回归用例（`test_set_and_trim_agree_on_the_same_change`）。
- **变速同时改时长**：渲染端是 `setpts=PTS/speed`，1.5 倍速真的会把这段缩短到 2/3；时长不变就会让模型与渲染输出不一致。
- `start_sec` 是用户**显式指定位置**，刻意**不**自动重排（只如实告警），自动"帮你挪"会把用户意图改掉。
- 改完结构**收尾重算总时长**；dry-run 内置"输入未被改动"自检，**有硬错误一条都不执行**。

### 其余本期改进

- **MG 动画质量深度改进**：专业动效提示词（easing/发光/渐变/粒子/错峰）、`mg_renderer` 支持 `line`/`circle`/`ring`/`arc`/`bg` 元素与逐关键帧 easing、8 个内置专业模板、StructureAgent `mg_dynamic` 引导、Generator LLM 自批判闭环。
- **字幕样式**：Clip 新增 11 个样式字段（字重/斜体/字距/描边宽色/阴影 x·y·blur·色/发光宽色），导出 drawtext 双通道渲染，新字幕默认样式统一（font_size=48）。
- **时间模型 O1**：时间线时长与 tick 换算统一，消除浮点漂移。
- **插件化**：统一贡献契约 + manifest 签名（HMAC）+ 权限白名单 + 依赖解析 + 启停持久化 + 配置迁移 + reload 回滚 + secret 加密 + 审计日志；子进程插件 RPC 强制 UTF-8（`-X utf8` + `PYTHONIOENCODING`），中文回传不再损坏。
- **新端点 `GET /api/pipeline/runs`**：列出最近管线运行记录，供 Pipeline 管理页展示。

完整路由清单（**37 个路由文件 / 269 条 `/api` 路由**）见 [docs/api_reference.md](docs/api_reference.md)。


## Persona 配置

（此部分与上一版 README 基本一致，保留核心内容，省略字段细节表以控制篇幅。完整字段说明见 [docs/Persona.md](docs/Persona.md)）

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
| 阶段四 | AudioAgent + QualityAgent 增强；渲染队列  | ✅ 完成 |
| 阶段五 | 前端完整时间轴编辑器；LoRA 微调管线       | ✅ 完成 |
| 阶段六 | 账号管理 / 插件与 Persona 市场 / 商业交付加固（P0–P10） | ✅ 完成 |
| 阶段七 | **素材内容理解**（R1–R10）：镜头级索引 + 内容查询 + 自然语言剪辑意图落位 + 量化评测门禁 | ✅ 完成 |
| 阶段八 | 真实画面理解（云端多模态开启后的评测）、跨素材一致性、成片级自动复核 | 🔄 进行中 |

------

## 开源协议

本项目采用 **MPL 2.0**（Mozilla Public License 2.0），详见 [LICENSE](LICENSE)。第三方依赖各自遵循其开源协议。

## 贡献

欢迎提交 Issue 与 Pull Request。开发前请阅读：

- [AGENTS.md](AGENTS.md)（项目地图与验证清单）
- [docs/development.md](docs/development.md)（开发指南）
- 计划与执行进度：[docs/fix-and-feature-plan.md](docs/fix-and-feature-plan.md)

### 验证清单（改动后必须全绿）

| 检查 | 命令 | 当前基线 |
| --- | --- | --- |
| 后端全量回归 | `python -m pytest tests -q`（工作目录 `J:\Clipwright`） | **3187 passed / 0 failed**（3 skipped、1 xfailed） |
| 后端可导入 | `python -c "import clipwright.main"` | OK |
| 内容理解指标门禁 | `python scripts/verify_all_metrics.py` | **9/9 PASS**（完整跑约 4.6 分钟） |
| 前端单测 | `npm run test`（工作目录 `J:\Clipweight-Client`） | **771 passed / 90 files** |
| 前端类型与构建 | `npx tsc --noEmit` · `npm run build` | 均无错误 |

> 数字是**实测基线**而非配额：改动让它们下降时，先确认是预期变化还是回归。指标脚本输出的"验证边界"段落必须和数字一起读——它写着每个数字是在什么素材、什么条件下测出来的。
