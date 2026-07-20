# 帧艺 ClipWright 内容视频编排引擎 — 架构设计文档

> **所属系统**：帧艺 ClipWright AI 辅助视频创作系统
>
> 本文档描述**内容视频编排引擎**（纯后端）的五层架构设计。

---

## 一、设计原则

1. **通用层只做"视频创作"的抽象，不做"视频风格"的假设。**
2. **Persona 是驱动核心，不是外层皮肤。**
3. **插件即类型。** 鬼畜区、知识区、数码区、Vlog——差异大到不能用参数覆盖。

---

## 二、总体架构：五层分离

```plaintext
┌─────────────────────────────────────────────────────┐
│               用户接口层 (REST API / Web)              │
│   Pipeline · Persona · Template · Type Maker · Edit   │
├─────────────────────────────────────────────────────┤
│              Persona 配置层 (YAML/JSON)               │
├─────────────────────────────────────────────────────┤
│              类型插件层 (Video Type Plugins)           │
│   内置: 知识长片/鬼畜快剪/数码评测/Vlog日常             │
│   用户自定义: Type Maker → JSON → DynamicPlugin       │
│   第三方: PluginLoader 发现加载 (PluginData/ 存储)     │
├─────────────────────────────────────────────────────┤
│            Agent 编排层 (动态路由 + 并行执行)           │
│   ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐     │
│   │结构  │ │素材  │ │剪辑  │ │动画  │ │音频  │     │
│   │Agent │ │Agent │ │Agent │ │Agent │ │Agent │     │
│   └──────┘ └──────┘ └──────┘ └──────┘ └──────┘     │
│                         ↕                            │
│                    ┌────────┐                        │
│                    │ 质检   │ ← 自愈循环回退          │
│                    │ Agent  │                        │
│                    └────────┘                        │
├─────────────────────────────────────────────────────┤
│            原子能力层 (38 个 Tool + 11 个 Skill)        │
│   FFmpeg · ffprobe · Vision · Whisper · TTS · CLIP   │
└─────────────────────────────────────────────────────┘
```

---

## 三、五层逐一拆解

### 3.1 原子能力层 — Tool + Skill

| 类别 | Tool 列表 |
|------|----------|
| 视频 | `video_trim` `video_concat` `video_overlay` `video_download` `video_crop` `video_thumbnail` `video_speed` `video_blur` `media_probe` |
| 音频 | `audio_extract` `audio_normalize` `audio_mix` `audio_replace` `bpm_detect` |
| 视觉 | `scene_detect` `semantic_match` `vision_llm` `face_detect` `background_remove` |
| 特效 | `effect_vignette` `watermark` `video_filter` `chroma_key` `video_stabilize` |
| 文字 | `generate_text_video` `subtitle_burn` `text_design` `typewriter_animation` `tracking_text` |
| MG 动画 | `mg_dynamic` — LLM 动态生成完整 MG JSON → MGRenderer → Hyperframes（见 `llm_mg` 插件） |
| 素材 | `material_filter` `frame_validator` |
| 质量 | `black_frame_detect` `audio_silence_detect` `subtitle_overflow` |
| 其他 | `speed_ramp` `color_correct` `lut_apply` `whisper_transcribe` `text_to_speech` `transition_apply` |

| Skill | 功能 |
|-------|------|
| `analyze_video_structure` | 视频结构分析 |
| `generate_captions` | 字幕生成 |
| `analyze_audio_rhythm` | 音频节奏分析 |
| `auto_caption` | 自动字幕（转写→拆分→同步） |
| `broll_matcher` | B-roll 匹配 |
| `script_analysis` | 文稿情绪分析 |
| `material_downloader` | 素材预下载 |
| `voiceover_sync` | 配音同步+闪避 |
| `auto_transition` | 自动转场推荐 |
| `background_music` | BGM 匹配 |
| `silence_cut` | 静音切除 |

### 3.2 Agent 编排层

6 个 Agent + 动态路由 + 自愈循环：

```plaintext
执行组 [0]: structure                     ← 结构分析（含 animation_intents 注入）
执行组 [1]: material                      ← 素材搜索
执行组 [2]: edit                          ← 时间线生成
执行组 [3]: animation + audio             ← 并行：动画编排（含 LLM MG 动画）+ 音频处理
执行组 [4]: quality                       ← 质检 + 自愈循环
    失败 → 自动回退到对应 Agent + 下游重做 (最多 3 次)
```

Agent 间通过 `AgentBus` 共享上下文。AnimationAgent 通过 `[逻辑动画]mg_dynamic:{...}` 标记调用 `llm_mg` 插件，LLM 动态生成完整 MG JSON（elements + keyframes + params），经 MGRenderer → Hyperframes 渲染为透明 MOV，最后 overlay 到主视频。

### 3.3 类型插件层

- **内置插件**: `knowledge_longform` `kichiku_fastcut` `digital_review` `vlog_daily`
- **能力插件**: `llm_mg` — LLM 驱动的动态 MG 动画生成（数据图表、标题揭示、进度条、对比图等）
- **用户自定义**: TypeMaker → JSON 配置 → `DynamicCategoryPlugin` 动态加载（`user_types/` 目录）
- **模板系统**: `VideoTemplate` + `{{变量}}` 占位符（`templates/` 目录）

### 3.4 Persona 配置层

四层复合：参数层 → 示例层 → 嵌入层 → 模型层。详情见 [Persona.md](Persona.md)。

### 3.5 用户接口层

```
# Pipeline
POST   /api/pipeline/run                 # 管线 v1 (固定序列)
POST   /api/pipeline/run-v2              # 管线 v2 (动态路由 + 并行 + 自愈)
POST   /api/pipeline/run-async           # 异步管线 + SSE 追踪
POST   /api/pipeline/submit              # 提交到队列
POST   /api/pipeline/retry/{id}/{agent}  # 重试 Agent
POST   /api/pipeline/batch               # 批量管线
POST   /api/pipeline/step/{agent}        # 单 Agent 执行
GET    /api/pipeline/trace/stream/{id}   # SSE 实时追踪
GET    /api/pipeline/result/{id}         # 异步结果查询
GET    /api/pipeline/tasks               # 任务列表
GET    /api/pipeline/stats               # 管线统计
GET    /api/pipeline/llm-usage           # LLM 用量统计

# Render
POST   /api/render/start                 # 渲染
POST   /api/render/queue                 # 队列渲染
GET    /api/render/queue/{id}            # 查询渲染队列
GET    /api/render/queue/stream/{id}     # SSE 进度流
GET    /api/render/status/{id}           # 渲染状态
GET    /api/render/download/{fn}         # 下载 MP4
GET    /api/render/thumbnail             # 缩略图
GET    /api/render/video                 # 视频代理
GET    /api/render/presets               # 导出预设

# 编辑
POST   /api/edit/session/create          # 对话式编辑会话
POST   /api/edit/session/{id}/chat       # 对话编辑
GET    /api/edit/session/{id}            # 会话状态
GET    /api/edit/capabilities            # 编辑能力

# 类型与模板
POST   /api/type-maker/create            # 创建视频类型
POST   /api/template/create              # 创建模板
POST   /api/template/batch/{id}          # 批量生成

# 素材
POST   /api/preprocess/start/{id}        # 素材预处理
POST   /api/webhook/subscribe            # Webhook 订阅
POST   /api/asset/upload                 # 上传素材
POST   /api/material/search              # 搜索素材
GET    /api/material/sources             # 素材源列表

# MG 动画 (llm_mg 插件)
POST   /api/plugin/llm_mg/generate       # LLM 生成 MG 动画
POST   /api/plugin/llm_mg/save-template  # 保存生成的 MG 动画为模板
GET    /api/plugin/llm_mg/templates      # 列出可用 MG 模板
GET    /api/plugin/llm_mg/generations    # 列出未保存的生成记录
```

---

## 四、关键数据流

```plaintext
模板变量 → VideoTemplate.render() → PipelineRequest
                                        ↓
                                PipelineOrchestratorV2.run()
                                        ↓
                           AgentDAG 分组 — asyncio.gather()
                                        ↓
                                AgentBus 上下文交换
                                        ↓
                                QualityAgent 自愈循环 (≤3 次)
                                        ↓
                                RenderService (多轨渲染)
                                        ↓
                                MP4 输出 + Webhook 通知

                ┌───────────────────────────────┐
                │    运行时数据目录               │
                │                                │
                │  PluginData/   插件运行时数据    │
                │    tmp/       渲染中间文件       │
                │    assets/    素材副本          │
                │    cache/     工具缓存          │
                │    plugins/   插件专属存储       │
                │  renders/     最终 MP4 输出     │
                │  library/     素材库文件         │
                └───────────────────────────────┘
```

## 五、Tool 注册

```python
register_builtin_tools()  # 注册全部 38 个内置 Tool
# 第三方插件通过 PluginLoader 注册
# 用户自定义类型通过 TypeConfig 动态注册
```

---

> 完整 API 列表见 [api_reference.md](api_reference.md)
