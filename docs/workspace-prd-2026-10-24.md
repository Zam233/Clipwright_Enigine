# 工作区 PRD —— 项目产物归口 + 素材自动索引 + 主 Agent 接管「新建制作」

- 版本：v1.0（2026-10-24）
- 范围：`J:\Clipweight-Client`（首页/编辑器）+ `J:\Clipwright`（工作区服务、索引、主 Agent 双轨）
- 前置结论（本轮已执行）：**首页「一句话出片（主 Agent）」按钮与 `/task` 启动页已删除**；`/task/{task_id}` 详情页保留，作为「新建制作走主 Agent」后的计划/步骤/产物视图。`新建制作` 现有选项**一个未删**。
- 关联文档：[workflow.md](workflow.md)、[content-understanding-plan-2026-09-14.md](content-understanding-plan-2026-09-14.md)、[material_system.md](material_system.md)、[agent-native-prd-2026-10-17.md](agent-native-prd-2026-10-17.md)

---

## 0. 一页结论

| 事项 | 结论 |
|---|---|
| 谁该走主 Agent | **「新建制作」链路**（不是首页那句一句话入口，那个入口已下线） |
| 当前实际走什么 | `requirements/proceed` 直调 `PipelineOrchestratorV2`（`api/requirements.py:490`），**没有 agent_mode 分支** → 永远老 6 阶段管线 |
| 本次新增选项 | 「新建制作」第 04 步：**工作区**（选已有 / 新建；默认按项目名建） |
| 工作区是什么 | 项目**唯一产物根**：工程文件、素材、下载、成片、缓存全在里面，可整体拷贝/备份 |
| 素材从哪来 | ① 系统下载落到工作区 `materials/`；② 用户手动往同一文件夹丢文件 |
| 素材怎么用 | 发现新文件（视频/图片/音乐）→ 自动索引（ffprobe/抽帧/场景切分/特征）→ **视觉模型解读**（标签/描述/质量旗标）→ 进素材检索与 Agent 可用范围 |
| 成本控制 | 只索引**新 sha256**；视觉按次计费 → 预算闸 + 开关 + 每工作区每日上限 |
| 主 Agent 接法 | `proceed` 加双轨：`agent_mode=on` → 建 Task（Kernel 规划+执行），前端订阅 `/api/task/{id}/events/stream` |

---

## 1. 背景与问题

### 1.1 入口与链路错位（已核实）

```
首页「新建制作」
  → projectApi.create（POST /api/project，只建空项目）
  → 编辑器 → useRequirementsAutoStart（RequirementsAgent 对话）
  → 确认规划书（ReviewPanel / AgentPanel）
  → requirementsApi.proceed
  → POST /api/requirements/proceed
  → PipelineOrchestratorV2.run()            ← 老 6 阶段管线，无 agent_mode 判断
```

主 Agent（Kernel）当前只有两个入口：
- `POST /api/task`（`api/task.py`）——原先由首页「一句话出片」进入的启动页使用；
- `POST /api/pipeline/run-async`（`api/pipeline.py:355`，`agent_mode=on` 时转 Task）——前端 `pipelineApi.runAsync` **无任何调用点**。

**结论**：即使 `agent_mode=on`，首页链路也不会触发主 Agent。这是本次要修的核心。

### 1.2 产物散落

| 现状位置 | 内容 | 问题 |
|---|---|---|
| `projects/<project_id>/assets/` | 项目素材（files/thumbnails/index.json） | 与工程文件分离，用户找不到"这个项目的全部东西" |
| `PluginData/archives/proj_xxx/media/` | 归档解包媒体 | 第三处 |
| `renders/` | 成片 | 第四处 |
| Mongo | 项目文档/时间线 | 用户不可见、不可 diff |
| `library/` | 全局素材库 | 与项目素材混用边界模糊 |

结果：备份/迁移/排障都要拼三四个目录；用户手动拍的素材没有任何"进项目"的正规入口。

### 1.3 用户原话（需求来源）

> 新建制作里的选项一个都不要删，这个才是要走主 agent 的。然后在这里加入一个新的选项，即工作区。项目产生的一切（包括工程文件）也会保存到工作区，下载的视频会单独保存在工作区下的一个文件夹，然后用户也可以单独在这个文件夹中手动添加素材，当发现新素材（视频、图片和音乐）后会主动索引并使用视觉模型解读。

---

## 2. 目标与非目标

### 2.1 目标（可验收）

| 编号 | 目标 | 可验证形态 |
|---|---|---|
| G1 | 新建制作走主 Agent | 确认规划书后创建 Task；前端跳到 `/task/{id}` 能看到 plan 与逐步骤事件 |
| G2 | 一个工作区装下全部产物 | 跑完一支片子，产物/工程/素材/下载全部落在同一根目录，目录树可整体打包 |
| G3 | 素材有正规投放口 | 往 `materials/` 丢文件 → ≤30s 出现在工作区素材列表并完成索引 |
| G4 | 新素材自动索引 + 视觉解读 | 视频/图片/音乐都能拿到：元数据、关键帧、场景切分、标签、描述、质量旗标 |
| G5 | 索引结果可被检索与 Agent 使用 | `content_query` / `semantic_match` / MaterialAgent 能命中工作区素材 |
| G6 | 成本可见可控 | 视觉解读按次计费：默认开、可关、有每日上限、能看"这次花了多少/跳过了多少" |

### 2.2 非目标

- 不做网盘同步、不做多用户共享工作区（仍以 project_id 隔离）。
- 不做实时文件系统事件级监听（v1 用轮询；见 §5.1）。
- 不替换现有 `AssetManager` / `content_index` / `ContentIndex`，只做**归口与编排**。
- 不改「新建制作」已有选项（选题/风格/配音/踩点/切分方式等）：本次只**新增**第 04 步。

---

## 3. 用户旅程

```
首页「新建制作」
 01 选题与文案   （不变）
 02 风格与类型   （不变）
 03 配音与字幕   （不变：上传/自动配音/切分方式）
 04 工作区       ← 新增
    · 选择已有工作区（列表带路径/素材数/最近扫描）
    · 或「新建工作区」（默认名 = 项目名，默认根目录 = <workspace_root>/<slug>-<短id>）
    · 三个开关：启动时自动索引（默认开）｜视觉解读（默认开，标注按次计费）｜把下载也放进 materials（默认开）
    · 一行只读摘要：`/workspaces/acme-0421 · 素材 128（视频 74 / 图 41 / 音 13）· 待索引 0`
 → 启动 → 需求对话（不变）→ 确认规划书
 → 【G1】建 Task：主 Agent 出计划 → 执行（步骤可见）→ 收尾
 → 产物写入工作区；`/task/{id}` 显示结论与产物路径
```

---

## 4. 工作区模型

### 4.1 目录结构（v1 定稿）

```
<workspace_root>/<slug>-<short_id>/          # 例：J:\Clipwright\workspaces\acme-7f3a
├── project.json          # 工程文件（人可读，含 meta/时间线/agent 参数）
├── timeline.json         # 渲染真值快照（每次管线结束写一次；与 project.json 分开便于 diff）
├── materials/            # ★ 下载与手动投放共用入口（用户要能自己打开这个目录）
│   ├── video/  image/  audio/         # 系统下载自动分类落位
│   └── （用户也可以直接把文件丢进 materials/ 根目录，扫描时按扩展名归类）
├── assets/               # 索引后的项目素材（AssetManager 视图）
│   ├── files/            # 软链（白名单内）或副本（白名单外）
│   ├── thumbnails/
│   └── index.json
├── renders/              # 成片、抽帧复核图
├── cache/                # 中间产物（可随时清空，不参与备份校验）
├── logs/                 # agent/渲染日志（可选保留最近 N 天）
└── .workspace.json       # 工作区元数据（见 §7.1）
```

约定：
- **只有 `materials/` 是用户写入区**；其余目录由系统写，用户手动改会被下次扫描覆盖（在 UI 明示）。
- `cache/`、`logs/` 可配置上限（默认 2GB / 保留 7 天），超限自动清。
- 工作区可整体拷贝：另一台机器上打开时，路径被重写进 `.workspace.json` 的 `paths.root` 并提示"已迁移"。

### 4.2 与现有存储的关系

| 现有 | 变化 |
|---|---|
| `settings.project_dir = projects/` | 工作区根改由 `settings.workspace_dir`（默认 `workspaces/`）；`projects/<id>` 保留为**兼容读路径**，首次打开时迁移（见 §4.3） |
| `AssetManager(project_id)` → `projects/<id>/assets` | 改为 `workspace/assets`；`AssetManager` 增加 `workspace_root` 形参（默认回退老路径） |
| `PluginData/archives/proj_xxx/media` | 新归档把媒体写进 `workspace/materials/`；老归档仍可解包到老路径 |
| `renders/` | 管线在工作区内渲染时落 `workspace/renders/`；无工作区时保持全局 `renders/` |
| Mongo 项目文档 | 增加 `workspace_root` 字段；时间线仍以 Mongo 为准，`timeline.json` 是导出快照 |

### 4.3 迁移与兼容（P0 必须做，否则老项目打不开）

1. 打开项目时：`workspace_root` 为空 → 检查 `projects/<id>/assets` 是否存在；
2. 存在则在**首次写入前**做一次"复制而非移动"的迁移：`projects/<id>/{assets}` → `workspaces/<slug>-<id>/assets`，工程文件从 Mongo 导出为 `project.json`；
3. 源目录**保留**一个 `MIGRATED_TO` 指针文件（内容为工作区路径），不删用户数据；
4. 迁移失败 → 回退老路径并在 UI 提示"该项目仍在旧布局，工作区功能不可用"，不允许半迁移状态继续跑管线。

---

## 5. 素材发现、索引与视觉解读

### 5.1 发现机制

| 触发 | 行为 |
|---|---|
| 工作区创建 / 选定时 | 全量扫描 `materials/`（一次） |
| 轮询 | 每 `scan.interval_sec`（默认 20s）快照 `(相对路径, size, mtime)` 集合，取差集 |
| 手动 | UI「立即扫描」按钮；`POST /api/workspace/{id}/scan` |
| 系统下载完成 | 直接投递一个"新增文件"事件（无需等轮询） |

**判定新素材**：先按 `(path,size,mtime)` 快速筛，再做 `sha256`（`content_index.sha256_of`）去重；同 sha256 已存在只更新 `last_seen_at`，**不重复索引**。改名/移动按 sha256 合并，不产生两条记录。

> v1 用轮询而非文件系统 watch：不引新依赖（watchdog 在 Windows 上对网络盘不可靠），且 20s 延迟对素材投放可接受。

### 5.2 索引流水线（视频）

复用现有能力，不新写算法：

1. `indexer.MediaIndexer.extract_metadata`（ffprobe：时长/分辨率/码率/音轨）；
2. `content_index.detect_transition_candidates` + `compute_shot_stats` → 镜头切分与运动强度；
3. 抽帧（每镜头 2–3 帧，`settings.content_index_keyframes_per_shot`）；
4. 视觉解读（见 §5.3）→ 镜头级标签/描述；
5. `content_index.save_index(ContentIndex)`（本地 `workspace/assets/index.json`；`content_index_mirror_mongo` 开时镜像 Mongo）；
6. 写 `AssetManager` 资产记录（`source=workspace`, `sha256`, `used_count`）。

图片：元数据 + 视觉分类/描述（无需抽帧）。
音频：时长/采样率/声道 + 可选 BPM（`bpm_detect`）；是否人声留作 P2。

### 5.3 视觉模型解读

| provider | 用哪个 | 说明 |
|---|---|---|
| `vision_provider=llm` | `vision_llm` 工具 / `settings.vision_llm_*` | 描述质量最好，**按次计费** |
| `vision_provider=transformers` | `content_vision.clip_zero_shot`（`google/vit-base-patch16-224`） | 本地、零 API 成本，标签较粗 |
| `vision_provider=none` | 跳过 | 记录 `vision: skipped(provider=none)`，不假装做过 |

输出（每素材）：

```jsonc
{"sha256":"…","kind":"video","duration_sec":37.2,"shots":9,
 "vision":{"provider":"llm","model":"…","summary":"…","tags":["舞台","特写手部","纸质纹理"],
           "quality":{"blur":0.12,"exposure":0.61,"jitter":0.08}},
 "indexed_at":"…","cost_usd":0.012}
```

### 5.4 成本与限流

- 只对新 sha256 调视觉；单文件成本上限 `workspace.vision_max_calls_per_file`（默认 8 帧）。
- 每工作区每日上限 `workspace.vision_daily_budget_usd`（默认 1.0）；超限把剩余文件标 `vision: deferred(daily_budget)`，**索引仍然完成**（元数据/标签可用）。
- 与全局 `services.budget.check_budget` 串联：全局预算耗尽 → 只做本地索引。
- 扫描任务是**后台 job**：回报 `{scanned, new, indexed, deferred, failed}`，UI 显示进度。

### 5.5 失败与降级

| 情况 | 行为 |
|---|---|
| 文件正在被写（大小变化中） | 跳过该轮，下一轮重试；连续 5 轮仍在变 → 标 `unstable` |
| ffprobe 失败/损坏 | 记 `index_failed` + 原因；不阻断其它文件 |
| 视觉失败 | 保留本地索引，`vision: failed`，下次扫描可重试（最多 3 次） |
| 磁盘不可写 | 工作区标 `degraded`，管线启动前置检查直接拒绝，并给出原因 |

---

## 6. 主 Agent 接管「新建制作」

### 6.1 后端：`proceed` 加双轨（与 `run-async` 对齐）

```python
# api/requirements.py::proceed_to_pipeline（示意）
if str(getattr(settings, "agent_mode", "off")).lower() == "on":
    # 1) 用 session 的 user_inputs + production_plan 建 TaskRequest
    #    （topic / persona_id / category_plugin_id / duration_sec / extra{workspace_root, …}）
    # 2) kernel.create(request) → kernel.plan(task)（plan_only 由前端开关）
    # 3) 返回 {"task_id": …, "pipeline_id": task.id}  ← 字段兼容，值是 task id
    return await _start_requirements_as_task(session, req, uid=…)
```

要点：
- **保留 `pipeline_id` 字段**（值 = task id），前端老代码不会立刻炸；
- Task 的 `extra` 必须带 `workspace_root`，MaterialAgent 才能从工作区索引检索；
- `agent_mode=off/shadow` 行为**逐字节不变**（这是回归基线）。

### 6.2 前端：识别并切订阅

| 返回 | 前端行为 |
|---|---|
| `task_id` 存在 | 跳 `/task/{task_id}`；`taskStore` 订阅 `/api/task/{id}/events/stream` |
| 只有 `pipeline_id` | 保持现状：SSE `/api/pipeline/{id}/events` |

`/task/{id}` 页已存在（`TaskPage.tsx` + `features/task/*`），本次**不重写**，只补：
- 顶部显示工作区路径与素材计数；
- 收尾产物区列出 `workspace/renders/*` 与 `project.json` 的下载链接。

### 6.3 PlanStep ↔ 工作区

| 能力 | 消费方式 |
|---|---|
| `material` 步骤 | 优先从工作区索引检索（`content_query` / `semantic_match`）；无命中才外网检索 |
| `edit` / `render` | 产物写 `workspace/renders`、`workspace/cache` |
| `quality` | 质检报告与抽帧写 `workspace/logs`、`workspace/renders` |

---

## 7. 数据契约

### 7.1 `.workspace.json`

```jsonc
{"schema":1,"project_id":"proj_xxx","name":"acme",
 "root":"J:\\Clipwright\\workspaces\\acme-7f3a",
 "created_at":"2026-10-24T10:00:00Z",
 "dirs":{"materials":"materials","assets":"assets","renders":"renders","cache":"cache"},
 "scan":{"auto":true,"interval_sec":20,"last_scan_at":"…","pending":0},
 "index":{"vision":true,"provider":"llm","daily_budget_usd":1.0,"spent_today_usd":0.12},
 "limits":{"cache_max_gb":2,"logs_keep_days":7}}
```

### 7.2 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/workspace` | 创建（`project_id`/`name`/`root?`）；返回 §7.1 |
| GET | `/api/workspace/{project_id}` | 状态（文件数/索引数/待索引/预算/健康） |
| POST | `/api/workspace/{project_id}/scan` | 触发扫描（`dry_run=true` 只列将新增） |
| GET | `/api/workspace/{project_id}/materials` | 素材列表（kind/状态/标签/缩略图 URL） |
| POST | `/api/workspace/{project_id}/materials` | 上传（写 `materials/`，走同一索引流水线） |
| DELETE | `/api/workspace/{project_id}/materials/{sha}` | **只删索引记录**；`purge=true` 才删磁盘文件（需二次确认） |
| GET | `/api/workspace/{project_id}/events` | SSE：扫描/索引进度 |

安全：`root` 必须在 `settings.workspace_dir` 或显式白名单内（`assert_allowed_path`）；不接受用户给的任意绝对路径写盘。

### 7.3 前端类型

```ts
interface Workspace { project_id: string; name: string; root: string;
  counts: { total: number; video: number; image: number; audio: number; pending: number };
  index: { vision: boolean; provider: string; spent_today_usd: number };
  scan: { auto: boolean; interval_sec: number; last_scan_at: string | null };
  health: 'ok' | 'degraded'; }
```

---

## 8. 改动清单

### 8.1 后端（`J:\Clipwright`）

| 文件 | 改动 |
|---|---|
| `config.py` | 新增 `workspace_dir` / `workspace_scan_interval_sec` / `workspace_vision_daily_budget_usd` / `workspace_cache_max_gb` |
| `services/workspace.py`（新） | 目录初始化、`.workspace.json` 读写、扫描调度、去重、迁移 |
| `services/workspace_index.py`（新，可并入上者） | 新素材 → 索引流水线编排（复用 `indexer` / `content_index` / `content_vision`） |
| `api/workspace.py`（新） | §7.2 七个端点 + SSE |
| `services/asset_manager.py` | 增加 `workspace_root` 形参（默认回退 `projects/<id>`） |
| `api/requirements.py` | `proceed` 双轨（§6.1） |
| `api/pipeline.py` | 无改动（`_start_as_task` 复用） |
| `services/pipeline_v2.py` | 渲染产物目录改为工作区（`extra_params.workspace_root` 优先） |
| `main.py` | 注册 `api/workspace.py`；启动时清理过期 `cache/`（按 §4.1 限额） |

### 8.2 前端（`J:\Clipweight-Client`）

| 文件 | 改动 |
|---|---|
| `pages/HomePage.tsx` | 04 工作区步骤（选择/新建/三开关/摘要）；`launch()` 把 `workspace_root` 与索引开关写进 `projectStore` |
| `stores/projectStore.ts` | `workspaceRoot` / `autoIndex` / `visionIndex` 字段 |
| `features/agent/ReviewPanel.tsx`、`AgentPanel.tsx` | 处理 `task_id` 返回：跳 `/task/{id}`（`/task` 启动页已删） |
| `pages/TaskPage.tsx` | 顶部工作区摘要 + 产物下载 |
| `services/api/workspace.ts`（新） | §7.2 客户端 |
| `features/workspace/WorkspaceMaterialsPanel.tsx`（新） | 素材列表/状态/标签/一键扫描（P2） |
| `router.tsx` | 已删 `/task`；如需「工作区」独立页再加 `/workspace/$projectId`（P3 可选） |

---

## 9. 验收标准

| # | 场景 | 期望 |
|---|---|---|
| A1 | 首页 | 无「一句话出片（主 Agent）」按钮；`/task` 不再可达；`/task/{id}` 可用 |
| A2 | 新建制作 | 原有选项全部保留；新增「工作区」可选可建 |
| A3 | 产物归口 | 跑完一支片子：`project.json`/`timeline.json`/`renders/`/`assets/` 全在工作区内，`projects/<id>` 不再新增写入 |
| A4 | 手动丢素材 | 往 `materials/` 丢 `a.mp4` → ≤30s 出现，`status=indexed`，带 `vision.summary` 与 `tags` |
| A5 | 去重 | 同一文件改名为 `b.mp4` 再丢 → 不产生第二条记录，`last_seen_at` 更新 |
| A6 | 视觉关闭 | `vision=false` → 索引完成但 `vision=skipped`；不产生 API 费用 |
| A7 | 预算 | 当日超 `daily_budget` → 剩余文件 `deferred`，索引仍完成 |
| A8 | 主 Agent | `agent_mode=on` + 确认规划书 → 返回 `task_id`；`/task/{id}` 见 plan 与逐步骤事件；`off` 时行为与今天一致 |
| A9 | 降级 | ffprobe 损坏文件 → 该文件 `index_failed`，其余继续；工作区 `health=degraded` 时不阻止已索引素材检索 |
| A10 | 迁移 | 打开老项目（`projects/<id>/assets` 存在）→ 自动迁移并留 `MIGRATED_TO`；旧路径仍可读 |

回归基线：
```powershell
python -m pytest tests -q            # 期望全绿（当前 4381 passed）
cd J:\Clipweight-Client; npx tsc --noEmit; npx vitest run   # 当前 963 passed
```

---

## 10. 分期

| 阶段 | 内容 | 人日 |
|---|---|---|
| P0 | 删入口（**已完成**）＋工作区目录/`.workspace.json`/迁移＋`POST/GET /api/workspace` | 2 |
| P1 | 启动扫描 + 轮询增量 + sha256 去重 + 本地索引（无视觉） | 2 |
| P2 | 视觉解读（llm/transformers）+ 预算闸 + SSE 进度 + 素材面板 | 3 |
| P3 | 主 Agent 接线（`proceed` 双轨）+ 前端按 `task_id` 切订阅 + `/task/{id}` 工作区摘要 | 3 |
| P4 | UI 收尾（新建制作 04 步、产物下载、cache 清理策略） | 2 |

合计约 12 人日。P3 之前工作区即可单独交付（产物归口 + 素材索引），主 Agent 接线不阻塞它。

---

## 11. 风险与不做

| 风险 | 处置 |
|---|---|
| 轮询大目录（上万文件）拖慢主循环 | 只 stat 不进内容；单次扫描上限（默认 5000 文件）+ 增量游标；扫描在独立线程 |
| 用户把工作区放在网络盘 | `root` 白名单内才允许；网络盘写慢 → 扫描超时后降级为手动 |
| 视觉成本失控 | 每日预算 + 单文件帧数上限 + 默认 transformers 优先、llm 需显式选择 |
| 迁移半途失败 | 严格"先复制后切换"，失败回退旧布局并禁跑管线（§4.3） |
| 用户误删 `materials/` 里的文件 | 索引记录标 `missing`（不自动删），UI 显示可"重新定位/移除记录" |
| 主 Agent 接线改变老行为 | `agent_mode=off` 分支逐字节不变 + A8 双跑回归 |

**明确不做**：对象存储/网盘、多用户并发编辑同一工作区、自动下载受版权保护内容、递归监听子目录的软链环。

---

## 12. 实施记录（2026-10-24）

| 需求 | 状态 | 落点 |
|---|---|---|
| P0 入口下线 | ✅ | 首页「一句话出片（主 Agent）」按钮、`/task` 启动页、`TaskLauncherPage.tsx` 与对应路由已删除；`/task/{taskId}` 保留复用 |
| P0 工作区目录/元数据/迁移 | ✅ | `services/workspace.py`：`Workspace.ensure/status/materials_files/load_registry/save_registry/migrate_legacy/write_project_files/cleanup_cache`；meta 原子写 `.workspace.json` |
| P0 API | ✅ | `api/workspace.py`：create/get/enable/scan/materials(upload+list+delete)/events(SSE)，`main.py` 已注册 |
| P1 扫描 + 去重 + 本地索引 | ✅ | `services/workspace_watcher.py`（20s 轮询 + 启动扫 + 每 10 分钟清 cache）；`services/workspace_index.py`（路径快筛 → sha256 去重 → `MediaIndexer` 元数据/标签 → `AssetManager` 登记） |
| P2 视觉 + 预算 | ✅ | `_vision_for()`：`content_vision.label_shot`（CLIP 本地 / LLM 描述）；`workspace_vision_daily_budget_usd` 闸，超限标 `deferred`；`workspace_*` 六个 settings + 设置页字段 |
| P2 素材面板 | ✅ | `features/workspace/WorkspaceMaterialsPanel.tsx`（路径/计数/立即扫描/上传/素材表），任务页与首页 04 步共用 |
| P3 主 Agent 双轨 | ✅ | `api/requirements.py::_proceed_as_task`：`agent_mode=on` → 确保工作区 → `AgentKernel.create/plan` → 后台执行；返回 `{task_id, pipeline_id(=task id), workspace_root, plan}` |
| P3 前端切订阅 | ✅ | `features/agent/proceedResult.ts`（task_id 优先）+ `AgentPanel`/`ReviewPanel` 跳 `/task/{id}`；`/task` 页新增工作区面板 |
| P4 新建制作 04 步 | ✅ | `HomePage` 新增「04 工作区」ConsoleStep（自动索引 / 视觉解读开关 + 路径 + 面板） |
| P4 cache 清理 | ✅ | `cleanup_cache()` 按 `workspace_cache_max_gb` 从最旧删；watcher 每 ~10 分钟执行一次 |
| 补齐：老管线也归口 | ✅ | `proceed` 的 `agent_mode=off` 分支同样 `ensure_workspace` 并把 `workspace_root/workspace_project_id` 注入 `pipeline_req.extra_params`；管线完成时（含自动建项目）`write_project_files(project.json, timeline.json)`。`_opt_bool()` 归一化前端传来的 `"true"/"false"` 字符串 |

### 12.1 与 PRD 的差异（必须知道）

1. **一个项目一个工作区**：目录由 `project_id` 决定（`<slug>-<id 后 6 位>`），未做"选择别的项目的工作区"——
   跨项目共用会让素材串味，先按最保守语义落地。
2. **素材是复制而非软链**：`workspaces/` 不在 `allowed_media_roots` 白名单内，
   `AssetManager.import_file` 会把文件复制进 `assets/files/`（大视频占双份盘）。
   后续可把 `workspace_dir` 加入白名单后走软链（代码已支持，只需放开白名单）。
3. **视觉默认本地 CLIP**：`settings.vision_provider=transformers`；切 `llm` 才按次计费，
   预算闸也只对 `llm` 生效（本地无费用）。
4. **SSE 已实现但前端未常驻**：素材面板是"操作后 reload"，避免首页/任务页各挂一条长连接；
   `/api/workspace/{id}/events` 可直接接入实时进度。
5. **产物下载未新增专区**：`/task/{id}` 复用既有 `TaskArtifacts`；工作区路径已在面板展示，
   未额外做"一键打开目录"（浏览器无法可靠打开本地目录）。
6. **渲染成品仍写全局 `renders/`**：`services/render.py` 的输出路径未按 `workspace_root` 改写，
   工作区 `renders/` 目前是"约定目录 + 面板展示位"，后续在 render 入口接 `workspace_root` 即可切换
   （改动面大，单独一批做，避免影响已有下载链接与项目归档）。

### 12.2 验收结果

| # | 场景 | 结果 |
|---|---|---|
| A1 | 首页无「一句话出片（主 Agent）」、`/task` 不可达 | ✅（前端 970 passed，tsc 通过） |
| A2 | 新建制作原有选项全部保留 + 新增 04 工作区 | ✅ |
| A3 | 产物归口 | ✅ 双轨（`agent_mode=on/off`）都落 `project.json`/`timeline.json`，`renders/`/`cache/`/`materials/` 目录就位（渲染文件物理落点见差异 6） |
| A4/A5 | 丢素材 ≤30s 索引、改名不重复 | ✅ 单测覆盖（`test_workspace.py`：new/indexed/deduped） |
| A6 | 视觉关闭不产生费用、标 skipped | ✅ 单测覆盖（`vision_disabled` / `provider=none`） |
| A7 | 超预算标 deferred、索引仍完成 | ✅ 代码路径覆盖（`vision_budget`） |
| A8 | `agent_mode=on` 返回 task 并跳详情；`off` 行为不变 | ✅ 单测 `test_proceed_as_task_creates_task_and_workspace`；老管线回归 132 passed |
| A9 | 坏文件 `index_failed` 不影响其它 | ✅ 单测 `test_index_failure_is_recorded_not_raised` |
| A10 | 老项目迁移留 `MIGRATED_TO`、源不删 | ✅ 单测 `test_migrate_legacy_copies_and_leaves_pointer` |

回归：后端 `pytest tests -q` → **4393 passed / 4 skipped / 1 xfailed**（工作区用例 15 条：`tests/test_workspace.py`）；
前端 `npx tsc --noEmit` 干净 + `vitest run` 970 passed。
> 环境备注：本轮全量曾出现 22 failed + 2 error，根因是机器上 `ffmpeg/ffprobe` 从 PATH 消失（WinGet 包被清掉），
> 与代码无关。已 `winget install Gyan.FFmpeg`（9.0.2 full build）恢复 `ffmpeg`/`ffprobe`，重跑即全绿。
