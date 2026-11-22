# Clipwright 工具工效 Coding Plan

| 字段 | 值 |
|---|---|
| 依据 | `docs/tool-ergonomics-prd-2026-11-22.md` |
| 版本 | v0.1 |
| 日期 | 2026-11-22 |
| 状态 | Ready for implementation |
| 目标 | 落实 R1–R10，优先解决批量、工作区查询、失败语义与提示词同步 |
| 分支建议 | `feat/zam_tool_ergonomics`（从 `main` 切出） |

---

## 0. 实施原则

1. **先契约、后实现**：批量工具先定 `status/items/error/job_id`，再接 Agent。
2. **Engine 管机制，Plugin 管能力**：
   - 时间线、工作区、产物、批量任务、校验、事件、质量门 → Engine；
   - provider 实现、素材源探测、厂商原生批量 API、类型插件 → Plugin。
3. **不在 engine 写 provider 名/厂商分支**：遵守不变量 #24。
4. **每个工具落地同批更新提示词**；提示词变更必须过 `test_prompt_lint` 与提示词来源测试。
5. **失败默认逐项返回**，不允许批量工具整批 500；写操作必须幂等。
6. **先有 fixture，再改行为**：以 `task_35690d4b72` 的工具轨迹做回归基线。

---

## 1. 分层判定

### 1.1 Engine 层（新增/修改）

| PRD | 工具/能力 | Engine 落点 | 说明 |
|---|---|---|---|
| R1 | `vision_llm_batch` | `clipwright/tool/vision_batch.py` | 通用视觉评分批处理；复用 `vision_score` 能力 |
| R2 | `image_generate` / `image_generate_batch` | **Plugin**（`plugins/image_generate/`） | 文生图能力、provider 选择、批量语义全部归插件；Engine 不注册该工具 |
| R2 | `timeline_insert_images` | `clipwright/tool/timeline_insert_image.py` 扩展 | 批量插入时间线；Engine 持有 timeline |
| R3 | `video_trim_batch` / `apply_edl` | `clipwright/tool/video_batch.py` | FFmpeg/时间线是 Engine 能力 |
| R4 | `generate_mg_batch` | `clipwright/tool/animation_batch.py` | MG 生成与时间线写入是 Engine |
| R4 | `preview_animation_clip` | `clipwright/tool/animation_preview.py` | 预览/渲染是 Engine |
| R4 | `validate_animation_clip` | `clipwright/tool/animation_validate.py` | schema/颜色/关键帧校验是 Engine |
| R5 | `glob` / `workspace_tree` / `read_file_range` | `clipwright/tool/workspace_search.py`、`fs_tools.py` | 工作区工具 |
| R5 | `artifact_search` / `artifact_summary` | `clipwright/tool/artifacts.py` | 产物索引是 Engine |
| R5 | `candidate_query` / `candidate_rerank` / `material_dedupe` | `clipwright/tool/material_query.py` | 对 `candidate_clips` 的通用查询/排序；不绑定素材源 |
| R6 | `grep_workspace` / `glob_workspace` / `read_artifact` | `clipwright/tool/workspace_search.py` + supervisor 白名单 | research 与主 Agent 共用 |
| R7 | `validate_agent_inputs` | `clipwright/services/stage_inputs.py`、`tool/surface.py` | 派发前 schema 预检；优先自动执行 |
| R8 | 批量 job / 进度 / 取消 | `clipwright/services/tool_jobs.py`、`agent_context.py` | 通用机制 |
| R9 | `quality_report` + 强制质量门 | `clipwright/agents/quality_agent.py`、`tool/quality.py`、`agent_kernel.py` | 质量门是 Engine 流程 |
| R10 | 只读复盘会话 | `clipwright/services/agent_review.py`、`api/task.py` | 不是生产 resume，也不暴露写工具 |
| 附加 | `scene_budget_check` / `script_scene_diff` | `clipwright/tool/scene_analysis.py` | 纯算法 |
| 附加 | `timeline_probe` | `clipwright/tool/timeline_view.py` 扩展 | 时间线检查 |
| 附加 | `audio_timeline_probe` / `mix_report` | `clipwright/tool/audio_inspect.py` | 音频检查 |

### 1.2 Plugin 层（只适合插件）

| 能力 | 插件落点 | 原因 |
|---|---|---|
| `material_source_probe` | `plugins/*_material/` + `clipwright/material/base.py` 可选 `probe()` | 只有素材源自己知道可用性、地区、限流、鉴权 |
| 厂商原生批量生图 | `plugins/image_generate/`、`plugins/image_gpt_images/` | 原生 batch API 属于 provider 实现；Engine 只定义可选 `generate_batch` 契约 |
| 插件素材源检索/排序的私有评分 | 对应 `*_material` 插件 | 私有索引、许可证、可用性不能写进 Engine |
| `image_generate`（单张工具） | `plugins/image_generate/` | 工具能力应由插件提供；无 provider/插件时工具不出现，Engine 不保留空壳 |
| `image_generate_batch`（批量工具） | `plugins/image_generate/` | 批量 API/参数/响应/调度归插件；Engine 不注册 |
| `web_images` / `fact_need` | `plugins/web_media/` | 网页媒体与事实判断本来就是插件贡献 |
| TTS/STT/embedding/rerank provider | 对应插件 | 遵守 provider 实现归插件 |
| Animation 类型的具体生成实现 | `plugins/text_animations`、`mg_animations`、`logic_animations` | Engine 只提供批量编排与时间线写入 |
| 自定义质量检查器 | 通过 `quality` 贡献/能力注册 | 具体标准属于插件；质量门属于 Engine |

### 1.3 明确不移动

- `web_search` / `web_fetch` 仍是 Engine 内置工具（现有边界）；
- `image_generate` **不再保留 Engine 工具**：工具与 provider 一起由 `image_generate` 插件提供；
- Engine 只保留 `ImageProvider` 协议、provider source 注册/解析、通用 workspace/job/asset 基础设施；
- 没有 image provider 插件时，`image_generate` 不出现在工具面，不存在“空壳工具”；
- `generate_mg` 的现有 Engine 内实现不搬进插件；
- 不新增按厂商名判断的分支，不把插件实现类搬回 Engine。

---

## 2. 工作包

### WP0 — 基线与契约（0.5 周）

**文件**

- `clipwright/schema/tool.py`：增加 batch 通用字段；
- `clipwright/tool/base.py`：`BatchResult`/`JobRef` 契约；
- `clipwright/services/tool_jobs.py`（新增）：job 注册、状态、取消、清理；
- `tests/contracts/test_tool_batch_contract.py`（新增）。

**步骤**

1. 定义统一返回：
   ```json
   {
     "job_id": "job_xxx",
     "status": "queued|running|done|partial|failed|cancelled",
     "items": [{"index": 0, "status": "ok|failed|skipped", "error": null}],
     "summary": {"total": 0, "ok": 0, "failed": 0}
   }
   ```
2. job 生命周期：`queued → running → done/partial/failed/cancelled`。
3. 每个 item 必须有独立 `status`；整批失败只用于参数/环境错误。
4. 统一错误四件套：`category/blocking_point/retryable/suggestion`。
5. 为 task `task_35690d4b72` 建离线 fixture（tool 轨迹摘要，不依赖网络）。

**测试**

- 批量中一项失败，其余仍成功；
- 取消 job 后不再写结果；
- 重复 idempotency key 不重复落盘。

---

### WP1 — R1 `vision_llm_batch`（P0）

**文件**

- `clipwright/tool/vision_batch.py`（新增）
- `clipwright/tool/__init__.py`（注册）
- `clipwright/agents/material_agent.py`（改用批量工具）
- `clipwright/tool/capabilities.py`（`vision_score` 能力解析）
- `tests/contracts/test_vision_batch.py`

**步骤**

1. 输入 `assets[]`、`scene_context`、`frame_count`、`concurrency`（默认 4）。
2. 按 `asset_id + source_fps + frame_count + scene_context_hash` 查缓存。
3. 批量并发调用 `vision_score`；单项失败返回 `failed`，不中断整批。
4. 结果回填到 material 的候选评分，不再每个候选单独调用。
5. 单批 ≤50；超过分页/拆批。

**验收**

- 43 场景 fixture 的视觉调用 ≤100；
- 单项失败不影响其余；
- 缓存命中率有指标。

**提示词同步**

- `clipwright/prompts/material.py`：补“优先 `vision_llm_batch`、先粗筛 top-K、禁止逐候选调用”；
- `clipwright/prompts/__init__.py` 导出新 builder（如需要）；
- `docs/api_reference.md` 工具列表。

---

### WP2 — R2 把 `image_generate` 整体迁到插件（P0）

**目标**

Engine 只留 `ImageProvider` 接口与通用基础设施；`image_generate` / `image_generate_batch` 由 `plugins/image_generate/` 提供，无插件时工具不存在。

**文件**

- Engine：
  - `clipwright/providers/base.py`（保留 `ImageProvider` 协议、request/result/capabilities）
  - `clipwright/plugins/contribution.py` / `contribution_dispatch.py`（保留 `IMAGE_PROVIDER`）
  - `clipwright/services/provider_sources.py`（源配置/角色绑定）
  - `clipwright/services/workspace_materials.py`、`asset`、`tool_jobs`（通用基础设施）
  - 删除 `clipwright/tool/image_generate.py` 及 `services/image_generation.py` 的 image-specific 实现
- Plugin：
  - `plugins/image_generate/main.py`：provider 实现 + `ImageGenerateTool` + `ImageGenerateBatchTool`
  - `plugins/image_generate/plugin.yaml`：声明 `image_provider` + `tool: image_generate` + `tool: image_generate_batch`
  - `plugins/image_gpt_images/`：只保留 provider，不重复注册 tool
- 通用 timeline：
  - `clipwright/tool/timeline_insert_image.py`（批量 `timeline_insert_images`）
- 测试：
  - `tests/contracts/test_image_plugin_tool.py`
  - `tests/contracts/test_image_batch.py`

**步骤**

1. 把 `ImageGenerateTool`、批量逻辑、账本/落盘注入移到 `plugins/image_generate/`；插件只通过公开的 `Workspace`、`AssetIndex`、`provider_sources`、`ToolJobs` 接口访问宿主。
2. 插件 manifest 声明工具贡献；`is_available()` 由插件自身判断 provider 源。
3. 无插件 / 无源时：
   - `ToolRegistry` 没有 `image_generate`；
   - `capabilities.resolve("image_generate")` 返回 None；
   - Agent 工具面不出现该 schema；提示词不得承诺它一定可用。
4. `image_generate_batch` 也由插件提供；provider 不支持 batch 时，插件内部并发单张，不新增 Engine 工具。
5. `timeline_insert_images` 留在 Engine（时间线是宿主资产）。
6. 逐项失败独立返回；写操作幂等。

**验收**

- 未安装 `image_generate` 时，工具面没有 `image_generate`；
- 安装后单张/批量都可用；
- Engine 中不再有 image-specific tool/service；
- 64 次串行生成 → ≤8 批；
- 生成图可被 `timeline_insert_images` 插入。

**验收**

- 64 次串行生成 → ≤8 批；
- 2 次 ReadTimeout 可单权重试；
- 生成图可在时间线中找到 asset。

**提示词同步**

- `clipwright/prompts/loop.py`：主 Agent 改用批量生成；生成后**必须** `timeline_insert_images`（或明确说明为什么暂不插入）；
- `clipwright/prompts/agents.py`：structure 的“AI 生成策略”改为批量生成/关键词一致；
- `docs/api_reference.md`。

---

### WP3 — R3 `video_trim_batch` / `apply_edl`（P0）

**文件**

- `clipwright/tool/video_batch.py`（新增）
- `clipwright/services/edl.py`（新增 EDL 校验/展开）
- `clipwright/agents/edit_agent.py`（批量提交）
- `tests/contracts/test_video_batch.py`

**步骤**

1. 定义 EDL item：`asset/start_sec/duration_sec/track/position`。
2. 复用现有 trim 缓存；批量结果逐项返回。
3. 单 item 失败只跳过该 item，并登记到 `failures[]`。
4. 与 `image_to_clips_batch` 共享 job 机制。

**验收**

- 77 次 trim → ≤6 次提交；
- 一个坏 asset 不阻塞其余片段。

**提示词同步**

- `clipwright/prompts/agents.py` → `EDIT_PROFILE_SYSTEM` 补充“批量 trim/EDL 优先”；
- `clipwright/agents/edit_agent.py` 内部系统提示（如有）同步；
- `docs/api_reference.md`。

---

### WP4 — R4 MG 批量、预览、校验（P0）

**文件**

- `clipwright/tool/animation_batch.py`（新增）
- `clipwright/tool/animation_validate.py`（新增）
- `clipwright/tool/animation_preview.py`（新增）
- `clipwright/agents/animation_agent.py`（批量生成/校验）
- `tests/contracts/test_animation_batch.py`

**步骤**

1. `generate_mg_batch` 接收多个 MG spec，逐项生成。
2. 生成前调用 `validate_animation_clip`：`glow_color` 空值、颜色格式、关键帧、时长。
3. 生成后返回 `preview_path`、`mg_def`、`issues[]`。
4. 批量写回时间线；失败项保留 issue，不整段重跑。

**验收**

- 36 次 MG → ≤8 批；
- `glow_color=""` 在生成前拦截；
- 单段预览可用。

**提示词同步**

- `clipwright/prompts/loop.py`：主 Agent 派 animation 时要求批量/预览；
- `clipwright/prompts/agents.py`：MG 选择规则补“颜色必须合法、先用 validate”；
- `clipwright/prompts/mg.py`：生成提示补批量契约；
- `docs/api_reference.md`。

---

### WP5 — R5 工作区查询（P1）

**文件**

- `clipwright/tool/workspace_search.py`（新增：`glob`、`workspace_tree`、`grep_workspace`）
- `clipwright/tool/fs_tools.py`（`read_file_range`）
- `clipwright/tool/artifacts.py`（`artifact_search`、`artifact_summary`）
- `clipwright/tool/material_query.py`（`candidate_query`、`candidate_rerank`、`material_dedupe`）
- `tests/contracts/test_workspace_search.py`

**步骤**

1. 所有查询限制在 `workspace_root`，复用 `assert_allowed_path`。
2. 返回有界结果 + 分页游标。
3. `read_file_range` 支持 offset/length，替代 100KB 硬失败。
4. `artifact_search` 按 id/标题/stage/时间检索，不读全文。

**提示词同步**

- `clipwright/prompts/loop.py`：用 `glob/workspace_tree` 替代反复 `list_dir`；大文件用 `read_file_range`；
- `clipwright/prompts/material.py`：候选查询/重排优先于全量读；
- `docs/api_reference.md`。

---

### WP6 — R6 research（P1，已先行修复部分）

**已完成**

- `d70eac2`：research 继承 `workspace_root/project_id`；
- worker 文件工具注入根目录；
- 工具全失败返回 `tool_failed`，`_run_research` 不再标 ok。

**剩余**

- `plugins/web_media` 保持 `fact_need`/`web_images`；
- 新增 Engine `grep_workspace`/`glob_workspace`，加入 `SUPERVISOR_TOOLS`；
- worker 版 `read_artifact`；
- 测试：research 读取真实报告、全失败状态。

**提示词同步**

- `clipwright/supervisor/spawn.py` 的 worker prompt：工作区路径 + 相对路径用法；
- `clipwright/prompts/loop.py`：research 任务明确“先 grep/glob 再 read”；
- `docs/api_reference.md`。

---

### WP7 — R7 派发前预检（P1）

**文件**

- `clipwright/services/stage_inputs.py`（预检函数）
- `clipwright/tool/surface.py`（`delegate_agent` 前调用）
- `clipwright/schema/agent.py`（错误结构）
- `tests/contracts/test_stage_preflight.py`

**步骤**

1. `validate_agent_inputs(stage, inputs)` 复用各 `*Input` Pydantic；
2. 返回 `errors[]`，每项含 `field/category/message/suggestion`；
3. 可修复字段给 suggestion，不直接整段失败；
4. 在 `delegate_agent` 入口自动执行，模型不需要手动调用工具。

**提示词同步**

- `clipwright/prompts/loop.py`：说明预检自动执行、看到 suggestion 先修再派；
- `clipwright/prompts/agents.py`：颜色/枚举字段示例；
- `docs/development.md`。

---

### WP8 — R8 进度/取消/心跳（P2）

**文件**

- `clipwright/services/tool_jobs.py`
- `clipwright/services/agent_context.py`（`emit_progress`）
- `clipwright/services/task_events.py`（job 事件）
- `clipwright/agents/animation_agent.py`（文字动画进度已修）
- `tests/test_event_contract.py`

**步骤**

1. 所有批量工具返回 `job_id`，支持 `cancel`。
2. 每个 item 的开始/完成/失败发 `subagent_progress`。
3. 单次长任务每 10s 发心跳。
4. 事件类型加入 `EVENT_TYPES`，前端 parser 同步。

**提示词同步**

- `clipwright/prompts/loop.py`：告知 Agent 可用 job_id 查询/取消，不要盲目重试；
- animation/material 阶段提示词补进度语义。

---

### WP9 — R9 quality 强制收尾（P2）

**文件**

- `clipwright/agents/quality_agent.py`（`quality_report`）
- `clipwright/tool/quality.py`
- `clipwright/services/agent_kernel.py`（收尾门）
- `tests/contracts/test_quality_gate.py`

**步骤**

1. 非 plan-only 在 `audio` 后默认派 quality；
2. `quality_report` 覆盖占位、黑帧、字幕溢出、音画不同步、响度；
3. 未通过不得 `done`，写 `blocked/missing_quality`；
4. 质量预算走 escrow 预留。

**提示词同步**

- `clipwright/prompts/loop.py`：收尾前必须读 `quality_report`；
- `clipwright/prompts/agents.py`：`QUALITY_SEMANTIC_QA_SYSTEM` 增加新检查项；
- `docs/api_reference.md`。

---

### WP10 — R10 只读复盘会话（P2）

**文件**

- `clipwright/services/agent_review.py`（新增）
- `clipwright/api/task.py`（`POST /{task_id}/review`）
- `clipwright/prompts/loop.py` 或独立 `REVIEW_SYSTEM`
- `tests/contracts/test_task_review.py`

**步骤**

1. 只读快照：events、tool calls、reports、blackboard；
2. 不暴露写工具、不派发阶段、不修改任务；
3. 按 agent 角色生成复盘；
4. done/failed 任务可复盘，但生产 `resume` 仍 409。

**提示词同步**

- 新建 `REVIEW_SYSTEM`：明确“不是续跑，不调用工具，只评价工具”；
- 不复用 `LOOP_NORMAL_SYSTEM`，避免模型继续干活（本次临时实验已证明会误判）。

---

## 3. 提示词同步清单（强制）

每次新增/修改工具，同一 PR 必须更新：

| 变更 | 提示词文件 | 必改内容 |
|---|---|---|
| `vision_llm_batch` | `clipwright/prompts/material.py` | 批量、粗筛、缓存 |
| `image_generate` / `image_generate_batch`（Plugin） | `clipwright/prompts/loop.py` | 工具未安装时不出现；安装后批量 + 插入 |
| `timeline_insert_images` | `clipwright/prompts/loop.py` | 生成后显式插入 |
| `video_trim_batch` | `clipwright/prompts/agents.py` | EDL/批量优先 |
| `generate_mg_batch` | `clipwright/prompts/loop.py`、`prompts/mg.py` | 批量 + 预览 + 校验 |
| `workspace_search` | `clipwright/prompts/loop.py` | glob/range 替代 list_dir |
| research 工具 | `clipwright/supervisor/spawn.py`、`prompts/loop.py` | 工作区相对路径、先 grep |
| `validate_agent_inputs` | `clipwright/prompts/loop.py`、`prompts/agents.py` | 预检/建议 |
| batch job | `clipwright/prompts/loop.py` | job_id/取消/不要盲重试 |
| quality | `clipwright/prompts/loop.py`、`prompts/agents.py` | 收尾前质检 |
| R10 | 新 `REVIEW_SYSTEM` | 只读复盘，不续跑 |

每次提示词改动必须运行：

```bash
python -m pytest tests/clipwright/test_prompt_lint.py \
  tests/clipwright/test_engine_prompt_sources.py \
  tests/clipwright/test_stream_system_prompt.py -q
```

---

## 4. 测试矩阵

| 层级 | 测试 |
|---|---|
| 契约 | `tests/contracts/test_tool_batch_contract.py` |
| batch | vision/image/trim/MG 各一个 partial failure + cancel + idempotency |
| 工作区 | glob/tree/range/grep/artifact search |
| research | workspace 注入、全失败 `tool_failed` |
| 预检 | `glow_color`、枚举、必填、suggestion |
| 事件 | `EVENT_TYPES`、前端 parser、job 进度 |
| 质量 | 未跑 quality 不得 done |
| 复盘 | done 任务只读、无写工具、resume 仍 409 |
| 回归 | `task_35690d4b72` fixture 的调用数指标 |

---

## 5. 验收与度量

- `vision_llm` 670 → ≤100；
- `image_generate` 64 → ≤8 批（插件工具；无插件时工具不存在）；
- `video_trim` 77 → ≤6 批；
- `generate_mg` 36 → ≤8 批；
- `list_dir` 60 → ≤10；
- `read_file` 超限失败 2 → 0；
- research 工作区失败 2/2 → 0；
- `glow_color` 类错误整段重跑 1 → 0；
- 非 plan-only quality 派发率 0 → 100%；
- 所有新增/修改工具均有提示词变更和对应测试。

---

## 6. 交付顺序

1. WP0 契约 + fixture；
2. WP1 vision batch（成本最大）；
3. WP2 image batch + insert；
4. WP3 trim/EDL；
5. WP4 MG batch/validate；
6. WP5 workspace query；
7. WP6 research 收尾；
8. WP7 preflight；
9. WP8 事件/心跳；
10. WP9 quality；
11. WP10 只读复盘。

每个 WP 一个独立 commit/PR，PR 描述必须包含：工具契约、Engine/Plugin 归属、提示词变更、指标对比、回滚方式。


---

## 7. 执行进度（2026-11-22）

| WP | 状态 | 提交 | 备注 |
|---|---|---|---|
| WP2 image_generate 插件化 | 完成 | `ac0bc00` | provider + image_generate/batch 工具迁入插件；Engine 删除该工具 |
| WP1 vision_llm_batch | 完成（工具+material 接入） | `e86b576` 及前序 | 批量、缓存、单项失败；material top 候选使用 |
| WP5 工作区查询 | 完成 | `83f32ab`、`2b9bb0a` | glob/tree/grep/range + artifact_search/summary |
| WP3 video_trim_batch | 工具完成，EditAgent 待接入 | `433ac04` | 批量并发、逐项失败 |
| WP4 generate_mg_batch | 工具完成，AnimationAgent 待接入 | `f528ba7` | 批量并发、逐项失败 |
| WP7 派发前预检 | 完成 | `6ef0c52` | input_invalid 拒绝，input_insufficient 软处理 |
| WP6 research 工作区 | 完成 | `d70eac2`、`873ce41` | 工作区注入、grep/glob/tree/range |
| WP8 进度/取消/心跳 | 部分完成 | — | 文字动画进度已修；batch job 进度/取消未做 |
| WP9 quality | 完成（质量门 + report） | `6e5bae3`、`b98813e` | final_timeline 后强制 quality；quality_report |
| WP10 R10 复盘 | 完成（快照+访谈） | `e3277c2`、`bfbfe55` | 只读 review 与按角色 interview |
| WP8 job 基础设施 | 完成（REST + 事件流） | `028df72`、`caa8fc0`、`958200f`、`6a72f26` | 有界 job、逐项进度、取消感知、任务事件流 |
| 调研 worker 工作区 | 完成 | `873ce41` | glob/tree/range 开放给 worker |

收尾状态（后续 Goal 已完成）：

- EditAgent 已做 EDL 全段预取并消费 `video_trim_batch`（`e5eb6eb`）；
- AnimationAgent 已对 `mg_dynamic` 与 typed 逻辑动画做 `generate_mg_batch` 预生成（`bf6a650`、`69540cc`）；
- `image_generate_batch` 已走 provider 原生 `generate_batch`，失败逐项回退（`d0e066b`、`5349add`）。

已完成但需注意：

- 通用 batch job 已有 REST 查询、逐项进度、取消感知与任务事件流；心跳仍由单次工具调用生命周期隐式承担；
- quality 门默认开启（`agent_quality_gate=True`），单测 fixture 默认关闭；

结论：工具工效改造的插件边界、批量工具、工作区查询、research、预检、quality、复盘与 job 基础设施已落地；
edit/animation 的批量消费与 image provider 原生 batch 仍需后续实现，**当前不宣称全部完成**。
