# Clipwright 工具工效 PRD

| 字段 | 值 |
|---|---|
| 文档 | 工具工效与 Agent 反馈 PRD |
| 版本 | v0.1 |
| 日期 | 2026-11-22 |
| 状态 | Draft |
| 来源 | `task_35690d4b72` 实机事件流、各阶段 tool calls 轨迹、stage reports（Agent 已结束，无法继续多轮对话） |
| 范围 | 主 Agent + structure / material / edit / animation / audio / quality / research 子 Agent |

---

## 1. 背景

`task_35690d4b72` 完成了 917 秒、43 场景的知识区长片，但 agent 的工具使用呈现出明显的**高调用、低单次产出、缺少批处理与工作区查询**特征：

- material 的 `vision_llm` 调用 **670 次**，单次中位 **5.7s**、最长 **86s**，第一轮直接打满 200 次工具上限；
- edit 的 `video_trim` 调用 **77 次**，一次只处理一个 clip；
- 主 Agent 的 `image_generate` 调用 **64 次**，中位 **28s**、最长 **122s**，2 次 ReadTimeout；
- 主 Agent 的 `list_dir` 调用 **60 次**，其中 4 次是猜错路径；
- animation 的 `generate_mg` 调用 **36 次**，第一次 attempt 因一个 `glow_color=""` 校验错误白跑 **407s**；
- research 的两个 run 各约 **17s**，都因工作区上下文缺失而只得到“路径不在允许目录”。

这些不是单个模型的“不会用”，而是工具面缺少**批量、查询、预检、进度和失败语义**，把成本转嫁给了 Agent 的回合与重试。

---

## 2. 目标与非目标

### 2.1 目标

1. 让高频操作从“逐条调用”变成“批量提交 + 结果回执”。
2. 让 Agent 能用少量调用定位工作区内容，而不是反复 `list_dir` / 猜路径。
3. 让长操作（视觉评分、图片生成、MG、trim）可见、可取消、可局部重试。
4. 让工具失败在 Agent 层可读、可决策，而不是把原始 Pydantic / 路径错误抛回去。
5. 让 research worker 默认拥有与主 Agent 一致的工作区语义。
6. 用可量化指标验证工效改善，而不是只增加工具数量。

### 2.2 非目标

- 不改变五层架构与“主 Agent + 阶段子 Agent”的职责边界；
- 不把业务 Agent 变成直接编排固定管线的执行器；
- 不为单个厂商/模型写专用工具；
- 不通过放宽 `assert_allowed_path` 解决路径问题；
- 不在本期重写时间线渲染或视频编辑内核。

---

## 3. 证据与工效基线

### 3.1 工具调用基线

| Agent | 工具 | 次数 | 中位耗时 | 最长耗时 | 失败/异常 |
|---|---|---:|---:|---:|---|
| 主 Agent | `image_generate` | 64 | 28s | 122s | 2 次 ReadTimeout |
| 主 Agent | `list_dir` | 60 | 109ms | 674ms | 4 次路径不存在 |
| 主 Agent | `read_artifact` | 40 | 104ms | 149ms | 0 |
| 主 Agent | `read_report` | 14 | 120ms | 134ms | 2 次路径被拒 |
| 主 Agent | `read_file` | 20 | 108ms | 139ms | 2 次超过 100KB |
| 主 Agent | `delegate_agent` | 28 | 8.5s | 1075s | 1 次 `glow_color` 校验错误 |
| 主 Agent | `research_agent` | 4 | 17.1s | 17.2s | 2 个 run 均因工作区缺失失败 |
| material | `vision_llm` | 670 | 5.7s | 86s | 0 次工具错误，但 200 次上限被耗尽 |
| edit | `video_trim` | 77 | 0.68s | 31.6s | 0 |
| edit | `image_to_clip` | 3 | 18.8s | 71s | 0 |
| animation | `generate_mg` | 36 | — | — | 第一次 attempt 因 `glow_color` 失败 |
| animation | `web_images` / `fact_need` | 4 / 2 | 0.7s / 2.1s | 2.1s | 0 |
| structure | 无工具调用 | 0 | — | — | 只产出 43 个分镜 |
| audio | 无工具调用 | 0 | — | — | 2 次 attempt，各约 9s |
| quality | 未派发 | 0 | — | — | 没有质检 |

### 3.2 关键事实

- **预算被视觉判定吃掉**：material 两次 attempt 共 313 次工具调用，其中 670 次 `vision_llm`；12 个场景最终 `material_placeholder`。
- **图片生成与插入脱节**：主 Agent 生成 30+ 张图，但 `candidate_clips` 里没有 `materials/image` 引用，`edit` 只从候选列表取图；没有 `timeline_insert_image` 调用，图片未进片。
- **文字动画“看起来消失”**：文字轨 32 条、动画轨 33 条已在项目时间线里；首条文字从 81.1s 开始且前 9 秒处于淡入，时间轴又需要向下滚动，容易被误判为缺失。
- **research 没有工作区**：`_run_research` 曾构造 `extra_params={}`，worker 的 `read_file/list_dir` 只传 `path`；相对路径按 CWD 解析后被白名单拒绝，且 worker 正常结束被误标为成功。

---

## 4. Agent 反馈摘要

> 下述反馈来自该任务的事件流、tool calls 轨迹与阶段报告；不是泛化的模型自述。

### 4.1 主 Agent

**用得不趁手**

- `image_generate` 一次一张：64 次调用、中位 28s、最长 122s；生成后还要自己决定插入时机。
- `read_file` 100KB 上限撞到 637KB / 167KB 的产物，只能退回 `read_artifact`。
- `list_dir` 60 次，其中 4 次在猜 `_cache/tmp`、`assets/_cache` 等不存在的路径。
- `delegate_agent` 有一次把 `glow_color` 的 Pydantic 错误原样返回，缺少派发前 schema 预检。

**缺少**

- `image_generate_batch`（批量 prompt + 异步轮询 + 单项重试）；
- `timeline_insert_images`（批量把生成图插入指定场景/位置）；
- `artifact_search` / `artifact_summary`；
- `read_file_range` / `read_file_tail`；
- `glob` / `workspace_tree`；
- `validate_agent_inputs`。

### 4.2 structure

无工具调用，无法从记录判断“趁手”程度；它只依赖 LLM。

**缺少**

- `scene_budget_check`：43 个分镜的总时长、场景数、段落密度与目标时长对账；
- `script_scene_diff`：改稿后看分镜增删。

### 4.3 material

**用得不趁手**

- `vision_llm` 670 次，中位 5.7s、最长 86s；第一轮 200 次上限耗尽；
- 缺少粗筛，模型只能对每个候选看 3 帧；
- 判定与下载未解耦，预算被重试吃光，最后 12 个场景占位。

**缺少**

- `vision_llm_batch`（一批候选一次评分，支持并发、缓存、局部重试）；
- `candidate_rerank`（按场景语义先粗排 top-K）；
- `material_dedupe`；
- `material_source_probe`（先确认源可用再下载）。

### 4.4 edit

**用得不趁手**

- `video_trim` 77 次，一次一个 clip；`image_to_clip` 3 次，最长 71s；
- 只能读 `candidate_clips`，不能主动查询某场景还有哪些图/视频；
- 没有“这张图/片段是否真的进了时间线”的验证工具。

**缺少**

- `video_trim_batch` / `apply_edl`；
- `timeline_probe`（查看某场景实际使用了什么素材）；
- `image_to_clips_batch`；
- `candidate_query`。

### 4.5 animation

**用得不趁手**

- `generate_mg` 36 次，没有批量生成，也没有生成后单段预览；
- 第一次 attempt 因 `glow_color=""` 失败，407 秒白跑；
- 文字动画逐 clip 生成时没有进度事件，前端看起来卡在 `fact_need`（已通过事件补齐修复）。

**缺少**

- `generate_mg_batch`；
- `preview_animation_clip`；
- `validate_animation_clip`（颜色/关键帧/时长前置校验）；
- `apply_animation_batch`。

### 4.6 audio

无直接工具调用；两次 attempt 各约 9 秒，主要是内部逻辑。

**缺少**

- `audio_timeline_probe`：确认旁白、字幕、BGM 的落点；
- `mix_report`：增益、响度、重叠、静音一次性报告。

### 4.7 quality

本任务没有派发 quality，因此没有工具反馈。

**缺少的不是单个工具，而是流程约束**

- 收尾前强制 quality；
- `quality_report`：占位、黑帧、字幕溢出、音画不同步、响度。

### 4.8 research

**用得不趁手**

- 只有 `read_file` / `list_dir`，没有工作区根，所有路径被拒；
- 没有 `grep` / `glob` / `read_artifact`，只能猜路径；
- 工具全失败后仍以“结论”文本正常结束，UI 显示成功（已修复）。

**缺少**

- 工作区上下文注入（已提交修复）；
- `grep_workspace` / `glob_workspace`；
- worker 版 `read_artifact`；
- 工具全失败时 fail-closed 的返回语义（已提交修复）。

---

## 5. 需求

### P0 — 先解决最大成本项

#### R1 `vision_llm_batch`

- 输入：`assets[]`（每个含 `asset_id/path/url`）、`scene_context`、`frame_count`、`concurrency`；
- 输出：每个 asset 独立 `{status, score, tags, description, error}`；
- 支持缓存键：`asset_id + source_fps + frame_count + scene_context_hash`；
- 单批最多 50 个，超时/失败项可单独重试；
- 目标：43 场景的视觉调用从 670 次降到 ≤ 100 次。

#### R2 生图工具整体由插件提供

- **Engine 只保留接口**：`ImageProvider` 协议、request/result/capabilities、`provider_sources` 源配置、通用 workspace/asset/job 基础设施。
- **Plugin `image_providers` 提供工具**：`image_generate`（单张）与 `image_generate_batch`（批量）都作为插件 `tool` 贡献注册；provider 实现也在同一插件（`image_gpt_images` 作为 provider 插件）。
- **无插件/无 provider 时**：`image_generate` 不出现在工具面，不存在“Engine 空壳工具”；Agent 不得假设它可用。
- 输入：`prompts[]`、`size`、`n`、`workspace_root`；
- 输出：每项 `{ok, rel_path, sha256, mime, error}`；
- 异步任务 + `job_id` 轮询，单项失败不拖垮整批；
- 生成后可选 `insert` 参数，调用 Engine 的 `timeline_insert_images`；
- provider 不支持原生 batch 时，插件内部并发单张，不新增 Engine 工具；
- 目标：64 次串行生成合并为 ≤ 8 个批次。

#### R3 `video_trim_batch` / `apply_edl`

- 输入：EDL 列表 `{asset, start_sec, duration_sec, track, position}`；
- 输出：逐项结果 + 整体失败项清单；
- 支持幂等键与 trim 缓存；
- 目标：77 次 trim 合并为 ≤ 6 次批量提交。

#### R4 `generate_mg_batch` + 前置校验

- 一次提交多个 MG 任务；
- 每项生成后立即返回 `preview_path` / `mg_def` / `issues[]`；
- 在写入时间线前校验 `glow_color` 等颜色字段、关键帧、时长；
- 目标：36 次 `generate_mg` 合并为 ≤ 8 批，`glow_color` 类失败在生成前拦截。

### P1 — 工作区与查询

#### R5 工作区查询工具

- `glob(pattern, root)`：按 glob 找文件；
- `workspace_tree(depth, filter)`：返回有界目录摘要；
- `read_file_range(path, offset, length)`：分段读大文件；
- `artifact_search(query)` / `artifact_summary(id)`；
- 目标：主 Agent 的 `list_dir` 从 60 次降到 ≤ 10 次。

#### R6 research 工作区与失败语义

- 强制继承 `task.request.extra.workspace_root` / `project_id`；
- worker 的 `read_file/list_dir/write_file` 注入根目录；
- 工具全部失败时返回 `tool_failed`，不得输出“成功”；
- 增加 `grep_workspace`、`glob_workspace`、`read_artifact`。

#### R7 派发前 schema 预检

- `validate_agent_inputs(stage, inputs)` 在 `delegate_agent` 前校验；
- 对 `glow_color`、颜色、枚举、必填字段给出可读错误；
- 可修复字段返回 `suggestion`，避免整段重跑。

#### R10 终态任务的只读复盘会话

- 已完成任务可以打开一个**只读复盘会话**，但不能复用生产 tool loop；
- 复盘会话不暴露写工具、不派发阶段、不修改黑板/项目/工作区；
- 输入是任务事件、tool calls、阶段报告与当前黑板快照；
- 输出按 agent 角色归档（main / structure / material / edit / animation / audio / quality / research）；
- 生产环境的 `resume` 对 done/failed 仍然返回 409，不复用“续跑”语义。

### P2 — 可观测与质量

#### R8 长操作进度与回执

- 所有批量工具返回 `job_id` + `progress`；
- 阶段事件至少包含“开始/完成/失败/跳过”；
- 文字动画、MG、trim、图片生成都发 `subagent_progress`；
- 心跳事件用于单次长任务，避免 UI 停在 `fact_need`。

#### R9 强制 quality 收尾

- 非 plan-only 任务在 `audio` 之后默认派发 `quality`；
- 提供 `quality_report`：占位、黑帧、字幕溢出、音画不同步、响度；
- 未通过时不得直接 `done`。

---

## 6. 工具契约原则

1. **批量优先**：只要输入是数组，默认支持批量，不要求 Agent 写循环。
2. **失败项独立**：批量结果必须逐项返回 `status`，不能整批 500。
3. **幂等**：所有写操作带 `idempotency_key`，重试不产生重复片段/文件。
4. **上下文注入**：工作区、项目、任务由宿主注入，不要求模型猜。
5. **有界**：批量大小、返回条数、文件读取长度都有上限，超限返回分页游标。
6. **可取消**：长任务必须可 `cancel(job_id)`。
7. **错误可读**：`category`、`blocking_point`、`retryable`、`suggestion` 四件套。
8. **不绕过安全**：批量工具仍在 `assert_allowed_path` 内工作。

---

## 7. 验收指标

| 指标 | 当前 | 目标 |
|---|---:|---:|
| material `vision_llm` 调用数（43 场景） | 670 | ≤ 100 |
| 主 Agent `image_generate` 调用数 | 64 | ≤ 8 批 |
| edit `video_trim` 调用数 | 77 | ≤ 6 批 |
| animation `generate_mg` 调用数 | 36 | ≤ 8 批 |
| 主 Agent `list_dir` 调用数 | 60 | ≤ 10 |
| 主 Agent `read_file` 超限失败 | 2 | 0（自动分段） |
| research 因工作区失败 | 2/2 run | 0 |
| `glow_color` 类校验失败 | 1 次整段重跑 | 0（派发前拦截） |
| animation 首次进度事件延迟 | 约 6 分钟 | ≤ 10 秒 |
| quality 派发率 | 0 | 100%（非 plan-only） |

---

## 8. 分期

### Phase 1（P0，1–2 周）

- `vision_llm_batch` + 候选粗筛/缓存；
- `image_generate_batch` + 自动插入；
- `video_trim_batch` / EDL；
- `generate_mg_batch` + 前置校验。

### Phase 2（P1，1 周）

- `glob` / `workspace_tree` / `read_file_range`；
- `artifact_search` / `artifact_summary`；
- research 工作区与失败语义；
- `validate_agent_inputs`。

### Phase 3（P2，1 周）

- 批量任务进度/取消/心跳；
- quality 强制收尾与 `quality_report`；
- 指标看板：调用数、批量率、失败率、阶段时长。

---

## 9. 风险与开放问题

- **批量会不会放大错误？** 需要逐项状态与重试；默认单批 50 个，失败隔离。
- **视觉缓存会不会遮蔽新素材？** 缓存键包含 sha256 / URL / 参数；素材变更主动失效。
- **图片自动插入会不会放错场景？** 默认只提供插入候选与预览，最终落位由 edit/主 Agent 确认。
- **quality 强制派发会不会增加时长？** 质量阶段使用独立预算（已有 escrow 机制），不挤占主 Agent。
- **research 失败判定的边界？** 只有“调用过工具且全部失败”才 fail-closed；无工具调用的纯推理仍可返回文本。

---

## 10. 临时续跑实验记录

为验证“结束的任务能否继续”，曾临时放开 `resume` 对终态任务的守卫，并注入只读复盘指令。结果：

1. 任务确实重新进入 tool loop；
2. 主 Agent 没有把指令当复盘，而是立即开始重跑 `material`，再次调用 `vision_llm`；
3. 立即取消，恢复任务 checkpoint / state 备份，并回滚临时代码；
4. 任务最终保持 `done`，没有把实验写入生产。

结论：**“继续任务”与“复盘访谈”必须是两个入口**。复盘不应复用生产 tool loop，而应走 R10 的只读会话；否则“问它问题”会被模型理解成“继续干活”。

## 11. 附录：证据入口

- 任务：`task_35690d4b72`
- 事件流：`GET /api/task/task_35690d4b72/events`
- 阶段工具轨迹：`workspaces/proj_64864ef93889-f93889/reports/tool_calls/*.json`
- 阶段报告：`workspaces/proj_64864ef93889-f93889/reports/*.md`
- 当前工作区：`workspaces/proj_64864ef93889-f93889`
