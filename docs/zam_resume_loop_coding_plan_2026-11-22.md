# Coding Plan：断点续跑、素材恢复与时间线历史

> 范围：#2 停止/暂停后的断点续跑，#3 material 降级后的断点恢复，#4 刷新后时间线历史不可见
> #1 只保留 `provider_error` 分类，不扩大重试/网关/UI 改造。
> 状态：待实施；本文件只描述方案，不含代码改动。

## 0. 根因回顾（已核实）

| # | 根因 | 证据 |
|---|---|---|
| 1 | LLM provider 返回 HTTP 500，`openai` SDK 和 `LLMService` 重试后仍失败；当前没有 `provider_error` 类别 | `logs/backend.err.log`: `LLM structured output failed: status=500, content=Error: Connection error.` |
| 2 | `stop_task()` 写 `user_stopped`，`_released_before()` 只认 `task_released`；`plan_ready` 分支去做规划书修订而非续跑 | 会话最后 3 条都是“继续”，没有新任务；日志 `规划书修订: ... feedback=继续` |
| 3 | `agent_run.run()` 对 `cached.degradations` 一律 `must_rerun`，新 attempt 全量重跑；阶段没有断点契约，material 覆盖 `candidate_clips` | attempt 1：43 候选/1 降级；attempt 2：36 占位/12 降级；output_revision 3→4 |
| 4 | timeline SSE `snapshot` 的 `has_more=false` 覆盖首屏 REST 的 `has_more=true`，前端隐藏“加载更早” | `GET /timeline?limit=500` → has_more=true；`stream?since_seq=latest` → `{items:[],has_more:false}` |

## 1. 范围与非目标

**做**

- #2：停止/暂停后的会话续跑走**检查点续跑**，不再误入“规划书修订”；pause 继续同一 task，cancel 用新 task 继承检查点。
- #3：material 阶段引入可持久化、可校验的检查点；重派时只补未完成/失败场景，合并已有候选，不覆盖完成结果。
- #4：修复 timeline 首屏与 SSE snapshot 的 `has_more` 合并语义；刷新后能一直加载到 seq 1。
- #1：新增并保留 `provider_error` 失败类别；不改上游重试策略、不加新的 provider 网关、不做额外 UI。

**不做**

- 不解决 Pexels/Pixabay 出网被拦（198.18.x.x）本身；只保证失败可续、不可用如实上报。
- 不改变 `AgentResult` v2 的必填字段与既有 wire 形状；新增字段全部可选/向后兼容。
- 不重构主 Agent 编排；material 仍可由主 agent 决定是否重派，重派时自动续跑。

## 2. 设计决策

### D1：释放状态只有一个真相

- 以 `task_released` 为准；`stop_task` 同时写 `user_stopped` 和 `task_released="cancelled"`；
- `_released_before()` 兼容历史 `user_stopped`，并扫描**整段 messages**而不是仅最后 10 条（避免用户连续说“继续”把标记挤出窗口）；
- `_release_session_after_task()` 的 `user_stopped` 提前返回分支不得再写一条矛盾的“没跑完”。

### D2：续跑 = 新 Task + 旧检查点，不是重新规划

- **pause**：同一 task `kernel.resume()`，不新建 task；
- **cancel/stop**：新建 task，但 `extra.resume` 携带：
  ```json
  {
    "from_task_id": "task_xxx",
    "released_reason": "user_cancelled",
    "completed_stages": ["structure", "material"],
    "blackboard_keys": ["script_skeleton", "scenes", "candidate_clips"],
    "run_ids": ["..."],
    "checkpoint_path": "..."
  }
  ```
- 复用现有 `kernel.seed_blackboard(prev_id, new_id)`；`context_loader` 增加 `## 断点续跑` 段，明确已完成阶段和当前产物；
- `_continue_as_task()` 不再调用 `_handle_plan_revision`；如果黑板上已有 `final_timeline`，主 agent 只做增量/收尾而不是重跑全链；
- 同一 stop→continue 操作加幂等键（`session_id:released_task_id`），防重复建 task。

### D3：阶段检查点契约（material 先落地）

新增 `StageCheckpoint`（可选字段，不影响现有 AgentResult）：

```text
checkpoint_id      # stage + input_digest
task_id / stage    # 归属
input_digest       # 黑板输入摘要，防拿错检查点
completed_units    # 已完成场景/片段索引
partial_outputs    # 已完成候选（material: candidate_clips）
failures           # 失败单位与原因
updated_at / attempt
```

- 存放：`<workspace>/checkpoints/<stage>.json`，原子写；上限（条目数/字节数）超限时按场景裁剪；
- `stage_subagent`：进入阶段前按 `plugin_id + stage + input_digest` 查找检查点，注入 `inputs.resume_checkpoint`；阶段结束后把 `checkpoint` 写回；
- `agent_run.run`：`cached.degradations` 时仍允许 rerun，但把 `cached` 的检查点/产物作为 resume 输入；**不因 rerun 丢弃已完成单位**；
- material：`MaterialInput` 增加可选 `resume_checkpoint`；`execute()` 读取 `completed_units`，只处理未完成/失败场景，最后与检查点中的候选合并；不允许用空结果覆盖已有 `candidate_clips`。

### D4：timeline 分页不因增量 snapshot 回退

- 后端 `/timeline/stream` 的 `snapshot` 增加 `has_more_before`（快照首条之前是否还有更早条目）；
- 前端 `applyFrame('snapshot')` 使用 `hasMore = prevHasMore || frame.has_more_before || frame.has_more`，仅显式 reset/归档时置 false；
- `loadEarlier` 仍用 `before_seq`；补“刷新→最新页→向前翻到 seq 1”的测试。

### D5：provider_error 只做分类

- `FAILURE_CATEGORIES` 增加 `"provider_error"`；
- `LLMService` 把 provider 500 转为结构化错误（带 `status_code/provider/model` 元数据），阶段结果为 `failure.category="provider_error"`、`retryable="retry_same"`；
- 不再把 provider 原始文本当最终汇报内容；不改变现有重试次数/退避，不新增 UI 分支。

## 3. 实施阶段

### Phase 1：provider_error（小改动，先合）

**文件**

- `clipwright/schema/agent.py`：`FAILURE_CATEGORIES += provider_error`
- `clipwright/services/llm.py`：新增 `LLMProviderError`（`status_code`, `provider`, `model`, `content`），`generate/structured_output/chat` 抛出
- `clipwright/services/stage_subagent.py` / `stage_adapter.py`：异常映射到 `FailureReport(category="provider_error")`
- `clipwright/services/agent_kernel.py`：任务终态仍走 `model_error`，但 `blocker_code=provider_error`

**测试**

- provider 500 fixture → `AgentResult.failure.category == "provider_error"`，不出现未分类 `unknown`；
- 现有 LLM 重试测试不回归。

**验收**：500 不再表现为裸 `Error: Connection error.`；只多一个分类，行为不变。

### Phase 2：停止/暂停续跑（#2）

**后端**

1. `requirements_service.py`
   - `stop_task`: `metadata={"user_stopped": True, "task_released": "cancelled", ...}`
   - `_released_before`: 扫描全量 messages，接受 `task_released` 或 `user_stopped`（兼容历史）
   - `_release_session_after_task`: `user_stopped` 分支补 `task_released` 或跳过矛盾追加
   - `_maybe_continue_as_task`: `plan_ready` 且已释放 → `_continue_as_task`，不进入 `_handle_plan_revision`
2. `_continue_as_task`
   - 复用 `seed_blackboard`；增加 `resume` extra 与 `completed_stages`；
   - 写 `context_loaded` 事件时带上 `seed_keys/resume_from`；
   - 幂等键 `session_id:released_task_id`，防重复 task；
   - `pause` 路径只调 `kernel.resume`，不建新 task。
3. `context_loader.py`
   - 新增 `## 断点续跑` 段（从 `extra.resume` 读取）；限制长度，和现有 `prev_run` 同预算。

**前端**

- `requirementsApi.streamChat`: 收到 `{type:"error"}` 立刻 reject（带可读 detail），不要静默返回 `{}`；
- `AgentPanel.sendChat`: catch 后清除 `busy`/`streaming`，显示“模型服务失败（provider_error）”，不再显示空转；
- 续跑返回 `task_id` 时 `taskStore.load` 保持不变。

**测试**

- `tests/clipwright/test_requirements_continue.py`（新）：
  - stop→chat("继续") → 新 task、`status=pipeline_running`、`extra.resume.from_task_id` 正确、`seed_blackboard` 被调用；
  - 不会调用 `_handle_plan_revision`；无新 task 的重复请求被幂等；
  - pause→resume 仍是同一 task id。
- 前端 `AgentPanel`：error 帧后 `busy=false`、有错误气泡、无永久“正在处理规划…”。

**验收**：用户停止后说“继续”能启动新 task，主 agent 看见旧黑板并跳过已完成工作；不再只停在 busyLabel。

### Phase 3：material 断点恢复（#3）

**后端**

1. `clipwright/schema/checkpoint.py`（新）：`StageCheckpoint` + 校验；`clipwright/services/checkpoints.py`（新）：原子读写、按 input_digest 查找、裁剪。
2. `stage_subagent.py`
   - 阶段开始前 `load_checkpoint(stage, input_digest)`；
   - `assemble()`/inputs 注入 `resume_checkpoint`（仅当 digest 匹配）；
   - 阶段结束后写 checkpoint（从 `result.details.checkpoint` 或显式字段）。
3. `agent_run.py`
   - `cached.degradations` 时把 cached 的 `run_id`/outputs/checkpoint 作为 `resume` 传给 `stage_subagent`；
   - `RunResult.details` 记录 `resumed_from_run_id`；`redo_request` 不新增。
4. `material_agent.py`
   - `MaterialInput.resume_checkpoint` 可选；
   - `execute()`：`completed_units` 中的场景直接复用；失败/未完成场景重试；
   - 场景结果写回检查点（在 `asyncio.gather` 完成后串行合并，避免写竞争）；
   - 下载/网络失败保留在 `failures`，不做“清空候选”；
   - 最终 `candidate_clips` = 已完成 + 新结果，按 `scene_index` 去重。
5. `stage_inputs.py`：material 装配时透传 checkpoint；不新增必需黑板键。

**测试**

- `tests/clipwright/test_material_checkpoint.py`（新）：
  - 3 场景，首轮成功 1 个 + 2 个失败 → checkpoint 保存；
  - 第二轮输入相同 digest → 只处理 2 个失败场景，不重复成功场景；
  - 候选合并后 `scene_index` 唯一，成功场景不被覆盖；
  - digest 变化 → 不读旧 checkpoint；
  - 无 checkpoint 的旧任务行为不变。
- `agent_run` 单测：degraded cached → `resumed_from_run_id` 出现，attempt 递增但完成场景不重跑。
- E2E：用 `proj_64864ef93889` 的网络失败场景复现，第二轮工具次数应只覆盖失败场景，`candidate_clips` 不被 36 个占位整体覆盖。

**验收**：material 降级后重派是“续跑”而非“全量重来”；已完成场景的候选和下载结果保留。

### Phase 4：timeline 历史（#4）

**后端**

- `clipwright/api/requirements.py` 的 `timeline/stream` snapshot 增加 `has_more_before`：
  ```python
  oldest = page["items"][0].seq if page["items"] else log.latest_seq() + 1
  has_more_before = log.has_more_before(oldest)  # 新增只读查询
  ```
- 保留原有 `has_more` 字段，避免旧前端破坏；REST `/timeline` 不变。

**前端**

- `sessionTimelineStore.applyFrame('snapshot')`：`hasMore = prevHasMore || frame.has_more_before || frame.has_more`；
- `useSessionTimeline` 首屏 `hasMore` 保持；`loadEarlier` 成功后有新增条目；
- `SessionTimeline` 的“加载更早”按钮在刷新后继续出现。

**测试**

- 后端：stream snapshot `has_more_before=true` 当 `latest_seq>1` 且无新条目；
- 前端 store：先 `hydrate(hasMore=true)`，再 `applyFrame(snapshot, has_more=false)` → `hasMore` 仍 true；
- `SessionTimeline`：刷新场景 → 点“加载更早” → 出现 seq 1 条目。

**验收**：`proj_64864ef93889` 对应会话刷新后可以翻到 seq 1，不再只看到最新 500 条。

## 4. 测试与验收矩阵

### 自动化

```bash
# 后端
python -m pytest tests/contracts tests/clipwright/test_requirements_continue.py \
  tests/clipwright/test_material_checkpoint.py tests/test_event_contract.py -q
python -m ruff check clipwright --select E9,F63,F7,F82,F823 --output-format=concise

# 前端
npx tsc --noEmit
npm run test
npm run build
```

### 手工验收

1. 启动真实任务，material 降级 → 主 agent 重派 → 工具轨迹只覆盖失败场景，`candidate_clips` 不整体丢失；
2. 停止任务 → 输入“继续” → 出现新 task、非规划书修订；刷新后仍能跟到 task；
3. 刷新 `req_b9b505119d0c` → 时间线最新页正常，“加载更早”可翻到 seq 1；
4. 伪造 provider 500 → 运行详情显示 `provider_error`，不挂住前端。

### 通过标准

- #1：`provider_error` 出现在 `AgentResult.failure.category`，无 `unknown`；
- #2：stop→continue 建立 1 个新 task，`extra.resume` 有旧 task/黑板书，无 plan revision；pause→resume 保持同一 task；
- #3：material 第二轮只补未完成单位，成功场景不重跑/不覆盖；
- #4：刷新后可加载完整历史（至少验证 925 条会话翻到 seq 1）。

## 5. 发布、回滚与 PR 切分

- **分支**：后端 `fix/zam_resume_loop`（#1/#2/#3），前端 `fix/zam_timeline_history`（#4）；两边都从各自默认分支切出。
- **PR 切分**：
  1. backend provider_error（小，先合）；
  2. backend stop/continue + checkpoint（#2/#3，含迁移测试）；
  3. frontend timeline pagination + error stream（#4/#2 的 UI 部分）。
- **回滚**：
  - checkpoint 读取失败/格式不符 → 视为无检查点，旧路径重跑；
  - `extra.resume` 缺失 → `_continue_as_task` 退回当前新任务逻辑；
  - 前端 `has_more_before` 缺失 → 回退 `has_more`，不阻断。
- **风险**：
  - 检查点可能包含大对象：按场景/字节裁剪，不写 timeline 大内容；
  - 并发写检查点：material gather 后串行合并，单写者；
  - 重复 stop/continue：幂等键 + 现有 `_live_task_id` 双重检查；
  - 旧会话无 `task_released`：`user_stopped` 兼容，不能要求历史数据迁移。

## 6. 里程碑

| 阶段 | 交付 | 预计 |
|---|---|---|
| M1 | provider_error 分类 + 单测 | 0.5 天 |
| M2 | stop/continue 断点续跑 + 前端错误收口 | 1.5 天 |
| M3 | material checkpoint + 合并/重试测试 | 2 天 |
| M4 | timeline has_more 修复 + 刷新 E2E | 1 天 |
| M5 | 真实项目回归 + PR | 0.5 天 |

总计约 **5.5 人天**（不含 provider 侧网络治理）。
