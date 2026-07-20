# 帧艺 ClipWright 内容视频编排引擎 — API 参考

## 基础信息

- **Base URL**: `http://localhost:8000`
- **格式**: 全部请求/响应均为 JSON
- **交互式文档**: `http://localhost:8000/docs`

---

## 健康检查

### `GET /health`

```json
{"status": "ok", "service": "clipwright-engine"}
```

---

## 智能预判

### `POST /api/pipeline/predict-script?script_text=...`
分析文稿并推荐配置。返回字数、句数、估算时长、推荐 Persona、推荐类型插件、情绪基调、关键主题。

### `POST /api/pipeline/predict-material?file_path=...`
分析素材文件并推荐使用方式。返回时长、分辨率、方向、是否有视频/音频、用途建议。

---

## 自适应 Persona

| 端点 | 说明 |
|------|------|
| `POST /api/learn/persona/{id}/record` | 记录用户编辑操作（亮度/对比度/文字样式/转场/变速） |
| `GET /api/learn/persona/{id}/preferences` | 获取学习到的偏好（镜头时长/转场权重/动画密度/字体） |
| `GET /api/learn/persona/{id}/history` | 获取学习历史 |

---

## 版本管理 (Undo/Redo)

| 端点 | 说明 |
|------|------|
| `POST /api/learn/version/{session}/snapshot` | 创建版本快照 |
| `POST /api/learn/version/{session}/undo` | 撤销 |
| `POST /api/learn/version/{session}/redo` | 重做 |
| `GET /api/learn/version/{session}/list` | 版本列表 |
| `POST /api/learn/version/{session}/goto/{pos}` | 跳转到指定版本 |
| `GET /api/learn/version/{session}/diff` | 对比两个版本的差异 |

---

## Pipeline

### `POST /api/pipeline/run`
全流程执行（固定序列）。

### `POST /api/pipeline/run-v2`
动态路由 + 并行执行 + 自愈循环。

### `POST /api/pipeline/run-async`
异步启动管线，通过 SSE 实时追踪进度。

### `GET /api/pipeline/trace/stream/{pipeline_id}`
SSE 流：实时追踪管线执行事件。

### `GET /api/pipeline/result/{pipeline_id}`
获取异步执行结果。

### `POST /api/pipeline/retry/{pipeline_id}/{agent_name}`
从失败的 Agent 重试。

### `POST /api/pipeline/regenerate-scene/{pipeline_id}/{scene_index}`
局部重新生成指定场景。

### `POST /api/pipeline/step/{agent_name}`
单 Agent 执行。

---

## 渲染

### `POST /api/render/start`
渲染时间线为 MP4。

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

### `POST /api/render/queue`
加入渲染队列，立即返回 `task_id`。

### `GET /api/render/queue/{task_id}`
查询队列任务状态。

### `GET /api/render/queue/stream/{task_id}`
SSE 流：实时推送渲染进度（裁剪/拼接/文字/音频各阶段进度百分比）。

**事件格式**:
```json
{"type": "progress", "task_id": "...", "status": "rendering", "progress": 45,
 "phase": "trim", "detail": "裁剪 3/6"}
```

### `GET /api/render/download/{filename}`
下载渲染完成的 MP4。

### `GET /api/render/thumbnail?path=...&time_sec=0.5`
提取视频帧缩略图。

### `GET /api/render/video?path=...`
代理视频文件（前端预览用）。

### `GET /api/render/presets`
列出所有导出预设。

### `GET /api/render/status/{render_id}`
查询渲染进度（优先队列状态，回退到文件检测）。404 表示任务/文件不存在。

```json
{"render_id": "...", "status": "completed", "progress": 100}
```

---

## 项目

| 端点 | 说明 |
|------|------|
| `POST /api/project/save` | 保存项目 |
| `GET /api/project/load/{id}` | 加载项目 |
| `GET /api/project/list` | 列出项目 |
| `DELETE /api/project/delete/{id}` | 删除项目 |

---

## 类型制作器

| 端点 | 说明 |
|------|------|
| `POST /api/type-maker/create` | 创建视频类型 |
| `PUT /api/type-maker/update/{id}` | 更新类型 |
| `GET /api/type-maker/list` | 列出类型 |
| `GET /api/type-maker/get/{id}` | 获取类型配置 |
| `DELETE /api/type-maker/delete/{id}` | 删除类型 |
| `POST /api/type-maker/duplicate/{id}` | 复制类型 |

---

## 模板

| 端点 | 说明 |
|------|------|
| `POST /api/template/create` | 创建模板（支持 `{{变量}}`） |
| `PUT /api/template/update/{id}` | 更新模板 |
| `GET /api/template/list` | 列出模板 |
| `GET /api/template/get/{id}` | 获取模板 |
| `DELETE /api/template/delete/{id}` | 删除模板 |
| `GET /api/template/variables/{id}` | 提取模板变量 |
| `POST /api/template/render/{id}` | 渲染模板 |
| `POST /api/template/batch/{id}` | 批量渲染 |
| `POST /api/template/run/{id}` | 渲染+执行管线 |
| `POST /api/template/intro-outro/create` | 创建片头/片尾 |
| `GET /api/template/intro-outro/list` | 列出片头/片尾 |
| `DELETE /api/template/intro-outro/delete/{id}` | 删除片头/片尾 |

---

## 对话式编辑

| 端点 | 说明 |
|------|------|
| `POST /api/edit/session/create` | 创建编辑会话 |
| `POST /api/edit/session/{id}/chat?message=...` | 发送编辑请求 |
| `GET /api/edit/session/{id}` | 获取会话状态 |
| `GET /api/edit/session/{id}/history` | 获取编辑历史 |
| `GET /api/edit/capabilities` | 列出编辑能力 |

支持的编辑操作: `apply_video_filter` `change_text_style` `change_video_speed` `apply_transition` `add_watermark` `apply_effect` `remove_background` `add_blur` `retime_scene` `change_audio`

---

## 素材预处理

| 端点 | 说明 |
|------|------|
| `POST /api/preprocess/start/{asset_id}` | 触发预处理 |
| `GET /api/preprocess/status/{asset_id}` | 查询预处理状态 |
| `GET /api/preprocess/tasks` | 列出预处理任务 |

预处理步骤: 转码代理 → 场景检测 → 音频提取 → 内容分析 (上传时自动触发)

---

## Webhook

| 端点 | 说明 |
|------|------|
| `POST /api/webhook/subscribe` | 订阅事件通知 |
| `POST /api/webhook/unsubscribe` | 取消订阅 |
| `GET /api/webhook/subscriptions` | 列出订阅 |
| `POST /api/webhook/test/{event_type}` | 测试通知 |

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

## 素材库

| 端点 | 说明 |
|------|------|
| `GET /api/material/sources` | 列出素材源 |
| `POST /api/material/search` | 搜索素材 |
| `POST /api/asset/upload` | 上传素材 |
| `POST /api/asset/upload-batch` | 批量上传 |
| `GET /api/asset/list` | 列出素材 |
| `GET /api/asset/probe?path=...` | 探测媒体信息 |

---

## 完整 Tool 列表 (38 个)

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
| `media_probe` | 媒体信息探测 | ffprobe |
| `audio_extract` | 音频提取 | ffmpeg |
| `audio_normalize` | 音量标准化 | ffmpeg |
| `audio_mix` | 多轨混音 | ffmpeg |
| `audio_replace` | 音频替换 | ffmpeg |
| `bpm_detect` | BPM 检测 | ffmpeg |
| `transition_apply` | 转场渲染 | ffmpeg |
| `scene_detect` | 场景检测 | ffmpeg |
| `semantic_match` | 语义匹配 | - |
| `vision_llm` | 视觉 LLM 接口 | - |
| `face_detect` | 人脸检测 | ffmpeg |
| `background_remove` | 背景去除/替换 | ffmpeg |
| `effect_vignette` | 电影效果 | ffmpeg |
| `watermark` | 水印叠加 | ffmpeg |
| `video_filter` | 调色/亮度/裁切 | ffmpeg |
| `generate_text_video` | 文字转视频 | ffmpeg |
| `subtitle_burn` | 字幕烧录 | ffmpeg |
| `text_design` | 文字样式设计 | - |
| `typewriter_animation` | 打字机动画 | ffmpeg |
| `tracking_text` | 追踪文字 | manim |
| `speed_ramp` | 速度曲线 | ffmpeg |
| `color_correct` | 色彩校正 | ffmpeg |
| `lut_apply` | LUT 应用 | ffmpeg |
| `chroma_key` | 绿幕抠像 | ffmpeg |
| `video_stabilize` | 视频稳定 | ffmpeg |
| `material_filter` | 素材筛选 | - |
| `frame_validator` | 帧验证 | ffmpeg/ffprobe |
| `whisper_transcribe` | 语音转文字 | - |
| `text_to_speech` | 文字转语音 | edge-tts |
| `black_frame_detect` | 黑帧检测 | ffmpeg/ffprobe |
| `audio_silence_detect` | 静音检测 | ffmpeg |
| `subtitle_overflow` | 字幕溢出检查 | - |

## 完整 Skill 列表 (11 个)

| Skill | 功能 |
|------|------|
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

---

## 对话式视频编辑

通过 `POST /api/edit/session/create` 创建会话后，发送自然语言指令修改已生成的视频：

```bash
curl -X POST "http://localhost:8000/api/edit/session/{id}/chat?message=把字幕改成金色粗体加发光效果"
```

返回 LLM 解析的操作和执行结果。

---

## LLM MG 动画生成插件

`llm_mg` 插件提供 LLM 驱动的动态 MG 动画生成能力。

### `POST /api/plugin/llm_mg/generate`

调用 LLM 生成 MG 动画 JSON 并渲染为 HTML。

```bash
curl -X POST "http://localhost:8000/api/plugin/llm_mg/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "description": "产品A和B性能对比，A胜出",
    "text_content": "骁龙8Gen3|天玑9300|骁龙胜出",
    "style_hint": "tech_dark",
    "scene_context": {"title": "性能对比", "keywords": ["CPU", "GPU"]}
  }'
```

响应:
```json
{
  "success": true,
  "html": "<!DOCTYPE html>...",
  "mg_def": {"animation_id": "mg_generated_xxx", "elements": [...]},
  "method": "llm",
  "fallback_template": null,
  "generation_id": "gen_20260720_195227_c334af9c"
}
```

### `POST /api/plugin/llm_mg/save-template`

将生成结果保存为可复用模板。

```bash
curl -X POST "http://localhost:8000/api/plugin/llm_mg/save-template" \
  -H "Content-Type: application/json" \
  -d '{"generation_id": "gen_20260720_195227_c334af9c", "custom_name": "骁龙对比动画"}'
```

### `GET /api/plugin/llm_mg/templates`

列出所有可用 MG 模板。

### `GET /api/plugin/llm_mg/generations`

列出未保存的生成记录。
