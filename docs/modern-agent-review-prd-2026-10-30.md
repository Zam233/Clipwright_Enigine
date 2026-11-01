# 现代 Agent 架构代码审查与 PRD

- 审查日期：2026-10-30
- 后端仓库：`J:\Clipwright`，分支 `feat/master-agent`
- 前端仓库：`J:\Clipweight-Client`
- 评审焦点：主 agent 与子 agent 的关系，对照 DeepSeek harness / opencode / Claude Code 的运行时语义
- 方法：按指定代码路径检查实现、契约、前端消费、任务检查点与回归测试；行号以本次工作树为准
- 状态：本文的审查结论为代码观察；PRD 设计、里程碑和验收为待实现内容

## 1. 结论

**部分达标，不是完整现代 agent 系统。**主 agent 已采用原生 tool-use loop；阶段子 agent 有统一 `AgentResult`、显式降级、工具记账、进度事件、报告/产物引用、取消句柄及前端运行卡片。这些已越过“仅在提示词里称作 agent”的阶段。

但当前父子关系主要是“主 agent 同步调用一个固定阶段执行器”，不是可寻址、可独立交互的子会话。事件流只有进程内有界热层，运行记录以进程内 dict 为主；工具轨迹在多个层次截断；部分真实工作（素材源搜索、MG 生成/渲染、插件或 Skill 内部调用）不经过统一 `call_tool` 记账。因此可见的事件数量不等于完整 transcript，也不能承诺进程重启后完整取回、追问、注入、恢复或回放。

降级守门能阻止“直接收尾不看降级”，但没有机器可验证的处置收据。模型口头说接受后，第二次收尾即可通过；最终 `TaskFinish.unfinished` 虽保留降级类型，任务仍会以 `done` 结束。该实现是提示词级守门与事实留存，不是强制闭环状态机。

### 12 维度评分

| 维度 | 评分 | 证据 | 判断 |
|---|---|---|---|
| 子 agent 生命周期 | 半达标 | `services/agent_run.py:216-303`；统一 `RunSpec`/`AgentResult`：`schema/run.py:28-90` | 有 run_id、状态、派发、取消、结果与幂等，但执行完成后生命周期对象主要在进程内，阶段与 research 的执行实现仍不同。 |
| 上下文隔离 | 半达标 | `services/agent_context.py:20-47`；`stage_subagent.py:421-430`；`schema/stage.py:40-45` | `ContextVar` 支持 asyncio task 上下文，阶段输入有显式契约；但没有每 run 独立会话历史/上下文仓库，部分输入仍允许黑板回退。 |
| 可观测性 transcript | 半达标 | `agents/base.py:222-263`；`schema/run.py:99-132`；`services/task_events.py:22-26,59-67` | 工具摘要和事件可见，但记录限 200、事件限 2000 且仅 Mongo 旁路；不等同无损 transcript。 |
| 流式进度 | 半达标 | `agent_context.py:59-76`；`animation_agent.py:361-402`；`material_agent.py:888-897` | 有逐段/汇总进度；进度对主 agent 只是共享 task event，并未形成 run 内部持续可订阅的增量输出/消息协议。 |
| 产物模型 | 达标 | `schema/run.py:67-78`；`stage_subagent.py:532-581,617-669` | 结构化 outputs、artifact 引用、报告路径、黑板合并已有较清晰模型；失败落盘时存在回退内联。 |
| 降级语义 | 半达标 | `agent_kernel.py:853-883`；`schema/run.py:74-78`；`tests/clipwright/test_degradation_to_master_agent.py:158-187` | 降级事实可上浮并触发一次续轮，但没有处置状态/证据字段与规则执行器，口头“接受”即可放行。 |
| 工具权限 | 半达标 | `tool/surface.py:29-41,575-700`；`services/agent_run.py:22-57` | 主 agent 工具面与 research 只读集分离；阶段 agent 依自身工具/注册表路径运行，插件内部及直接调用存在绕过统一账本的路径。 |
| 可中断恢复 | 半达标 | `agent_run.py:159-199,250-280`；`agent_kernel.py:1055-1120` | 有取消句柄、task checkpoint、黑板/阶段 sidecar；AgentRun 本身、事件流、run 内工具游标和 thinking transcript未持久化，重启后不能恢复正在执行的子会话。 |
| 成本归因 | 半达标 | `stage_subagent.py:143-161,471-480`；`schema/run.py:80-89`；`agent_run.py:325-343` | run 有 cost/tokens/provider/model 字段；阶段成本依赖进程级 usage 差值，并发阶段会互相污染差值，且成本未知无法参与金额闸精确约束。 |
| 错误自愈 | 半达标 | `agent_loop.py:368-425`；`agent_kernel.py:853-873`；`stage_subagent.py:442-469` | JSON 断裂一次纠错、阶段失败分类、降级再派有基础；但重试预算/策略、子 run 状态转换和恢复点没有统一的机器状态机。 |
| 人机协同 | 半达标 | `agent_kernel.py:534-577`；`agent_run.py:282-290`；`CopilotTaskRunTab.tsx:364-447` | task pause/resume/cancel/inject、风险审批和 run 卡片存在；人工注入落在 task hints，无法针对某个子 run 注入或追问。 |
| 前后端契约 | 半达标 | `schema/task.py:27-58`；前端 `eventSchema.ts:18-47`；`tests/test_event_contract.py` | 事件类型有跨仓集合测试；前端 run detail parser 忽略 tool_calls/degradations/完整轨迹字段，事件型 UI 不能解决后端持久化缺失。 |

## 2. 重点问题核查

### 暗区与工具轨迹

1. `BaseAgent.call_tool` 记账，但不是所有产生外部效果/成本的行为都会经过它。素材检索直接进入 `MaterialRegistry.search`：`agents/material_agent.py:452-473`；Audio 的 BGM 搜索也直接调用 Registry：`agents/audio_agent.py:28-62`；Animation 从素材库搜索图片：`agents/animation_agent.py:1242-1263`。这些不必然是原子工具调用，但属于昂贵、耗时且影响产出的动作，应有统一 operation/span 轨迹。
2. Animation 直接构造并调用 `MGGenerator.generate_for_type`：`agents/animation_agent.py:743-790`。MGGenerator 内部使用 `LLMService` 并发生成、修复/评估：`animation/mg/generator.py:19,174-189`；视觉评估走 `asyncio.to_thread`：`animation/mg/visual_eval.py:85-115,141-187`。Hyperframes 的生成/渲染也位于阶段内部调用链。当前可通过独立 MG trace、degradation 和进度事件观察一部分，但不进入 `BaseAgent.last_tool_calls`。
3. `BaseAgent.call_skill` 只执行技能，没有像 `call_tool` 一样构造 `last_tool_calls` 或 `subagent_tool` 事件：`agents/base.py:295-308`。Kernel surface 的 `_execute_skill` 也只将技能结果返回，技能内部 `tool_calls` 至多被截成 20 条：`tool/surface.py:535-545,804-807`。
4. 插件 material source 是通过 `MaterialRegistry.search`/代理调用，而非统一 `ToolRegistry.execute`。插件 RPC 入口见 `plugins/runtime/proxies.py:114-125`、`plugins/runtime/worker.py` 的 material_source 分支。若未显式埋点，插件源内部搜索与调用耗时无法进入 AgentResult 工具轨迹。

### contextvars 与线程

`task_scope` 使用 `ContextVar` 并在 `stage_subagent` 中包住 Agent 的 async 执行：`agent_context.py:21-47`、`stage_subagent.py:421-429`。普通 asyncio 子任务通常继承创建时 context，因此 `asyncio.gather` 下的 animation/material task scope 仍可用。但 `asyncio.to_thread` 会复制当前 context 的快照，不保证在线程中改变的预算/上下文值回写到父任务；`run_in_executor` 默认也不应假设自动传播。`agent_context.take_event_budget` 是读-改-写 ContextVar，线程并发/跨线程时也不是共享原子预算。现有 LLM provider 使用 `asyncio.to_thread`（`services/llm.py:487-492`），但其调用回调多在协程恢复后触发；后续若把工具执行移入线程或在 worker 内直接发事件，归属可能丢失。设计上应显式传 `RunContext`/`trace context`，并用线程安全共享额度，而非把 ContextVar 当跨线程账本。

### 2000 事件上限与指定 task

`task_events.emit` 每 task 仅保留最近 2000 条并删除最早记录：`services/task_events.py:22-26,59-67`；SSE 从内存 `events_since` 取数：`api/task.py:155-184`。Mongo 写入是 fire-and-forget 旁路，事件接口没有按 seq 从 Mongo 回读：`task_events.py:143-171`。所以若事件数超过 2000，游标继续递增但历史中间段消失；重连请求旧 `since_seq` 只会拿到剩余尾部，客户端没有 gap/reset 协议。每阶段工具事件预算 400：`agent_context.py:24-26,37-47`，而 BaseAgent 自身轨迹另限 200：`agents/base.py:239-246`。

指定检查点 `_cache/tasks/task_dc670b2f8d.json` 显示 task `done`、`tool_calls=59`、`turns=63`、墙钟 3221.16 秒、`unknown_cost_calls=6`；sidecar 共 13 次阶段记录，其中 material 的 `StageResult.tool_calls` 正好 200 条，animation run 有 12 项降级，且有 29 个动画任务。检查点只保存 task、黑板和阶段列表，不保存 task_events（`agent_kernel.py:1055-1120`），因此本地证据可验证被保留的轨迹，不代表完整 event transcript 可跨重启取回。用户提供的事件计数 `subagent_tool 60 / subagent_progress 58` 与代码预算相容，但本审查无法从上述 sidecar 单独重建该事件流全量；应把它视为用户指定的运行观察，不夸大为已从持久化库独立复算。

### get_agent_run、thinking、可寻址子会话

- `get_agent_run` 的描述称返回 AgentResult 全量：`tool/surface.py:89-100`；其实现调用 `agent_run.inspect`，从 `_RUNS` 返回 `model_dump`：`surface.py:615-631`、`agent_run.py:102-105,136-156`。对仍在内存且已填充的 AgentResult，可返回完整字段；但阶段 `StageResult.tool_calls` 本身最多来自 `last_tool_calls` 最近 200 条（`stage_subagent.py:471-480`、`agents/base.py:239-246`），research worker 的轨迹受其 adapter 返回值所限；run 从 `_RUNS` 淘汰（最多 400）后，只能部分回落 stage 表或 research tracker。故端到端“全部真实调用、无限期可取回”不成立。
- 主 agent `thinking` 由 `agent_loop` 的 provider 回调产生：`agent_loop.py:310-322,342-350`。阶段 `StageResult`/`AgentResult` 没有 reasoning 字段，`stage_subagent.run_stage` 未向阶段 Agent 的 LLM 调用接入 `on_reasoning`，父 agent 得到的是工具调用概要而非子 agent thinking。无需暴露隐藏推理文本；产品需求应定义为可审计的 progress/narration/decision summary，而非要求私有 chain-of-thought。
- 子 run 当前有 run_id、get/list/cancel，前端按 run_id 从事件折叠出状态、成本、报告路径：`CopilotSubagentTab.tsx:38-64,98-157`；但 run 是同步 `await handle` 的派发，调用方持有结果后才继续，run API 只有查询/取消，无 run-scoped message/inject、resume、replay 或持续订阅端点。它是“可寻址的运行记录”，还不是可单独交互/恢复的会话。

### 降级是否能被口头接受绕过

可以。kernel 收到降级后，在第一次 `status=ok` 收尾时强制增加一次续轮：`agent_kernel.py:853-873`。随后只要第二轮 outcome 仍为 `ok`，代码不解析模型结论是否包含接受的 kind、影响、证据或实际动作；直接将 pending degradation 写进 `unfinished` 后，因 `gate_reason == tool_loop_completed` 仍把 Task 设为 `done`：`agent_kernel.py:874-900`。单测也明确验证“第二遍才允许收尾”：`tests/clipwright/test_degradation_to_master_agent.py:158-187`。因此守门阻止沉默收尾，但不是阻止口头接受，也不强制接受时将任务标成带审阅的部分成功。

## 3. 差距清单

| 级别 | 现象与证据 | 影响 | 修法 | 验收标准 |
|---|---|---|---|---|
| P0 | 子 run 完成但 transcript 不持久；`agent_run._RUNS` 仅进程内且 400 淘汰（`agent_run.py:60-74,91-116`），事件流 2000 截断（`task_events.py:22-26,59-67`），run state 未列入 task sidecar（`agent_kernel.py:1076-1092`） | 重启/高负载后无法证明 agent 做过什么；追问、回放、审计建立在不完整数据上 | 建立 append-only RunEvent/ToolCall store，持久化 run spec、状态迁移、工具调用、进度与 artifact 引用；事件 API 支持游标 gap 提示和持久化回读 | 运行 5000 次 run/tool event 后按 run_id/seq 分页逐条校验无丢失；杀进程重启后 transcript hash 一致；游标落后时明确返回 gap/reset token，不静默跳过 |
| P0 | 降级门只有一次提示词续轮，接受路径不校验：`agent_kernel.py:857-885` | 模型用一句话即可让有实质影响的降级进入 done | 新增 `DegradationDecision` 收据：kind/scope/action/impact/evidence/replacement_run_id/approver；规则区分必须修复、可接受、需人工审核，kernel 对账每条 pending 降级 | 测试模型第二轮只输出“接受”时状态非 done；每条降级要么有验证通过的修复 run，要么有明确结构化接受和影响字段；P0 类降级不得由模型自行接受 |
| P1 | 工具轨迹在 agent 实例截到 200（`base.py:239-246`），事件预算每阶段 400（`agent_context.py:24-47`），插件/素材/MG 有直调（上述证据） | 长阶段中间调用/失败消失，失败原因与成本无法端到端归因 | 抽象 `OperationRecorder`：工具、skill、素材源、LLM、渲染器都以 operation/span 记账；摘要可限长，事实账本不得截断 | 43 场 material+animation E2E 中，registry 实际调用计数与持久化 operation 数相等；失败、耗时、成本未知可按 stage/run 查询 |
| P1 | run 无 run-scoped inject、resume、replay；只有 task-level inject 与 run cancel/query（`agent_kernel.py:564-577`、`agent_run.py:159-199`、`surface.py:601-631`） | 主 agent 能派发和取消，但无法像 harness 子会话一样追问/修正指定 worker | Run 状态机加入 message queue、pause/resume/cancel、独立 SSE、resume token 和可重放输入/结果；阶段型 Agent 先支持追加 brief 和重跑 attempt | 针对 run_id 注入消息只影响该 run；取消后不影响兄弟 run；服务重启后可恢复 paused run；重放同一输入生成同一事件序列前缀并标注 replay |
| P1 | `ContextVar` 承担 task/stage/event budget，上下文传播跨 executor 不可依赖；预算为 ContextVar 读改写（`agent_context.py:20-26,50-56`） | 新线程/插件回调中事件可能失去 task/run 归属，多个子任务可能各自复制事件预算 | 显式 `RunContext(task_id, run_id, stage, trace_id, event_sink)` 参数；ContextVar 仅作便利访问；共享预算采用锁/原子计数 | 用 asyncio task、`asyncio.to_thread`、`run_in_executor`、插件子进程四类测试；每条事件 run_id 正确；阶段总事件数严格不超过共享预算 |
| P1 | 阶段 reasoning 不回流；只有主 `agent_loop` 发 thinking，子 AgentResult 无 reasoning/progress transcript 字段 | UI 对主 agent 与子 agent 的可解释性不对称，无法追溯子 run 做出的决策摘要 | 不收集私有 chain-of-thought；统一输出可展示 `narration`、progress、decision summary、tool call/result | 前端 run tab 可按时间查看阶段 progress/narration/tool/result/degradation，字段通过 schema 校验；provider 无 reasoning 时 UI 明示未提供 |
| P2 | `AgentResult` 与 stage/research adapter 的持久化、取消、产物语义仍有双轨；`agent_run.py:306-310,373-427,430-495` | 不同 kind 在恢复、权限、回放上有差别，新增 agent 类型需扩写分支 | 把 kind adapter 降为实现插件，生命周期统一由 RunRuntime 持有；RunResult 统一 schema，领域产物放 typed artifact | 新增第三种 adapter 无需修改 RunRuntime 生命周期逻辑；stage/research 共用状态迁移及分页接口契约测试 |
| P2 | 前端 `AgentRunDetail` 未解析 tool_calls/degradations，RunDetail 也未展示完整轨迹：`eventSchema.ts:89-103`、`CopilotSubagentTab.tsx:25-36` | 即便工具结果含摘要，子 agent tab 仍只展示状态、成本、报告、失败与结论 | 增加 run transcript 面板，工具/进度/降级/产物分 tab 或时间线显示并分页加载 | 端到端 UI fixture 含 25 个调用、1 降级、2 工件；分页后全展示，错误和未知字段可展开查看 |
| P2 | 成本按进程全局 usage 差值估算：`stage_subagent.py:143-161,410-480`；`LLMService` 全局总计：`services/llm.py:74-86` | 并发 stage 的 token/cost 差值可互相计入，task 当前 tokens 还可能为 0 | LLM 请求直接返回 request-level usage/cost，带 run_id/call_id 聚合；未知成本单独标记 | 两个并发不同模型 run 的 mock usage 分别归因准确；task/run/operation 汇总求和一致，误差为 0 token |
| P3 | 缺少 transcript 丢失与运行时语义的可视化诊断（前端当前显示事件型进展） | 用户难以区分事件采样、持久化缺口、run 健康状态 | 增加 transcript completeness、first/last seq、gap_count、tool budget 使用率与恢复点标志 | 完整性状态在运行详情中可见；模拟截断/重连展示“缺失 seq 范围”，禁止显示为完整记录 |

## 4. PRD

### 4.1 背景与现状

Clipwright 的主 Kernel 已从计划表驱动切换为原生 tool-use loop；派发类工具进入 `tool/surface.py`，阶段执行复用固定的 `StructureAgent`、`MaterialAgent` 等；run 结果有统一 `AgentResult` schema。阶段会把工具摘要放进结果、通过 `subagent_tool` 发进 task event，并可报告动画/素材进度。产物可引用化，阶段报告写入工作区，降级会传回主 agent 并触发续轮。

当前阶段主要采用“一次性、同步、固定类型的子任务”。缺失项集中在：完整且 durable 的逐调用 transcript；对所有内部 operation 的统一记录；run-scoped 交互与恢复；强制可验证的降级决策；多线程/插件上下文归属；前端 run transcript 展示。目标不是复制某个产品外观，而是让父子 agent 的关系具备现代 harness 的可寻址性、可观测性和可恢复性。

### 4.2 目标与非目标

**目标**

- 每个子 agent 有稳定 `run_id`、`parent_run_id`、`task_id`、trace/span 与 attempt。
- run 从创建、执行、等待输入、暂停、取消、恢复到完成均有持久化状态转换。
- 每个实际 operation 有 append-only 记录；内容可分页获取，摘要只用于模型上下文，不替代账本。
- 主 agent 可查看、追问/注入、暂停、取消、恢复或重放指定 run，且不影响兄弟 run。
- 降级处置是机器可核对的结构化结果；未处置的关键降级不能标为普通 done。
- 前端以同一套 run event/parser 展示状态、工具轨迹、进度、产物、降级、错误与完整性。

**非目标**

- 本 PRD 不要求开放任意 agent-to-agent 网络或无界递归；仍采用有上限的深度/并发/预算策略。
- 不把私有 chain-of-thought 作为产品 transcript；展示 provider 明确提供的 reasoning 也需按产品策略处理，默认记录可审计摘要与行动理由。
- 不替换视频领域的确定性 stage 输入校验、时间线不变量、权限网关与 artifact 内容模型。
- 不要求一次性将所有插件迁移至新 SDK；通过兼容 adapter 分批接入。

### 4.3 差距到设计映射

| 差距 | 设计响应 |
|---|---|
| P0 transcript 易丢失 | durable RunStore + RunEvent append-only + seq gap 语义 + checkpoint transcript cursor |
| P0 降级可口头放行 | DegradationDecision 收据 + policy evaluator + finalization gate |
| P1 内部操作暗区 | OperationRecorder 统一接 Tool/Skill/MaterialSource/LLM/Renderer/Plugin adapter |
| P1 run 不可单独操作 | Run control API 与 run-scoped message channel；状态机支持 pause/resume/replay |
| P1 contextvars 线程语义 | 显式 RunContext + 可并发安全 EventSink/Budget |
| P1 子 agent 可见信息不对称 | 统一 RunEvent 投影为 SSE 与查询 API，不采集非必要思维链 |
| P2 前端只展示摘要 | run detail transcript 分页、筛选及完整性提示 |
| P2 并发成本差值 | request-level usage/cost 关联 call_id/run_id |

### 4.4 核心契约

#### 事件

统一 `RunEvent` 信封字段：`event_id`、`task_id`、`run_id`、`parent_run_id`、`trace_id`、`seq`、`ts`、`type`、`span_id`、`causation_id`、`attempt`、`payload`、`schema_version`。至少支持：

- 生命周期：`run_created`、`run_started`、`run_waiting`、`run_paused`、`run_resumed`、`run_cancel_requested`、`run_cancelled`、`run_completed`、`run_failed`。
- 行动：`operation_started`、`operation_finished`、`tool_call`、`tool_result`、`progress`、`narration`、`artifact_written`。
- 人机与质量：`message_injected`、`approval_requested`、`approval_resolved`、`degradation_reported`、`degradation_decided`。

写入采用 append-only 持久化为事实源；内存只作 cache。SSE 支持 `after_seq`，返回 `oldest_seq`、`latest_seq`、`has_gap` 和恢复游标。旧 `TaskEvent` 继续投影为兼容事件至少一个发布周期；跨仓契约测试核对 event type 与必需字段 schema，而非仅类型名称。

#### 工件

统一 `ArtifactRef`：`artifact_id`、`task_id`、`run_id`、`name`、`media_type`、`schema_version`、`path/uri`、`sha256`、`bytes`、`preview`、`created_at`、`producer_operation_id`。不可变内容按需读取；run 结果保存引用。现有 `StageResult.artifacts`、`report_path`、`stage_report` 与 `persist_artifacts` 作为兼容适配器继续工作。

#### 状态机

`created → queued → running ↔ waiting_input/paused → completed | failed | cancelled`；`running → recovering → running` 用于进程恢复。所有终态必须记录结束原因与 usage snapshot。取消请求与取消完成分开记；取消未能中止不可取消线程操作时，状态显示 `cancel_requested`，待执行边界收敛后再进终态。

每个 run 的 `attempt` 单调递增；恢复既有 run 保持 run_id 并新开 attempt；明确的 replay 可新建 run 并记录 `replay_of`。`parent_run_id` 支持可配置深度上限，当前星型模式映射为 max_depth=1。

#### 权限与预算

RunSpec 写入创建时的权限快照：工具/能力白名单、风险档位、审批要求、预算、最大并发、最大深度。adapter 不可自行扩大权限；运行中权限变化需新建 attempt 并事件化。每次 costly operation 必须登记金额或 `cost_unknown=true`；超过预算时进入等待/收尾策略，未知成本按独立额度计数而非视作零。

#### Run API

- `GET /api/tasks/{task_id}/runs`：按游标分页列举 run 摘要。
- `GET /api/tasks/{task_id}/runs/{run_id}`：run 状态、spec 脱敏视图、结果、artifact refs、完整性摘要。
- `GET /api/tasks/{task_id}/runs/{run_id}/events?after_seq=`：持久化分页事件。
- `GET /api/tasks/{task_id}/runs/{run_id}/events/stream`：SSE 增量。
- `POST .../{run_id}/messages`：向指定 run 注入文本/结构化控制消息。
- `POST .../{run_id}/pause|resume|cancel|replay`：幂等控制操作，返回状态与 operation id。

现有 `get_agent_run/list_agent_runs/cancel_agent_run` 保留为薄 adapter，兼容旧参数名与 payload。

### 4.5 前端体验与可见性

Copilot 子 agent tab 由当前状态卡扩展为 run transcript：顶部显示 run 状态、attempt、模型、成本（含未知项）、耗时、完整性和父级；中部以时间线展示 progress、operation/tool 的开始/结束、结果摘要、失败、降级决策与 artifact；底部提供 run-scoped 输入、暂停/继续、取消、重放（按权限显示）。主任务事件仍可聚合显示子 run 摘要，但点开后用 run API 分页取完整轨迹，不依赖 task event 内存列表。

前端对 gap、断线、恢复中、成本未知和旧版快照分别呈现不同状态；不能把“没有事件”解释成“没有执行”。事件 parser 保留未知类型与 raw payload；契约测试增加必需字段/版本兼容断言。

### 4.6 迁移与兼容

1. **兼容投影**：先引入 RunEvent/RunStore，现有 `task_events.emit` 与 `agent_run._emit_run` 双写到兼容投影；新增 run 查询 API，不改旧工具 schema。
2. **读路径切换**：前端先读新 run API，缺失时回落旧 task events/supervisor snapshot 并标注 source；已有 `AgentResult` 在读取时转换成首个 compatibility snapshot。
3. **operation 接入**：先覆盖 `BaseAgent.call_tool`、`call_skill`、Kernel surface，再接 MaterialRegistry、MGGenerator/Hyperframes、插件 RPC。逐模块比对调用计数。
4. **恢复迁移**：task checkpoint sidecar 增加 run IDs、event cursors 和状态快照；旧的 stage sidecar 只作为已完成 run 的历史记录，不伪装成可恢复的 live session。
5. **删除旧路径**：至少一个兼容周期后，确认生产 UI/API 不再依赖 stage list 的旧形状，再下线重复字段；事件历史按保留策略归档而非静默截断。

### 4.7 里程碑

**M1：可靠 Run Ledger（可独立验收）**

- 新增持久化 RunStore、RunEvent schema/version、分页查询与 SSE gap 协议。
- `agent_run` 状态与结果写入持久化；完成 task checkpoint sidecar 只保存恢复指针，不再承载唯一记录。
- 前端 run tab 展示状态、summary、cost、degradation、report/artifacts 与 completeness。
- 验收：新增 5000 个事件后分页总数与写入数相同；后端进程重启后按 run_id 查询结果；SSE 客户端落后时明确收到 gap 元信息；旧 `get_agent_run` 仍通过兼容测试。

**M2：完整 operation 归因与降级闭环（可独立验收）**

- 接入 `OperationRecorder` 至 BaseAgent、Skill、素材源、MG/renderer、插件 RPC；将原 200/400 设为 UI 采样阈值，不再删事实账本。
- 引入结构化降级决策与 kernel evaluator；完善 request-level tokens/cost 归因。
- 验收：包含失败和重试的 material/animation fixture 中 operation 计数与 stub 调用次数逐项相等；“口头接受”无法让必须修复类降级进入普通 done；并发两个模型的 token 成本各自精确归属。

**M3：可交互、可恢复的子会话（可独立验收）**

- run-scoped message/inject、pause/resume/cancel/replay API；RunContext 显式传递；前端 transcript 订阅与控制。
- 支持进程恢复后恢复 queued/paused run，running run 按幂等策略转 recovering 并继续或明确失败。
- 验收：对一个 run 注入指令只影响目标 run；取消不影响兄弟 run；模拟进程重启后 paused run 可恢复，事件 seq 单调且无重复副作用；UI 可看到注入、恢复、取消和 replay 关系。

### 4.8 验收标准总表

- 任一子 agent 的 `run_id` 能跨进程查询完整状态、spec 脱敏视图、所有 operation、artifact refs、降级决策及成本账本。
- operation 事实记录不受 UI 采样上限影响；返回分页有稳定游标，去重依据 `event_id`。
- task/run/event 数据在 5000+ events、事件超过旧 2000 上限时仍完整；历史游标过期必须有明确 gap，而非空结果。
- asyncio task、`asyncio.to_thread`、`run_in_executor`、插件 RPC 的 task/run/stage 归属测试通过；预算为 run 级共享上限。
- 关键降级缺少有效决策收据时，任务最终状态不得是普通 `done`；结构化接受必须携带影响说明，规定为不可接受的降级必须修复或等待人工审查。
- 子 agent 详情页支持分页查看工具调用、失败、progress、degradation、artifacts；可见 transcript completeness 与成本未知数；筛选与断线恢复保持 seq 连续。
- 现有 `TaskEvent`、`AgentResult`、`StageResult`、旧工具名与 checkpoint 均有兼容测试；迁移期间不会重复执行已完成的幂等 operation。
- 对 reasoning：验收记录可展示的 narration/decision summary；不以 chain-of-thought 内容完整性作为完成条件。

### 4.9 风险与回滚

| 风险 | 缓解与回滚 |
|---|---|
| 持久化事件量增长、写入延迟上升 | append-only 存储加索引、批量写和压缩归档；先 shadow 双写比较；回滚时关闭新读路径，旧 task events 继续运行。 |
| 双写导致重复事件 | 使用 event_id 唯一索引与幂等 upsert；对账任务统计重复和缺口；回滚新投影不删除原事件。 |
| 新旧状态机冲突 | 新状态只由 RunStore 转换；旧字段通过 adapter 投影；保留旧 endpoint 一个发布周期。 |
| OperationRecorder 接入面广 | 从可控 Tool/Skill 入口开始逐模块启用；每个 adapter 可 feature flag 回退原执行实现，但记录 `instrumentation_gap`。 |
| run 恢复导致重复副作用 | 每次 operation 要求幂等 key/side-effect receipt；不可幂等操作恢复时转人工确认，不自动重放。 |
| UI 事件洪峰或大结果卡顿 | 服务端分页、前端按需加载、只传 preview 和引用；任何采样都明示遗漏范围并允许拉取全量。 |
| 降级 gate 误阻正常完成 | policy evaluator 配置可审计、按 kind 分级；回滚可恢复提示词 gate，但不得删除降级事实与历史 decision。 |


## 执行记录（2026-10-30 · M3 收口）

本轮把三份 PRD 的 M3 全部落地，并跑通后端全量回归（4840 passed / 4 skipped / 1 xfailed）
与前端全量回归（128 files / 1107 tests，`tsc` 干净）。要点：

- **全量轨迹可读**：`GET /api/task/{id}/tool-calls` 按 `stage/attempt` 分页读回
  `<workspace>/reports/tool_calls/<stage>-<n>.json`；工作区解析与阶段侧一致
  （`request.extra.workspace_root` → `proceed_project_id` → 任务工作区），
  前端 `TaskToolTrace` 面板默认折叠、点开摊开整份（失败数/内部操作数/第几次尝试）。
- **技能调用不再黑区**：`BaseAgent.call_skill` 与 `call_tool` 一样记进
  `last_tool_calls` 与 `subagent_tool` 事件（轨迹里以 `skill:<名>` 区分）。
- **收据必须真实**：幂等回放不再算"重派修复"（`result.replayed` → 台账不写
  `remediated`，改发 `degradation(mode=replayed_not_fixed)`）；降级结果不回放，
  真重跑。信息型降级（`llm_json_*`/`mg_critique_skipped`/`text_repair` 等）不参与
  收尾守门，只有影响成片的（占位画面、弱动效、工具失败率…）才要收据。
- **幂等键按参数派生**：`idem_key_for(task, tool, args)` 取代 `f"{task}:{name}:{len(calls)}"`
  ——旧键在续跑一轮从 0 重数，会把"本轮第 3 次 delegate_agent"命中上一轮第 3 次的结果
  （实测：派 structure 拿回 edit 的历史结果）。
- **时间线以黑板为准**：`assemble` 不再让 `inputs.timeline` 的旧指针顶掉黑板现值，
  audio 不会再用粗剪版定稿（否则动画阶段的文字层整段消失）。
- **预算**：`/proceed` 建的任务给 160 轮 / 800 次工具调用（旧默认 80 轮常被返工截断）。


## 执行记录（2026-10-30 · 第 8 轮 · M2/M3 收尾 + 完整 E2E）

- **子会话可寻址（M2）**：新增 `GET /api/task/{id}/runs`、`GET .../runs/{run_id}`、
  `POST .../runs/{run_id}/message`（追问：排队进该阶段下一次派发的 brief，`inject` 事件可见）、
  `POST .../runs/{run_id}/replay`（重放：`refresh=true` 跳过幂等回放，真重跑）。
  `AgentResult.brief` 记下原始要求，重放才能"照原样再干一次"。前端 `TaskRuns` 面板
  逐条列出 stage/attempt/状态/降级/工具数，并给「追问 / 重放」两个入口。
- **Kernel 确定性补派**：模型提前收尾且提示词推不动时，运行时用同一套工具面自己补派缺失阶段
  （计 usage、进事件流、成功且未回放才写 `remediated` 收据）；开关 `agent_kernel_recovery`
  （单测默认关闭，避免真实派发）。实测：一条 60 秒片子里真的触发了
  「Kernel 补派 animation（降级待修）」。
- **回合预算与摸底护栏**（本轮最大收益）：诊断出一次任务 161 次调用里 121 次是
  `list_dir`/`read_artifact`/`list_artifacts`/`list_reports`——模型每轮重新摸底，
  160 回合被烧光而 `final_timeline` 第 16 回合就有了。三项修复：
  1. 上下文里给出状态快照（黑板键、磁盘产物、已派发 run、时间线远程/本地资产分布）；
  2. 同参数只读查询直接复用上次结果（`agent_tool_dedupe`，标注 `deduped`）；
  3. 提示词带「剩余回合」，剩余 ≤5 时查询类工具直接拒；守门在即将失败前给**一次**
     「只写降级收据」的最后通牒（独立预算，不被前期续跑吃掉）。
  效果：同一条片子从「160 回合耗尽失败」变成 **30 回合 done**。
- **预算真正生效**：`/proceed` 的 `TaskLimits` 之前被塞进 `TaskRequest`（无此字段）被静默忽略，
  仍是默认 80 回合；改为 `kernel.create(..., limits=)`，现在 160 轮 / 800 次调用。
- **完整前端 E2E（通过）**：`e2e/final-flow.spec.ts` 单次连跑 12.3 分钟，
  首页新建制作 → 创意简报 →「确认简报」→ 规划书 →「确认实施」→ `/api/requirements/proceed`
  返回 task → 事件流有 `subagent_tool` → `/tool-calls` 读到 67 条轨迹、前端面板渲染 67 行 →
  任务 `done`（30 回合）→ 时间线回写项目（5 轨 / 31 片段 / 60s）→ 导出「加入渲染队列」→
  渲染完成 → 「下载成片」14.7MB → `ffprobe` h264 1920x1080 30fps 60.0s，页面零未捕获异常。

## 执行记录（2026-10-30 · 第 9 轮 · M4 前端可见性 + M5c 需求会话问答 + 导出重试快照）

- **思考流与工具全部可见（§13 / M4 前端）**
  - `AgentPanel` 现在真的消费 SSE `reasoning` 帧（旧实现声明了 `liveReasoning` 却从不赋值，
    折叠条永远是空的）：逐块累积、每轮提问清空、正文已在流式时仍然可见（上限 20 万字）。
  - `ThinkingDisclosure` 改增量累积（旧实现每帧把全部片段重新 join 一遍，长任务 O(n²) 拖住主线程），
    首帧指纹变化或 seq 后退时整体重建（不会把上一轮思考挂到新任务上），到 40 万字上限如实说明停止追加。
  - `ToolCallRows` 三路合并（`tool_call`/`tool_result` 按 causation 配对 + `subagent_tool` + Skill），
    并**删除 `slice(-8)`**：主 agent / 子 agent / 技能各有来源筛选与计数；`source=kernel`（运行时补派）、
    `deduped`（复用上次结果）、`remaining_turns` 逐行标注；窗口化 60 行 + 「加载更早的」逐段翻到底，
    既不截断也不把上千行一次塞进 DOM。`ModelTurnRows` 同样取消 `slice(-6)`。
  - 后端 `subagent_tool` 补 `kind` / `run_id` / `seq_in_run`，前端 schema 同步（`ToolDetail.seq_in_run`）。
- **需求会话 ask_user（§15.5 / M5c）**——需求对话没有工具轮，因此复用同一个 `ask_user` 服务与同一套
  `question`/`answer` 事件语义，只把调用点放在 service：
  - LLM 结构化输出带 `ask_user` 指令 → `RequirementsService._ask_from_llm` 建问题
    （owner=`requirements`，事件流键 `requirements:<session_id>`，与任务事件按前缀隔离）→ 会话进
    `waiting_user`，本轮不再推进（不猜、不替用户选）；
  - `POST /api/requirements/{sid}/answer` 回答问题 → 答案还原成一句人话喂回对话 → agent 继续；
    `GET /{sid}/questions?status=pending|all`、`GET /{sid}/events?since_seq=` 供刷新/重连重建；
  - `waiting_user` 下用户直接打字 = 自由文本回答（自动应答后回到 gathering），不留死胡同；
  - 错误码对齐任务侧：404（跨会话答别人的问题）/ 409（已答、已取消）/ 410（超时或服务重启）/ 400；
  - 前端 `AgentPanel` 挂 `TaskQuestionCard`（`askerLabel="需求 agent"`），SSE `question` 帧到达即出卡，
    回答后回显 agent 续跑结果并刷新待答列表。需求会话**不访问任务事件 API**。
- **导出重试拆分（§20 M3 尾巴）**：提交时冻结时间线快照 + FNV-1a 指纹；失败卡片只给「重试本次提交」
  （跑快照），时间线改过才额外给「用当前时间线重新提交」，并把两侧指纹显示出来。旧实现永远跑
  "当前时间线"却叫重试，改完时间线再点重试会让人以为在重试、其实跑的是新内容。
- **回归**：后端 `pytest` **4924 passed / 4 skipped / 1 xfailed**（新增
  `test_requirements_ask_user.py` 9 例、`test_requirements_ask_user_api.py` 7 例）；
  前端 `tsc` 干净、`vitest` **136 文件 / 1171 例**（新增 `CopilotToolTransparency` 8 例、
  `exportSnapshot` 7 例、`AgentPanel` +5 例）；`stage_tool_failures` 失败率门禁仍绿。
- **顺手修的环境脆弱点**：`test_dead_extra_keys.py::test_plumbing_audit_passes_strict` 用 `text=True`
  按 locale（Windows 中文机 cp936）解码子进程的 UTF-8 输出，报告里一有中文就 `UnicodeDecodeError`
  → `stdout=None` → `json.loads(None)`；改为父进程显式按 UTF-8 解码。
- **未完成**：M6（动画提示词 v2：九段式模板、S1–S12 风格模块、lint、逐类改写）尚未开始；
  `TaskToolTrace` 已有后端 `limit/offset` 分页，本轮未再加虚拟滚动。

## 执行记录（2026-10-30 · 第 10 轮 · M6 动画提示词 v2）

- **九段式模板**：`prompts/text/_base.md`、`prompts/types/_base.md`（角色与交付物 / 硬约束 /
  常量表 / 分段关键帧公式与 easing 词典 / 风格模块引用 / 不要清单 / 自检清单 / 输出格式 /
  失败回退）。类型提示词保留七段旧标题（`text_specs.load_text_prompt` 与
  `validate_text_registry` 按这七段切分），另补 Constants / Output / Self-check / Fallback 四段。
- **风格模块 S1–S12**：`prompts/style_catalog.json`（id/name/series/keywords_zh/keywords_en/
  palette_hex/motion_tags/composition_tags/suitable_types/avoid_types/forbidden/
  renderer_paths/reference_source/prompt_version/status）+ `prompts/styles/S1–S12.md` 正文
  （视觉语言 / 关键参数倾向 / 适用与回避 / 禁止项 / 使用纪律）。
  `scripts/styles.py` 提供 `search` / `show` / `match`：默认只给 ≤3 条摘要、排序确定
  （分数→编号）、`avoid_types` 过滤、`show` 才读正文——不把 12 份模块正文一次拼进 prompt。
- **逐类改写（10 份）**：六类文字（emphasis_keyword / reveal_line / quote_block /
  label_callout / number_punch / title_stack）+ 四类 MG（bar_chart / line_chart /
  comparison / flow_chart）。要点：
  - drawtext 提示词只写 `keyframes_expr.EASING_EXPR` 的 21 个名字；`back-out`/`bounce`/
    `elastic-out` 明确标注为「MG 名字，drawtext 不认」，且只出现在反例行里；
  - 粒子 / 模糊 / 遮罩 / 扫光 / 独立卡片层一律标注「MG HTML 路径」或「本期不落」，
    并在 `docs/animation_system.md` 新增「渲染侧需求登记」表逐条列出所需能力；
  - Example 换成**可渲染 JSON**：扁平关键帧（不再用 `properties` 包裹，`repair_mg_json`
    会把它当未知 props 删掉）、每帧声明完整动画属性、带 `time=0` 起始帧；
  - 每份提示词声明 `style_modules: [S…]`，并给了"收束"阶段让动画覆盖率 ≥45% 且末段静止 ≥0.6s。
- **lint 进 CI**：`python -m clipwright.animation.prompts.lint_prompts --strict`
  （`.github/workflows/ci.yml` 新增一步）。检查段落齐全、反例行豁免（"不要写 X"不算违规）、
  占位符残留、duration/元素/字数上限、按渲染路径的 easing 白名单、drawtext 不支持能力词
  必须标注、Example 必须过 `validate_mg_json`、风格模块引用必须存在。
  现状 **0 error / 136 warning**：未迁移的老 MG 类型只报 warning，迁移后自动升级为 error
  （`MIGRATED_TYPES`）。
- **指标回归** `tests/clipwright/test_prompt_metrics.py`：把每份提示词自己的 Example 喂真实
  校验链路——motion coverage ≥45%、末段静止 ≥0.6s、`position` 不落字幕带（底边 ≤920px）、
  单行 ≤14 字、元素 ≤3、关键帧升序且在片段内、同输入两次结果一致、
  `repair_mg_json` 不得改动结构（修复率 = 0）。
- **顺手修不稳定测试**：`test_render_parallel::test_failed_mov_does_not_abort_others` 的
  2.5s 绝对阈值在 Windows 上连跑三次就随机红（实测 2.76s），改成相对串行基线的 80%
  （4×1.0×0.8=3.2s），仍能抓"串行化 / gather 悬挂"。
- **回归**：后端 `pytest` **4966 passed / 4 skipped / 1 xfailed**（+41：lint 29、指标 12）；
  提示词 lint strict 0 error；前端 `tsc` 干净、`vitest` 136 文件 / 1171 例。

## 执行记录（2026-10-30 · 第 11 轮 · 收口：progress 落 INDEX + 全链路 E2E）

- **`progress` 补上「进度时间线」落 INDEX.md（§14）**：旧实现只在 `progress.md` 里写时间线，
  而 `stage_report.update_index()` 是**整文件重写**——用户刚在 INDEX.md 看到的进度会被下一次
  报告写入抹掉（"看得见但会消失"的假功能）。现在：
  - `progress.timeline_lines()` 从 `progress.md` 读回最近 N 条；
  - `update_index()` 重写时合并出 `## 进度时间线` 小节（两份文件各自维护，谁也不覆盖谁）；
  - `list_reports()` 不再把 `progress.md` 当成阶段报告（否则报告数虚高、还能被当输入读）。
  新增 `tests/clipwright/test_progress_index.py`（4 例）钉住"反复重写也不丢时间线"。
- **完整前端 E2E（通过）**：`npx playwright test e2e/final-flow.spec.ts`，19.8 分钟一次跑完：
  首页新建制作 → 创意简报 → 「确认简报」→ 规划书 → 「确认实施」→ `/api/requirements/proceed`
  返回 `task_28fd9249b4` → 事件流 31 条（`subagent_tool` 2 条）→ `/tool-calls` 61 条轨迹
  → 任务 `done`（模型自己核了 audio 是否真落盘）→ 时间线回写项目（5 轨 / 43 片段 / 60s）
  → 导出「加入渲染队列」→ 渲染完成 → 「下载成片」4.2 MB →
  `ffprobe`：h264 1920x1080 30fps **58.0s**，页面零未捕获异常。
  （第一次跑在 18:34 后端 socket 不再 accept 而挂死：进程活着、日志停在 `vision_llm`，
  重启后端后同一脚本一次通过；挂死现象与代码改动无对应关系，已在日志里留痕。）
- **回归**：后端 `pytest` 全绿（含新增 progress/INDEX 4 例、prompt lint 29 例、指标 12 例）；
  前端 `tsc` 干净、`vitest` 136 文件 / 1171 例；`stage_tool_failures` 门禁绿；
  事件契约 29 种前后端同序（跨仓测试 `tests/test_event_contract.py` ↔ `eventSchema.test.ts`）。

## 执行记录（2026-11-01 · 第 12 轮 · run 寻址、身份与事件契约收口）

**触发**：主/子 Agent 审查发现，run_id 来自别的任务时，任务 owner 校验挡不住
详情/追问/重放；重放与降级重跑共用旧 run_id，工具事件会挂错记录或覆盖历史。

### 后端修法

1. **归属**
   - 新增 `agent_run.get_run_for_task()`；`api/task.py` 用 `_run_for_task()` 统一收口
     详情、追问、重放；`inspect()` 的 stage 回退候选按 `task_id` 过滤。
   - 跨任务访问返回 404（不暴露“这个 run 存在但不属于你”）。
2. **身份**
   - `idem_key` 与 `run_id` 分离：显式重放、降级重跑、同键再次执行生成 `#r…` 新 run_id；
     `attempt` 按任务+阶段递增；`_LIVE` 记录 `task_id/attempt`。
   - `StageSpec.run_id` 贯通到阶段内 `subagent_tool.run_id`；记录不再被覆盖。
3. **事件契约**
   - `agent_run_end.detail` 发完整 `failure` 对象 + `needs` + `duration_ms` + `attempt` +
     `report_path` + `degradations`；`FailureReport.needs` 上浮到 `AgentResult.needs`。
   - 所有 `task_end.detail` 补 `completed` / `unfinished`。
   - `/runs` 的 `report_path` 改读顶层；列表合并 `_LIVE` 运行中记录（`live=true`）。
4. **生命周期**
   - `/events/stream` 接受 `since_seq`；终态且游标追平直接结束，避免“回放→EOF→重连”。
   - 终态任务 replay 返回 409。
   - `/proceed` 幂等命中若目标是 `task_*`，同时返回 `task_id` / `mode=task`。

### 验证

- 新增 `tests/clipwright/test_run_addressing_api.py`：跨任务详情/追问 404、运行中 run 列表。
- 新增 `tests/clipwright/test_agent_run_replay.py`：refresh 新 run_id + attempt 递增、
  `agent_run_end` 完整字段。
- `test_task_api.py` + `test_event_contract.py` + `test_run_addressing_api.py` +
  `test_agent_run_replay.py`：26 passed；Agent/Kernel/Loop/Stage 等 11 文件：139 passed。

### 已知残留

- 进程重启后事件历史与统一 AgentRun 的恢复链不完整：Mongo 只有写路径，
  `task_events` 普通任务不全量落 JSONL，`/runs` 无法重建重启前阶段结果。
  需要补“从 Mongo/全量 JSONL 读取恢复”，或持久化统一 AgentResult。
