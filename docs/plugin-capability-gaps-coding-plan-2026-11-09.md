# 插件能力缺口补齐：Coding Plan

> 状态：Draft / 待评审  
> 目标分支：`feat/zam_agent_motion_video`（engine 与前端同名）  
> 背景：动效插件（`J:\clipwright-motion-plugin`）已能渲染，但审计发现插件在**接口层**拿不到 12 类数据，同时在**进程层**反而过宽（继承宿主环境变量、读不受限）。  
> 原则：engine 只补**通用**能力接口；领域语义仍留在插件。能不给的（时间线写、默认跨插件调用）明确列为非目标。

## 0. 范围与非目标

### 0.1 要补的（对应审计 12 项）

| # | 缺口 | 目标接口 |
|---|---|---|
| 1 | 配音音频与对齐结果 | `request.cues[]` + `request.audio` |
| 2 | 完整 Timeline（只读摘要） | `timeline.summary` / `timeline.neighbors` |
| 3 | 用户素材（只读） | `materials.list/read` + 请求内 `materials[]` 引用 |
| 4 | 其他插件/引擎产物 | `artifact.list/get` 增加 `scope=task|project|granted` |
| 5 | 项目/brief/persona | `request.context`（摘要，非全量） |
| 6 | 渲染设置 | `request.render_settings` + `request.output` |
| 7 | 任务上下文 | `task.context` + `request.context.stage` |
| 8 | 引擎能力（LLM/搜索/TTS/align/frame_check） | `capability.call`（默认拒绝，按 grant 开放） |
| 9 | 其他插件 tool/skill | `plugin.call`（仅 manifest `dependencies` 声明的目标） |
| 10 | 进度/取消事件 | v2 `event` 帧 + `bridge.on_event` |
| 11 | ClipKind 校验结果/同类 clip 摘要 | `timeline.neighbors` + `request.validation` |
| 12 | 写回 Timeline | **非目标**（见 0.2） |

### 0.2 非目标（明确不做）

- **插件直接写 Timeline**：时间线是 engine 与 Agent 的契约，插件写回会绕过类型校验与用户可见的编辑记录。插件只能通过工具结果**建议**修改，由 Agent 落盘。
- **默认开放跨插件调用**：会让插件边界退化成“谁都能调谁”；只允许 manifest 声明依赖 + 管理员授权。
- **默认开放引擎能力（LLM/搜索/TTS）**：这些是计费/合规敏感能力，必须按 grant 开放。
- **OS 级恶意插件沙箱**：本计划只做“合作型插件”的边界与最小权限；恶意隔离另立里程碑。
- 不改动内置 Category/Agent/Timeline schema 语义。

## 1. 设计总览

```text
┌────────────────────────── engine ──────────────────────────┐
│  Task/Project 数据（brief/persona/timeline/captions/NEL）  │
│  Materials（只读）  Artifacts（按 scope）  引擎能力（grant）│
│            │                                                │
│   Capability Broker（新增）：request 组装 + 权限判定        │
│            │  v2 RPC（request/response/event）               │
└────────────┼────────────────────────────────────────────────┘
             ▼
       plugin worker（插件）
```

核心新增两件东西：

1. **`PluginRequestContext`**：渲染/工具调用时随 scope 下发的只读上下文（cues/audio/materials/neighbors/settings）。
2. **Capability Broker**：宿主侧统一判定“这个插件这次能不能读这个材料/调这个能力”，避免每个 handler 各写一套。

### 1.1 新增/扩展的 RPC 方法

| 方法 | 方向 | 作用 |
|---|---|---|
| `task.context` | worker→host | topic/category/duration/stage/persona 摘要 |
| `timeline.summary` | worker→host | 总时长、轨数、clip 数、fps/尺寸 |
| `timeline.neighbors` | worker→host | 当前 clip 前后 N 个 clip 的只读摘要 |
| `narration.cues` | worker→host | 字幕 cue + NEL 事件 + BPM |
| `audio.get` | worker→host | 配音音频 artifact 引用（不返回本地路径） |
| `materials.list` | worker→host | 只读素材清单（kind/size/hash/ref） |
| `materials.read` | worker→host | 只读素材字节（限大小，返回 base64 或 artifact） |
| `capability.call` | worker→host | 受控调用引擎能力（LLM/search/align/frame_check） |
| `plugin.call` | worker→host | 调声明依赖的其他插件 tool/skill |
| `event.subscribe` | worker→host | 订阅 job/task 事件（取消、进度） |
| `event`（帧） | host→worker | 推送取消/进度 |

### 1.2 请求契约 v2（`ClipRenderRequest` 扩展）

```json
{
  "contract": "media_overlay.v2",
  "clip": {"id": "...", "kind": "...", "start_sec": 0, "duration_sec": 4},
  "context": {
    "task_id": "...", "project_id": "...", "stage": "animation",
    "topic": "...", "category": "motion_scene", "duration_sec": 60,
    "persona": {"palette": ["#..."], "font": "..."},
    "brief_summary": "……"
  },
  "cues": [{"start_sec": 1.2, "duration_sec": 1.8, "text": "……",
            "type": "number|emphasis|turn|question|enum", "nel": true}],
  "audio": {"artifact_id": "art_...", "duration_sec": 60, "role": "dub"},
  "materials": [{"artifact_id": "art_...", "mime": "image/png",
                 "size": 12345, "sha256": "..."}],
  "neighbors": [{"clip_id": "...", "kind": "...", "start_sec": 0, "duration_sec": 3,
                 "text": "……"}],
  "render_settings": {"width": 1920, "height": 1080, "fps": 30,
                      "codec": "prores4444", "loudnorm": true},
  "output": {"container": "mov", "alpha": true}
}
```

兼容：`media_overlay.v1` 保留；插件用 clip_kind meta 的 `render_contract` 声明要哪个版本，engine 按声明组装。v1 字段不删。

## 2. 里程碑

### M1 安全基线（P0，2–3 天）

**目标**：先把“进程层过宽”收住，否则后续接口都是建立在漏水的桶上。

1. `plugins/runtime/host.py`：worker 启动环境改为**白名单**（PATH/SYSTEMROOT/TEMP/编码/LANG），显式剥离 `CLIPWRIGHT_*`、`*_API_KEY`、`*_TOKEN`。保留插件自己的 config 注入（走 `_flat_config`，不经 env）。
2. `plugins/runtime/permissions.py`：读操作增加**根目录白名单**（plugin_dir、data_dir、scope 内 workspace、显式授权的 materials/artifacts 目录）；读越界抛 `PermissionViolation`。保留“读自己资源”的兼容路径（plugin_dir/data_dir）。
3. `services/plugin_jobs.py`：job 环境已白名单，补充**网络策略声明**：未声明 `network` 的 runtime 在文档与返回中标注 `network_policy: not_enforced`（OS 级限制不可用时不得宣称隔离）。
4. 文档：`docs/pluginization-plan` 增补“合作型插件威胁模型”段落，明确哪些是约定、哪些是强制。

**测试**
- worker 环境不含 `CLIPWRIGHT_API_TOKEN`（注入假 token，插件读不到）。
- 读 `../materials/x.png` 被拒；读自己 data_dir 通过。
- 既有 sandbox_demo 权限测试不回归。

**验收**：插件进程 `os.environ` 中无密钥；越界读有稳定错误码。

### M2 请求上下文 v2（P1，4–6 天）

**目标**：让插件在渲染时拿到“这一镜需要知道的一切通用信息”。

1. `services/plugin_render.py::build_request` 升级：组装 `context / cues / audio / materials / neighbors / render_settings / output`。
2. 新增 `services/plugin_context.py`：
   - `build_context(timeline, clip, *, task, project)`：从 brief/persona 取**摘要**（不含全量 prompt）；
   - `build_cues(timeline, window)`：从 caption clip 取 `{start_sec,duration_sec,text}`；叠加 NEL（`narration_events.extract_nel`）与 BPM；
   - `build_audio(timeline)`：找 `metadata.dubbing/narration` 的音频 clip，返回 artifact 引用（无登记则返回空 + `reason`）；
   - `build_materials(timeline, project)`：只读素材清单（来自 `materials/` 索引），最多 N 条 + 总量上限；
   - `build_neighbors(timeline, clip, radius=2)`。
3. `ClipKindDefinition` 增加 `render_contract_version`（默认 v1），按声明选 v1/v2 组装。
4. 字节/条数上限：cues ≤ 200 条，neighbors ≤ 8，materials ≤ 32，总 JSON ≤ 256KB；超限截断并附 `truncated: true`。

**测试**
- 有配音+字幕的 timeline：`cues` 时间正确、`audio` 指向 artifact；无配音时 `audio: null` 且 `reason` 可读。
- v1 插件仍收到旧字段；v2 插件收到新字段。
- 超限截断有标记。

**验收**：动效插件可仅凭 `request.cues` 把动画对齐到配音句子（插件侧改动另计）。

### M3 只读素材与 artifact 视图（P1，3–5 天）

**目标**：插件能“看”用户素材与授权产物，但不能改。

1. `services/plugin_rpc.py` 新增：
   - `materials.list(kind?, limit?)` → `[{artifact_id|ref, name, mime, size, sha256}]`；
   - `materials.read(ref, max_bytes=4MB)` → `{data_b64|artifact_id, sha256}`；只读，限 4MB。
2. `PluginWorkspace` 增加**只读根**：`materials/` 与 `renders/`（engine 产物）映射为只读 lease，路径校验仍禁止越界。
3. `artifact.list/get` 增加 `scope` 参数：`own`（默认）、`task`（同任务产物）、`granted`（显式授权）；`project` 需额外 grant。
4. 所有读取返回 `sha256/size`，不返回绝对路径。

**测试**
- 读 `materials/` 成功且不能写；写 `materials/` 被拒。
- `artifact.list(scope=task)` 能看同任务其他阶段产物；`scope=project` 未授权被拒。
- 4MB 上限、路径穿越、跨项目读取被拒。

**验收**：插件能把用户图片读进场景（转 base64 或 art 引用），且无法篡改素材。

### M4 事件与取消通知（P1，2–3 天）

**目标**：插件知道自己被取消/进度变化，而不是事后发现。

1. 复用协议 v2 的 `event` 帧：host→worker `job.progress`、`job.cancelled`、`task.cancelled`。
2. `bridge.on_event(handler)`：插件的 tool/renderer 可注册回调；worker 读循环把 event 分发给 handler。
3. `PluginJobManager`：cancel/timeout/kill 时向关联 worker 推送 `job.cancelled`（带 job_id、reason）。
4. task cancel 传播：`cancel_for_task` 后推送 `task.cancelled`。

**测试**
- 长 job 被 cancel → 插件 handler 收到事件，可在 5s 内返回 `cancelled`。
- 无 handler 时事件被丢弃且不报错。
- 既有 job 状态机测试不回归。

**验收**：插件能在取消后清理临时状态并立即结束，而不是等超时。

### M5 跨插件调用（P2，3–5 天）

**目标**：允许“声明依赖 + 授权”的组合能力，默认拒绝。

1. manifest 增加 `dependencies`（已有字段）语义：声明可调用的插件 id + 方法白名单。
2. `plugin.call`：校验调用方 manifest 依赖、被调方 manifest 是否允许被调用（`exposes: [tool/skill]`）、owner 隔离（被调方拿到的 scope 仍是调用方的 task/project）。
3. 默认拒绝：未声明即 `PERMISSION_DENIED`；循环调用检测（深度 ≤2）。

**测试**
- A 调 B 的 tool 成功（已声明）；未声明被拒；B 调 A 形成环被拒。
- 被调方越权读调用方 workspace 被拒。

**验收**：动效插件可复用 TTS 插件的“按句合成”能力，而不复制实现。

### M6 Timeline 读模型（P2，2–3 天）

**目标**：跨镜一致性所需的最小只读视图；**不提供写**。

1. `timeline.summary`：duration/fps/尺寸/轨数/clip 数。
2. `timeline.neighbors`：当前 clip 前/后 N 个 clip 的 `{clip_id,kind,start,duration,text_preview}`。
3. 权限：只返回当前 task/project 内、已脱敏文本（≤80 字）。

**测试**
- 请求 window 边界正确；不在窗口的 clip 不返回。
- 脱敏：长文本截断；不含 metadata 原文。

**验收**：插件能按相邻镜头的主色/节奏做衔接，不依赖全量 timeline。

### M7 引擎能力受控调用（P3，4–6 天）

**目标**：把 LLM/搜索/TTS/STT/质检等做成“申请—授权—调用”，而不是硬编码进插件。

1. `capability.call(name, payload)`：name 走 `tool/capabilities` 解析；按 manifest `permissions` + 管理员 grant 判定。
2. 默认能力：`align`（配音/字幕对齐）、`frame_check`、`media_probe` 可开；`web_search`/`llm` 需显式 grant + 配额。
3. 计费/审计：调用归属 plugin/task，写审计日志；失败返回稳定错误码。

**测试**
- 未授权 `capability.call` 被拒；授权后 align 返回 cue。
- 配额用尽返回 `QUOTA_EXCEEDED`。

**验收**：插件可以“按需”用引擎对齐，而不是自己装 whisper。

### M8 文档、迁移与回归（3–5 天）

1. 插件 SDK 文档：request v2 字段、各 RPC 方法、错误码、权限声明。
2. 兼容矩阵：v1/v2 插件 × 各能力；老插件零改动可继续跑。
3. 迁移脚本/示例：把动效插件切到 v2（cues/audio/materials）。
4. 全量回归：插件测试集 + 内置 pipeline + 前端。

## 3. 代码落点

| 改动 | 文件 |
|---|---|
| worker 环境白名单、读根限制 | `plugins/runtime/host.py`、`permissions.py` |
| 新 RPC handler | `services/plugin_rpc.py`（新增方法，仍走既有注册） |
| 请求组装 | `services/plugin_render.py`、新 `services/plugin_context.py` |
| 只读 lease/素材 | `services/plugin_workspace.py`、`services/artifact_query.py` |
| 事件推送 | `plugins/runtime/host.py`、`worker_rpc.py`、`bridge.py` |
| 跨插件调用 | `services/plugin_rpc.py`、`schema/plugin.py`（manifest 字段） |
| 能力受控调用 | `tool/capabilities.py` + 新 `services/plugin_capabilities.py` |
| 文档 | `docs/plugin-sdk.md`、`docs/api_reference.md`、本计划 |

## 4. 兼容与版本

- 请求契约：`media_overlay.v1` 不变；v2 仅新增字段。`ClipKindDefinition.render_contract` 决定用哪个版本。
- RPC：新增方法不改变既有方法语义；旧 worker（v1 协议）不认识的 event 帧忽略。
- 权限：M1 的读根白名单可能影响“读任意文件”的老插件——发布说明标注为**破坏性安全修复**，并提供 `extra_read_roots` 配置逃生口。

## 5. 测试策略

| 层 | 覆盖 |
|---|---|
| Unit | context 组装、cue 提取、截断、脱敏、路径校验 |
| Integration | 真 subprocess 插件读 materials、订阅事件、跨插件调用 |
| Security | env 泄露、越界读、跨项目 artifact、跨插件环、grant 绕过 |
| E2E | 动效插件按 cues 对齐配音出片（真 Remotion + 真音频） |
| Regression | 内置 pipeline、插件测试集、前端 typecheck/build |

## 6. 顺序与依赖

```text
M1 安全基线
  └─ M2 请求上下文 v2 ── M3 只读素材/artifact
        └─ M4 事件/取消
              ├─ M5 跨插件调用
              └─ M6 timeline 读模型
                    └─ M7 引擎能力受控调用
                          └─ M8 文档/迁移/回归
```

M1 必须先做（安全债）；M2/M3 可并行；M7 依赖 M5 的授权模型。

## 7. 验收清单（DoD）

- [ ] worker 环境无密钥；越界读被拒；文档写明威胁模型。
- [ ] v2 请求含 context/cues/audio/materials/neighbors/render_settings，且有上限与截断标记。
- [ ] 插件可只读 materials/ 与授权 artifact，不能写。
- [ ] 取消/进度事件可达插件，插件能在 5s 内响应取消。
- [ ] 跨插件调用默认拒绝，声明+授权后可用，环检测生效。
- [ ] timeline 只读摘要可用，写仍由 Agent 负责。
- [ ] 引擎能力按 grant 开放，带配额与审计。
- [ ] v1 插件零改动可运行；全量回归通过。

## 8. 执行进度

| 里程碑 | 状态 | 证据 |
|---|---|---|
| M1 安全基线 | ✅ 完成 | `host.py` ENV_ALLOWLIST；`permissions.check_read_path`（读根=插件目录/Python 环境/sys.path）；`test_plugin_security_m1.py` 2 例、权限单测 16 例、子进程回归 27 例 |
| M2 请求上下文 v2 | ✅ 核心完成（插件侧未接） | `services/plugin_context.py`（context/cues/audio/materials/neighbors/render_settings + 截断）；`build_request` 按 `render_contract` 组装 v2；7 例测试 |
| M3 只读素材/artifact scope | ✅ 完成 | `services/plugin_materials.py`；`materials.list/read`、`artifact.list(scope=own/task)`；`test_plugin_materials.py` 4 例 + `test_plugin_materials_rpc.py` 1 例 |
| M4 事件/取消 | ✅ 完成 | 协议 `event` 帧、`host.notify`、`bridge.on_event`/`emit_event`、job 取消/超时/task 取消通知；`test_plugin_events_m4.py` 2 例 + `test_plugin_jobs.py` 3 例 |
| M5 跨插件调用 | ✅ 完成 | `plugin.call`（dependencies + runtime_config.exposes + 链深/环）；`test_plugin_cross_call_m5.py` 3 例 |
| M6 timeline 读模型 | ✅ 完成 | `timeline.summary`（task 级只读快照）；`test_plugin_timeline_m6.py` 2 例 |
| M7 引擎能力受控 | 未开始 | — |
| M8 文档/迁移/回归 | 进行中（本表 + API 文档待补） | — |

## 9. 工作量估算

| 里程碑 | 估时 |
|---|---|
| M1 安全基线 | 2–3 天 |
| M2 请求上下文 v2 | 4–6 天 |
| M3 只读素材/artifact | 3–5 天 |
| M4 事件/取消 | 2–3 天 |
| M5 跨插件调用 | 3–5 天 |
| M6 timeline 读模型 | 2–3 天 |
| M7 引擎能力受控 | 4–6 天 |
| M8 文档/迁移/回归 | 3–5 天 |
| **合计** | **约 3–4 周**（单人） |
