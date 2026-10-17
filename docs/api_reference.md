# 帧艺 ClipWright 内容视频编排引擎 — API 参考

## 基础信息

- **Base URL**: `http://localhost:8080`
- **格式**: 全部请求/响应均为 JSON
- **交互式文档**: `http://localhost:8080/docs`

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
| `mask_type` | string? | null | 蒙版：`none`/`rect`/`rectangle`/`ellipse`/`polygon`/`star`/`heart`（`star`/`heart` **仅预览生效**，导出记 warning 并跳过） |
| `mask_rect` | dict? | null | 蒙版区域/形状框 `{x,y,w,h}`（归一化；多边形/星形/心形用其中心与半径） |
| `mask_points` | int? | null | `polygon` 边数 / `star` 角数（3-12，默认 5） |
| `mask_rotation` | float? | null | 蒙版旋转（度，-180~180） |
| `mask_feather` | float? | null | 边缘羽化宽度（像素 0-50）；导出用 geq 在隐函数上做线性过渡 |
| `mask_inverted` | bool? | null | 反相（形状之外可见，内部透明） |
| `keyframes[].bezier` | dict? | null | 自定义缓动 `{x1,y1,x2,y2}`（CSS `cubic-bezier`），**存在时优先于 `easing` 名字** |
| `text` | string? | null | 文字内容 |
| `font` / `font_size` / `font_color` / `text_align` | — | null | 文字样式 |
| `transition_in` / `transition_out` / `transition_duration_sec` | — | null | 转场 |
| `keyframes` | list[dict] | [] | 关键帧动画序列 |
| `metadata` | dict | {} | 扩展元数据 |

**整数 tick 输入（可选，O1 第二阶段）**：请求体可以用整数 tick 提交时间，服务端折算成上面的秒字段：

| 输入字段 | 类型 | 折算到 |
|------|------|--------|
| `start_ticks` | int ≥0 | `start_sec` |
| `duration_ticks` | int >0 | `duration_sec` |
| `source_offset_ticks` | int ≥0 | `source_offset_sec` |

1 tick = 1/120000 秒（所有标准帧率的帧周期都是整数 tick：24→5000、29.97→4004、59.94→2002）。
同时给出秒字段时**秒优先**；非整数 tick 直接 422。**响应里不会出现 tick 字段**——浮点秒
仍是唯一序列化真值，避免同一段时间出现两个真值来源。`Timeline` 同理接受 `fps_num`/`fps_den`
（有理数帧率，如 `30000/1001`）折算为 `fps`。详见 [整数时间基座](timebase.md)。

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
{
  "status": "ok",
  "service": "clipwright-engine",
  "mongodb": "ok",
  "llm": "ok",
  "ffmpeg": "ok",
  "hyperframes": "ok",
  "queue_running": "0",
  "queue_pending": "0"
}
```

`status` 取值 `ok` / `degraded`。**参与判定的只有"健康状态字段"**：`service`（这是服务**名字**）、`queue_running` / `queue_pending`（这是**计数**）不参与——它们恒不等于 `ok`，混进判定会让 `status` **永远为 `degraded`**（轮 147 修，之前 `docker-compose.yml` 的健康检查判 `status == 'ok'`，等于容器永远 unhealthy）。

- MongoDB 断连（`disconnected`）**计入不健康**：轮 80 的文档这样写，但代码里一直把它放在豁免名单里（注释与实现自相矛盾），轮 147 才真正对齐——就绪探针不许假绿。
- `llm` 无 key 时 `no_key`（**不**计入不健康：未配置 LLM 是功能取舍，不影响服务能用）；`checking` 表示冷启动探测尚未出结论，也不计入。
- 冷启动后首次请求可能短暂看到 `ffmpeg: not_found`（后台探针还没回填），几秒后复探即 `ok`。

---

## Pipeline

| 端点 | 说明 |
|------|------|
| `POST /api/pipeline/run-async` | 异步启动管线（唯一入口；动态路由 + 并行执行 + 自愈循环），通过 SSE 实时追踪 |
| `GET /api/pipeline/runs?limit=50` | 列出最近的管线运行记录（按时间倒序） |
| `GET /api/pipeline/status/{pipeline_id}` | 查询管线状态 |
| `GET /api/pipeline/result/{pipeline_id}` | 获取异步执行结果（含时间线 JSON） |
| `GET /api/pipeline/trace/{pipeline_id}` | 获取管线事件轨迹 |
| `GET /api/pipeline/trace/stream/{pipeline_id}` | SSE 流：实时追踪管线执行事件 |
| `POST /api/pipeline/retry/{pipeline_id}/{agent_name}` | 从失败的 Agent 重试（B3：重放前置成功结果，仅重跑目标 + 下游） |
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

**预设**（9 个）: `bilibili` `youtube` `tiktok` `weibo` `1080p` `720p` `480p` `prores422hq`（ProRes 422 HQ，`.mov` 母带交付）`h265_10bit`（H.265 HEVC 10bit）

### `GET /api/render/backends`

列出**全部渲染后端**（内建 `local`/`remote` + 插件贡献的 `renderer_backend`）：

```json
{ "backends": [{ "name": "local", "available": true,
                 "capabilities": {"name": "local", "pix_fmt": "yuv420p", "supports_gpu": false,
                                  "supports_soft_subtitle": true, "cancel": "cancel_id"} }],
  "requested": "local", "effective": "local", "remote_render_url": false }
```

`requested` = `settings.renderer_backend` 的配置值；`effective` = **回退后真正生效**的后端
（未注册/探测失败时回退 `local`，排障时一眼看出为什么走了本机）。
插件如何贡献后端见 [插件作者指南](plugin_authoring.md) 的「渲染后端贡献」。

**渲染后复核（V4，默认关闭；`settings.render_review=true` 或 `CLIPWRIGHT_POST_RENDER_REVIEW=1` 开启）**：
开启后每次渲染额外产出「切点复核图」（每个剪辑点前后三帧对照）与「全片总览图」
（12 帧等间隔联络表），并可选交视觉模型打分。响应体因此多三个字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `review_findings` | list | 复核问题的**派工列表**：`{kind, redo_agent, severity, detail, evidence, cut_times}`；`kind` ∈ animation/edit/audio/material/other，`redo_agent` 可直接喂给自愈机制；描述说不清时 `kind=other` 且**不派工** |
| `visual_score` | float? | 视觉评分（0-100），未开启视觉模型时为 `null` |
| `review_failed` | bool | 是否未过评分门槛（`settings.render_review_min_score`，默认 0 = 不启用；也可用 `CLIPWRIGHT_REVIEW_MIN_SCORE` 覆盖）。**只作标记，不改变渲染成功与否** |

复核结论由 `services/post_review.py` 归类与投票（严重度加权，平票按 素材>动画>声音>剪辑）；
调用方也可把 `review_findings` 放进 `QualityAgent` 的 `constraints`（键 `review_findings`），
质检会转成 `QualityIssue` 并复用既有「分类 → redo_agent」映射。

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
| `GET /api/project/{project_id}/memory` | 读取项目决策记忆 `memory.md`（四段式；空记忆返回空文档而非 404） |
| `PUT /api/project/{project_id}/memory` | 整份覆盖项目决策记忆（人工编辑后保存） |
| `POST /api/project/{project_id}/memory/append` | 追加记忆条目（不覆盖人工编辑；`{entries: {段名: [文本]}}` 或 `[[段名, 文本]]`） |
| `GET /api/project/{project_id}/memory/summary` | 记忆摘要（`limit` 每段条数，可直接拼进提示词） |

### 项目决策记忆（V8，`memory.md` 四段式）

每个项目在自身目录（与 `project.json` 同级）下有一份 **`memory.md`**，四段固定：

| 段落 | 别名（中文可写） | 记什么 |
|------|------------------|--------|
| `Strategy` | 策略 / 方向 | 这个片子的路线：选题角度、节奏定位、受众 |
| `Decisions` | 决策 / 决定 | 做过哪些代价高或不可逆的选择 |
| `Reasoning` | 理由 / 原因 / 依据 | 每条决策背后的依据（数据、反馈、约束） |
| `Outstanding` | 待办 / 遗留 / 风险 | 未解决项、已知风险、需复核点 |

- 程序只**追加**，不整体覆盖；人工手写的前言/条目在下一次程序记账后仍然保留。
- 未知段名**报 400**，不静默丢弃（错别字当场暴露）。
- `memory.md` **独立成文件**，不写入 `project.json`；随归档 zip 往返（导入回报 `memory_restored`），
  复制项目时一并复制；回收站软删除**保留**记忆，彻底 purge 才删除目录。
- Agent 侧对应原子工具 `project_memory`（`action=read|append`）。

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
| `POST /api/persona/forge/chat/message` | 发送消息（可带 `attachments`：图片/文件，见下） |
| `POST /api/persona/forge/chat/message/stream` | **流式发送消息（SSE）**：逐块 `delta`，末尾 `done`；中断时 `error`（带 `partial`） |
| `POST /api/persona/forge/chat/knowledge` | 注入知识（超长按 H1 自动分段） |
| `POST /api/persona/forge/chat/commit` | 提交并生成 `PersonaManifest` |
| `GET /api/persona/forge/chat/state/{session_id}` | 获取会话状态 |
| `whisper_transcribe`（工具） | 转写输出含 `phrases`/`packed_text`/`silences`/`compression`（V6 短语级打包，逐词 JSON 字符数可降 90%+） |
| `timeline_view`（工具） | 复核图工具（V7）：`mode=cuts` 切点三帧对照条 / `mode=overview` 全片联络表 |
| `project_memory`（工具） | 项目决策记忆工具（V8）：`action=read` 读回四段式记忆与摘要 / `action=append` 追加决策与待办（不覆盖人工编辑） |
| `event_spot`（工具） | 领域事件定位（R3）：`mode=text` 用领域词表（generic/sports/food/tech_review/knowledge）在文本/转写里找事件，返回时间区间 + 命中词 + 证据句 + 置信度；`mode=audio` 检测音频事件（静音/大响段/起始沿）并按镜头切分 |
| `content_query`（工具） | 素材内容查询（R4）：`mode=summary` 已索引素材构成 / `search` 自然语言检索（自动把"黑场/有人脸/特写/掌声/最长/最亮"路由成结构化条件或排序，返回带 `ref`、时间区间与"为什么命中"的证据）/ `filter` 纯结构化筛选 / `shot_at` 按时间定位镜头 |
| `GET /api/persona/forge/chat/sessions` | **列出会话**（`?scope=mine|all`；返回标题/消息数/知识数/六维完成度，按更新时间倒序） |
| `DELETE /api/persona/forge/chat/sessions/{session_id}` | **删除会话**（内存 + `chatforge_sessions/` 落盘文件） |
| `GET /api/persona/forge/chat/knowledge/{session_id}` | **列出知识条目**（id/来源/字数/预览） |
| `GET /api/persona/forge/chat/knowledge/{session_id}/{kb_id}` | 读取单条知识全文 |
| `PUT /api/persona/forge/chat/knowledge/{session_id}/{kb_id}` | **编辑知识**（`content`/`source`；只改存储上下文，**不重跑模型**） |
| `DELETE /api/persona/forge/chat/knowledge/{session_id}/{kb_id}` | **删除单条知识** |
| `DELETE /api/persona/forge/chat/knowledge/{session_id}` | 清空会话知识库 |

**流式回复（SSE）**：`POST /api/persona/forge/chat/message/stream`，请求体与同步接口一致
（含 `attachments`），响应 `text/event-stream`：

```
data: {"type":"delta","text":"好的，"}

data: {"type":"done","session_id":"...","reply":"...","persona_draft":{...},"progress":{...},"missing_dimensions":[...]}

data: {"type":"error","detail":"流式生成中断: ...","partial":"已产出的文本"}
```

- **POST 的 SSE**（正文可能很长、还可能带附件，EventSource 发不了 POST 体）→ 前端用
  `fetch` + `ReadableStream` 读（`services/api/ssePost.ts`），非 2xx 时按普通 HTTP 错误抛出；
- 收尾与同步路径**完全一致**（合并草稿 / 落盘 / 返回完整状态），因此前端可以在流中断后
  无缝回退到同步接口，不会出现两套会话状态；
- 上游中断时保留 `partial` 已产出的文本并写进会话（用户不必重问）。

**附件（图片/文件消息）**：`POST .../message` 的请求体可带

```json
{ "session_id": "abc", "message": "参考这张图",
  "attachments": [{ "type": "image", "name": "ref.png", "mime": "image/png",
                    "data_url": "data:image/png;base64,..." }] }
```

- 限额：单条消息 ≤ 4 个附件、单附件 ≤ 8MB（超限 400，错误信息说明是哪一条）；

- 图片在 `enable_visual_llm` 开启时**真做一次视觉分析**（写临时文件 → `VisionService`），
  分析结论作为该轮上下文喂给模型；未开启时只写一行「用户附了一张图片」——**不编造内容**；
- 附件与派生文本随会话落盘，重启后仍在；知识条目在恢复老会话时自动补 `id`（历史数据没有 id）。

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

### AI 生成工具（火山引擎）

三个生成类工具由内置第三方插件提供，经 `POST /api/tool/execute` 调用；产物统一下载落地到
`PluginData/assets/`（生成 URL 24h 失效，落地前经 `assert_public_url` 防 SSRF），
返回 `{success, url/path（本地）, remote_url, provider, ...}`。

| 工具 | 插件 | Provider（默认加粗） | 关键参数 |
|------|------|---------------------|----------|
| `ai_image_generate` | ai_image_gen | **volcengine(Seedream)** / dalle / flux / local | prompt, width/height（按比例收敛到 Seedream 合法像素区间）, size（直传 `2K`/`2048x2048`，优先）, image（参考图，图生图）, seed |
| `ai_video_generate` | ai_video_gen | **volcengine(Seedance)** / kling / runway | prompt, duration_sec（默认 5）, aspect_ratio, resolution（480p/720p/1080p）, image_url（首帧，图生视频）, seed |
| `ai_music_generate` | ai_music_gen | **volcengine(豆包音乐)** / suno | prompt, instrumental（默认 true=纯音乐 BGM）, lyrics（人声歌曲，优先于 prompt）, duration_sec（纯音乐 [30,120]，人声 [30,240]）, genre/mood/model_version（人声可选） |

环境变量/插件配置：

- 图片/视频（方舟 Ark，Bearer Key）：`ARK_API_KEY`；可选 `ARK_BASE_URL`、`ARK_SEEDREAM_MODEL`（默认 `doubao-seedream-4-0-250828`）、`ARK_SEEDANCE_MODEL`（默认 `doubao-seedance-1-5-pro-251215`）；插件配置键 `api_key` / `ark_base_url` / `model` / `poll_interval_sec` / `poll_timeout_sec`
- 音乐（火山引擎 OpenAPI，AK/SK V4 签名，服务 `imagination`）：`VOLC_ACCESS_KEY` / `VOLC_SECRET_KEY`（兼容 `VOLCENGINE_*` 前缀）；插件配置键 `access_key` / `secret_key`（secret 加密落盘） / `billing_mode`（prepaid→GenBGM/GenSongV4，postpaid→GenBGMForTime/GenSongForTime） / `poll_interval_sec` / `poll_timeout_sec`

**工具可用性与素材入库（轮次 61）**：

- 三个工具均按 provider 重写 `is_available()`（凭据配置优先、env 兜底）——未配置的工具会被
  `ToolRegistry.list_agent_callable()` 过滤，Agent 的 function schemas 中不出现，即"只有可用工具才会被调用"。
- 生成成功后产物登记进 `PluginData/plugins/<id>/generated.json` 历史索引，并经
  `clipwright/plugins/generated_source.py::make_generated_source` 注册为 MaterialSource——
  MaterialAgent 场景关键词 / AudioAgent BGM 关键词可检索命中（score 0.70-0.92），
  视频/图片经候选素材进入时间线，音乐进入 BGM 铺轨链路。
- AnimationAgent 主动从素材库检索图片（含 AI 生成图）建立语义索引供 LLM 选图入动画；
  管线 animation 分发与需求服务动画重做均传入 AI 生成图片历史。
- 需求服务在规划书与需求对话上下文注入「## AI 生成能力」概览（仅存在已配置工具时）；
  简报 `asset_ratio` 支持 `ai_generated` 可选档。

---

## 需求分析 (Requirements Agent)

| 端点 | 说明 |
|------|------|
| `POST /api/requirements/init` | 初始化需求分析会话（创意简报） |
| `POST /api/requirements/chat` | 发送需求消息 |
| `POST /api/requirements/chat/stream/{session_id}` | SSE 流式对话。事件类型：`status`（typing）/ `delta`（gathering 态回复文本增量，轮69 真流式）/ `result`（完整结果，契约与 `/chat` 一致）/ `error`。无 `delta` 时前端按旧契约一次性渲染 |
| `POST /api/requirements/upload/{session_id}` | 上传参考文件 |
| `GET /api/requirements/session/{session_id}` | 获取会话状态 |
| `GET /api/requirements/plan/{session_id}` | 获取制作规划书 |
| `POST /api/requirements/proceed` | 确认规划书并启动管线 |
| `POST /api/requirements/{session_id}/intent` | **意图落位预览（R6，只读）**。请求：`{message, timeline, selected_clip_ids?, region_start_sec?, region_end_sec?, project_id?}`；响应：`{intent, resolution:{preview[], affected_clip_ids[], questions[]}, problems[]}`，`blocked=true` 表示含硬错误不可执行。**不写任何状态**——供前端"意图确认卡"在执行前展示"将改动什么 + 需要用户拍板的问题"。非法时间线 400、超时 504 |
| `POST /api/requirements/{session_id}/intent/apply` | **意图 → 补丁 → dry-run（可选执行）（R7）**。请求在上述字段外加 `{apply?: bool=false, answers?: {问题码: 选项值}, intent_raw?: 意图JSON}`；响应：`{patch, report, applied, blocked, problems[]}`，`applied=true` 时另有 `timeline`（新时间线）。语义：`apply=false` 纯只读；`apply=true` 时只有 dry-run **无硬错误**才真正执行，否则 `blocked` 且**一条都不改**。传 `intent_raw`（`/intent` 已解析的那份）保证"执行的就是用户确认过的那份意图"。非法时间线 400、超时 504 |

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
curl -X POST http://localhost:8080/api/voice/upload \
  -F "file=@sample.wav"
```

响应：
```json
{"filename": "sample.wav", "saved_as": "PluginData/uploads/xxx.wav", "size": 123456, "data_uri": "data:audio/wav;base64,...", "mime": "audio/wav"}
```

### `POST /api/voice/clone`

```bash
curl -X POST http://localhost:8080/api/voice/clone \
  -H "Content-Type: application/json" \
  -d '{"voice_name": "我的音色", "audio_url": "https://example.com/sample.wav", "provider": "qwen_tts"}'
```

### `POST /api/voice/synthesize`

```bash
curl -X POST http://localhost:8080/api/voice/synthesize \
  -H "Content-Type: application/json" \
  -d '{"voice_id": "v_xxx", "text": "你好世界"}'
```

响应：
```json
{"audio_path": "...", "duration_sec": 2.5, "voice_id": "v_xxx", "provider": "qwen_tts", "text": "你好世界"}
```

### `POST /api/voice/dub`

```bash
curl -X POST http://localhost:8080/api/voice/dub \
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
| `GET /api/animation/mg/list` | 列出内置 MG 模板（含 params 定义，供预览工坊） |
| `GET /api/animation/mg/generations` | 列出最近 MG 生成记录（`limit`，新→旧） |
| `GET /api/animation/mg/generations/{generation_id}` | 取单条生成记录（含 `mg_def`，可回看/重渲） |
| `POST /api/animation/mg/html` | **HTML 即时预览**：返回合成 HTML 本身，不渲染、不起 Chromium（前端 iframe 内用 `window.__timelines[].seek()` 拖动播放） |
| `POST /api/animation/preview` | 单镜头 MG 渲染预览：Hyperframes 直出 MP4（需本机 Chromium；不可用返回 503） |

### `POST /api/animation/mg/html`

请求体与 `POST /api/animation/preview` 完全一致（二选一）：

```json
{"animation_id": "mg_title_reveal", "params": {"text": "标题"}, "width": 1280, "height": 720, "fps": 30}
```

`animation_id`（内置模板）或 `mg_json`（LLM 生成的 MG 定义）二选一；`width`/`height` ≥ 320。

响应：

```json
{"html": "<!DOCTYPE html>…", "duration_sec": 4.5, "width": 1280, "height": 720, "fps": 30,
 "animation_id": "mg_title_reveal", "variables": {"text": "标题", "accent": "#fbbf24"}}
```

- 与主管线同源：HTML 内置 `hf_timeline` 运行时，动画已暂停并注册到 `window.__timelines[<composition-id>]`。
- **变量参数化（P1-5）**：HTML 通过 `<html data-composition-variables="…">` 声明参数
  （`id/type/label/default`），文本用 `data-var-text`、图片用 `data-var-src`、
  标量样式用 `var(--key, 兜底值)` 绑定；响应里的 `variables` 是本次取值回显。
  渲染时可由上游 `--variables '{"text":"…"}'` 覆盖（`render_mg_html_to_mov(variables=…)`），
  不传则用声明里的 default，结果与旧的内联替换完全一致。
- 前端约定（`src/features/preview/mgHtmlPreview.ts`）：放进 `sandbox="allow-scripts"` 的 iframe
  （**不开 allow-same-origin**，LLM 生成的脚本不得在应用源执行），只用 `postMessage` 双向通信；
  父页面发 `seek/play/pause`，iframe 回传 `{duration, time, playing}`。
- 背景守卫与主管线一致：默认强制透明叠加（剥离不透明 bg 层）。

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
| `GET /api/plugin/list` | 列出已加载插件（含 `contributions`：已注册贡献 kind/id；`declared_only`：manifest 声明但未注册的键——用于发现「加载成功但零贡献」） |
| `GET /api/plugin/discover` | 发现可用插件 |
| `GET /api/plugin/capabilities` | 系统能力概览 |
| `POST /api/plugin/load-all` | 加载全部插件 |
| `POST /api/plugin/load/{plugin_id}` | 加载插件 |
| `POST /api/plugin/unload/{plugin_id}` | 卸载插件 |
| `GET /api/plugin/{plugin_id}/config` | 获取插件配置 |
| `PUT /api/plugin/{plugin_id}/config` | 更新插件配置 |
| `DELETE /api/plugin/{plugin_id}/config` | 删除插件配置 |
| `GET /api/plugin/{plugin_id}/ui` | 获取插件 UI 定义 |
| `GET /api/plugin/health` | 插件健康聚合（ok/degraded/error 分类 + Hook/Agent 计数） |

**插件 Agent（SA-2/3/4）**：插件 manifest 声明 `kind: agent` + `permissions: [orchestrate]` 后，可在 `initialize()` 经 `AgentRegistry.register(agent, name, plugin_id, deps)` 注册自定义 Agent——按 deps 并入主管线 DAG（输入 `PluginAgentInput`、输出 `PluginAgentOutput`），或经 `services.subagent.run_sub_agent(ctx, name, payload)` 作为子代理被宿主 Agent 调用（嵌套深度上限 2、宿主取消感知、用量以「宿主:子代理」复合名归因、熔断 3 次/60s、默认 120s 超时）。Agent 边界 Hook `PRE_AGENT`（改写 input/skip）与 `POST_AGENT`（只读观测）已在管线接线。


---

## 模型提供商（Provider 源）

「设置 → 模型提供商」页的数据面：一个**源**（凭据 + 模型清单）可被多个**角色**引用，
角色由工程文件/业务流程消费，源里改密钥不影响工程。密钥以 Fernet 加密落盘
（`ClipwrightData/provider_sources.json`；未配置 `CLIPWRIGHT_WEBHOOK_SECRET_KEY` 时明文兼容），
接口一律只回显掩码。

| 端点 | 说明 |
|------|------|
| `GET /api/providers?kind=` | 源列表（脱敏：`api_keys_masked`/`key_count`/`has_keys`）+ 角色绑定总览 |
| `POST /api/providers` | 新建源 `{name,kind,provider,base_url,timeout_sec,proxy,headers,api_keys[],models[]}` |
| `GET /api/providers/available` | 各类型可选 provider 名（**门面驱动**，含插件注册的 llm/tts/embedding/rerank provider）+ `role_kinds` 映射 |
| `GET /api/providers/roles` | 角色绑定（`main/flash/vision/embed/rerank/tts`） |
| `PUT /api/providers/roles` | 绑定/解绑角色 `{role, source_id, model}`（`source_id=""` 解绑） |
| `GET /api/providers/{id}` | 源详情（脱敏） |
| `PUT /api/providers/{id}` | 更新源（`api_keys` 传掩码=保留旧值） |
| `DELETE /api/providers/{id}` | 删除源（引用它的角色自动解绑） |
| `POST /api/providers/{id}/models` | 覆盖模型清单 |
| `POST /api/providers/{id}/models/upsert` | 自定义模型（新增/覆盖 `{id,name?,enabled?}`） |
| `DELETE /api/providers/{id}/models/{model_id}` | 删除模型（解绑引用它的角色） |
| `POST /api/providers/{id}/models/fetch` | 「获取模型列表」（调用 provider 的 models 端点；不支持自动拉取的 provider 会明确说明，可改用自定义模型） |
| `POST /api/providers/{id}/test` | 源级连通性探测（**不改动角色绑定**）：`llm`/`embedding` 走模型列表端点、`rerank` 发一次最小请求、`tts` 明确返回不支持；返回 `{ok, latency_ms, detail}` |

**绑定规则（重要）**：

- 角色必须绑定**对应类型**的源（`main`/`flash`/`vision` ← `llm`，`embed` ← `embedding`，
  `rerank` ← `rerank`，`tts` ← `tts`）；错配会被 400 拒绝，而不是留到运行期才炸。
- 源被角色绑定期间**不允许改类型**（需先解绑）。
- 模型被**停用或删除**后，引用它的角色自动解绑，响应里以 `unbound_roles` 回传。
- 多把 API Key 目前只使用第一条（其余作为备用保存、便于手动切换），不做轮询。

**角色消费点**：`main`/`flash`（`services/llm.py`，含 vision 走的 `vision` 角色）、
`embed`/`rerank`（`rag/embedder.py`、`rag/reranker.py`）、`tts`（`services/voice.py`）。
绑定缺失时全部回退既有 `settings.*_provider/model/api_key/base_url` 平铺字段（零行为变化）；
源的 `timeout_sec/proxy/headers` 会真正落到传输层（LLM 内层 SDK / httpx）——
注意 provider 未声明这些参数时会按签名过滤并给一次性告警（兼容旧版插件 provider）。

**模型测试面**：`GET /api/test/config` 与 `POST /api/test/{llm,embed,rerank}` 报告的是
**真实生效**的 provider/model，并附 `effective`（`provider_source` / `settings`）与 `source`
（绑定的源名）——不再是只读旧平铺字段的"假配置"。

**设置页下拉**：`GET /api/settings` 的 `llm_provider` / `rag_embed_provider` /
`tts_default_provider` 选项已并入门面中已注册的 provider —— 插件注册的 provider
会直接出现在下拉里，禁用插件后消失。

---

## 模型测试 (/api/test)

| 端点 | 说明 |
|------|------|
| `POST /api/test/llm` | LLM 接口测试 |
| `POST /api/test/embed` | Embedding 测试 |
| `POST /api/test/rerank` | Rerank 测试 |
| `GET /api/test/config` | 模型配置 |

---

## 运行时配置 (/api/settings)

把 `.env` 配置项搬到「设置 → 后端配置」页管理。优先级：`settings.json`（store）> 环境变量/.env > 默认值。
secret 字段（密钥类）读取时只返回 `has_value` 与掩码占位、绝不回显明文；落盘经 Fernet 加密（未配置
`CLIPWRIGHT_WEBHOOK_SECRET_KEY` 时拒绝保存 secret，返回 409）。所有端点需管理员权限（jwt 模式仅 admin；
off/token 模式放行）。`requires_restart=true` 的字段（CORS/Mongo/端口/路径/最大并发渲染等）只在启动时读取，保存后需重启。

| 端点 | 说明 |
|------|------|
| `GET /api/settings` | 分组返回全部字段元数据与当前生效值（`groups[].fields[]`，含 `kind/choices/secret/hot/requires_restart/source`） |
| `PUT /api/settings` | 部分更新，body `{"values": {key: value}}`；value 为 `null`/`""` 表示清除该键覆盖（回退 env/默认）；返回 `applied/cleared/requires_restart/warnings` |
| `POST /api/settings/reset` | 清除 store 覆盖，body `{"keys": [...]}`（空 = 全部），还原为 env/默认值 |

`source` 取值：`store`（settings.json 覆盖）/ `env`（环境变量或 .env 显式提供）/ `default`（默认值）。
热更新语义见 `docs/development.md`「运行时配置」。

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
