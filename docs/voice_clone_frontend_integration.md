# 声音克隆与 TTS 配音 — 前端集成指南

> 本文档供前端开发者使用，说明如何调用声音克隆/TTS API，以及 auto-dub 自动配音的前端配合方式。

---

## API 端点概览

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/voice/upload` | POST | 上传音频 → data_uri（base64） |
| `/api/voice/clone` | POST | 克隆音色并持久化 |
| `/api/voice/list` | GET | 列出所有已克隆音色 |
| `/api/voice/{db_id}` | DELETE | 删除音色 |
| `/api/voice/synthesize` | POST | 文字 → 语音 |
| `/api/voice/dub` | POST | 文案切分 + 逐段配音 |
| `/voice_audio/{filename}` | GET | 静态音频文件访问 |

Base URL: `http://localhost:8000`

---

## 1. 上传音频样本

上传一段人声音频（5-60秒，纯人声，无背景音乐），获取 `data_uri` 供克隆使用。

```bash
curl -X POST http://localhost:8000/api/voice/upload \
  -F "file=@sample.wav"
```

响应：
```json
{
  "filename": "sample.wav",
  "saved_as": "PluginData/uploads/a1b2c3d4e5f6.wav",
  "size": 245760,
  "data_uri": "data:audio/wav;base64,UklGRi...",
  "mime": "audio/wav"
}
```

> 前端可直接将 `data_uri` 传给 `/clone`，无需手动 base64 编码。

---

## 2. 克隆音色

三种音频来源方式（三选一）：

| 参数 | 说明 | 适用场景 |
|------|------|----------|
| `data_uri` | base64 data URI（来自 upload） | 前端上传的文件 |
| `audio_url` | 公网可访问的 URL | 已有公网音频链接 |
| `audio_path` | 本地文件路径 | 服务端已有文件 |

### 提供者选择

| 提供者 | clone 要求 | 特点 |
|--------|-----------|------|
| `qwen_tts`（默认） | 支持 data URI，无需公网 URL | 推荐，最简单 |
| `cosyvoice` | 需公网 URL | 自动上传本地文件到公网 |
| `minimax` | 需公网 URL + audition_text | 自动上传，需额外提供试听文案 |

```bash
# 使用 data_uri 克隆（推荐）
curl -X POST http://localhost:8000/api/voice/clone \
  -H "Content-Type: application/json" \
  -d '{
    "voice_name": "我的音色",
    "data_uri": "data:audio/wav;base64,...",
    "provider": "qwen_tts"
  }'
```

响应：
```json
{
  "id": "v_abc123",
  "voice_id": "qwen_xxx",
  "voice_name": "我的音色",
  "provider": "qwen_tts",
  "target_model": "qwen3-tts-vc-2026-01-22",
  "created_at": "2026-07-21T10:00:00"
}
```

---

## 3. 管理音色

```bash
# 列出所有音色
curl http://localhost:8000/api/voice/list

# 删除音色
curl -X DELETE http://localhost:8000/api/voice/v_abc123
```

---

## 4. 合成语音

### 单段合成（synthesize）

```bash
curl -X POST http://localhost:8000/api/voice/synthesize \
  -H "Content-Type: application/json" \
  -d '{
    "voice_id": "v_abc123",
    "text": "你好，这是一段测试语音。",
    "provider": "qwen_tts"
  }'
```

响应：
```json
{
  "audio_path": "PluginData/tts_output/xxx.mp3",
  "audio_url": "/voice_audio/xxx.mp3",
  "duration_sec": 3.2,
  "voice_id": "v_abc123",
  "provider": "qwen_tts",
  "text": "你好，这是一段测试语音。"
}
```

### 逐段配音（dub）

将长文案按句号/换行切分，逐段合成，返回分段结果。

```bash
curl -X POST http://localhost:8000/api/voice/dub \
  -H "Content-Type: application/json" \
  -d '{
    "voice_id": "v_abc123",
    "text": "第一段内容。第二段内容。第三段内容。",
    "split_mode": "sentence"
  }'
```

响应：
```json
{
  "segments": [
    {"index": 0, "text": "第一段内容。", "audio_path": "...", "audio_url": "/voice_audio/seg0.mp3", "duration_sec": 2.1},
    {"index": 1, "text": "第二段内容。", "audio_path": "...", "audio_url": "/voice_audio/seg1.mp3", "duration_sec": 1.8},
    {"index": 2, "text": "第三段内容。", "audio_path": "...", "audio_url": "/voice_audio/seg2.mp3", "duration_sec": 2.0}
  ],
  "total": 3,
  "total_duration_sec": 5.9
}
```

---

## 5. Auto-Dub 自动配音（管线集成）

当运行视频管线时，AudioAgent 可自动触发配音，无需前端手动调用 TTS API。

### 触发条件

同时满足以下全部条件时，AudioAgent 自动调用 `dub_script` 技能：

1. `extra_params.video_mode` = `"voiceover"`
2. `extra_params.auto_dub` = `true`（默认值）
3. `audio_config.voice_id` 非空（来自 persona 的 `audio.voice_clone_model_id` 或 `extra_params.voice_id`）
4. `extra_params.script_text` 非空且非纯空白
5. 时间线中不存在 `metadata.narration=True` 的 clip（避免重复配音）

### 管线请求示例

```json
{
  "persona_id": "my_persona",
  "category_plugin_id": "knowledge_longform",
  "topic": "我的主题",
  "extra_params": {
    "video_mode": "voiceover",
    "script_text": "今天我们来聊一个有趣的话题。首先，让我们看看数据。结论是...",
    "voice_id": "v_abc123",
    "auto_dub": true
  }
}
```

### 前端配合

1. **音色选择面板**：用户在 Persona 配置或 Pipeline 参数中选择已克隆的音色（`voice_id`）
2. **文案输入**：用户提供配音文案（`script_text`）
3. **开关控制**：提供 `auto_dub` 开关，允许用户关闭自动配音
4. **进度展示**：管线运行时，AudioAgent 阶段会产生 `自动配音: N 段旁白` 笔记，可通过 SSE 追踪展示

### 配音失败降级

配音失败**不会**阻断管线：
- 失败信息写入 `audio_notes`（如 `"自动配音失败: provider timeout"`）
- 时间线保持不变（无旁白轨）
- Pipeline 状态仍为 `COMPLETED`（非 `FAILED`）
- 前端可展示降级提示

### 音频文件访问

合成的音频文件存储在 `PluginData/tts_output/`，通过 `/voice_audio/{filename}` 静态挂载访问。

---

## 6. 错误处理

| HTTP 状态码 | 含义 | 前端动作 |
|-------------|------|----------|
| 200 | 成功 | 展示结果 |
| 400 | 参数错误 / 服务不可用 / API Key 未配置 | 展示错误详情（`detail` 字段） |
| 404 | 音色不存在（DELETE 时） | 提示音色已被删除 |

常见 400 错误：
- `"未配置 TTS API Key"` → 需要在 `.env` 中设置 `CLIPWRIGHT_TTS_DASHSCOPE_API_KEY`
- `"所有公网上传服务均不可用"` → CosyVoice/MiniMax 需要公网 URL，检查 `CLIPWRIGHT_TTS_PUBLIC_UPLOAD_SERVICES` 配置
- `"音色绑定的是实时模型"` → 音色是用实时模型克隆的，不支持 HTTP 合成，需重新克隆
