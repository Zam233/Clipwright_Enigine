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
│        Agent 编排层 (1 主 Agent + 阶段子 Agent)         │
│   ┌──────────────────────────────────────────────┐    │
│   │ 主 Agent（任务内核：规划 / 工具 / 收尾）       │    │
│   │   └─ delegate_agent(stage=…) ─→ 阶段子 Agent   │    │
│   │      结构 · 素材 · 剪辑 · 动画 · 音频 · 质检    │    │
│   │      失败 → 回主 Agent 决策（重派/跳过/接受）   │    │
│   └──────────────────────────────────────────────┘    │
├─────────────────────────────────────────────────────┤
│            原子能力层 (40 个 Tool + 12 个 Skill)        │
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
| TTS | `voice_clone` `text_to_speech` |
| 其他 | `speed_ramp` `color_correct` `lut_apply` `whisper_transcribe` `transition_apply` |

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
| `dub_script` | 文案切分 + 逐段配音（**由 `plugins/tts_feature` 提供**；engine 自 2026-11-03 起不再内置） |

### 3.2 Agent 编排层

**1 个主 Agent + 阶段子 Agent**。主 Agent 在任务内核里持有规划、工具调用与收尾，用
`delegate_agent(stage=…)` 把阶段工作派给子 Agent；每个阶段有结构化输入/输出（`StageResult`）、
归属到一次 run、并写回共享黑板（`task_blackboard`）。阶段顺序由主 Agent 按依赖判断
（默认 structure → material → edit → animation → audio → quality），不是写死的固定管线。

```plaintext
主 Agent（任务内核）
  ├─ delegate_agent(stage="structure")   → 场景 / 分镜
  ├─ delegate_agent(stage="material")    → 候选素材
  ├─ delegate_agent(stage="edit")        → 时间线草稿
  ├─ delegate_agent(stage="animation")   → 动画 / MG
  ├─ delegate_agent(stage="audio")       → 配音 / 混音 / final_timeline
  └─ delegate_agent(stage="quality")     → 质检；问题回流主 Agent 决策
```

主 Agent 也可以派只读调研子 Agent（`research_agent`）、读工作区产物、直接调用工具与技能。
老 6 阶段管线（`pipeline.py` / `pipeline_v2.py`）已删除，`run_pipeline` 能力下线。

Agent 结果通过黑板交换；AnimationAgent 通过 `[逻辑动画]mg_dynamic:{...}` 标记调用 `llm_mg` 插件，LLM 动态生成完整 MG JSON（elements + keyframes + params），经 MGRenderer → Hyperframes 渲染为透明 MOV，最后 overlay 到主视频。

### 3.3 类型插件层

- **内置插件**: `knowledge_longform` `kichiku_fastcut` `digital_review` `vlog_daily`
- **能力插件**: `llm_mg` — LLM 驱动的动态 MG 动画生成（数据图表、标题揭示、进度条、对比图等）
- **用户自定义**: TypeMaker → JSON 配置 → `DynamicCategoryPlugin` 动态加载（`user_types/` 目录）
- **模板系统**: `VideoTemplate` + `{{变量}}` 占位符（`templates/` 目录）

### 3.4 Persona 配置层

四层复合：参数层 → 示例层 → 嵌入层 → 模型层。详情见 [Persona.md](Persona.md)。

### 3.5 用户接口层

```
# Task —— 创作唯一入口（主 Agent + 阶段子 Agent）
POST   /api/task                         # 创建任务
GET    /api/task/{task_id}               # 任务详情（plan / steps / finish）
GET    /api/task/{task_id}/events        # 事件列表（SSE 在同一路径的 /stream）
POST   /api/task/{task_id}/pause         # 暂停
POST   /api/task/{task_id}/resume        # 恢复
POST   /api/task/{task_id}/cancel        # 取消

# Pipeline —— 老 6 阶段管线已下线，仅保留历史记录 / 诊断端点
GET    /api/pipeline/runs                # 历史运行记录
GET    /api/pipeline/trace/{id}          # 历史 trace
GET    /api/pipeline/result/{id}         # 历史结果
POST   /api/pipeline/run-async           # 410 Gone
POST   /api/pipeline/retry/{id}/{agent}  # 410 Gone：改用主 Agent 的 delegate_agent(stage=…)

# 声音克隆与 TTS
POST   /api/voice/upload                 # 上传音频 → data_uri
POST   /api/voice/clone                  # 克隆音色
GET    /api/voice/list                   # 列出已克隆音色
DELETE /api/voice/{db_id}                # 删除音色
POST   /api/voice/synthesize             # 文字 → 语音
POST   /api/voice/dub                    # 文案切分 + 逐段配音
```

> ⚠ 对账注记（2026-08）：/api/pipeline/tasks、/stats、/llm-usage 三端点未实现，
> 已并入上方 Pipeline 清单标注；/api/template/batch/{id}（下方）亦未实现。
>
> # Render
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
模板变量 → VideoTemplate.render() → 任务请求（/api/task）
                                        ↓
                          主 Agent（任务内核）规划 → 工具/技能
                                        ↓
                          delegate_agent(stage=…) 派发阶段子 Agent
                                        ↓
                          黑板 + 产物（artifacts）回传主 Agent
                                        ↓
                          quality 复查 / 失败回流主 Agent 决策
                                        ↓
                          RenderService（多轨渲染）
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
