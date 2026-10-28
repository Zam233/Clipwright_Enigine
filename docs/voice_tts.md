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

**字幕烧录（caption burn-in）**：`audio_config.subtitle_enabled`（默认 `True`）控制是否将旁白分段烧录为字幕。为 `True` 时，AudioAgent 依据旁白分段在文字轨生成 `kind=caption` 的字幕 clip（与旁白音频 clip 对齐，`text` 截断至 100 字符，轨道名「字幕轨」）；为 `False` 时仅正常配音、不生成字幕。字段类型为 `StrictBool`，非布尔字符串会被 Pydantic 拒绝。

### 上传配音（可不上传）与字幕对齐

配音有两种来源，**都允许**，且都由前端「开始创作 → 03 配音与字幕」触发：

| 来源 | 入口 | 后端行为 |
|---|---|---|
| 用户上传音频 | `extra_params.audio_path` + `audio_duration_sec`（需求会话 init 的 `extra`） | 实测时长（ffprobe）后铺一条 `metadata.dubbing=true` 的音频 clip 锚定总时长；**字幕按配音实测时间重建** |
| 不上传 + 自动配音 | 无 `audio_path`，`video_mode=voiceover` + `auto_dub=true` + `voice_id` | 走 TTS，逐句音频落旁白轨；字幕按 **TTS 实测句级时间戳**重建 |
| 都不开 | 关掉自动配音且未上传 | 纯画面模式，字幕按场景时长估算 |

**对齐规则（`AudioAgent._align_captions_to_uploaded_dub`）**：

1. 取时间线上现有字幕文本（EditAgent 的字数比例估算）按顺序拼成转写文本；
2. `STTService.align` 对齐到配音：有 whisper 用强制对齐，没有则按字数比例摊总时长；
3. 段数与字幕段数**一致** → 用实测时间 + 原始文案（转写错字不改字幕）；
4. 段数**不一致**（无标点长文常见）→ 按配音时长等比分配给原有每段字幕，保证"切几段就几段"；
5. 重建时**继承既有字幕样式**（字体/字号/描边/阴影），不会把样式洗掉；
6. 对齐不可用时写入 `audio_notes`（`上传配音：字幕对齐不可用（…）`），**不静默保留估算**。

**切分方式一致**：`extra_params.split_mode`（`period` / `punctuation`）同时决定前端预览与
后端 `EditAgent._split_sentences()` 的分段——"预览几段 = 成片几段"，避免预览与成片对不上。

> 历史缺陷（已修，见 [subtitle-bug-report](subtitle-bug-report-2026-10-23.md) S25–S27）：
> 对齐逻辑曾缩进在 `audio_config.sfx` 分支里（只有传音效才跑）；前端上传入口曾被
> `mode === 'voiceover'` 门控自我隐藏（无音频→visual→按钮不渲染）。

**关闭方式**：不传 `audio_path` 即可；想连 TTS 一起关掉，把 `auto_dub=false` 并留空音色。

## 相关文档

- [API 参考](api_reference.md) — 完整 API 端点说明
- [快速开始](quickstart.md) — TTS 配置步骤
- [开发指南](development.md) — 新增 Provider
