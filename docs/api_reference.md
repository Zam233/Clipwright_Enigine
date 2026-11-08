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
| `POST /api/pipeline/run-async` | **已退役**：执行引擎已在 M3 物理删除。`agent_mode=on` 时转成主 Agent 任务（返回体仍带 `pipeline_id`），其余情况返回 **410**。新任务请用 `POST /api/requirements/proceed` 或 `POST /api/task` |
| `GET /api/pipeline/runs?limit=50` | 列出最近的管线运行记录（按时间倒序） |
| `GET /api/pipeline/status/{pipeline_id}` | 查询管线状态 |
| `GET /api/pipeline/result/{pipeline_id}` | 获取异步执行结果（含时间线 JSON） |
| `GET /api/pipeline/trace/{pipeline_id}` | 获取管线事件轨迹 |
| `GET /api/pipeline/trace/stream/{pipeline_id}` | SSE 流：实时追踪管线执行事件 |
| `POST /api/pipeline/retry/{pipeline_id}/{agent_name}` | **已退役**：返回 **410**（执行引擎已删除；失败恢复请走主 Agent 的 `POST /api/task/{id}/resume`，或让主 agent 重新 `delegate_agent` 该阶段） |
| `POST /api/pipeline/predict-script` | 分析文稿并推荐配置（请求体：`PredictScriptRequest`）。返回字数、句数、估算时长、推荐 Persona、推荐类型插件、情绪基调、关键主题 |
| `POST /api/pipeline/predict-material` | 分析素材文件并推荐使用方式（请求体：`PredictMaterialRequest`）。返回时长、分辨率、方向、是否有视频/音频、用途建议 |

## 任务与子 Agent（/api/task）

> 实现：`clipwright/api/task.py`、`services/agent_run.py`、`services/task_events.py`。
> 任务事件是唯一事实来源；`task.status` 只是缓存。

| 端点 | 说明 |
|------|------|
| `POST /api/task` | 创建任务；`plan_only=true`（默认）只规划不执行 |
| `GET /api/task/{task_id}` | 任务快照 + 事件条数 |
| `GET /api/task/{task_id}/events?since_seq=&before_seq=&limit=` | 事件分页；返回 `has_more` / `oldest_available_seq` / `latest_seq` / `spilled_max_seq` |
| `GET /api/task/{task_id}/events/stream?since_seq=` | SSE；先 REST 水合再接流。发送 `task_end` 后关闭；终态且游标已追平时直接 EOF |
| `POST /api/task/{task_id}/resume` | 续跑；终态 409，等待回答时 409（需要先 answer 或 cancel） |
| `POST /api/task/{task_id}/pause` / `cancel` | 暂停 / 取消 |
| `POST /api/task/{task_id}/inject` | 给运行中的主 Agent 插话 |

### 终止机制字段（devplan 2026-11-02，`schema/task.py`）

`Task.to_public()` 与 `task_end.detail` 都带下面这些**新增**字段（旧字段全部保留）：

| 字段 | 含义 |
|---|---|
| `terminal_reason` | 终态**唯一机器原因**：`achieved` / `plan_only_completed` / `capped` / `blocked` / `user_cancelled` / `externally_interrupted` / `model_error` / `invalid_completion` / `dry_run_completed`。旧 `finish.reason` 字符串继续并行产出（暂不停用） |
| `blocker_code` | blocked 的根因码：`no_progress` / `repeat_fail` / `degradation_unresolved` / `budget_exhausted` / `model_error` / `invalid_completion` / `missing_artifact` |
| `goal` | 完成约束：`{id,title,completion_criteria,max_rounds,rounds_used,status,blocked_reason,blocker_fingerprint,blocker_streak,blocked_after_consecutive_rounds,closeout_required}`；`status ∈ active/achieved/capped/blocked/paused` |
| `closeout` | 收口状态：`{mode:normal\|soft_landing, trigger, trigger_round, max_rounds, max_tool_calls, max_wall_sec, rounds_used, tool_calls_used, deliverable_status, report_path}`；收尾报告落在 `reports/agent-closeout.json` |
| `escrow` | 质量预算托管：`{state,reserve_usd,main_budget_usd,main_spent_usd,quality_spent_usd,released_usd,ratio,floor_usd,cap_usd,quality_remaining_usd}`；`state=disabled` 表示未配置金额上限（不冻结） |

`TaskStatus` 新增 `capped`：**既不算 done 也不算 failed**，不计入成功率（`evaluation` 单列
`capped_count`），会话映射 `plan_ready`（只有 `done` 才 `pipeline_done`）。

新增事件：`goal_update`（Goal 状态迁移）、`soft_landing`（进入收口模式，一次性）。
| `POST /api/task/{task_id}/plan` | 重新规划 |
| `GET /api/task/{task_id}/questions?status=` + `POST .../answer` | 待答问题与作答（404/409/410 分别表示不存在/已回答或取消/超时失效） |
| `GET /api/task/{task_id}/result` + `POST .../to-project` | 任务产物快照 / 落成可继续编辑的项目 |
| `GET /api/task/{task_id}/runs` | 子 Agent run 列表；**含运行中占位记录**（`live=true`），`report_path` 取顶层字段 |
| `GET /api/task/{task_id}/runs/{run_id}` | run 全量结果；run 不属于该任务时 404（不泄露存在性） |
| `POST /api/task/{task_id}/runs/{run_id}/message` | 给该阶段子会话排队补充要求（下次派发生效） |
| `POST /api/task/{task_id}/runs/{run_id}/replay` | 真重跑；`refresh=true`，生成 `#r…` 新 `run_id`；终态任务 409 |
| `GET /api/task/{task_id}/tool-calls?stage=&attempt=&limit=&offset=` | 读盘上的全量工具 transcript |

事件契约（前端按此渲染，不得只发分类字符串）：

- `agent_run_start.detail`：`run_id`、`kind`、`stage`、`role`、`brief`、`inputs`、`attempt`
- `agent_run_end.detail`：`run_id`、`status`、`attempt`、`summary`、`cost_usd`、`duration_ms`、
  `report_path`、`tool_calls_path`、`degradations`、`failure`（完整对象）、`needs`
- `task_end.detail`：`reason`、`usage`、`completed`、`unfinished`、`tool_calls`

身份规则：`idem_key` 只用于幂等复用；`run_id` 标识每一次真实执行。显式重放、降级重跑、同键再次执行
都必须生成新的 `run_id`，否则工具事件会挂到旧 run 或覆盖历史记录。

## 调度中枢（Supervisor，v1.4）

> 实现：`clipwright/api/supervisor.py`（`router` + `admin_router`）。
> `/api/pipeline` 前缀下的这些端点**不属于** `pipeline.py`，注意别在那边找。

| 端点 | 说明 |
|------|------|
| `GET /api/pipeline/{pid}/ledger?stage=` | 账本（每条阶段尝试的判定/失败类别/降级/用量/耗时）；未知管线返回 `found=false` |
| `GET /api/pipeline/{pid}/attribution?llm=` | 归因（规则 R1–R4 优先，`llm=true` 才允许规则不命中时走兜底判定） |
| `GET /api/pipeline/{pid}/diagnosis` | 诊断书（模板化，无 LLM）：结论/问题/证据/已尝试/建议/成本 |
| `GET /api/pipeline/{pid}/decisions` | 调度决策序列 + `accepted_degraded` |
| `GET /api/pipeline/{pid}/budget` | 预算快照（未配置预算 → `level=unlimited`、`ratio=null`，**不阻断**） |
| `POST /api/pipeline/{pid}/dispatch` | 人工改派：`{target_stage, reason, issues?, auto_retry?}` → **只登记工单**（执行语义已随 M3 退役，返回 `execution_status=retired`）；真重跑请由主 agent 重新 `delegate_agent` 该阶段 |
| `POST /api/pipeline/{pid}/accept-suggestion` | 执行诊断建议：`{suggestion_id?, action, target_stage?}`。`accept_degraded` 只登记；`retry_with_model`/`redo_stage` 触发重跑；`manual` 不执行 |
| `GET /api/pipeline/{pid}/subagents` | 子代理树（含状态/模型/耗时/成本）；空树不是 404 |
| `POST /api/pipeline/{pid}/subagents` | 派生临时 worker（带 `run_shell`/`run_python` 时返回 `pending_review`，未审核不实例化） |
| `POST /api/pipeline/{pid}/subagents/{sid}/cancel` | 取消子代理（不存在 404 / 已结束或不可取消 409） |
| `POST /api/pipeline/{pid}/exec` | 请求执行：`{kind: shell\|python, command\|code, cwd?, timeout_sec?}` → `done/failed/pending_approval/rejected` |
| `GET /api/pipeline/{pid}/exec` | 执行日志（命令/风险等级/理由/退出码/耗时/输出尾部） |
| `GET /api/pipeline/{pid}/exec/pending-approvals` | 待授权命令队列 |
| `POST /api/pipeline/{pid}/exec/{aid}/approve\|reject` | 授权 / 拒绝（high、critical 需 `confirm_high_risk=true`） |
| `GET\|PUT /api/pipeline/{pid}/exec/auto-mode` | 自动模式开关（只减免 `med`，`high`/`critical` 仍弹） |
| `GET /api/supervisor/workers/pending-review` | 带执行权限的待审 worker（含独立 LLM 风险结论） |
| `POST /api/supervisor/workers/{wid}/review` | 人工审核 `{approve, confirm_high_risk}` |
| `GET /api/supervisor/tools` | 主代理可授予的工具全集、需审核的工具、`executable`（哪些真有执行通道） |
| `GET\|PUT /api/supervisor/routes` | 模型路由表 + provider 白名单 |
| `GET\|PUT /api/supervisor/mode` | 调度中枢模式 `off\|shadow\|on`（默认 off） |

执行网关的三条不可协商规则：硬底线（读凭据 / 不可逆破坏）**任何模式都拒绝且不可授权**；风险等级由确定性规则与**独立 LLM 判定**取 `max()`（规则只升不降，判定失败一律升 `high`）；每条执行（含被拒/超时）都写 `ExecRecord` 并入 trace。

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
| `GET /api/project/{project_id}/termination` | **读项目级终止策略**（devplan 2026-11-02）：返回 `{policy, resolved, overridable}`；`policy` = 项目里显式写过的键（空 = 跟随全局），`resolved` = 「项目 > 全局 > 默认」解析后的快照（含 `source`），`overridable` = 允许覆盖的键名 |
| `PATCH /api/project/{project_id}/termination` | **写项目级终止策略**：只改 `project.json` 的 `agent_state.termination`（保留 `agent_state` 其它键）；body `{policy:{...}}`，值为 `null`/`""` 表示清除回退全局；未知键 400。可覆盖 `soft_landing_round` / `soft_landing_enabled` / `closeout_rounds` / `closeout_tool_calls` / `closeout_max_wall_sec` / `reserve_ratio` / `reserve_floor_usd` / `reserve_cap_usd`（**只对之后创建的任务生效**，任务创建时快照） |
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
| `POST /api/capability/{name}` | **通用能力网关**（插件对外 HTTP 入口的唯一通道）：按**能力名**解析（`capabilities.resolve_name`，能力名优先、工具名兜底）再执行，body `{params}`，返回 `ToolExecResult`。状态码语义：`404` 解析不到（插件未装/被禁用）、`422` 参数或路径不合规、`503` 已解析但依赖缺失、`200` 成功；`costly`/`dangerous` 档记审计。**网关内没有任何具体 provider/能力名分支** |
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
| `POST /api/requirements/{session_id}/intent` 新增选区快照字段 | devplan/PRD W3.2：`selected_clip_ids` 现在是**冻结快照**（发送时生成），并新增 `selection_summary`（按行组织的可见摘要，≤8000 字符）与 `selection_truncated`。服务端校验：**超过 20 个** → 400；**含不在本次 timeline 里的 id**（已删除/来自其它项目）→ 400 |
| `POST /api/requirements/{session_id}/intent/apply` | **意图 → 补丁 → dry-run（可选执行）（R7）**。请求在上述字段外加 `{apply?: bool=false, answers?: {问题码: 选项值}, intent_raw?: 意图JSON}`；响应：`{patch, report, applied, blocked, problems[]}`，`applied=true` 时另有 `timeline`（新时间线）。语义：`apply=false` 纯只读；`apply=true` 时只有 dry-run **无硬错误**才真正执行，否则 `blocked` 且**一条都不改**。传 `intent_raw`（`/intent` 已解析的那份）保证"执行的就是用户确认过的那份意图"。非法时间线 400、超时 504 |
| `GET /api/requirements/{session_id}/messages` | 需求消息历史分页（`before_id` + `limit`）：向上翻不重复、不丢 |
| `GET /api/requirements/{session_id}/events` | 会话事件（`question`/`answer`/`thinking` 等）：刷新/重连按 `since_seq` 补齐 |
| `GET /api/requirements/{session_id}/timeline` | **会话时间线（唯一有序流，session-timeline PRD §5.1）**。参数 `since_seq`/`until_seq`（向后翻）、`before_seq`（向前翻，取 `seq < before_seq` 的最后 `limit` 条）、`limit≤500`、`resolve=0|1`（0 = 只给索引不读 `items/*.json`，轨迹/调试用）。响应 `{items[], has_more, latest_seq, next_seq, archived_until_seq, counters, backfilled}`；`backfilled` = 本次请求里从 `session.messages` 回填了几条（存量会话迁移，幂等）。条目按 `seq` 升序且**已解析内容**。类型：`thinking`/`content`/`tool`/`skill`/`subagent`/`report`/`plan` + 扩展 `question`/`answer`/`approval`/`degradation`/`stage`/`artifact` |
| `GET /api/requirements/{session_id}/timeline/stream` | **时间线实时流（SSE）**。`since_seq=-1`（默认）只推订阅之后的新帧；传 `since_seq` 先补一帧 `snapshot`。帧 `op`：`snapshot`/`append`/`chunk`（只带 `seq+delta`，前端按 seq 找到条目追加 = 流式渲染）/`patch`/`finish`/`ping`（10s）/`error`。`append/patch/finish` 帧的 `item` **带已解析的 `content`**（>64KB 才省略）——否则外置的 `report`/`plan` 在实时侧没有内容、卡片不渲染 |
| `POST /api/requirements/{session_id}/plan/dispatch` | **主 agent 派发「生成制作规划书」（plan-dispatch PRD D1，幂等）**。body `{retry?: bool=false}`；响应 `{task_id, run_id, status:"planning", reused}`。幂等键 `session_id:brief_hash`：同 brief 且任务在跑 → 返回既有 task_id（`reused=true`）；`retry=true` 强制重建。错误：404 会话不存在 / 400 `no_brief`（先出简报）。任务侧：`plan_only=true`，kernel 派 `delegate_agent(structure)` → 收尾写工作区 `reports/plan.json`（`{session_id, brief_hash, scenes, scene_count, summary, generated_by:"kernel", run_id}`）→ 需求侧轮询终态读产物回填 `session.production_plan`（`brief_hash` 不一致则作废回 `brief_ready`），状态由任务终态驱动 |
| `POST /api/requirements/{session_id}/stop` | **用户点「停止」（中止续跑 §停止）**。无 body；响应 `{stopped, task_id, status:"plan_ready", note}`。语义：`kernel.cancel()` 真打断协程 → 会话状态回 `plan_ready`（前端输入区/附件/发送恢复）→ 消息流与时间线各写一条 `metadata.user_stopped=true` 的说明（续跑的主 agent 由此知道是**用户主动喊停**，不是异常终止）。没有活任务时 `stopped=false` 且**不伪造**说明。详见 [docs/agent-stop-control-2026-11-02.md](agent-stop-control-2026-11-02.md) |

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

### 失败响应（结构化错误）

`/api/voice/synthesize` 与 `/api/voice/dub` 的失败响应不是一句人话，而是带分类的对象
（`services/voice_errors.py`），前端据此决定「去配凭据」还是「重试」：

```json
{"detail": {"message": "provider 鉴权失败（请检查 API Key / 权限）：401 …",
            "error_code": "auth_failed", "stage": "auth",
            "provider_id": "cosyvoice", "retryable": false}}
```

| 字段 | 取值 |
|---|---|
| `error_code` | `auth_failed` / `rate_limited` / `provider_timeout` / `provider_not_found` / `voice_not_found` / `uploader_missing` / `quota_exceeded` / `invalid_request` / `stream_broken` / `decode_failed` / `unknown` |
| `stage` | `config` / `auth` / `request` / `stream` / `decode` / `unknown` |
| `retryable` | 超时、限流、流断连为 `true`；鉴权、额度、参数、未知错误为 `false`（**未知不重试**，避免编程错误把预算烧光） |

错误文本会剥掉 `sk-…` / `Bearer …` / `api_key=…` 片段，密钥不进日志与响应。
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
| `GET /api/plugin/contributions?kind=&slot=&owner=` | 已注册贡献清单（kind:id → owner）。前端按 slot 拉取插件 UI 贡献，**唯一的贡献数据源** |
| `GET /api/plugin/prompt-symbols` | **提示词符号清单与来源**：26 常量 + 8 builder、签名、是否被插件覆盖、覆盖它的 plugin id、`wired`（false = 还没有消费方在运行时解析，**覆盖了也不生效**）、最近被拒绝的覆盖原因。不返回提示词正文，只给长度 |

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
| `GET /api/providers/schemas?kind=` | **插件声明的 provider 配置 schema**（`config_schema` + `capabilities`）。前端按它渲染表单，不写死厂商字段；`secret: true` 的字段服务端加密、读回掩码、回传掩码=保留旧值 |
| `GET /api/providers/capabilities?kind=&provider=&model=` | **能力协商结果**（直接问实现 `impl.capabilities(model)`）：`streaming/tool_calls/vision/json_mode/reasoning_effort/reasoning_content/…`。解析不到返回 `capabilities=null` + `error`，不抛 404（诊断接口要能显示「没装」） |
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
| `dub_script` | 文案切分 + 逐段配音（**由 `plugins/tts_feature` 提供**，2026-11-03 起不在 engine；插件不在 → 能力缺失 → `/api/voice/dub` 503） |
## 工作区 (/api/workspace)

> 实现依据：[workspace-prd-2026-10-24.md](workspace-prd-2026-10-24.md)。
> 一个项目 = 一个工作区目录（工程文件 / 素材 / 下载 / 成片 / 缓存同根，可整体备份）。

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/workspace` | 创建或取回（幂等）。body：`{project_id, name?, root?, auto_index?, vision?}`；`root` 必须位于 `settings.workspace_dir` 内 |
| GET | `/api/workspace/{project_id}` | 状态：`counts{total,video,image,audio,indexed,pending}` / `index{vision,provider,spent_today_usd}` / `scan{auto,last_scan_at}` / `health` |
| POST | `/api/workspace/{project_id}/enable` | 只改开关：`{auto_index?, vision?}` |
| POST | `/api/workspace/{project_id}/scan` | 扫描 `materials/` 并索引新增；`dry_run=true` 只列 `would_index` |
| GET | `/api/workspace/{project_id}/materials?kind=` | 已索引素材（sha256/路径/状态/标签/视觉摘要） |
| POST | `/api/workspace/{project_id}/materials?overwrite=false` | 上传到 `materials/<kind>/`（devplan T3.1）：**1 MiB 流式分块写 `.part` → `os.replace` 原子替换**；超 2 GiB → 413 并清 `.part`；同名默认 **409**（覆盖需 `overwrite=true`）；不支持扩展名 → 400；返回 `{saved,kind,bytes,overwritten,scan}` |
| GET | `/api/workspace/{project_id}/legacy-assets` | **旧素材库只读清单**（devplan T3.3）：`{count, assets:[{asset_id,filename,kind,sha256,path,exists,in_workspace}], legacy_dir}`；`in_workspace=true` 表示工作区已有同内容文件 |
| POST | `/api/workspace/{project_id}/legacy-import?dry_run=true` | **把旧素材复制进工作区**（devplan T3.3）：只复制不移动、SHA256 去重、默认 dry_run；`{planned[],migrated,reused,missing,failed,scan}` |
| GET | `/api/workspace/{project_id}/media-tree?limit=2000` | 按**目录结构**列出工作区全部媒体（实时磁盘真相，不是索引注册表）；系统目录跳过 |
| DELETE | `/api/workspace/{project_id}/materials/{sha256}?purge=` | 默认只删索引记录；`purge=true` 才删磁盘文件 |
| GET | `/api/workspace/{project_id}/events?since=` | **JSON**：扫描/索引进度事件（`{events[], last_seq}`，立即返回；旧实现是 SSE，普通 GET 会挂到超时） |
| GET | `/api/workspace/{project_id}/events/stream?since=` | SSE：同一份事件的长连接版本（`seq` 游标 + `data:` 帧） |

**目录约定**：`materials/` 是唯一用户写入区（系统下载也落这里），新文件由轮询
（`settings.workspace_scan_interval_sec`，默认 20s）发现；`assets/` 是
`AssetManager` 视图（files/thumbnails/index.json），`renders/`/`cache/`/`logs/` 由系统写。

**索引流水线**：`MediaIndexer.extract_metadata/classify` → `AssetManager.import_file`
（sha256 去重 + 缩略图）→ `content_vision.label_shot`（CLIP 本地 / LLM 描述，按
`settings.vision_provider`）。视觉预算：`workspace_vision_daily_budget_usd`，超限标
`deferred` 但索引仍完成。








## 展示 spec 字段（`display_specs`）与契约端点

> 当前形态见 [docs/genui.md](genui.md)。一句话：**能力在插件，展示层只读，权威数据在上层字段**。

### `CreativeBrief.display_specs` / `ProductionPlan.display_specs`

| 字段 | 形状 | 说明 |
|---|---|---|
| `display_specs` | `{"<spec_id>": {"version":1,"items":[...]}}`，默认 `{}` | 展示容器（键由插件契约声明，当前是 `brief` / `plan`）；**只读展示**，确认与执行仍用上层字段 / `markdown_content` |

- 写入：服务层装配点 `services/display_spec/assemble.py::ensure_display_specs(source, payload)` 按**插件契约**清洗/生成；
  插件未安装或被禁用时**原样返回、不创建容器**（A6 口径）。
- 读取：`GET /api/requirements/session/{session_id}` 返回的 `creative_brief` / `production_plan` 里带该字段；
  2026-11-03~07 的老会话带 `genui` 键，由 `get_session` **读时归一**（浅拷贝、只补不存）。
- **不影响业务判定**：`_brief_hash` 排除 `{"genui", "display_specs"}`；确认、幂等、规划书作废判定都不读它们。
- 老键 `genui`：**只读兼容**，新写入一律 `display_specs`（不双写）。

### `GET /api/plugin/contributions?kind=display_spec`

前端契约的**运行期唯一来源**（插件可启停，构建期快照必然与运行态不一致）。

- 返回：`{"contributions": [{"kind": "display_spec", "id": "brief", "owner": "genui", "meta": {...}}]}`；
  `meta` 含 `spec_id/version/source/components/containers/actions/tones/badge_tones/text_sizes/limits/order/trust`（`recipe` 不下发前端）。
- 前端：`src/features/genui/contractSource.ts` 30s TTL 缓存 + 复用 `onPluginSlotsInvalidated` 失效广播；
  拿不到契约 → 不渲染（不猜白名单）。

