# 主 Agent 的终止机制收口：工程 PRD（可行性设计）

| 结论 | 内容 |
|---|---|
| 做什么 | 在 Kernel 单点落定终态、Goal、结构化原因；结构上限前软着陆并完成可交付收尾。 |
| 不做什么 | 不把 0 改成有限预算、不取消用户真中止、不改会话终态驱动规则、不重写工具权限体系。 |
| 谁验收 | 后端维护者验收 Kernel/API/续跑；前端维护者验收任务摘要、事件与恢复；产品负责人按第 11 节人工走查。 |

## 0. 背景与现状

现在结束语义分散在 Kernel 闸门、tool loop 返回、收尾守门、事件、任务状态和会话 watcher。`_gate_hit` 是唯一预算/无进展/重复失败判点，但 `_control_hit` 在它之前检查外部控制；在当前实际顺序中 `_gate_hit` 先检查显式预算，再重复失败、再无进展，和本 PRD 决定的优先级相反（`clipwright/services/agent_kernel.py:953-957,1374-1410`）。

- `_NO_PROGRESS_LIMIT=6`、`_REPEAT_FAIL_LIMIT=5`，按任务 ID 保存 `_PROGRESS`、`_REPEAT_FAILS`；成功终态需 `final_timeline`，未处置降级收据会把成功候选改成失败（`clipwright/services/agent_kernel.py:37-44,56-96,1127-1269`）。
- 模型循环结构护栏是 400 轮、4000 次工具调用、单次 LLM 内最多 24 个工具轮次；这些不是当前明确的产品预算（`clipwright/services/agent_loop.py:57-62,310-318,556-562`）。`TaskLimits` 的回合/工具/墙钟默认 0，语义为不限，金额 null 为不限（`clipwright/schema/task.py:135-165`）。
- 每次工具执行前 guard 可抛 `LoopAborted`；触闸后 Kernel 记录 `gate_hit`，并将任务标为 failed；取消/暂停另有分支（`clipwright/services/agent_loop.py:323-337,572-580`；`clipwright/services/agent_kernel.py:1074-1114`）。
- 模型自然收尾后，Kernel 还检查 `final_timeline`、降级收据，必要时要求模型继续、Kernel 补派或写收据；之后保存 finish、task_end、结果快照与 checkpoint（`clipwright/services/agent_kernel.py:1116-1321`）。
- 用户停止由 `POST /api/requirements/{session_id}/stop` 调用 `stop_task()`，实际 cancel 协程、消息与时间线各留 `user_stopped=true`，并把会话置 `plan_ready`；没有活任务返回 `stopped=false`（`clipwright/api/requirements.py:295-305`；`clipwright/services/requirements_service.py:3363-3422`）。
- 会话 watcher 以任务终态释放状态：done→pipeline_done，其余终态→plan_ready；会话消息与 timeline 会留 task_released/reason（`clipwright/services/requirements_service.py:3424-3465,3494-3534`）。续跑创建新 Task，传入 `max_turns=160/max_tool_calls=800`，继承上一任务黑板并传 previous_task_id（`clipwright/services/requirements_service.py:3225-3335`）。
- 终态原因现有 `TaskFinish.reason` 是自由字符串；task_end.detail.reason 被前端映射状态，既识别固定成功值又依赖 `gate:`、`tool_loop_error` 等前缀（`clipwright/schema/task.py:178-185`；`src/stores/taskStore.ts:91-109,346-365`）。摘要视图展示 finish 的原因、完成与未完成项（`src/features/task/TaskFinishSummary.tsx:26-105`）。
- 任务 checkpoint 保存 `Task.model_dump()` 为 JSON，重启由 `Task.model_validate()` 反序列化；新增字段须有默认值，旁路状态则须 sidecar 兼容（`clipwright/services/agent_kernel.py:1504-1525,1601-1669`）。
- 任务事件是 task_events 流；前端 taskStore 折叠、限存 400 条，SSE 终态 task_end 后关闭（`clipwright/schema/task.py:28-63,105-130`；`src/stores/taskStore.ts:19-27,309-373`；`src/features/task/useTaskEvents.ts:54-60,92-106`）。
- 时间线写回不是终止判定：每次阶段结束可增量写项目，`last_writer` 与 `agent_sig` 防止覆盖用户编辑；只写时间线键，写回失败不反转任务成功（`clipwright/services/task_project_sync.py:15-18,32-50,101-128,166-253`）。

调研依据：三层职责是模型自然停、策略层完成判定、引擎硬刹车（`docs/agent-termination-research-2026-11-02.md:14-36`）。opencode 的 `MAX_STEPS_PROMPT` 是在最后一步禁用工具、要求文字汇报而非直接 cancel（同文 `:135-151`）；DSH goal 对连续 3 轮同一 blocker 才允许 blocked，且必须有 `blocked_reason`（同文 `:92-104`）。

## 1. 术语

- **soft landing（软着陆）**：结构性硬上限前切换到只读/收尾工具面的运行模式，不再开启新的生成或阶段工作；模型必须提交交付摘要后才允许终态。达到上限本身不是失败理由。
- **max_steps**：一次任务的结构性硬上限，统一以 Kernel 记账的模型 round 计；第一版固定 400 rounds，与现有 `STRUCTURAL_MAX_ROUNDS` 一致。工具调用仍有 4000 次硬上限，单次 LLM 工具轮次仍为 24。
- **round**：一次模型请求及其工具决策窗口；`TaskUsage.turns` 是全任务累计轮数，子工具轮次在一个 provider 请求内部受 24 限制。每次重入 run_loop 仍累计，不清零。
- **Goal**：任务级、可持久化的完成约束对象，包含目标描述、可判定完成标准、max_rounds、累计轮数、状态、blocker 连续计数与证据。Goal 是主 Agent 最终收尾的目标，不等于用户原始视频制作目标的替代品。
- **terminal_reason**：稳定枚举，任务终态唯一机器原因；`finish.reason` 保留作既有可读兼容字段。
- **收口模式**：软着陆触发后进入的受限运行状态；只能核验、写报告/产物索引、必要的时间线最终同步、finish 文本输出，不得创建新阶段 run 或执行生成/付费/破坏性工具。

## 2. 不变量与红线

1. `TaskLimits.max_turns/max_tool_calls/max_wall_sec=0` 仍表示不限；`budget_usd=null` 仍表示不设金额限额（`clipwright/schema/task.py:144-165`）。新增 `Goal.max_rounds` 与 Kernel 结构性 max_steps 是另一层硬保险，不得反向改变旧 TaskLimits 的语义。
2. 会话状态仍只由任务终态驱动：done→pipeline_done，其它终态→plan_ready；stop_task 独占 `user_stopped` 记录职责（`clipwright/services/requirements_service.py:3363-3422,3424-3465`）。
3. 保持 `task_end`、`gate_hit` 等既有事件可读；新事件类型只增不删，前端旧客户端忽略未知事件。`task_end.detail` 继续包含 `reason`、`completed`、`unfinished`，不移除现有字段（`clipwright/schema/task.py:28-63`；`clipwright/services/agent_kernel.py:1312-1319`）。
4. 成功标准仍是领域产物：非 plan_only 任务必须有 `final_timeline`；plan_only 必须有 `reports/plan.json`；未处置降级仍不能伪装成功（`clipwright/services/agent_kernel.py:762-804,1120-1170,1180-1265`）。
5. 工具仍经 surface 的权限声明执行；收口模式只缩减可见工具面，不得绕过 capability、tier 或 dry_run 约束（`clipwright/services/agent_loop.py:143-152,433-439`；`AGENTS.md:68-85`）。工具名不可写死为能力映射；收口策略按工具自声明的 permission_tier / dry_run_capable 分类，不以硬编码业务工具名单作为安全边界。
6. task_end 是任务收敛事件；事件投影、SSE、taskStore 继续以该事件作为收束点（`clipwright/api/task.py:217-256`；`src/features/task/useTaskEvents.ts:92-106`）。
7. 时间线回写若发现 `last_writer=editor` 或 agent_sig 不匹配必须跳过；收尾不得覆盖用户已编辑内容（`clipwright/services/task_project_sync.py:190-213`）。

## 3. 数据模型变更

在 `clipwright/schema/task.py` 新增 enum 和模型；JSON 使用 snake_case，checkpoint 与 `to_public()` 同形。

```python
from enum import Enum

class TerminalReason(str, Enum):
    achieved = "achieved"
    capped = "capped"
    blocked = "blocked"
    user_cancelled = "user_cancelled"
    externally_interrupted = "externally_interrupted"
    dry_run_completed = "dry_run_completed"
    plan_only_completed = "plan_only_completed"

class GoalStatus(str, Enum):
    active = "active"
    achieved = "achieved"
    capped = "capped"
    blocked = "blocked"
    paused = "paused"

class Goal(BaseModel):
    id: str
    title: str
    completion_criteria: list[str] = Field(default_factory=list)
    max_rounds: int = 400
    rounds_used: int = 0
    status: GoalStatus = GoalStatus.active
    blocked_reason: str = ""
    blocker_fingerprint: str = ""
    blocker_streak: int = 0
    blocked_after_consecutive_rounds: int = 3
    closeout_required: bool = True
```

Task 新增字段：`goal: Goal | None = None`（旧 checkpoint 可缺省）；`terminal_reason: TerminalReason | None = None`；`blocker_code: str = ""`（仅当 Goal=blocked 时记录 `no_progress|repeat_fail|model_error|missing_artifact` 等根因）；`closeout: dict[str, Any] = Field(default_factory=dict)`，保存 `mode`（normal/soft_landing）、`trigger_round`、`deliverable_status`。`TaskFinish.reason` 字符串不删，新增 `terminal_reason: TerminalReason | None` 镜像字段；推荐逐步以后者为主。

JSON 契约示例：

```json
{
  "goal": {"id":"goal_task_x","title":"在收口模式内完成任务并交付收尾报告","completion_criteria":["final_timeline 存在，或 plan_only 的 reports/plan.json 存在","完成项与未完成项均已写入 finish"],"max_rounds":400,"rounds_used":398,"status":"capped","blocked_reason":"","blocker_fingerprint":"","blocker_streak":0,"blocked_after_consecutive_rounds":3,"closeout_required":true},
  "terminal_reason":"capped",
  "closeout":{"mode":"soft_landing","trigger_round":360,"deliverable_status":"delivered"}
}
```

纯新增策略：Pydantic 默认值使旧 checkpoint 可解析；序列化有新字段。所有新 enum 必须从首发版本就稳定，不以动态字符串填充。`to_public()` 暴露上述字段；旧客户端缺字段时按 `terminal_reason ?? finish.terminal_reason ?? mapLegacy(finish.reason)` 回退。

## 4. 事件契约

**新增 `goal_update` 事件**，由 Kernel 的 Goal 状态迁移点发出，detail 固定：`goal_id:string`、`status:GoalStatus`、`rounds_used:int`、`max_rounds:int`、`blocker_streak:int`、`blocked_reason:string`（active 时空串）、`terminal_reason:TerminalReason|null`。前端事件流显示“目标进度/收尾模式/目标结束”，结构明细可展开；缺字段显示 `—`，未知 status 原样显示且不据此改 Task.status。

**新增 `soft_landing` 事件**，在进入收口模式时由 Kernel 发一次，字段：`trigger:string`（`round_threshold|structural_cap|tool_call_cap`）、`rounds_used:int`、`max_rounds:int`、`tools_policy:"closeout_only"`、`goal_id:string`。前端折叠事件行显示“进入收口模式”，展开显示阈值和工具面限制。重复重入必须幂等，仅发一次。

**变更 `task_end` detail**：保留 `reason`（兼容历史字符串），新增 `terminal_reason`（枚举字符串）、`goal_status`、`goal_id`、`closeout_mode`、`completed`、`unfinished`。`reason` 对旧消费者继续供文案使用；新前端状态只读 `terminal_reason`，缺失时才沿用旧前缀映射。自然成功 reason：`tool_loop_completed` 保留，terminal_reason=`achieved`；软着陆完成 terminal_reason=`capped`；因此 capped 是“预算封顶但已交付”，不是失败。

`gate_hit` 保留作为过程/拦截事实事件，新增 `terminal_reason`、`threshold`、`observed`、`will_soft_land`；达到结构上限时先发 `will_soft_land=true`，不是立刻 task_end。未知事件的旧 UI 行为是按字符串标签显示，store 继续忽略其业务字段，不崩溃（`src/features/task/EventFeed.tsx:47-55,101-110`；`src/features/task/useTaskEvents.ts:20-38`）。

## 5. 终止判定

唯一最终判定函数是 `AgentKernel._gate_hit()`，它返回 `GateDecision`（continue / soft_landing / terminal + reason enum + evidence），不直接在多个调用点设置终态。`_control_hit()` 只采集 pause/cancel 信号；每轮结束、每次工具执行前、异常路径与恢复路径统一把信号交给判定器。状态迁移及 finish/task_end 仅由 `_finish()` 完成。现有工具前 guard 顺序在 `agent_loop.py:323-337`，改为执行前只检查外部取消与工具面状态，预算/完成判断不应在工具已经被选出后才成为非结构化错误。

优先级固定如下（用户规定的五类顺序保留；外部干预最后仅指在终态决策前被接受的非取消外部中断，用户主动取消在实际协程取消处理时具备强制优先权）：

1. **完成标准**：已确认目标产物、quality 收尾与降级收据均满足，Goal→achieved，terminal_reason=`achieved`（plan_only=`plan_only_completed`）。Goal 完成是“无需再干”的证据，不能被剩余预算覆盖。
2. **无进展**：连续 6 次检查指纹未变，先进入软着陆；收口内仍 6 次无新增证据则终止 `no_progress`，Goal→blocked，写明最后 blocker。
3. **熔断**：同工具+参数跨阶段累计失败达到 5 次，正常模式直接切收口；收口模式禁止再次调用该指纹，仍无法形成交付则 `repeat_fail` / Goal blocked。
4. **预算**：TaskLimits 非零上限仍作为显式用户/部署预算；金额预算到达立即收口（不得再调用付费工具），原因 `budget_exhausted`。结构性 max_steps 触发进入收口，收口完成记 `capped`；收口自己的最大轮次耗尽仍须写一次确定性摘要，若摘要成功交付记 `capped` 且 `deliverable_status=partial`，不是静默取消；预算是根因时记 `budget_exhausted`，Goal 保持 active/paused，不得偷映射为 capped。
5. **外部干预**：用户 cancel 是不可覆盖的硬中止 `user_cancelled`；pause→Goal paused、任务非终态。服务/进程/协程意外中断为 `externally_interrupted`，可续跑但不自动复活；未捕获 provider/内部错误为 model_error。

| 边界 | 判定 |
|---|---|
| TaskLimits 0 | 不限制，不参与触发；仅 kernel max_steps 结构护栏仍有效。 |
| `max_steps=400`、round=359 | 达 90%（360 前一轮后）预告；在 round=360 后下一次模型调用前进入软着陆。 |
| round 恰等 400 | 禁止再进入普通工具回合；强制收口模式，不得先执行第 401 回合工具。 |
| `max_tool_calls=4000` | 第 4000 次工具结果回填后立即进入软着陆；第 4001 次不执行。 |
| 极大值 | Python 整数不溢出；`max_steps` 配置上限 10000，超过启动校验拒绝，避免误设成无限循环。 |
| 无进展恰等 6 | 第 6 次检查进入软着陆；收口期间的进展按独立签名计数，写 finish 报告视为完成交付，但不伪造原任务达成。 |
| 重复失败恰等 5 | 进入收口，不允许第 6 次同参数失败调用。 |
| 连续 blocker 2/3 | 2 轮不得宣告 blocked；第 3 轮同一 fingerprint 且写了非空证据才允许 Goal blocked。 |

取舍：完成标准优先于预算，避免已产出 final_timeline 仍被硬阈值报失败；但金额预算是不可突破的付费安全边界，因此入收口后付费工具面关闭，收尾写作不得再产生可计费工具调用。用户取消优先于所有“必须交付”目标，不能用 Goal 强行继续用户明确停止的任务。

## 6. 软着陆设计

**阈值与上限**：第一版 max_steps=400 rounds，取现有 `STRUCTURAL_MAX_ROUNDS=400`，避免骤然缩短可运行量；在 90% 即 round 360 进入软着陆。收口模式另限 8 次模型 round、128 个纯收尾工具调用、最长 600 秒。当前结构上限仍为 4000 工具调用/单请求 24 工具轮次（`clipwright/services/agent_loop.py:57-62`）。收口 8 轮沿用 Claude 连续续跑最多 8 次的防循环依据（调研 `docs/agent-termination-research-2026-11-02.md:51-60`）；不能使用无上限软着陆。

**注入提示词原文**（系统提示词后追加，作为收口模式唯一权威规则）：

> CRITICAL — 已进入主 Agent 收口模式。普通执行预算已到达收口阈值；这不是停止交付的理由。你必须在本模式内完成最终交付：先核验已有产物与质量阶段结果，再写出结论、已完成项、未完成项及其可核对原因。不得声称未验证的工作已完成。工具面已经关闭生成、付费、写时间线主体、派发子 Agent、重跑阶段及其他会扩展任务范围的能力；不得尝试绕过限制或要求用户恢复工具。只允许读取已有报告/产物、核验 final_timeline 或 plan_only 规划书是否存在、写入收尾报告/产物索引、调用既有非破坏性 final_timeline 同步入口，以及输出最终文本。若原完成标准未达成，明确列出 blocker、证据与下一步所需输入；即使达到 max_steps，也必须交付这份收尾报告。完成后立即结束，不得继续探索。

**收工具面**：通过 `tool.surface` 构建受限 schema 集，而不是只在 prompt 里要求模型不调用。允许权限元数据为 `read` 的工具；允许 `write_local` 且 capability 明确标记为 `closeout_report` 的报告写入；允许内部只读产物读取与既有 timeline sync（仅当同步无覆盖用户编辑风险）；其余 `costly`、`dangerous`、普通 `write_local`、delegate/research 派发全部不暴露。插件缺少 tier/dry_run 元数据时 fail closed。dry-run 不等价于收口，不能拿 dry_run_capable 作为开放写操作的理由。具体能力白名单通过工具自声明 `closeout_safe=true` 扩展，禁止在 agent_kernel 写死工具名，符合能力由插件提供的不变量（`AGENTS.md:68-85`）。

**允许动作**：读已有 artifacts/reports，验证 `final_timeline`/`reports/plan.json`，写唯一 `reports/agent-closeout.json`（原子替换，含 task_id、goal_id、terminal_reason、完成/未完成清单、证据引用），生成自然语言 finish。不得通过 `delegate_agent` 触发 quality 或任何阶段；如果 quality 尚未执行，明确记录 `quality_not_run`，不得把质量阶段偷塞到收口。正常模式中原有 quality Agent 仍是阶段流程的一部分，且 final_timeline / 降级收据门槛不变。

**失败回退**：收口模型请求异常或达到 8 轮时，Kernel 从现有 task state、黑板与事件构造确定性报告：标明已观察到的产物键、缺少的完成标准、最后失败证据，落 `reports/agent-closeout.json`；文件写失败仍将摘要放入 `TaskFinish` 与 `task_end`。Goal→capped，terminal_reason=`capped`，`deliverable_status=partial`。若用户取消则不做额外收尾，保留 interrupted 文本与 user_stopped 记录，原因改为 user_cancelled。

**quality 与 final_timeline**：soft landing 不要求跳过领域完成标准，也不伪造 quality 通过。收口触发时若 quality 已运行，摘要引用其报告；未运行则写明缺失。`final_timeline` 已存在时允许在完成收尾前调用一次原有项目同步，但受 `last_writer`/`agent_sig` 守卫；失败只记项目同步 warning，不改变任务终态（`clipwright/services/task_project_sync.py:190-253`）。若时间线尚未生成，不派 edit/audio/quality，交付 blocker 摘要并标 capped。

取舍：采用 400 而非小预算是保留当前真实执行量，同时收口预留 40 轮（90% 提前切换）；不选更高如 1000，因为 8 轮 soft closeout 已足够呈现状态，增加探索阶段只会扩大成本和重复调用风险。4000 工具调用护栏仍然独立存在；该护栏可导致较早收口，不允许额外再跑满一个 8 轮普通窗口。

## 7. Goal 语义

Goal 创建于 `AgentKernel.create()`，缺省目标依据 `plan_only` 选择：普通任务 title=`在 max_steps_prompt 规范内完成创作并交付收尾报告`；完成标准包含领域产物与已完成/未完成报告；plan_only 标准为 `reports/plan.json`。用户本轮 instruction 追加为目标约束，不得覆盖系统交付目标。max_rounds 缺省 400；第一版全局可配置范围 1..10000。

生命周期与转换：

```text
active ──完成标准满足──> achieved
active ──结构上限/进入收口并完成收尾──> capped
active ──同一 blocker 连续 3 轮且有理由──> blocked
active ──pause──> paused ──显式 resume──> active
active/paused ──用户 cancel──> user_cancelled 终态（Goal 状态保留 paused/active，不新增冲突的 Goal 终态）
capped/blocked/achieved ──终态，不允许 task resume；续跑通过 requirements 新建任务并链接 previous_task_id
```

**一致映射**：Goal achieved ↔ `terminal_reason=achieved`（plan_only 可用专用细分 `plan_only_completed`，Goal 仍 achieved）；Goal capped ↔ `capped`；Goal blocked ↔ `blocked`。三者严格一一对应；`no_progress`、`repeat_fail` 等根因放在 `blocker_code`，不作为并行终止原因。其他终止源（user_cancelled、budget_exhausted、model_error 等）不宣称 Goal 已决议，Goal 保留 active/paused 快照，Task 的 `terminal_reason` 编码本次停止源。Goal 状态不得依赖 status 字符串反推。

**blocked 防误判**：每轮模型结束时，Kernel 计算 blocker fingerprint：`sha256(category + normalized_blocking_point + sorted(missing_artifact_refs))`。相同条件连续 3 轮才满足门槛；每轮必须有结构化 `failure.category` 或明确缺失标准，并保存 `blocked_reason`（20–500 字，包含阻塞事实、已尝试动作、所需解除条件；仅“困难/不确定/还没完成”拒绝）。fingerprint 改变则 streak=1。仅模型说“做不到”不构成 blocker，判断由 Kernel 结合工具证据与既有 `failure` 字段；不得引入写死领域判断规则（`clipwright/services/agent_loop.py:582-639`）。

**收口耦合**：目标不是在 max_steps 处取消任务，而是规定“在 MAX_STEPS_PROMPT 规范之内把任务真正做完”。结构触顶将 Goal 从 active 转入 capped_pending（内部执行态，可不公开枚举）后进入软着陆；只有收尾报告已写或确定性 fallback 已产出后，Goal 才变 capped。因而 capped 仍须交付。预算/错误触发收口也是同一收尾流程，但终止原因仍保留根因，不把所有收尾都混成 capped。

**stop_task 与续跑**：stop_task 继续调用 `kernel.cancel()`，设 Goal 快照为 paused/active 的取消前状态、任务 terminal_reason=user_cancelled；用户之后发新指令时 `_continue_as_task()` 建新 Task、复制 Goal 的 completion criteria 和历史轮次、继承黑板，并将上一轮 terminal_reason/blocked_reason 注入上下文；新 Goal id 独立，previous_goal_id 指回原 Goal。终态 Task API `/resume` 仍 409（`clipwright/api/task.py:259-305`），续跑只走 requirements 新 Task 通路（`requirements_service.py:3180-3335`）。

跨轮自治 continuation（goal driver）不在 M1 自动实现。若后续引入，取消/服务重启不自动唤醒 Goal；需显式用户 resume。最大轮数计入跨 Task 累计，不能每个 continuation 新建后重置 400。

## 8. 终止原因编码化

枚举值、产生点与映射：

| terminal_reason | 产生函数/条件 | Task.status | Goal |
|---|---|---|---|
| `achieved` | `_finish`：目标标准与领域收尾守门通过 | done | achieved |
| `plan_only_completed` | `_finish_plan_only` 产物落盘通过 | done | achieved |
| `capped` | `_finish`：结构上限触发收口，收尾报告已交付或由确定性 fallback 产出 | done（有收尾报告，可能为 partial） | capped |
| `blocked` | `_gate_hit`/Goal 判定：同 blocker 3 轮且 reason 有效；`blocker_code` 记录 no_progress/repeat_fail/degradation_unresolved/budget_exhausted/model_error/invalid_completion 等根因 | failed | blocked |
| `user_cancelled` | `cancel()` 与 `stop_task()` | cancelled | 保留取消前 Goal 快照 |
| `externally_interrupted` | checkpoint 恢复发现运行任务无协程、服务重启中断 | failed（续跑由新 Task） | paused |
| `model_error` | `_run_loop` provider/tool-loop 异常无法恢复 | failed | active/paused |
| `invalid_completion` | 模型声称完成但缺 final_timeline/plan artifact，且收尾恢复耗尽 | failed | active |
| `dry_run_completed` | dry_run 预览完成 | done | achieved（目标限定为预览） |

持久化：Task checkpoint 是权威落点；`TaskFinish.terminal_reason` 是终态快照；task_end.detail.terminal_reason 是事件投影；requirements session message/timeline metadata 保存 `task_id`、`terminal_reason`、`goal_id`，但会话 status 仍只由 Task.status 驱动。项目时间线 metadata 不写 task terminal_reason，避免污染编辑器产物且 `last_writer` 专注于内容写入语义（`task_project_sync.py:101-128`）。

API：`GET /api/task/{id}` 的 `task` 对象必有 nullable terminal_reason，终态任务非空；`GET /events` 与 SSE task_end 使用上述 detail。`POST /requirements/{session_id}/stop` 返回体新增 terminal_reason，保留 stopped/task_id/status/note。`requirements_service._release_session_after_task()` 在消息 metadata 及时间线 content meta 写统一原因代码。

结构化日志字段固定：`task_id`、`goal_id`、`terminal_reason`、`goal_status`、`closeout_mode`、`rounds_used`、`max_steps`、`tool_calls`、`blocker_fingerprint`。不得仅写中文原因字符串。前端 `terminalStatusOf()` 首读 enum；新枚举映射由 `labels.ts` 一处维护；未知枚举映射 `unknown_terminal` 并原样显示，不伪装 failed（`src/stores/taskStore.ts:91-109`）。

## 9. 改动清单

| 文件 / 函数 | 改什么 | 兼容性与验收点 |
|---|---|---|
| `clipwright/schema/task.py`：`TerminalReason`、`Goal`、`TaskFinish`、`Task`、`to_public()` | 增加可选字段和默认值，TaskFinish.reason 保留 | 老 checkpoint 可解析；新 task REST 与 checkpoint 字段一致。 |
| `clipwright/services/agent_kernel.py`：`create`、`_gate_hit`、`_on_tool_call`、`_finish`、`cancel/pause/resume`、`_load_checkpoint` | GateDecision 单点、Goal 记账/迁移、soft landing 工具面、统一终态写入；状态清理不能漏 Goal | 任一终态 task_end 恰一条且含 enum；cancel 仍真打断；软着陆工具拦截不执行副作用。 |
| `clipwright/services/agent_loop.py`：`run_loop`、工具构建、系统/用户 prompt | 400/4000/24 保持结构护栏；接受 closeout mode 与剩余 rounds；原文提示；收口工具 schema 过滤 | 工具执行前拒绝不可见工具；round 400 不再发普通工具调用；normal prompt 不变。 |
| `clipwright/tool/surface.py` 与工具契约声明 | 增加 `closeout_safe`/`closeout_report` 元数据并在 schema 构建时筛选 | 缺元数据的插件在 closeout 默认不可用；不更改正常权限档位与 capability 解析。 |
| `clipwright/services/requirements_service.py`：`_continue_as_task`、`stop_task`、`_release_session_after_task`、`watch_task_session`、`_persist` | 续跑链接前一 Goal 和终止原因；stop 返回机器原因；会话消息与 timeline metadata 同码 | status 映射不改；user_stopped 仍只由 stop_task 写；timeline 不重复不覆盖。 |
| `clipwright/services/task_project_sync.py`：`sync_timeline_to_project` | 确保只允许被明确标记为 closeout-safe 的最终同步；保持增量 writer/fingerprint 守卫 | editor 最后写入时不覆盖；回写失败不改变终态。 |
| `clipwright/api/task.py`：`get_task`、事件路由、`cancel/resume`、后台异常 | 保证 error/cancel 路径也通过 Kernel finalize；响应直接含 Task 新字段 | 旧字段照旧；未知 reason 不导致 500；终态 resume 仍 409。 |
| `clipwright/api/requirements.py`：stop endpoint | 返回 terminal_reason 和既有字段 | 老前端只读 note/status 仍可用。 |
| `src/types/task.ts` | TerminalReason、Goal、Task/Finish/TaskEvent 字段，字段可选以兼容事件历史 | 未收到新增字段按旧契约读。 |
| `src/stores/taskStore.ts`：`terminalStatusOf`、`applyEvent` | terminal_reason 优先映射，Goal 更新仅合并快照；task_end 仍保留缺省 finish | 旧事件仍可映射；未知 enum => unknown_terminal。 |
| `src/features/task/labels.ts`、`TaskFinishSummary.tsx`、`EventFeed.tsx`、`TaskToolbar.tsx` | 新原因标签、Goal/收口摘要、soft_landing 与 goal_update 展示；终态按钮不提供 resume | `capped` 明确显示“已封顶，已交付收尾报告”，`blocked` 显示 blocker_reason；老任务仍显示旧 reason。 |
| `src/features/agent/AgentPanel.tsx` | Stop 结果处理新字段但不把 AbortController.abort 当后端取消成功；仍以服务端 session 状态为准 | 快速停止、重复停止、无活任务的 stopped=false 均正确显示。 |

验收基准不含自动测试命令；用户明确由人工完成测试/验证。本 PRD 只定义接口和人工验收，不要求执行 pytest/vitest/tsc/py_compile。

## 10. 兼容与迁移

- **存量任务**：旧 checkpoint 无 goal/terminal_reason，Pydantic defaults 解析为 `goal=null`、`terminal_reason=null`。读取终态任务时仅在响应层派生 legacy view，不回写虚构枚举；续跑新建 Task 时由需求指令建立新 Goal。
- **旧事件**：保留 `task_end.detail.reason` 字符串与 completed/unfinished；旧 `gate_hit` 不要求新字段。新客户端兼容旧事件映射；旧客户端忽略新增字段和类型。
- **旧前端**：API Task 新字段只增不删；新事件未知类型按既有字符串 label 显示。不要把 Goal 状态新增到 TaskStatus union，避免旧组件 toolbar 状态机误判。
- **checkpoint 重启**：缺省字段安全；新 checkpoint enum 只写字符串值。损坏/未知枚举不得使 `_load_checkpoint()` 整个任务消失：读取时保留原始值为 `legacy_terminal_reason` 诊断并返回 unknown_terminal view；不得将未知原因转成 achieved。
- **续跑任务**：当前 160/800 的 limits 是旧调用方显式设置；M1 保留其 JSON 和实际行为直到新的 `goal.max_rounds` 生效，M2 起改为 max_steps=400 + closeout，显式用户预算单独保留。迁移时不可把 160/800 当 TaskLimits 缺省值。
- **事件消费与会话投影**：task_end 仍单事件最终收束；`task_end.detail.completed/unfinished` 不能缺省成空数组覆盖前端已有信息（`AGENTS.md` #17；`taskStore.ts:346-365`）。
- **timeline**：终止信息写 requirements timeline metadata，不写项目 timeline metadata；不得改写 `last_writer`/`agent_sig` 或把 terminal event 当成内容 writer。

## 11. 人工验收步骤

1. 创建普通创作任务，确认 Task REST 中 `goal.status=active`、max_steps=400，并确认计划/用户需求仍按既有路径装载。
2. 在 359、360、399、400 round 边界观察事件：360 进入软着陆；400 后不得有普通工具调用；事件 detail 含真实阈值。
3. 软着陆期间尝试读已有报告、生成付费工具、阶段派发与写任意项目文件：只读及专用报告写入成功，其余工具不可见或拒绝且无副作用；最终得到收尾报告与 completed/unfinished。
4. 以已有 `final_timeline` 触发完成：Goal achieved 与 terminal_reason achieved 一致，task_end 只有一条；确认时间线写回项目，metadata 的 last_writer/agent_sig 正常。
5. 模拟缺 final_timeline、quality 未跑、降级收据未处置：不得宣称 achieved；报告指出缺项；quality/降级原守门不被软着陸绕过。
6. 同一工具+参数连续失败 4 次不触发熔断，第 5 次切收口；收口时同动作不可再次执行。无进展连续 5/6 次边界同样检查。
7. 造成同一 blocker 连续 2 轮与 3 轮：第 2 轮仍不得 blocked；第 3 轮必须持久化具体 blocked_reason 与 fingerprint。
8. 在运行中点需求页停止：协程真取消，返回 stopped=true、terminal_reason=user_cancelled；messages/timeline 各有一条 user_stopped；随后发新指令建新 Task，Goal 指向 previous_goal_id 且黑板产物继承。
9. 无活任务时再次点停止：stopped=false，不新增 user_stopped 内容；会话状态只按现有契约释放。
10. 刷新页面与重启后读取 checkpoint：Goal、rounds_used、terminal_reason 恢复；旧任务旧事件仍可看，未知 enum 显示待适配且不显示“成功”。
11. 验证 API 客户端：`GET /api/task/{id}`、events、SSE task_end 和 requirements stop 的同一任务原因完全一致；status 映射仍由后端终态驱动。

## 12. 风险与回滚

| 风险 | 真实成因 | 回滚 |
|---|---|---|
| 软着陆切工具面导致报告写入也被误禁 | 工具面 capability 插件化，write_local 工具并不全是报告工具（`AGENTS.md:68-85`） | 配置 `agent_soft_landing_enabled=false` 回退到现有结构性终止；保留新增字段和 task_end reason。 |
| 新枚举与旧自由字符串双真相 | 当前前端依赖 `finish.reason` 字符串前缀映射（`taskStore.ts:91-109`） | 前端先退回 legacy mapper；后端继续产出 terminal_reason，不删字段；不可删除 `reason`。 |
| watcher / stop_task 并发写状态及重复消息 | watcher 与 stop_task 是并行会话写入者（`requirements_service.py:3363-3465,3494-3534`） | 用 task_id + terminal event 幂等键；若冲突回退只读 checkpoint reason 重投影，保留用户停止标记。 |
| soft landing 期间同步覆盖用户时间线编辑 | 现有 writer 守卫依赖 last_writer/agent_sig（`task_project_sync.py:190-213`） | 关闭收口同步，只允许用户手动打开结果；不回滚已标记 editor 的项目时间线。 |
| Goal 达成与 Task ended 不一致 | 成功标准与 `final_timeline` / quality / degradation ledger 分开（`agent_kernel.py:1127-1269`） | Feature flag 关闭 Goal 驱动，只留 telemetry 和 terminal_reason；领域 gate 继续以原行为运行。 |

全局回滚开关仅禁用 Goal 续跑/软着陆策略，不移除 schema 字段、API 字段或已有 `task_end` 字段，避免新旧 checkpoint 与客户端互不兼容。

## 13. 分期

**M1：原因结构化与 Goal 数据落盘（可独立发布/回滚）**

新增 enum/model/checkpoint/API/事件字段；旧 `_gate_hit` 判定和工具面不改，前端先读 terminal_reason、缺字段再走 legacy reason。Goal 初始化并记录状态，但不影响执行。验收门槛：旧 checkpoint 可读；每条新终态 API/event/checkpoint 同码；旧前端字段仍齐。回滚：关闭 `terminal_reason_v1` 写入开关，字段保留 nullable。

**M2：单点判定与 90% 软着陆（可独立发布/回滚）**

`_gate_hit` 产出 GateDecision；接入 Goal 轮次、360/400 与 4000 工具上限、受限工具面、8 轮/600 秒收尾 fallback。当前显式 160/800 仅作为 task limits 保留，不再决定结构性 soft landing。验收门槛：触阈无第 N+1 次普通工具副作用；收尾报告必有；budget、cancel、pause 原有行为正确。回滚：`agent_soft_landing_enabled=false` 后调用旧 gate 路径。

**M3：Goal blocker、requirements 续跑与 UI 收口（可独立发布/回滚）**

引入连续 3 轮 blocked、previous_goal_id、原因注入上下文、会话 timeline metadata、前端 Goal/terminal reason 摘要和新事件呈现。验收门槛：stop 真中止、无活任务不伪造记录、续跑不丢黑板；capped 仍交付；前端未知值不误判成功。回滚：关闭 `goal_blocker_v1`，继续用 terminal_reason 与旧 session status 映射。

## 14. 开放问题

1. 90% 的软着陆阈值是否保持全局固定，还是允许项目级覆盖？本 PRD 首发定 90%、400 rounds，覆盖上限为 10000。
2. 预算达到后是否允许继续执行任何可能产生费用的质量核验？本 PRD 首发规定全部关闭，仅复用已有 quality 报告。
3. `capped` 的 Task.status 是否统一按 done（有收尾交付）计入成功率，还是新增独立 terminal TaskStatus？本 PRD 首发采用 done，并以 terminal_reason 区分“目标完成”与“封顶交付”。
4. 用户 cancel 的 TaskFinish 是否保留部分已完成/未完成项目清单，还是严格维持当前 `completed=[]/unfinished=["cancel"]`？本 PRD 首发建议实现时保留 checkpoint 中已有证据，但停止不追加模型收尾调用。
5. task_end 中旧 `detail.reason` 后续何时停止生产？本 PRD 要求至少保留两个客户端大版本周期，具体下线版本需产品负责人确认。

## 15. 作者最终裁定记录（2026-11-02）

> 本节是对 §14 开放问题及新增质量预算需求的最终裁定。与正文冲突的原条款以本节新口径为准；正文原文保留，不做删除或改写。

| 裁定 | 覆盖 PRD 哪一节哪一句 | 新口径 |
|---|---|---|
| 1：软着陆轮次=600，支持项目级覆盖 | §1「第一版固定 400 rounds」；§5 边界表「max_steps=400」与「round 恰等 400」；§6「max_steps=400」「90% 即 round 360」；§11 步骤 1/2；§13 M2「360/400」；§14 问题 1「90%、400 rounds」 | **口径 A**：结构性 `max_steps=600`，普通轮次跑满 600 即进入收口；收口窗口独立为 8 轮、128 次收尾工具调用、600 秒；`STRUCTURAL_MAX_CALLS=4000`、单请求 24 工具轮次不变，命中任一结构上限均进入收口而非直接终止。解析顺序为项目配置 > 全局 settings > 默认 600；只对之后创建的任务生效。 |
| 2：质量 agent 预算 escrow | §5 第 4 项「金额预算到达立即收口」；§6「收口不得派 quality」；§6.5「quality 未执行则记录 quality_not_run」 | `budget_usd` 非空时任务创建冻结质量预留，默认 20% 且有 0.05 USD 最低额、2.00 USD 上限；主 agent 金额闸只使用扣除预留后的额度，quality 派发时从冻结额结算，主 agent 与收口不得动用预留。`budget_usd=null` 不冻结虚构额度；轮次/工具调用不预留。预留、主 agent 使用、quality 使用、释放余额必须在 TaskFinish、task_end 和日志可见。 |
| 3：capped 不算 done、不计成功率 | §4 task_end「软着陆完成 terminal_reason=capped；因此 capped 是预算封顶但已交付」；§8 表「capped … Task.status=done」；§9 前端「capped 明确显示…」；§10「其它终态」；§11 步骤 3/10；§13 M3「capped 仍交付」；§14 问题 3「采用 done」 | 新增独立 `TaskStatus='capped'`。capped 是中性终态：不算 done、不算 failed、不计入成功率；会话按不变量 #14 映射为 `plan_ready`（只有 done 映射 `pipeline_done`）。界面文案建议：“已到达运行上限，已完成收尾交付（不等同于全部完成）”，不能被读成失败或完成。 |
| 4：保留完成/未完成清单，旧 reason 不停产 | §4「task_end.detail 继续包含 reason、completed、unfinished」；§8 `TaskFinish.reason` 兼容；§11 步骤 8；§14 问题 4「建议…」与问题 5「至少保留两个客户端大版本周期」 | `TaskFinish.completed/unfinished` 永久保留，capped 终态必须有内容，优先由收口报告产出，模型收口失败时由 Kernel 确定性 fallback 产出。`task_end.detail.reason` 暂不停用，不设下线计划；新代码只读 `terminal_reason`，旧 `reason` 与新字段并行生产。 |

明确冲突优先级：本节关于 600/项目覆盖、质量 escrow、capped 独立状态及成功率排除、completed/unfinished 保留和旧 reason 并行生产的规定，覆盖上述正文原条款；正文其它未被本表覆盖的约束继续有效。
