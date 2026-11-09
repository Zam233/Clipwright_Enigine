# Agent 用工程文件制作动效视频：插件 PRD

> 状态：Draft / 待评审  
> 目标分支：`feat/zam_agent_motion_video`（engine 与前端同名）  
> 目标：在 ClipWright 内通过**独立 subprocess 插件**实现“Agent 读写工程文件、生成 React/TSX 场景、在渲染时由插件执行并返回结果”的动效视频工作方式。  
> 核心边界：engine 只提供通用插件接口、Timeline/ClipKind 扩展、受控 RPC、任务作业、工作区和 artifact；视频类型、提示词、工具、Skill、工程格式、场景运行时、预览/渲染算法全部由插件实现。

## 1. 背景与问题

本项目已有类型插件、`PromptBundle`、工具/技能贡献、插件 renderer backend、动态能力解析和 subprocess 插件运行形态。类型提示词可以规定 Agent 的工作步骤，但当前 engine 仍有三个缺口：

1. `Track.kind` / `Clip.kind` 使用封闭的 `ClipKind` 枚举，插件不能声明新的轨道/片段类型；未知 kind 在渲染分派中可能被静默忽略。
2. subprocess worker 只有宿主→worker 的单向请求-响应，插件无法在 Agent 工具或渲染回调期间请求宿主的工作区、artifact 和受控进程执行。
3. 任务产物展示主要围绕单一 `output_path` 和可编辑 Timeline，不能统一列出任意插件/视频类型产生的 MP4、图片、报告和工程包。

本 PRD 不把 dsh-drawai 的画布或 Remotion 实现移植进核心。它规定一个插件可以：

- 贡献一个新的视频类型和提示词；
- 贡献工具、Skill 和 subprocess Category/Skill 代理；
- 贡献新的 `ClipKind`，其元数据 schema 和渲染/预览契约由插件声明；
- 在引擎渲染到该 Clip 时，通过受控 RPC 调用插件；
- 由插件在自己的 worker/受控 job 中执行 Python、Node、Remotion 或其他运行时；
- 返回一个经过宿主登记的渲染结果，由 engine 的通用合成器继续完成整片输出。

## 2. 用户场景

用户选择插件提供的“代码动效视频”类型，向 Agent 说明主题、时长、画幅、风格和素材边界。类型提示词规定工具清单和顺序：

1. 读取/确认 brief、验收标准和声音需求；
2. 生成插件私有工程文件和分镜表；
3. 为一个或多个场景写入 React/TSX 源码；
4. 通过插件工具校验工程并提交 preview；
5. 用户查看 engine 通用 artifact 列表中的预览图/视频；
6. 用户反馈后，Agent 继续读写工程文件并重试；
7. 进入整片渲染时，engine 看到插件 ClipKind，调用插件的渲染适配器；
8. 插件在 worker 内执行受控 job，返回带尺寸、帧率、时长、透明通道等信息的媒体 artifact；
9. engine 将插件结果作为该片段的渲染输入，与普通视频/图片/字幕/音频合成；
10. engine 和插件共同登记最终成片、预览、QA 报告和可恢复工程，通用 artifact UI 向所有视频类型展示。

**重要区分**：

- 核心 `clipwright/schema/timeline.py` 的 `Timeline` 是 engine 既有公开数据契约。Category 的 `post_process_timeline(Timeline)`、`validate_clip(Clip)` 以及跨进程传递经过 schema 校验的 Timeline，不等于 engine 理解插件领域格式。
- 插件私有的 `project.json`、`timeline.json`、`scene-*.tsx`、`runtime.lock.json` 属于插件领域格式，engine 只把它们当工作区文件/artifact，不读取其语义。
- 插件代码只在插件 worker/宿主批准的 job 中执行；engine 不执行时间线里的 Python、JavaScript、HTML 或 TSX。

## 3. 目标与非目标

### 3.1 目标

- 以 subprocess 插件为目标运行形态，不靠 in-process 访问宿主内部对象。
- 增加可声明、可校验、可回收的插件 `ClipKind` 扩展点。
- 增加双向 subprocess RPC，使插件能请求通用 workspace、artifact、job、日志和进度能力。
- 渲染时由 engine 调用插件 ClipKind renderer；插件返回标准化媒体/artifact 结果，engine 负责通用合成。
- 将通用 artifact 列表、预览、播放和下载能力提供给所有视频类型，不绑定 motion。
- 保持现有内置 ClipKind、Timeline、7 段 Agent、SSE 和本机渲染兼容。
- 通过一个独立插件实现 dsh-drawai **工作流层面**的同类流程（工程文件 + React/TSX 场景 + 预览 + 用户反馈 + 渲染成片）；不移植其画布、品牌、素材或私有格式。

### 3.2 非目标

- 不把 `.drawai`/drawio、mxGraph、画布 UI、DrawAI 右侧栏或其前端 bundle 放进 engine。
- 不把 Remotion、React、Shotcraft、mgstyles、字体/图片库或具体场景模板放进 engine。
- 不在 engine 内执行插件工程文件或模型生成的任意脚本。
- 不新增“通用 shell 工具”；插件只能通过声明的 job/runtime 和双向 RPC 请求受控执行。
- 不让 engine 解释插件私有 `project.json` / `timeline.json` / `scene-*.tsx`。
- 不把某一种动效的字段、模板、风格、镜头卡写进通用 Timeline schema 或通用 artifact schema。
- 不要求第一版提供 motion 专用 React 场景编辑器；工程文件由 Agent 工具和插件运行时维护。

## 4. 职责边界

### 4.1 Engine 提供的通用能力

1. **贡献注册与生命周期**：声明、校验、冲突、禁用/卸载回收 `category`、`skill`、`tool`、`prompt`、`clip_kind` 等贡献。
2. **Timeline/ClipKind 宿主契约**：内置 kind 保持兼容；插件 kind 作为字符串注册，拥有 display metadata、metadata schema 和 renderer/preview capability 声明。engine 不拥有插件 kind 的领域含义。
3. **双向 RPC**：worker↔host 的版本化 request/response/event 通道；宿主负责路由、超时、重入、帧上限、取消和归属校验。
4. **Scoped workspace**：按 plugin/task/project 授权读写工程文件；engine 不解释文件内容；插件私有工程必须位于显式命名空间，不能与 engine 根级快照重名。
5. **Plugin Job Broker**：只执行管理员批准的 opaque runtime policy，提供 argv、cwd、输入/输出、超时、取消、日志和资源上限；不按 Remotion/Node/Python 等品牌分支，也不实现任何插件领域的 job handler。领域参数 → 通用 JobSpec 的转换在插件侧完成。
6. **Artifact Registry**：登记 owner、task/job/project、MIME、大小、hash、role、来源和保留策略；提供安全列表、读取、播放/下载引用。
7. **通用 Timeline 合成**：接收插件 renderer 返回的标准化媒体结果，在已知的通用合成模式下与其他片段合成；不执行插件代码。
8. **通用 Artifact UI**：按 MIME 展示所有视频类型产生的 artifact；视频类型不需要各自开发播放器。

### 4.2 插件负责的领域能力

- `BaseCategoryPlugin` 的完整实现，包括抽象方法 `translate_persona()` 和 `get_shot_params()`、提示词包、rules、traits、declares。
- 需求/结构/素材/动画/音频/质量阶段的领域提示词、工具清单、流程和质量门槛。现有 PromptBundle 的身份槽为 `genre/anti_patterns/lexicon`，阶段槽为 `structure/material/edit/animation/audio/subtitle/quality`；没有 `requirements` 槽。需求阶段若需额外提示，使用身份槽或 `PROMPT` 贡献。
- 插件私有工程 schema、React/TSX scene、场景模板、风格包、镜头库、运行时描述和 QA 规则。
- 插件工具/Skill：创建/读取/编辑/校验工程，生成 preview 请求，提交 render callback，解释插件返回结果。
- 插件 ClipKind renderer/previewer 的领域实现：如何把 clip metadata 转为 React/Remotion/Python 输入，如何执行并返回媒体 artifact。
- 插件 runtime/job handler：由插件把领域参数转成通用 job 请求；engine 只执行宿主批准的 runtime policy。

## 5. 功能需求

### FR-1【插件】：视频类型提示词嵌入

插件必须贡献一个 Category，并实现：

- `translate_persona()`、`get_shot_params()`；
- `get_prompt_bundle()`：3 个身份槽 + 7 个阶段槽；
- `get_param_rules()`、`get_signature_traits()`、`get_declares()`；
- 若需 requirements 阶段专属文本，使用 `genre`/`anti_patterns` 或 `PROMPT` 贡献，不声明不存在的 `requirements` 槽。

提示词必须引用插件实际注册的 tool/skill/kind ID，规定读取工程、写入工程、preview、render、反馈、重试和 QA 顺序。提示词不能替代 schema、权限、路径和资源校验。

### FR-2【插件】：工具与 Skill

领域插件至少贡献一组工具和一个 Skill，工具名称和 schema 由插件拥有。建议覆盖：

- `project_create/read/validate`；
- `scene_read/write`（原子写 + revision）；
- `preview`；
- `render` / `render_status` / `render_cancel`；
- `qa` / `deliver`；
- `clip_kind_insert` 或等价的 Timeline 写入工具。

这些不是 engine 内置工具名。engine 只提供它们执行时所需的通用 RPC、workspace、artifact、job 和 ClipKind registry。

### FR-3【Engine】：插件 ClipKind 注册

新增通用贡献类型 `clip_kind`。manifest 逐项声明并由 engine 校验：

```json
{
  "kind": "clip_kind",
  "id": "plugin_motion_scene",
  "meta": {
    "display_name": "代码场景",
    "track_label": "代码动效",
    "icon": "Film",
    "metadata_schema": {},
    "render_contract": "media_overlay.v1",
    "preview_contract": "artifact.v1",
    "capabilities": ["preview", "render"]
  }
}
```

字段含义只到通用层：`id`、展示元数据、schema、契约版本和能力标记。engine 不理解 schema 内的 motion 字段。

- `Clip.kind` / `Track.kind` 允许内置 kind 字符串和已注册插件 kind；保留 `ClipKind` 枚举作为内置常量兼容层。
- 写入/保存/最终化时按已注册 kind 查 metadata schema；未注册 kind、schema 不匹配或插件已卸载时显式失败。
- 插件 kind 必须声明 renderer/preview capability；不能注册一个会在渲染时静默丢弃的 kind。
- 插件禁用后，历史 Timeline 不删除；读取时显示 unavailable，渲染时返回明确错误/占位 artifact，不静默跳过。

**兼容与迁移（必须实现，不能只放宽字段）**

现有代码大量直接比较 `ClipKind` 枚举（例如 `quality_agent.py:155-304`、`render.py:1440-1487`、`preroll.py`、`narration_events.py`）。实施时必须：

- 盘点全部消费者，把“内置 kind 判定”与“插件 kind 判定”分开：内置路径继续用枚举；插件路径统一走 `ClipKindRegistry.is_kind(clip, id)` / `resolve_definition(kind)`，禁止在 Agent/渲染器中新增 `if kind == "某插件kind"`。
- 未迁移的消费者遇到插件 kind 必须显式走“未知类型”分支（告警/失败），不得按内置默认路径静默处理。
- `metadata_schema` 限定为 JSON Schema 子集（type/required/enum/properties/maxProperties/maxLength/maxItems），带 `schema_version`；同一 `clip_kind` 只能有一个定义，冲突按门面策略拒绝；major 升级需注册新 id，minor 只允许加可选字段。
- 插件卸载/禁用后的历史 clip 行为必须固定为：**读取**保留原字符串并标记 `kind_unavailable`；**编辑**只允许改 clip 的时长/位置等通用字段，不允许改 metadata；**保存**原样保留不校验插件 schema；**渲染**返回失败或占位 artifact（由任务策略决定），**预览**显示不可用原因。四种行为都要有测试。

### FR-4【Engine + 插件】：渲染时插件回调

选择 B：插件在 engine 渲染到对应 Clip 时执行，并返回结果；engine 不执行插件时间线代码。

engine 向插件 renderer 发送通用 `ClipRenderRequest`：

```json
{
  "contract": "media_overlay.v1",
  "plugin_id": "...",
  "clip_kind": "plugin_motion_scene",
  "clip_id": "...",
  "task_id": "...",
  "project_id": "...",
  "start_sec": 0,
  "duration_sec": 4,
  "fps": 30,
  "width": 1920,
  "height": 1080,
  "metadata": {},
  "input_artifacts": [],
  "render_settings": {}
}
```

其中 `metadata` 与 `input_artifacts` 是不透明但有大小/类型上限的 payload；engine 不解析插件私有工程文件。

**执行分工（与 FR-8 的边界一致）**：`React/TSX/Remotion/Python` 这些字眼只描述插件侧的实现选择，engine 不为任何一种做分支、探测或安装。engine 只做三件事：把 `ClipRenderRequest` 交给插件的 renderer RPC；按管理员批准的 runtime policy 执行插件提交的通用 JobSpec；校验返回的通用媒体结果并合成。若插件需要 Node/Remotion，由插件自己的 runtime 描述与 job 请求表达，engine 只按不透明 `runtime_id` 执行。

插件在 worker 内：

1. 校验自己的 metadata/schema；
2. 通过 host broker 请求已批准的 workspace/artifact/runtime/job；
3. 按插件自己声明的 runtime 执行场景代码（语言/框架由插件决定，engine 不感知品牌）；
4. 返回 `ClipRenderResult`：artifact 引用、媒体类型、宽高、fps、duration、alpha、warnings。

engine 只校验通用媒体契约、artifact owner、尺寸/时长预算，然后把返回媒体按 `media_overlay.v1` 合成到整片。不得把插件返回的脚本或 HTML 当作 engine 代码执行。

**MVP 媒体契约收窄**：`media_overlay.v1` 第一版只接受**一个带 alpha 的透明视频**（H.264/H.265 + alpha 或 ProRes 4444，具体编码在 M0 冻结），不接受帧序列。必须定义并可测：

- 尺寸：与 clip 的 `width/height` 一致；不一致时按 `contain` 缩放并居中，或在 strict 模式直接失败（由 clip 的 `metadata.fit` 决定，缺省 strict）。
- 帧率：与 `fps` 一致；允许 ±0.01 的容器级误差，不做补帧。
- 时长：`duration_sec` 允许 ±1 帧的取整误差；超出即失败，不做静默截断。
- 时间基：从 0 开始，与 clip 在时间线上的 `start_sec` 对齐；合成时不再二次偏移。
- alpha：必须保留透明通道；检测到无 alpha 时按 overlay 失败处理，不自动当黑底视频。
- 合成：作为上层 overlay 参与现有 ffmpeg 合成链，顺序与普通 overlay 一致；不做运动模糊/色彩空间转换。

帧序列和逐帧回调推迟到 `frame_provider.v1`，不能在这一版里用“先写帧序列、engine 自己拼”的方式绕过契约。

### FR-5【Engine】：subprocess 双向 RPC

当前 worker 只有 host→worker 请求。必须新增协议 v2：

- worker 可向 host 发起 `workspace.*`、`artifact.*`、`job.*`、`log.*`、`progress.*` 请求；
- host 在等待 `tool.call` 或 `clip.render` 响应时仍能处理 worker 请求，禁止单管道死锁；
- request ID 分方向/分命名空间，response 不可冒充 request；
- 所有帧有大小、嵌套、并发和超时上限；取消会结束关联 pending calls；
- worker/host/插件/任务归属写入每次请求上下文；
- 插件死亡、宿主取消、超时和卸载都返回稳定错误码。

不允许用 `shell` permission 直接放行 `subprocess`。插件执行子进程只能走宿主 Job Broker。

### FR-6【Engine】：Category/Skill subprocess 适配

插件声明的 Category 静态部分（PromptBundle、rules、traits、declares、display metadata）以 schema 形式返回，由 engine 适配到现有 `CategoryRegistry`；`post_process_timeline(Timeline)`、`validate_clip(Clip)` 等需要执行逻辑的调用通过版本化 RPC，输入输出使用核心 `Timeline`/`Clip` 契约，不使用插件私有工程文件 schema。

Skill 以 metadata/schema 注册，执行请求通过 RPC 转发。若适配失败或声明未授权，插件加载 fail-closed；不能把不支持的 kind 静默跳过。

### FR-7【Engine】：通用 Workspace 与 Artifact

插件 workspace 采用明确命名空间，例如：

```text
<project-root>/plugins/<plugin_id>/<project_id>/motion/
  project.json
  timeline.json
  scenes/scene-001.tsx
```

`<project-root>/project.json`、`<project-root>/timeline.json` 是 engine 自身快照，插件不得读写或覆盖。engine 只对相对路径、owner、revision、大小和 hash 做通用校验。

Engine 提供：`list/read/write_atomic/mkdir/delete/rename/stat/hash/register_artifact`。Artifact 至少包含 `artifact_id/owner/task_id/project_id/job_id/path/mime/size/sha256/role/status/retention`。插件不能伪造 owner 或访问其他 task/project。

现有 `PluginData/plugins/<plugin_id>/` 是插件配置/数据根；最终 workspace 根的位置必须在 ADR 中裁决，不得同时无说明地创建两棵工程树。

### FR-8【Engine】：通用 Runtime/Job Broker

Engine 只认识不透明运行时描述符和安全策略，不识别 Node、npm、Remotion、Python 等品牌语义。插件/管理员登记：runtime_id、版本、digest、平台、可执行入口、环境白名单、输入输出策略和批准状态。插件工具提交领域参数，插件侧转换为通用 job request；engine 只按批准策略校验并执行。

Job 支持：argv 非 shell、cwd/输入/输出授权、超时、取消、日志脱敏、进度、并发/输出预算、进程树回收、job↔task/plugin 归属和宿主重启恢复。平台不能实际提供某资源限制时必须报告 capability unavailable，不假装已隔离。

### FR-9【Engine + 前端】：通用 Artifact 列表与播放器

所有视频类型共用，不新增 motion 专用 UI：

- engine 提供按 task/project/job/owner 查询 artifact 的通用 API；
- 前端将任务产物从单一 `output_path` 扩展为 artifact 列表；
- MIME allowlist：video 播放、image 预览、text/json/md 阅读、其他文件下载；不执行 HTML/JS；
- 显示状态、尺寸、时长（可选）、错误和下载/打开动作；
- artifact 的列表、预览、播放和下载不依赖 Timeline 是否存在；
- 现有 Timeline “落成项目”入口保持兼容。

**API 与展示契约（可测）**：

- `GET /api/artifacts?task_id=&project_id=&job_id=&owner=&status=&cursor=&limit=`，默认按 `created_at desc`，`limit` 上限 100，使用 cursor 分页（不用 offset 以避开并发插入漂移）；返回 `items[]` + `next_cursor` + `total_estimate`（可为 null）。
- MIME 只由宿主根据文件内容/扩展名计算并校验，不接受插件自报；allowlist 为 `video/mp4`、`video/quicktime`、`image/png`、`image/jpeg`、`image/webp`、`text/plain`、`application/json`、`text/markdown`、`application/zip`；其余一律下载，不内联渲染。
- 播放/预览走 `GET /api/artifacts/{id}/raw`，服务端按 artifact owner + task/project 归属校验；不返回任意本地路径，前端不拼路径。
- 列表项至少展示：`role`、`mime`、`size`、`status`（pending/ready/failed/expired）、创建时间、失败原因（若有）；视频显示时长需后端 probe 后才展示，不猜。
- 无 Timeline 时任务页仍渲染 artifact 区块；`has_timeline=false` 只影响“在编辑器中打开”，不影响列表与播放。
- `expired` artifact 保留元数据、隐藏内容并显示原因；不静默从列表消失。

### FR-10【Engine】：ClipKind 通用前端兜底

前端不为具体插件写 motion editor。轨道/片段展示使用贡献返回的 label/icon/color；未知/不可用 kind 显示明确状态。预览优先请求插件返回的通用 artifact/preview contract，前端使用通用播放器/图片查看器；需要专用 UI 时另行贡献受控 UI bundle，不改变核心渲染契约。

## 6. 安全、可靠性和兼容性

- 插件代码只在 worker/host broker 管控的 job 内执行；engine 不执行时间线脚本。
- 双向 RPC fail-closed：帧越界、未知方法、owner 不匹配、超时和版本不匹配均拒绝。
- 现有 subprocess permission 当前对读操作放行、写限制在插件数据根；在 OS 级沙箱未实现前，文档不得宣称恶意插件隔离。MVP 安全边界是“合作型插件 + manifest/owner/path 约束”；生产隔离需 OS sandbox 里程碑。
- 所有 task/project/plugin/job/artifact 访问有 owner 校验；跨任务测试不能只测 API happy path。
- 渲染时插件失败必须成为任务/渲染失败或明确 unavailable，不得静默丢轨道。
- 占位 artifact 只在任务显式选择 `partial_render` 策略时生成，且任务终态必须是 `failed`/`partial`（不得算成功）；默认策略是不生成占位、直接失败并给出 clip/kind/plugin 与错误码。占位内容不可被误认为成片（带明显“渲染不可用”画面 + 元数据标记）。
- 给定工程、素材、runtime digest 和 seed 时，插件输出可复现；engine 只验证媒体结果。
- 默认不改既有内置 kind、Timeline 语义、AgentBus/SSE 31 类事件集合和本机 renderer。

## 7. MVP 验收标准

1. subprocess 插件能声明 Category、Skill、工具、Prompt 和一个 `clip_kind`；插件加载成功，禁用后贡献全部回收。
2. 协议 v2 测试证明：一次 tool/clip render 调用中，worker 能并发请求 workspace 与 job；host 无死锁；超时/取消/worker death 都结束 pending calls。
3. 插件能创建自己的命名空间工程文件，engine 根级 `project.json`/`timeline.json` 不被覆盖；revision 冲突可测。
4. 插件能创建包含自定义 kind 的 Timeline；engine 保存时校验 kind/schema，前端显示其通用轨道信息。
5. engine 渲染到自定义 Clip 时调用插件；插件执行受控 runtime/job 并返回 `media_overlay.v1` 结果（MVP 为单个透明视频）；engine 按尺寸/帧率/时长/alpha 契约校验并完成整片合成。engine 不读取或执行插件私有工程文件代码。
5a. 媒体契约的反例可测：无 alpha、尺寸不符、帧率不符、时长超 1 帧、路径越权，分别返回稳定错误码且不产出成片。
6. preview 与 render 都可由插件返回通用 artifact；前端 Artifact 列表能展示 MP4/图片/JSON/工程包，视频可播放。
7. task cancel、render cancel、插件卸载、worker death、宿主重启都能回收或标记关联 job；不会把取消结果报告为成功。
8. 路径穿越、跨 task/project artifact、伪造 owner、RPC 注入、shell 注入、env 泄漏、未知 kind 静默丢失均有失败测试。
9. 现有内置视频类型、Timeline、renderer、SSE、插件和前端单测/E2E 无回归。
10. 插件的 dsh-drawai 类 MVP 完成：brief → 工程文件 → React/TSX 场景 → preview artifact → 用户反馈 → render-time callback → final artifact → QA artifact。
11. ClipKind 消费者盘点完成：QualityAgent、渲染分派、预检、旁白事件等不再直接比较插件 kind；未知 kind 全部走显式分支。
12. Job 状态机覆盖 cancel×success 竞态、幂等重复提交、宿主重启恢复，终态唯一且不可回退。
13. 通用 artifact API 的分页/owner/MIME/expired 契约有接口测试；无 Timeline 的任务页能列出并播放插件产物。

## 8. 非 MVP / 后续

- 帧序列与逐帧 `frame_provider.v1` RPC；MVP 只接受单个透明视频（`media_overlay.v1`），帧序列不进验收。
- OS 级 sandbox（Windows Job Object/AppContainer、POSIX process group/rlimit 等平台策略）。
- 将插件 kind 直接映射为可编辑 Timeline 内置元素的专用属性面板。
- 成片自动回填普通 Timeline 的完整导出适配。
- 画布协作、DrawAI `.drawai` 兼容和专用场景编辑器。
