# 帧艺 ClipWright 内容视频编排引擎 — API 参考

## 基础信息

- **Base URL**: `http://localhost:8000`
- **格式**: 全部请求/响应均为 JSON
- **交互式文档**: `http://localhost:8000/docs`

---

## Timeline 数据模型

Pipeline 输出的核心数据结构。前端编辑器与后端 Agent 共享此 schema（见 `clipwright/schema/timeline.py`）。

### Clip 字段

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `id` | string | — | 全局唯一 ID |
| `kind` | ClipKind | — | video / audio / text / image / caption / shape / waveform / animation |
| `asset_id` | string | — | 引用的素材 ID 或媒体路径 |
| `track_id` | string | — | 所属轨道 ID |
| `start_sec` | float ≥0 | — | 时间轴起始时间（秒） |
| `duration_sec` | float >0 | — | 持续时长（秒） |
| `source_offset_sec` | float ≥0 | 0 | 素材内起始偏移 |
| `speed` | float >0 | 1.0 | 播放速度倍率 |
| `volume` | float ≥0 | 1.0 | 音量 0-1 |
| `opacity` | float 0-1 | 1.0 | 不透明度 |
| `blend_mode` | string? | null | 混合模式 (normal/multiply/screen/overlay 等) |
| `enabled` | bool | true | 是否启用（禁用的片段不参与渲染） |
| `label_color` | string? | null | 标签颜色 hex（如 #4F8CFF） |
| `notes` | string? | null | 片段备注 |
| `eq_preset` | string? | null | 音频 EQ 预设名称 |
| `fx_brightness` | float 0-2? | null | 亮度（1=默认） |
| `fx_contrast` | float 0-2? | null | 对比度（1=默认） |
| `fx_saturation` | float 0-2? | null | 饱和度（1=默认） |
| `fx_blur` | float 0-10? | null | 模糊半径 px |
| `fx_hue` | float 0-360? | null | 色相旋转角度 |
| `image_fit` | ImageFit? | null | 画面适配 (cover/contain/fill) |
| `image_rect` | dict? | null | 归一化矩形 {x,y,w,h} |
| `text` | string? | null | 文字内容 |
| `font` / `font_size` / `font_color` / `text_align` | — | null | 文字样式 |
| `transition_in` / `transition_out` / `transition_duration_sec` | — | null | 转场 |
| `keyframes` | list[dict] | [] | 关键帧动画序列 |
| `metadata` | dict | {} | 扩展元数据 |

### 字幕样式字段 (Caption Style Fields)

`caption` 片段额外支持以下样式字段（均为可选；颜色字段接受 `#RRGGBB` 或 `#RRGGBBAA`）：

| 字段 | 类型 | 说明 |
|------|------|------|
| `font_weight` | string? | 字重，`normal` / `bold` |
| `font_italic` | bool? | 是否斜体 |
| `letter_spacing` | float? | 字间距（px） |
| `stroke_width` | float ≥0? | 描边宽度（px） |
| `stroke_color` | string? | 描边颜色（hex） |
| `shadow_x` | float? | 阴影水平偏移（px） |
| `shadow_y` | float? | 阴影垂直偏移（px） |
| `shadow_color` | string? | 阴影颜色（hex） |
| `shadow_blur` | float ≥0? | 阴影模糊半径（px） |
| `glow_color` | string? | 发光颜色（hex） |
| `glow_width` | float ≥0? | 发光宽度（px） |

> **注意**: Clip 模型设置 `extra="allow"`，前端自定义字段在 pipeline 合并时不会被丢弃。

---

## 健康检查

### `GET /health`

```json
{"status": "ok", "service": "clipwright-engine"}
```

---

## Pipeline

| 端点 | 说明 |
|------|------|
| `POST /api/pipeline/run` | 全流程执行（同步），返回 `PipelineState` |
| `POST /api/pipeline/run-v2` | 动态路由 + 并行执行 + 自愈循环 |
| `POST /api/pipeline/run-async` | 异步启动管线，后台执行，通过 SSE 实时追踪 |
| `GET /api/pipeline/runs?limit=50` | 列出最近的管线运行记录（按时间倒序） |
| `GET /api/pipeline/status/{pipeline_id}` | 查询管线状态 |
| `GET /api/pipeline/result/{pipeline_id}` | 获取异步执行结果（含时间线 JSON） |
| `GET /api/pipeline/trace/{pipeline_id}` | 获取管线事件轨迹 |
| `GET /api/pipeline/trace/stream/{pipeline_id}` | SSE 流：实时追踪管线执行事件 |
| `POST /api/pipeline/retry/{pipeline_id}/{agent_name}` | 从失败的 Agent 重试（B3：重放前置成功结果，仅重跑目标 + 下游） |
| `POST /api/pipeline/step/{agent_name}` | 单 Agent 执行（deprecated，见 B5） |
| `POST /api/pipeline/predict-script` | 分析文稿并推荐配置（请求体：`PredictScriptRequest`）。返回字数、句数、估算时长、推荐 Persona、推荐类型插件、情绪基调、关键主题 |
| `POST /api/pipeline/predict-material` | 分析素材文件并推荐使用方式（请求体：`PredictMaterialRequest`）。返回时长、分辨率、方向、是否有视频/音频、用途建议 |

### `GET /api/pipeline/runs`

列出最近管线运行记录（新增端点，供前端 Pipeline 管理页使用）。

```json
[
  {
    "pipeline_id": "pl_xxx",
    "status": "completed",
    "agent_name": "EditAgent",
    "started_at": "2026-08-01T10:00:00Z",
    "finished_at": "2026-08-01T10:00:10Z",
    "summary": "…"
  }
]
```

---

## 渲染

| 端点 | 说明 |
|------|------|
| `POST /api/render/queue` | 加入渲染队列，立即返回 `task_id` |
| `GET /api/render/queue` | 列出队列任务 |
| `GET /api/render/queue/{task_id}` | 查询队列任务状态 |
| `GET /api/render/queue/stream/{task_id}` | SSE 流：实时推送渲染进度（裁剪/拼接/文字/音频各阶段进度百分比） |
| `POST /api/render/start` | 渲染时间线为 MP4（同步） |
| `GET /api/render/status/{render_id}` | 查询渲染进度（优先队列状态，回退到文件检测）。404 表示任务/文件不存在 |
| `GET /api/render/presets` | 列出所有导出预设 |
| `GET /api/render/download/{filename}` | 下载渲染完成的 MP4 |
| `GET /api/render/thumbnail?path=...&time_sec=0.5` | 提取视频帧缩略图 |
| `GET /api/render/video?path=...` | 代理视频文件（前端预览用） |

### `POST /api/render/start`

**请求体**:
```json
{
  "timeline": { "tracks": [...], "duration_sec": 600, ... },
  "output_path": "renders/output.mp4",
  "audio_file_path": "/path/to/voiceover.wav",
  "bgm_file_path": "/path/to/bgm.mp3",
  "settings": {
    "preset": "bilibili",
    "width": 1920, "height": 1080, "fps": 30, "bitrate": "5M"
  }
}
```

**预设**: `bilibili` `youtube` `tiktok` `weibo` `1080p` `720p` `480p`

### `GET /api/render/queue/stream/{task_id}`

**事件格式**:
```json
{"type": "progress", "task_id": "...", "status": "rendering", "progress": 45,
 "phase": "trim", "detail": "裁剪 3/6"}
```

### `GET /api/render/status/{render_id}`

```json
{"render_id": "...", "status": "completed", "progress": 100}
```

---

## 项目管理

| 端点 | 说明 |
|------|------|
| `POST /api/project` | 创建项目 |
| `GET /api/project` | 列出项目 |
| `POST /api/project/folders/rename` | 重命名文件夹 |
| `POST /api/project/folders/delete` | 删除文件夹 |
| `GET /api/project/{project_id}` | 获取项目 |
| `PUT /api/project/{project_id}` | 更新项目 |
| `DELETE /api/project/{project_id}` | 删除项目 |
| `POST /api/project/{project_id}/duplicate` | 复制项目 |
| `PATCH /api/project/{project_id}/rename` | 重命名项目 |
| `PATCH /api/project/{project_id}/folder` | 移动项目到文件夹 |
| `POST /api/project/{project_id}/tags` | 添加项目标签 |
| `DELETE /api/project/{project_id}/tags/{tag}` | 删除项目标签 |
| `GET /api/project/{project_id}/thumbnail` | 获取项目缩略图 |

---

## Persona

| 端点 | 说明 |
|------|------|
| `GET /api/persona/list` | 列出 Persona |
| `POST /api/persona/create` | 创建 Persona |
| `GET /api/persona/{persona_id}` | 获取 Persona |
| `PUT /api/persona/{persona_id}` | 更新 Persona |
| `DELETE /api/persona/{persona_id}` | 删除 Persona |
| `GET /api/persona/{persona_id}/prompt` | 获取 Persona 提示词 |
| `PUT /api/persona/{persona_id}/prompt` | 更新 Persona 提示词 |
| `GET /api/persona/{persona_id}/knowledge` | 获取 Persona 知识 |
| `POST /api/persona/{persona_id}/knowledge` | 添加 Persona 知识 |

### PersonaForge

| 端点 | 说明 |
|------|------|
| `POST /api/persona/forge/from-prompt` | 自然语言 → Persona |
| `POST /api/persona/forge/from-script` | 脚本分析 → Persona |
| `POST /api/persona/forge/refine` | 迭代优化 Persona |
| `POST /api/persona/forge/dialogue/generate-questions` | 对话引导：生成问题 |
| `POST /api/persona/forge/dialogue/build` | 对话引导：生成 Persona |

### Chat Forge

| 端点 | 说明 |
|------|------|
| `POST /api/persona/forge/chat/start` | 开始对话会话 |
| `POST /api/persona/forge/chat/message` | 发送消息 |
| `POST /api/persona/forge/chat/knowledge` | 注入知识 |
| `POST /api/persona/forge/chat/commit` | 提交并生成 `PersonaManifest` |
| `GET /api/persona/forge/chat/state/{session_id}` | 获取会话状态 |

### RAG 知识库

| 端点 | 说明 |
|------|------|
| `POST /api/persona/{persona_id}/rag/query` | 语义检索 |
| `POST /api/persona/{persona_id}/rag/index` | 建立向量索引 |
| `GET /api/persona/{persona_id}/rag/status` | 查询 RAG 状态 |
| `DELETE /api/persona/{persona_id}/rag/index` | 删除索引 |

---

## 类型制作器

| 端点 | 说明 |
|------|------|
| `GET /api/type-maker/list` | 列出视频类型 |
| `POST /api/type-maker/create` | 创建视频类型 |
| `GET /api/type-maker/{type_id}` | 获取类型配置 |
| `PUT /api/type-maker/{type_id}` | 更新类型 |
| `DELETE /api/type-maker/{type_id}` | 删除类型 |
| `POST /api/type-maker/preview` | 类型预览 |

---

## 模板

| 端点 | 说明 |
|------|------|
| `GET /api/template/list` | 列出模板 |
| `POST /api/template/create` | 创建模板 |
| `GET /api/template/{template_id}` | 获取模板 |
| `PUT /api/template/{template_id}` | 更新模板 |
| `DELETE /api/template/{template_id}` | 删除模板 |
| `POST /api/template/{template_id}/apply` | 应用模板 |

---

## 素材库

| 端点 | 说明 |
|------|------|
| `GET /api/material/sources` | 列出素材源 |
| `POST /api/material/search` | 跨源搜索素材 |
| `GET /api/material/asset/{source_id}/{asset_id}` | 获取素材详情 |

### 素材管理 (/api/asset)

| 端点 | 说明 |
|------|------|
| `POST /api/asset/upload` | 上传素材 |
| `POST /api/asset/import-path` | 按路径导入素材 |
| `POST /api/asset/import-url` | 按 URL 导入素材 |
| `GET /api/asset/list` | 列出素材 |
| `GET /api/asset/{asset_id}` | 获取素材 |
| `GET /api/asset/{asset_id}/file` | 获取素材文件 |
| `GET /api/asset/{asset_id}/thumbnail` | 获取素材缩略图 |
| `DELETE /api/asset/{asset_id}` | 删除素材 |

---

## 素材预处理

| 端点 | 说明 |
|------|------|
| `GET /api/preprocess/operations` | 列出预处理操作 |
| `POST /api/preprocess/submit` | 提交预处理任务 |
| `POST /api/preprocess/batch-submit` | 批量提交预处理任务 |
| `GET /api/preprocess/queue` | 列出预处理队列 |
| `GET /api/preprocess/task/{task_id}` | 查询任务状态 |
| `DELETE /api/preprocess/task/{task_id}` | 取消/删除任务 |
| `GET /api/preprocess/task/{task_id}/results` | 获取任务结果 |

预处理步骤: 转码代理 → 场景检测 → 音频提取 → 内容分析 (上传时自动触发)

---

## Webhook

| 端点 | 说明 |
|------|------|
| `GET /api/webhook/events` | 列出支持的事件类型 |
| `GET /api/webhook/list` | 列出订阅 |
| `POST /api/webhook/register` | 注册订阅 |
| `DELETE /api/webhook/{webhook_id}` | 删除订阅 |
| `PUT /api/webhook/{webhook_id}/toggle` | 启用/禁用订阅 |
| `POST /api/webhook/{webhook_id}/test` | 测试通知 |
| `POST /api/webhook/notify` | 手动触发通知 |
| `GET /api/webhook/deliveries` | 投递记录 |

支持事件: `pipeline` `render`

---

## Tool / Skill

| 端点 | 说明 |
|------|------|
| `GET /api/tool/list` | 列出所有 Tool |
| `POST /api/tool/execute` | 执行 Tool |
| `POST /api/tool/batch` | 批量执行 |
| `GET /api/skill/list` | 列出所有 Skill |
| `POST /api/skill/execute` | 执行 Skill |

---

## 需求分析 (Requirements Agent)

| 端点 | 说明 |
|------|------|
| `POST /api/requirements/init` | 初始化需求分析会话（创意简报） |
| `POST /api/requirements/chat` | 发送需求消息 |
| `POST /api/requirements/chat/stream/{session_id}` | SSE 流式对话 |
| `POST /api/requirements/upload/{session_id}` | 上传参考文件 |
| `GET /api/requirements/session/{session_id}` | 获取会话状态 |
| `GET /api/requirements/plan/{session_id}` | 获取制作规划书 |
| `POST /api/requirements/proceed` | 确认规划书并启动管线 |

---

## 视频编辑器 (/api/video-editor)

| 端点 | 说明 |
|------|------|
| `GET /api/video-editor/status` | 编辑器服务状态 |
| `GET /api/video-editor/projects` | 列出编辑器项目 |
| `POST /api/video-editor/projects/create` | 创建项目 |
| `GET /api/video-editor/projects/{project_id}` | 获取项目 |
| `PUT /api/video-editor/projects/{project_id}` | 更新项目 |
| `DELETE /api/video-editor/projects/{project_id}` | 删除项目 |
| `POST /api/video-editor/projects/{project_id}/undo` | 撤销 |
| `POST /api/video-editor/projects/{project_id}/redo` | 重做 |
| `POST /api/video-editor/projects/{project_id}/clips/add` | 添加片段 |
| `POST /api/video-editor/projects/{project_id}/clips/remove` | 移除片段 |
| `POST /api/video-editor/projects/{project_id}/clips/move` | 移动片段 |
| `POST /api/video-editor/projects/{project_id}/clips/split` | 分割片段 |
| `POST /api/video-editor/projects/{project_id}/export` | 导出项目 |

---

## 字幕与语音转文字

| 端点 | 说明 |
|------|------|
| `POST /api/subtitle/import` | 导入字幕 |
| `POST /api/subtitle/export` | 导出字幕 |
| `POST /api/subtitle/transcribe` | 字幕转写 |
| `POST /api/subtitle/align` | 字幕对齐 |
| `POST /api/stt/transcribe` | 语音转文字（带时间戳） |
| `POST /api/stt/align` | 文案→音频对齐 |

---

## 声音克隆与 TTS（/api/voice）

| 端点 | 说明 |
|------|------|
| `POST /api/voice/upload` | 上传音频文件，返回 `data_uri`（base64）供克隆使用 |
| `POST /api/voice/clone` | 克隆音色并持久化元数据。支持 `audio_path`（本地）、`audio_url`（公网）、`data_uri`（base64） |
| `GET /api/voice/list` | 列出所有已克隆音色 |
| `DELETE /api/voice/{db_id}` | 删除指定音色记录 |
| `POST /api/voice/synthesize` | 用已克隆音色合成语音 |
| `POST /api/voice/dub` | 文案切分 + 逐段配音 |

### `POST /api/voice/upload`

```bash
curl -X POST http://localhost:8000/api/voice/upload \
  -F "file=@sample.wav"
```

响应：
```json
{"filename": "sample.wav", "saved_as": "PluginData/uploads/xxx.wav", "size": 123456, "data_uri": "data:audio/wav;base64,...", "mime": "audio/wav"}
```

### `POST /api/voice/clone`

```bash
curl -X POST http://localhost:8000/api/voice/clone \
  -H "Content-Type: application/json" \
  -d '{"voice_name": "我的音色", "audio_url": "https://example.com/sample.wav", "provider": "qwen_tts"}'
```

### `POST /api/voice/synthesize`

```bash
curl -X POST http://localhost:8000/api/voice/synthesize \
  -H "Content-Type: application/json" \
  -d '{"voice_id": "v_xxx", "text": "你好世界"}'
```

响应：
```json
{"audio_path": "...", "duration_sec": 2.5, "voice_id": "v_xxx", "provider": "qwen_tts", "text": "你好世界"}
```

### `POST /api/voice/dub`

```bash
curl -X POST http://localhost:8000/api/voice/dub \
  -H "Content-Type: application/json" \
  -d '{"voice_id": "v_xxx", "text": "第一段文案。第二段文案。", "split_mode": "sentence"}'
```

响应：
```json
{"segments": [{"audio_path": "...", "duration_sec": 2.0, "text": "第一段文案。"}], "total": 2, "total_duration_sec": 4.0}
```

### 静态音频文件

合成的音频文件通过 `/voice_audio/{filename}` 静态挂载访问。

---

## 动画

| 端点 | 说明 |
|------|------|
| `GET /api/animation/list` | 列出所有动画定义 |
| `GET /api/animation/onscreen` | 列出屏幕动画 |
| `GET /api/animation/transitions` | 列出转场动画 |
| `GET /api/animation/get/{animation_id}` | 获取动画详情 |

---

## EDL / FCPXML

| 端点 | 说明 |
|------|------|
| `POST /api/edl/import/edl` | 导入 EDL |
| `POST /api/edl/import/fcpxml` | 导入 FCPXML |
| `POST /api/edl/export/edl` | 导出 EDL |
| `POST /api/edl/export/fcpxml` | 导出 FCPXML |

---

## 字体

| 端点 | 说明 |
|------|------|
| `GET /api/fonts/list` | 列出可用字体 |
| `GET /api/fonts/default` | 获取默认字体 |
| `GET /api/fonts/resolve` | 字体解析 |
| `POST /api/fonts/clear-cache` | 清空字体缓存 |

---

## 插件系统

| 端点 | 说明 |
|------|------|
| `GET /api/plugin/list` | 列出已加载插件 |
| `GET /api/plugin/discover` | 发现可用插件 |
| `GET /api/plugin/capabilities` | 系统能力概览 |
| `POST /api/plugin/load-all` | 加载全部插件 |
| `POST /api/plugin/load/{plugin_id}` | 加载插件 |
| `POST /api/plugin/unload/{plugin_id}` | 卸载插件 |
| `GET /api/plugin/{plugin_id}/config` | 获取插件配置 |
| `PUT /api/plugin/{plugin_id}/config` | 更新插件配置 |
| `DELETE /api/plugin/{plugin_id}/config` | 删除插件配置 |
| `GET /api/plugin/{plugin_id}/ui` | 获取插件 UI 定义 |

---

## 模型测试 (/api/test)

| 端点 | 说明 |
|------|------|
| `POST /api/test/llm` | LLM 接口测试 |
| `POST /api/test/embed` | Embedding 测试 |
| `POST /api/test/rerank` | Rerank 测试 |
| `GET /api/test/config` | 模型配置 |

---

## 其他

| 端点 | 说明 |
|------|------|
| `POST /api/proxy/generate` | 代理生成 |
| `POST /api/proxy/switch` | 切换代理 |
| `POST /api/vision/analyze` | 视频/图像分析 |
| `POST /api/vision/import` | 视觉导入 |
| `POST /api/waveform/generate` | 生成音频波形 |

---

## 学习与微调 (/api/learning)

| 端点 | 说明 |
|------|------|
| `GET /api/learning/status` | 学习系统状态 |
| `GET /api/learning/datasets` | 列出数据集 |
| `POST /api/learning/datasets/create` | 创建数据集 |
| `DELETE /api/learning/datasets/{dataset_id}` | 删除数据集 |
| `GET /api/learning/jobs` | 列出训练任务 |
| `GET /api/learning/jobs/{job_id}` | 获取训练任务 |
| `POST /api/learning/jobs/create` | 创建训练任务 |
| `POST /api/learning/jobs/{job_id}/start` | 启动训练 |
| `POST /api/learning/jobs/{job_id}/cancel` | 取消训练 |
| `DELETE /api/learning/jobs/{job_id}` | 删除训练任务 |
| `GET /api/learning/models` | 列出模型 |

---

## 完整 Tool 列表 (44 个)

| Tool | 功能 | 依赖 |
|------|------|------|
| `video_trim` | 视频裁剪 | ffmpeg |
| `video_concat` | 视频拼接 | ffmpeg |
| `video_overlay` | 视频叠加 | ffmpeg |
| `video_download` | 下载远程素材 | ffmpeg |
| `video_crop` | 裁切比例 | ffmpeg |
| `video_thumbnail` | 封面缩略图 | ffmpeg |
| `video_speed` | 变速播放 | ffmpeg |
| `video_blur` | 模糊效果 | ffmpeg |
| `video_filter` | 调色/亮度/裁切 | ffmpeg |
| `media_probe` | 媒体信息探测 | ffprobe |
| `audio_extract` | 音频提取 | ffmpeg |
| `audio_normalize` | 音量标准化 | ffmpeg |
| `audio_mix` | 多轨混音 | ffmpeg |
| `audio_replace` | 音频替换 | ffmpeg |
| `bpm_detect` | BPM 检测 | ffmpeg |
| `scene_detect` | 场景检测 | ffmpeg |
| `semantic_match` | 语义匹配 | - |
| `vision_llm` | 视觉 LLM 接口 | - |
| `face_detect` | 人脸检测 | ffmpeg |
| `background_remove` | 背景去除/替换 | ffmpeg |
| `effect_vignette` | 电影效果 | ffmpeg |
| `watermark` | 水印叠加 | ffmpeg |
| `chroma_key` | 绿幕抠像 | ffmpeg |
| `video_stabilize` | 视频稳定 | ffmpeg |
| `generate_text_video` | 文字转视频 | ffmpeg |
| `subtitle_burn` | 字幕烧录 | ffmpeg |
| `text_design` | 文字样式设计 | - |
| `typewriter_animation` | 打字机动画 | ffmpeg |
| `tracking_text` | 追踪文字 | manim |
| `text_diagram` | 文字图表生成 | - |
| `material_filter` | 素材筛选 | - |
| `frame_validator` | 帧验证 | ffmpeg/ffprobe |
| `black_frame_detect` | 黑帧检测 | ffmpeg/ffprobe |
| `audio_silence_detect` | 静音检测 | ffmpeg |
| `subtitle_overflow` | 字幕溢出检查 | - |
| `speed_ramp` | 速度曲线 | ffmpeg |
| `color_correct` | 色彩校正 | ffmpeg |
| `lut_apply` | LUT 应用 | ffmpeg |
| `transition_apply` | 转场渲染 | ffmpeg |
| `whisper_transcribe` | 语音转文字 | - |
| `text_to_speech` | 文字转语音 | edge-tts |
| `voice_clone` | 音色克隆 | - |
| `list_animations` | 列出动画编目 | - |
| `describe_llm_mg` | LLM 描述 MG 动画 | - |

## 完整 Skill 列表 (4 个)

| Skill | 功能 |
|------|------|
| `analyze_video_structure` | 视频结构分析 |
| `generate_captions` | 字幕生成 |
| `analyze_audio_rhythm` | 音频节奏分析 |
| `dub_script` | 文案切分 + 逐段配音 |
