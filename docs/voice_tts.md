# 语音与 TTS 系统

## 概述

语音系统提供声音克隆、语音合成（TTS）、配音切分等能力，支撑 AudioAgent 在管线中的音频处理需求。

## 支持的 Provider

| Provider | 说明 | 依赖 |
|----------|------|------|
| Qwen-TTS | 通义千问语音合成 | `dashscope>=1.22.0` |
| CosyVoice | 阿里云 CosyVoice | `dashscope`, 需 Workspace ID |
| MiniMax | MiniMax 语音服务 | `dashscope` |

## 配置

```bash
# DashScope API Key（Qwen-TTS / CosyVoice / MiniMax 共用）
CLIPWRIGHT_TTS_DASHSCOPE_API_KEY=sk-...

# CosyVoice 所需的 Workspace ID
CLIPWRIGHT_TTS_WORKSPACE_ID=your_workspace_id

# 默认 TTS 提供者
CLIPWRIGHT_TTS_DEFAULT_PROVIDER=qwen_tts

# 公网上传服务（CosyVoice / MiniMax 克隆需要公网 URL）
CLIPWRIGHT_TTS_PUBLIC_UPLOAD_SERVICES=uguu,catbox
```

## API 端点

### 上传音频

`POST /api/voice/upload` — 上传音频文件，返回 base64 data_uri 供克隆使用。

### 克隆音色

`POST /api/voice/clone` — 克隆音色并持久化元数据。支持 `audio_path`（本地）、`audio_url`（公网）、`data_uri`（base64）三种输入。

### 音色管理

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/voice/list` | GET | 列出所有已克隆音色 |
| `/api/voice/{db_id}` | DELETE | 删除指定音色记录 |

### 语音合成

`POST /api/voice/synthesize` — 使用已克隆音色将文字合成为语音。

### 配音

`POST /api/voice/dub` — 文案切分 + 逐段配音，返回分段音频片段列表。

### 静态音频访问

合成的音频文件通过 `/voice_audio/{filename}` 静态挂载访问。

## Dub Script Skill

`dub_script` skill 提供文案切分与逐段配音能力，委托 VoiceService 执行。可用于自动化配音流程。

## 前端集成

### 音频上传与克隆

1. 用户通过 `/api/voice/upload` 上传参考音频（5-60 秒纯人声，无背景音乐）
2. 前端将返回的 `data_uri` 传给 `/api/voice/clone` 创建音色
3. 用户选择已克隆音色，调用 `/api/voice/synthesize` 或 `/api/voice/dub` 生成配音

三种音频来源方式（三选一）：
| 参数 | 说明 | 适用场景 |
|------|------|----------|
| `data_uri` | base64 data URI（来自 upload） | 前端上传的文件 |
| `audio_url` | 公网可访问的 URL | 已有公网音频链接 |
| `audio_path` | 本地文件路径 | 服务端已有文件 |

合成的音频文件通过 `/voice_audio/{filename}` 静态挂载访问。

### Auto-Dub 自动配音（管线集成）

当运行视频管线时，AudioAgent 可自动触发配音，无需前端手动调用 TTS API。

**触发条件**（需全部满足）：
1. `extra_params.video_mode` = `"voiceover"`
2. `extra_params.auto_dub` = `true`（默认值）
3. `audio_config.voice_id` 非空
4. `extra_params.script_text` 非空且非纯空白
5. 时间线中不存在 `metadata.narration=True` 的 clip（避免重复配音）

**前端配合**：
- 音色选择面板：用户在 Persona 配置或 Pipeline 参数中选择已克隆的音色
- 文案输入：用户提供配音文案（`script_text`）
- 开关控制：提供 `auto_dub` 开关，允许用户关闭自动配音
- 进度展示：管线运行时，AudioAgent 阶段产生 `自动配音: N 段旁白` 笔记，可通过 SSE 追踪

**失败降级**：配音失败不会阻断管线，失败信息写入 `audio_notes`，Pipeline 状态仍为 `COMPLETED`。

## 相关文档

- [API 参考](api_reference.md) — 完整 API 端点说明
- [快速开始](quickstart.md) — TTS 配置步骤
- [开发指南](development.md) — 新增 Provider
