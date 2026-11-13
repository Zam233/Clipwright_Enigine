# Agent 动效视频插件：Coding Plan（subprocess + ClipKind + 渲染时插件回调）

> 对应 PRD：[agent-motion-video-plugin-prd.md](agent-motion-video-plugin-prd.md)  
> 分支：`feat/zam_agent_motion_video`（engine 与前端）  
> 核心原则：engine 只提供通用贡献、Timeline/ClipKind、双向 RPC、workspace、job、artifact 和合成接口；动效视频类型、工程格式、工具、Skill、React/TSX、运行时和渲染逻辑全部由独立 subprocess 插件实现。  
> 本计划是设计级 Coding Plan，开始写生产代码前必须完成 M0 的契约与安全 Gate。

## 0. 设计裁决

### 0.1 目标运行形态

本计划**必须支持 subprocess 插件**，不以 in-process 作为 MVP 绕过方案。现有 subprocess 机制只能 host→worker 单向请求，且不支持 Category/Skill/ClipKind，因此先扩展协议和贡献适配，再做领域插件。

### 0.2 两类时间线数据必须分开

- `clipwright/schema/timeline.py` 的核心 `Timeline` / `Clip` 是 engine 公开契约。Category 的 `post_process_timeline(Timeline)`、`validate_clip(Clip)` 以及 RPC 传递经过 schema 校验的核心数据是合法的通用接口。
- 插件私有 `project.json`、`timeline.json`、`scene-*.tsx`、`runtime.lock.json` 是插件格式。engine 不解析、不校验其业务字段，只通过 workspace/artifact API 访问不透明文件。
- engine 自身项目根的 `project.json`/`timeline.json` 与插件命名空间文件必须物理隔离。

### 0.3 渲染模型

选择“渲染时插件回调”（方案 B）：

1. Agent/插件先写 Timeline，其中 `Clip.kind` 是插件注册的 `clip_kind`；
2. engine 进入 render 时按 kind 找 renderer contract；
3. engine 通过双向 RPC 给插件发送 `clip.render`；
4. 插件在 worker 内通过 host broker 请求 workspace/job/runtime，执行自己的代码；
5. 插件返回标准媒体 artifact（MVP 为单个透明视频，见 M5 的 `media_overlay.v1` 容差表；不是每帧一次 RPC）；
6. engine 只校验通用结果并合成整片。

engine 不执行插件返回的 Python/JS/HTML/TSX。逐帧回调另立 `frame_provider.v1`，不混入 MVP。

运行时品牌只出现在插件侧：插件用 React/TSX/Remotion/Python 是它自己的实现选择；engine 只接受不透明 `runtime_id` 与已批准的 job policy，不按语言或框架探测、安装、分支。

## 1. 现状取证与基线

### 1.1 已有能力（实施前固定测试）

| 能力 | 真实位置 | 现状 |
|---|---|---|
| Category/P​​rompt | `clipwright/category/base.py`、`schema/category_spec.py`、`services/type_spec_runtime.py` | 已有 `PromptBundle`：身份 3 槽 + 阶段 7 槽；无 `requirements` 槽；Category 还必须实现 `translate_persona`/`get_shot_params` |
| Contribution | `plugins/contribution.py`、`contribution_dispatch.py` | 有 18 类枚举；需新增 `clip_kind`，并接入门面/manifest/回收 |
| subprocess 启动 | `plugins/runtime/host.py`、`worker.py`、`loader.py` | 可启动/握手/调用/停止；只支持当前 RPC kinds，非支持 kind 会整插件失败 |
| subprocess 代理 | `plugins/runtime/proxies.py` | 目前只为 tool/hook/material_source/prompt 造代理；ui_slot/export_preset 是声明式 loader 分支 |
| RPC | `plugins/runtime/protocol.py`、`host.py`、`worker.py` | v1 是 host→worker request/response；worker 无出站请求；host 对未知 response id 丢弃 |
| Timeline | `schema/timeline.py` | `ClipKind` 是封闭枚举，render.py 只处理内置 kind |
| renderer | `services/render.py`、`api/render.py`、`providers/render/` | 有本机 renderer backend 与 animation/mg 路径；没有 plugin ClipKind render dispatch |
| workspace | `services/workspace.py`、`tool/fs_tools.py`、`tool/surface.py` | 有 project workspace、materials/renders/logs/artifacts 目录和相对路径入口；必须避免插件文件覆盖 engine 根快照 |
| artifact | `services/stage_subagent.py:persist_artifacts`、`tool/artifacts.py`、`api/workspace.py` | 已有部分文件式产物链，但没有统一 owner/MIME/retention 列表模型 |
| render job | `api/render.py` 模块级队列 + Mongo `render_tasks`、`services/render.py` kill tree | 这是现有内置渲染队列，不直接当 Plugin Job Broker 复用；需抽通用 job 生命周期或新建 broker |
| task cancel/events | `agent_kernel.cancel()`、`schema/task.py`、前端 `eventSchema.ts` | 事件集合为硬契约；job 取消和 plugin job_id 绑定尚不存在 |
| artifact UI | 前端 `src/features/task/TaskArtifacts.tsx`、`src/features/copilot/artifactOpen.ts` | 主要单一 `output_path`/timeline 结果；需要通用 artifact 列表与 MIME 播放器 |

### 1.2 M0 输出

- 真实调用链快照和兼容测试；
- ADR-1：插件 workspace 根（项目 workspace 的 `plugins/<plugin_id>/<project_id>/`，还是 PluginData，最终只能选一套）；
- ADR-2：runtime registry/审批策略；
- ADR-3：artifact schema/retention/自动登记 vs 插件显式登记；
- RPC v2 契约草案和死锁模型；
- ClipKind/render contract/preview contract 的 JSON Schema；
- 事件复用方案（优先复用既有 `progress`/`artifact` 语义，不直接新增事件类）。

## 2. M1：双向 subprocess RPC v2（engine，5–8 天）

### 2.1 协议目标

在现有 NDJSON 管道上建立可重入双向 request/response/event：

```text
host ── request(id=h:1) ──> worker
host <─ response(id=h:1) ── worker
host <─ request(id=w:1) ─── worker
host ── response(id=w:1) ─> worker
host <─ event(job/progress) ─ worker
```

建议消息字段：`version`, `direction`, `id`, `kind=request|response|event`, `method`, `params`, `scope`, `deadline`, `parent_call_id`。实现不可把 worker 发来的 request 当作未匹配 response 丢弃。

### 2.2 必须解决

- host request ID 与 worker request ID 分命名空间，禁止碰撞；
- host 处理 worker request 时，不能同步阻塞等待同一个 worker response；使用独立 async broker task；
- 同一连接上允许有限并发，设置 in-flight 上限、递归深度和单帧/累计字节上限；
- request/response 超时后取消下游，并向上游返回稳定错误码；
- worker death、stdin/stdout broken pipe、宿主取消、插件 unload 时清空所有 pending；
- event 不是任意 task 新事件：优先映射为既有通用 progress/artifact 载荷，不能未经契约评审增加事件类型；
- 版本协商：不支持 v2 的 worker 必须 fail closed，不回退到半双工假成功。

### 2.2b 重入时序与取消矩阵（必须写进设计与测试）

渲染回调的典型时序：

```text
host: clip.render(id=h:7) ──▶ worker
worker: workspace.read(id=w:1) ──▶ host
host:  读文件 → response(id=w:1) ──▶ worker
worker: job.submit(id=w:2) ──▶ host
host:  create job(job:42) → response(id=w:2) ──▶ worker
host:  progress(job:42) 事件 ──▶（复用既有 progress 载荷）
worker: 等待 job.done 或取消
worker: response(id=h:7) ──▶ host
```

规则：

- host 在等待 `h:7` 的 response 期间，必须能处理 `w:1`、`w:2`；用独立 task 做 broker 分派，不能在同一个 await 链里串行。
- `parent_call_id` 记录 `w:*` 属于哪个 `h:*`；`h:7` 取消/超时时，级联取消其下所有 `w:*` 与关联 job。
- 同方向 ID 单调递增、不复用；跨方向 ID 可重复但必须带 `direction`，响应必须回给发起方向。
- 任何一方收到“不属于自己的 response id”即协议错误，不能像现状一样静默 `continue`。

取消矩阵（每一格都要有测试）：

| 触发 | 正在执行的 tool/render call | 已提交 job | 终态 |
|---|---|---|---|
| host 取消 task | 发送 cancel，等待 worker ack 或超时 | 取消 job + 杀进程树 | call=cancelled，job=cancelled |
| worker 崩溃 | pending 全部失败 | 取消 job | call=worker_dead，job=cancelled |
| 插件 unload | 拒绝新请求，取消在途 | 取消 job | call=unavailable，job=cancelled |
| job 超时 | 向 worker 返回 `JOB_TIMEOUT` | 标记 timed_out | job=timed_out，call 由插件决定成功/失败 |
| host 重启 | pending 丢失 | 标记 interrupted（不伪造成功） | job=interrupted |

### 2.3 实现落点

- `clipwright/plugins/runtime/protocol.py`：v2 message schema、方向、ID、limits、errors；
- `worker.py`：异步写出请求、读取 host response、broker dispatch、request cancellation；
- `host.py`：双向 reader/router、worker request handler、pending namespace；
- `proxies.py`：workspace/job/artifact/log/progress 的插件代理；
- `tests/clipwright/test_plugin_runtime_subprocess.py` 与新并发/死锁测试。

### 2.4 M1 验收

- 一个 fake worker 在一次 tool call 内并发发起 workspace read 和 job submit，host 都能完成；
- worker 等 host response 时，host 不等待 worker 当前 tool response，循环无死锁；
- unknown method/version/frame overflow/timeout/cancel/worker crash 都有稳定错误；
- 100 个并发请求不产生 ID 碰撞；超过 in-flight 上限被拒；
- 不改变现有 v1 插件行为，或明确以协议版本迁移测试证明兼容。

## 3. M2：通用 scoped workspace + artifact registry（engine，5–8 天）

### 3.1 Workspace

在 ADR-1 选定单一根目录后，提供 worker broker 方法：

`workspace.list/read/write_atomic/mkdir/delete/rename/stat/hash`

每个请求携带宿主签发的 scope：`plugin_id/task_id/project_id/workspace_id/lease_id`。scope 由 host 在 tool/clip render 开始时创建，传入 worker，调用时由 host 校验；不依赖 `contextvars` 跨线程传播。

规则：

- 相对路径必须落在当前 plugin/project namespace；拒绝绝对路径、`..`、UNC、drive 切换、junction/reparse point escape；
- engine 根快照 `project.json`/`timeline.json` 与插件 namespace 物理隔离；
- read/write/delete/rename 分别有权限和字节/文件数预算；
- write 支持 `expected_revision`/`expected_sha256`，冲突返回 `WORKSPACE_REVISION_CONFLICT`；
- 合作型插件默认可读范围也按 scope 收窄；若底层无法阻止恶意 Python 读全盘，必须标记为非 OS sandbox，不能宣称 fail-closed 恶意隔离；
- 不把插件私有文件语义放进 engine schema。

### 3.2 Artifact

统一模型：

```json
{
  "artifact_id": "art_…",
  "owner": {"plugin_id":"…","task_id":"…","project_id":"…","job_id":"…"},
  "path": "plugins/<plugin_id>/<project_id>/renders/pilot.mp4",
  "mime": "video/mp4",
  "size": 123,
  "sha256": "…",
  "role": "preview|render|image|report|project|log",
  "status": "pending|ready|failed|expired",
  "retention": "task|project|permanent"
}
```

插件只能申请登记，宿主核验 owner/path/size/hash/MIME 并返回 artifact ID。未决定“自动扫描输出”还是“插件显式 register”前不写生产实现；推荐显式登记 + 宿主校验，避免扫描任意文件。

实现候选：复用现有 workspace/artifacts 文件路径和媒体读取安全校验，但新增通用 registry/owner/schema；不把内置 `render_tasks` 当 artifact store。

### 3.3 M2 验收

- 跨 plugin/task/project 的 list/read/write/artifact request 失败；
- symlink/junction/UNC/drive/path traversal 失败；
- revision 冲突、atomic write crash、大小/文件数限制可测；
- artifact owner、MIME、hash、size 由宿主计算/校验，插件不能伪造；
- 重启后 ready artifact 可查询；expired artifact 按 retention 清理或明确返回 expired。

## 4. M3：Plugin Runtime/Job Broker（engine，7–10 天）

### 4.1 通用 runtime 描述

engine 不认识 Node/Remotion/Python 等品牌，只认识已批准的：

`runtime_id / version / digest / platform / executable policy / env allowlist / input-output policy / approval state`

在 `PluginManifest`/独立 runtime registry 增加严格 schema；`runtime_config` 不再承载任意未校验配置。管理员批准可执行入口和 digest，插件不能自行把新 executable 注入宿主。

### 4.2 Job API

通过 worker→host RPC：`job.submit/status/cancel/list`，request 绑定 workspace lease 与 task/plugin owner。

插件侧把自己的领域参数转换为通用 JobSpec；engine 不实现 motion job handler，不安装/升级运行时。

通用 JobSpec：

```json
{
  "runtime_id": "approved-runtime",
  "argv": ["registered-entry", "operation", "<workspace-relative-input>"],
  "cwd": "<workspace-relative-dir>",
  "inputs": [{"artifact_id":"…","path":"<relative>"}],
  "outputs": [{"path":"<relative>","max_bytes":536870912}],
  "timeout_ms": 900000,
  "limits": {"max_output_bytes":536870912,"max_concurrency":1}
}
```

宿主强制：`shell=False`、argv 数量/长度、cwd/input/output scope、env 白名单、日志脱敏、timeout、取消、进程树回收、job 状态和 idempotency。若 OS 无法实施 CPU/memory/network 限制，结果标为 capability unavailable；不要把“没有 shell”写成完整沙箱。

### 4.3 Task/插件生命周期

新增 job registry：`job_id → task_id/project_id/plugin_id/worker_id/parent_call_id`。现有内置 `render_tasks` 保持独立，不复用其语义。

状态机（唯一终态，不可回退）：

```text
queued ─▶ running ─┬─▶ succeeded
                   ├─▶ failed
                   ├─▶ cancelled
                   ├─▶ timed_out
                   └─▶ interrupted   (host restart)
queued ────────────┬─▶ cancelled        (取消发生在排队期)
                   └─▶ interrupted
```

竞态规则：

- cancel 与 success 同时到达：以**宿主先落库的终态**为准；若 success 已提交 artifact 且任务被取消，artifact 标记 `orphaned` 而非删除；任务层面仍报 cancelled，不得把取消报成成功。
- 重复 submit：以 `idempotency_key` 去重，返回同一 job_id 与状态；没有 key 时每次新 job，但必须各自有 run_id。
- 宿主重启：running → interrupted；由插件工具决定是否重提，engine 不自动重跑。
- 插件 unload / worker death：在途 job → cancelled，向等待方返回 `JOB_CANCELLED`/`WORKER_DEAD`。
- artifact 状态随 job 迁移：pending → ready / failed / orphaned；expired 只由 retention 触发。

每个迁移都要有单测与一个并发测试（cancel 与完成同时触发）。

进度复用通用 `progress`/artifact 返回或宿主轮询，不新建 motion 事件类型；前端现有 31 类事件契约不能被任意新增破坏。

### 4.4 M3 验收

- fake runtime 在 Windows 上用受控 fixture 执行；禁止 shell 注入；
- env 中不存在 API/model secrets；
- job owner 越权、输出路径劫持、超时、取消进程树、worker death、restart recovery 有测试；
- job status/progress/artifacts 可与 task 关联；
- 一个 worker 可在 tool/clip render 中并发 workspace 与 job RPC，无死锁。

## 5. M4：subprocess Category/Skill/ClipKind 贡献适配（engine，5–8 天）

### 5.1 Category

worker `describe` 返回声明式 CategorySpec：PromptBundle、rules、traits、declares、display metadata；engine schema validate 后适配 `CategoryRegistry`。必须要求插件实现 `translate_persona` / `get_shot_params` 的等价声明或 RPC。

- 纯数据 prompt/rules/traits/declares 不让 engine 解析 motion 字段；
- `post_process_timeline(Timeline)` / `validate_clip(Clip)` 通过 v2 RPC，传的是核心 Timeline/Clip schema；
- RPC 不接收/解析 `project.json`、私有 `timeline.json`、`scene-*.tsx`；
- 不支持的声明不能静默跳过；整个 plugin fail closed 或返回明确 `declared-but-unavailable`（按契约选择）。

### 5.2 Skill

worker 返回 Skill metadata/input schema；`skill.execute` 通过 v2 RPC 转发，由 worker 自己编排领域工具，结果大小/超时/错误码受宿主限制。

### 5.3 ClipKind

新增 `clip_kind` contribution：

```json
{
  "id":"plugin_motion_scene",
  "meta":{
    "display_name":"代码场景",
    "track_label":"代码动效",
    "icon":"Film",
    "metadata_schema":{},
    "render_contract":"media_overlay.v1",
    "preview_contract":"artifact.v1",
    "capabilities":["preview","render"]
  }
}
```

engine 建立 `ClipKindRegistry`，内置 enum 映射到内置定义，插件 kind 以字符串注册；门面负责声明白名单、冲突、owner、卸载回收。

Timeline/Clip 兼容策略：

- 保持现有内置 `ClipKind` 常量；将 Pydantic `Clip.kind` / `Track.kind` 从仅 Enum 改为“内置值或已注册字符串”的 validator；
- 保存/最终化按 registry 查 schema；未知 kind 显式失败；
- `render.py` 增加 plugin kind 分派，并对未知 kind 显式报错，禁止静默丢失；
- 插件禁用后历史 clip 仍可读取，但标记 unavailable，渲染返回明确错误/占位 artifact。

**消费者迁移（不能只改 validator）**

当前直接比较 `ClipKind` 的位置至少包括：`agents/quality_agent.py:155-304,510-867`、`services/render.py:1440-1487`、`services/preroll.py`、`services/narration_events.py`、`services/trait_collector.py`、`agents/edit_agent.py`/`animation_agent.py` 的 kind 判断。M4 必须：

- 引入 `ClipKindRegistry.resolve(kind) -> ClipKindDef | None` 与 `is_kind(clip, id)`；内置 kind 与插件 kind 都经它解析。
- 内置分支保持枚举比较；插件 kind 一律走 registry，禁止在 Agent 内新增字符串硬编码。
- 未迁移点遇到插件 kind 必须显式走 unknown 分支（告警或失败），不能落到内置默认逻辑。
- 增加回归测试：内置 8 种 kind 行为不变；插件 kind 在 quality/preroll/narration/trait 中不被误判为 video/animation。

**schema 与卸载语义（可测）**

- `metadata_schema` 限定 JSON Schema 子集（type/required/enum/properties/maxProperties/maxLength/maxItems），带 `schema_version`；同 id 冲突拒绝；major 升级换新 id，minor 只加可选字段。
- 插件卸载后的四态：读取保留原字符串并标 `kind_unavailable`；编辑只允许通用字段；保存原样保留不校验插件 schema；渲染失败或按 `partial_render` 生成占位。前端显示不可用原因。四种状态各有测试。

## 6. M5：渲染时插件回调（engine + 插件契约，7–10 天）

### 6.1 通用 render contract

新增版本化 `media_overlay.v1`：

- engine→worker：`clip.render` + `ClipRenderRequest`（kind/clip/task/project/time/fps/size/metadata/input artifacts/settings）；
- worker→engine：`ClipRenderResult`（artifact_id、mime、width/height/fps/duration、alpha、warnings）；
- `metadata` 私有部分只按大小、JSON 深度和 schema 引用限制；engine 不读取插件工程文件。

**MVP 媒体契约**：只接受一个带 alpha 的透明视频（具体编码在 M0 冻结，候选 H.264/H.265+alpha 或 ProRes 4444）；不接受帧序列。校验项与容差：

| 项 | 规则 | 失败行为 |
|---|---|---|
| 尺寸 | 等于 clip width/height；不等时按 `fit: contain` 缩放居中，`fit: strict` 直接失败 | 稳定错误码，不产出成片 |
| 帧率 | 等于 clip fps（容器级 ±0.01） | 失败，不补帧 |
| 时长 | ±1 帧取整误差 | 超差失败，不静默截断 |
| 时间基 | 从 0 开始，与 clip `start_sec` 对齐 | 不对齐失败 |
| alpha | 必须存在透明通道 | 无 alpha 失败，不当黑底视频 |

合成规则：作为上层 overlay 进入现有 ffmpeg 链，顺序与普通 overlay 一致；不做色彩空间转换或运动模糊。一次 callback 内执行整段 render，不逐帧 RPC。

### 6.2 Preview

插件工具可以提交 `preview` job；结果登记为通用 artifact。engine 提供 preview contract/通用 artifact API，前端复用 MIME 播放器。若需要渲染当前 clip 的精确帧，走 `preview-frame` 的通用 contract，不增加 motion 专用 endpoint。

### 6.3 合成器

在 `services/render.py` 提取通用 plugin overlay 输入适配：

- 验证 artifact owner 和媒体 probe；
- 验证 clip duration/fps/width/height/alpha 与 6.1 的容差表；
- 透明视频进入既有 overlay/ffmpeg 合成链；帧序列不在 MVP；
- 插件调用失败、artifact 不匹配、kind unavailable 都是明确 render failure 或 unavailable，占位策略必须可观察；
- engine 不执行插件返回的脚本、HTML、TSX。

不要为某个插件添加专用分支；若某编码在本机合成链不可用，返回 capability unavailable，而不是悄悄转码或丢帧。

## 7. M6：通用 Artifact 列表与播放器（engine + 前端，4–7 天）

### 7.1 Engine

- 新增通用 `GET /api/artifacts?task_id=&project_id=&job_id=&owner=&status=&cursor=&limit=`，owner 校验；`limit` ≤100，cursor 分页，按 `created_at desc`；
- 返回 `items[]`（含 role/mime/size/status/created_at/failure_reason）+ `next_cursor` + `total_estimate`；
- MIME 由宿主按内容/扩展名计算，不接受插件自报；allowlist 与 PRD FR-9 一致，其余强制下载；
- `GET /api/artifacts/{id}/raw` 做 owner + task/project 归属校验，复用现有媒体安全读取；不返回本地绝对路径；
- 支持 ready/pending/failed/expired/orphaned 和 retention；expired 保留元数据、隐藏内容；
- 任务结果保持兼容，`output_path` 不删除，但不再是唯一产物来源。

### 7.2 Frontend

- `TaskArtifacts` 从 timeline-only 单个 output 扩展为 artifact list；
- `video/*` 进入通用 video player，`image/*` 图片预览，`text/*`/JSON/Markdown 文本查看，其他类型下载；
- 不执行 HTML/JS；下载/系统打开遵循现有安全语义；
- 显示 job 状态、错误、大小/时长（若有 probe）；
- 无 Timeline 时仍渲染 artifact 区块；`has_timeline` 只控制“在编辑器中打开”；
- 分页用 cursor（加载更多），失败/过期项显示原因而不是消失；
- 任何视频类型的插件 artifact 都能使用，不能出现 motion-specific 判断。

### 7.3 验收

使用 fixture plugin 产生 MP4、PNG、JSON、工程压缩包：任务页列出四项；MP4 能播放；PNG 能预览；JSON 能阅读；压缩包能下载；不存在 Timeline 时同样可见；权限越权失败。

## 8. M7：独立动效插件 MVP 与集成验证（插件仓库，1–2 周）

插件负责全部领域内容：

- Category + PromptBundle + rules/traits/declares；
- tool/skill；
- 私有工程 schema 和命名空间文件；
- `clip_kind` 声明与 metadata schema；
- React/TSX/Remotion 场景、runtime lock、style/shot 模板；
- `clip.render` 适配器；
- preview/render/QA/deliver；
- 许可、runtime manifest、平台说明。

建议私有工程目录：

```text
<project-root>/plugins/<plugin_id>/<project_id>/motion/
  project.json
  timeline.json
  scenes/scene-001.tsx
  components/*.tsx
  assets/manifest.json
  runtime.lock.json
  reports/qa.json
```

工程文件只由插件读写；engine 只通过 workspace API 读写不透明文件。

端到端：brief → storyboard → scene file → custom ClipKind insertion → preview artifact → 用户反馈 → render-time callback → final artifact → QA artifact。

## 9. 文件改动策略

### 9.1 Engine 允许改动（通用）

- `clipwright/plugins/runtime/{protocol,worker,host,proxies}.py`：双向 RPC、scope、broker；
- `clipwright/plugins/contribution.py`、`contribution_dispatch.py`、`schema/plugin.py`：clip_kind/runtime/job/category/skill 声明与校验；
- 新通用 `clipwright/services/plugin_workspace.py`、`plugin_artifacts.py`、`plugin_jobs.py`（最终按 M0 ADR 调整）；
- `clipwright/schema/timeline.py`：内置 kind 兼容 + 注册 kind validator，不添加 motion 字段；
- 新 `clipwright/services/clip_kind_registry.py` / renderer dispatch adapter；
- `clipwright/services/render.py`、`api/render.py`：通用 plugin ClipKind render contract 合成；
- `clipwright/api/plugin.py`、`api/workspace.py`、新 artifact API：通用 owner/list/read/download；
- `clipwright/main.py`：broker/registry/恢复初始化；
- `clipwright/loader.py`：生命周期、贡献回收、runtime leases；
- 前端 `TaskArtifacts`/通用 artifact components、时间线/预览的通用 kind fallback；
- tests、API reference、development docs 和本 PRD/Coding Plan。

### 9.2 明确不进入 engine

- `motion_project`、`motion_scene` 等具体工具/Skill；
- motion project schema、Shotcraft、mgstyles、动画类型、React/TSX 模板；
- Node/Remotion/Python 领域命令和 runtime 安装器；
- 具体插件的 metadata 字段、视觉 rubric、风格/镜头库、QA 规则；
- 插件私有工程文件解析器。

## 10. 测试策略

| 层 | 验收 |
|---|---|
| Protocol | 双向 RPC、request namespace、重入/死锁、frame limit、timeout、cancel、worker death |
| Contribution | clip_kind/category/skill manifest 声明、冲突、卸载、静态 Category adapter、失败闭包 |
| Timeline | 内置 kind 回归、插件字符串 kind 保存/读取/schema、未知 kind 显式失败、禁用 kind 不静默丢失 |
| Workspace | scope、路径穿越、symlink/junction/UNC、revision、task/project owner、root snapshot 隔离 |
| Job | runtime approval/digest、argv no-shell、env scrub、cwd/I/O allowlist、timeout、kill tree、restart recovery |
| Artifact | owner/hash/mime/size/retention、列表/读取/下载、跨 owner 拒绝、expired 状态 |
| Render | fake plugin 返回 media_overlay.v1；只合成透明视频；尺寸/帧率/时长/alpha 容差正反例；probe mismatch/失败/取消可观察 |
| ClipKind | 内置 8 种 kind 行为不变；插件 kind 在 quality/preroll/narration/trait 走 registry；卸载四态；schema 冲突/版本 |
| Job | 状态机唯一终态；cancel×success 竞态；幂等 key；host restart→interrupted |
| Artifact API | cursor 分页、owner、MIME allowlist、expired/orphaned、无 Timeline 列表 |
| Frontend | 任意视频类型 artifact list；MP4 播放、图片、JSON、下载；无 Timeline 仍可见 |
| E2E | subprocess plugin：type/prompt/tool/skill → project → custom kind → preview → feedback → render callback → final artifact |
| Regression | 全量 pytest、前端 typecheck/unit/build/e2e；既有 31 类事件契约不破坏 |

CI 不安装真实 Remotion/浏览器运行时；使用 deterministic fake runtime 和 fake plugin。至少一次手工/隔离环境跑真实插件 runtime，且结果标注为平台集成测试，不把它伪装成普通 CI 沙箱证明。

## 11. 顺序与依赖

```text
M0 ADR/取证/契约冻结
  ↓
M1 双向 RPC v2
  ↓
M2 scoped workspace + artifact
  ↓
M3 runtime/job broker
  ├─ M4 subprocess Category/Skill/ClipKind
  │    ↓
  └──── M5 render-time plugin callback
             ↓
           M6 generic artifact UI/API
             ↓
           M7 independent motion plugin + E2E
```

M4 之前不写领域插件；M5 之前不允许 plugin kind 在 render 中静默降级；M6 必须在插件最终验收前完成，因为插件 MP4/preview 不能只落一个 `output_path`。

依赖澄清：M4 与 M5 的**契约设计**可以并行（render contract、metadata schema、ClipKind 定义），但 M5 的**可运行 dispatch**依赖 M4 的 ClipKind registry 与 subprocess renderer 调用落地；不要把两者当作完全独立可交付。

## 12. Definition of Done

- [ ] M0 ADR 完成：workspace 根、runtime approval、artifact/retention、RPC v2、事件复用、Timeline vs plugin files。
- [ ] subprocess worker 可双向 RPC；无死锁、超时/取消/死亡可回收。
- [ ] Category/Skill/ClipKind 贡献可声明、校验、注册、禁用回收；不支持项 fail-closed。
- [ ] `Clip.kind` 支持已注册字符串 kind，内置 enum 全回归；未知 kind 显式错误。
- [ ] engine render-time 调用插件并只合成标准媒体 artifact；不解析/执行插件私有代码。
- [ ] workspace/artifact/job 有 owner、scope、hash/MIME/size/retention 和安全测试。
- [ ] task cancel→job cancel、worker death、plugin disable、restart recovery 可观察。
- [ ] 通用 artifact 列表/播放器适用于所有视频类型，Timeline 不存在时仍可展示。
- [ ] plugin repo 自己拥有 motion schema/tools/skills/runtime/scene/QA；engine 没有 motion 领域文件和分支。
- [ ] ClipKind 消费者盘点完成：quality/preroll/narration/trait/render 不再直接比较插件 kind；插件卸载后读/编辑/保存/渲染四态有测试。
- [ ] Job 状态机唯一终态；cancel×success 竞态、幂等提交、宿主重启恢复有并发测试。
- [ ] `media_overlay.v1` 只接受单个透明视频，尺寸/帧率/时长/alpha 容差逐项有正反例。
- [ ] 通用 artifact API 支持 cursor 分页、owner 校验、MIME allowlist、expired/orphaned 状态；无 Timeline 时前端可见并可播放。
- [ ] 现有内置视频、renderer、31 类事件、插件和前端回归通过。
