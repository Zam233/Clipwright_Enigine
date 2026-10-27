# PRD：Clipwright 现代 Agent Kernel 改造

- 版本：v1.0（2026-10-27）
- 后端：`J:\Clipwright`，Python 3.14，分支 `feat/master-agent`，HEAD `6b70244`
- 前端：`J:\Clipweight-Client`，HEAD `2820b19`
- 基线：后端 4461 passed；前端 983 passed（以任务给定基线为准）
- 范围：Agent Kernel、AgentRun、工具策略、事件/记忆/评估、旧编排执行路径收敛；不改成片领域确定性校验
- 本文中的“现状”均为代码观察；“目标/验收/迁移”均为待实现设计

---
## 0. 一页结论

**一句话判定：**当前系统有 observe/decide、上下文装载、报告和工具安全等现代 Agent 的软能力，但主执行仍是 `PlanStep → executor` 的计划表编排；目标是让 Kernel 以模型发出的原生 tool call/result 多轮闭环自主选择下一动作，子 agent 与调研 worker 共用一个 AgentRun 运行时。

**可判定分界：**对任一运行任务，若执行前没有必须生成完整步骤表，模型每轮收到可用工具 JSON Schema 并能在本轮发起零个或多个 tool call，Kernel 执行并把结果按 provider 协议回填，模型随后继续调用或给出完成终态；且删除 Plan/PlanStep 的 `next_step()` 后任务仍能完成，则通过。任何路径若把模型输出先转为有序步骤数组，再由循环逐步执行该数组，即仍属编排管线。

### 0.1 目标结果

| 维度 | 目标 | 判定证据 |
|---|---|---|
| Kernel | 一个 tool-use loop 是唯一 agent 主循环 | 生产路径中没有 `Plan.next_step()` 决定下一执行动作；有 provider 请求记录显示 `tools` 非空 |
| AgentRun | 阶段能力与临时调研 worker 共用契约及生命周期 | 两种任务实例化同一 `AgentRun` schema、同一事件序列、同一审核入口 |
| 领域不变量 | 依赖约束作为信息与确定性校验，不作为隐式执行 DAG | 模型可选择非典型先后；输入缺失由工具返回 needs；时间线/预算等确定性闸仍拒绝非法状态 |
| 证据 | 每次选择可关联到上下文、推理事件、调用及结果 | 同一 trace/span 关联 `context_loaded → thinking → tool_call → tool_result` |
| 删除 | 旧管线执行能力与执行路由退出主应用 | 运行路径无 `pipeline_v2` import、无 pipeline 执行路由、无字符串 capability executor 分支 |

### 0.2 保留的已有资产

保留并升级：observe/decide（`agent_decide_policy=always`，融入每轮 tool loop）、structured stream 的 `thinking`（不伪造 provider 无内容）、`context_loader` 的上下文裁剪与 `context_loaded`、append-only 报告/INDEX 与 reports 工具、artifact 引用/按需读取、受约束黑板、checkpoint/sidecar/真取消/收尾守门/限额/幂等。`supervisor/spawn.py` 的 `LLMService.with_tools` 作为协议实现参考，不直接复制其生命周期。

### 0.3 估算

预计 4–6 周日历时间，约 24–32 人日（单人含全量回归、前端联调与小规模评估；模型供应商适配/端到端测试环境可延长）。两周版本只交付 M0 与 M1 的最小垂直切片，不宣称完成“真正 Agent 化”。

---
## 1. 现状判定

### 1.1 判定方法

本节以运行路径为判定对象，不按模块名称、提示词措辞或 UI 展示判定。符合任一项即仍有编排管线特征：

- 模型先输出 `steps[]`，执行器通过索引/状态选下一项；
- 一个字符串 capability 经 Kernel 的 `if/elif` 被解释为某类固定子流程；
- 阶段名字映射到固定 Agent 类后执行，模型只决定何时调用该名字；
- 主 agent 与 worker 的工具、状态、审核、输出和生命周期要经过两套实现。

### 1.2 主 Kernel 仍计划表驱动

| 观察 | 证据 | 结论 |
|---|---|---|
| Task 持有可选 Plan，Plan 持有有序 PlanStep，`next_step()` 返回首个 pending step | `clipwright/schema/task.py:58-88` | 持久化状态模型把执行序列作为一等对象 |
| run loop 先确保 plan 存在，再读取 `task.plan.next_step()` 并调用 `_run_step` | `clipwright/services/agent_kernel.py:602-638` | 主循环依据步骤表选择执行动作 |
| `_run_step` 发 tool_call 事件后调用 `deps.executor(task, step)`；调用前没有模型原生 function call | `clipwright/services/agent_kernel.py:662-681` | tool_call 当前是事件名，不等同于 provider tool call |
| `LLMPlanner.plan` 通过 `structured_output(... output_schema=_PLAN_SCHEMA)` 生成 steps | `clipwright/services/agent_kernel.py:110-129,183-250` | 模型输出结构化计划，而非标准 tools |
| 每步 `_decide` 可以调用 structured output 决定 continue/replan/finish，但 `_after_step` 将 replan 输出重新扩展进 Plan | `clipwright/services/agent_kernel.py:812-892,910-943` | 决策回路存在，但行动仍重新落回有序步骤表 |
| fallback 在规划失败时按 `EXECUTION_ORDER` 构造六个 `delegate` 步骤 | `clipwright/services/agent_kernel.py:258-279` | 退化路径本身是一条硬编码阶段管线 |
| 默认执行器依 capability 字符串分支至 pipeline、worker、stage、closure、exec 或 surface | `clipwright/services/agent_kernel.py:68-104` | Kernel 负责解释字符串命令并分流多套实现 |

因此当前实际形状是“计划表执行 + 每步决策”，不是模型驱动工具闭环。这里不是否认已有 observe/decide；问题在于模型并未拥有行动协议本身。

### 1.3 delegate 是阶段能力解释器

| 观察 | 证据 | 结论 |
|---|---|---|
| `stage_capability` 维护查询能力名集合与派发能力名集合 | `clipwright/services/stage_capability.py:16-25` | `delegate` 的调用参数需按能力名特殊解释 |
| 能力入口只接受 `delegate/run_stage`，其余名称拒绝或进入 query 分支 | `clipwright/services/stage_capability.py:33-53` | `delegate` 不是对任意 AgentSpec 的统一派生协议 |
| `_run_stage` 必须解析 `stage`，限制在 `STAGE_NAMES`，再构造 `StageSpec` | `clipwright/services/stage_capability.py:98-141` | 模型选择的是预置阶段枚举，而不是可组合 AgentSpec |
| 执行阶段时固定 `agent_for(stage)` 并运行 `agent.execute(input_obj, context)` | `clipwright/services/stage_subagent.py:349-364` | 阶段名直接绑定固定 Agent 类 |
| `agent_for` 中 `_STAGE_DEPENDENCIES` 明列六个 stage→class 映射 | `clipwright/services/stage_subagent.py:30-57` | 子代理类型集合仍是封闭枚举 |
| `stage_inputs.assemble` 按 stage 分支创建各自 Pydantic 输入 | `clipwright/services/stage_inputs.py:96-195` | 显式输入能力已存在，但它依赖阶段模板而非通用 AgentRun 输入协议 |

`delegate` 的名称听起来通用，执行语义却是“按 stage 查固定实现、装配专用输入、合并领域产物”。目标保留领域适配器，但由统一 AgentRun 托管其输入、工具、预算、追踪和审核，不再由 Kernel 识别 stage 字符串。

### 1.4 子代理与调研 worker 是两套生命周期

| 方面 | 阶段子 agent | 调研 worker | 证据 |
|---|---|---|---|
| 输入 | `StageSpec`、阶段 args、显式 inputs/黑板兼容、专用输入模型 | role/system_prompt/tools/provider/model | `schema/stage.py:32-53`；`supervisor/spawn.py:49-59` |
| 运行 | 固定 Agent 类 `.execute()` | LLM 对话，可选真实 tools 循环 | `stage_subagent.py:349-364`；`spawn.py:204-240` |
| 结果 | `StageResult`、领域产物、黑板写入、报告 | 文本结论、tool_calls、用量/失败 | `schema/stage.py:55-84`；`spawn.py:253-267,268-328` |
| 注册与状态 | `_RUNS` / `_IDEM` 与 checkpoint sidecar | tracker + worker registry | `stage_subagent.py:40-44,64-83`；`spawn.py:20-26,121-133` |
| 审核 | 阶段路径按内部可信 Agent 直接跑 | 工具含 exec 时先风险评估再待人工审核 | `stage_subagent.py:333-364`；`spawn.py:135-156,159-181` |
| 取消 | 依 Kernel 任务句柄/`wait_for` | tracker cancel callback 取消 worker asyncio task | `agent_kernel.py:529-546`；`spawn.py:127-133,197-200` |
| 成本 | 进程级用量快照差估算 | 响应 usage 计算并写 llm_tracker | `stage_subagent.py:115-132,405-407`；`spawn.py:245-251,381-433` |

共同根因：两类“子 agent”不是同一运行时中不同 spec，而是不同对象模型与调用链。结果是同一 run 无法统一重试、取消、审查、trace 关联及成本归因。

### 1.5 已有基础设施与边界

| 能力 | 当前证据 | PRD 处理 |
|---|---|---|
| 工具面可生成 schema | `clipwright/tool/surface.py:107-152,198-205` | Kernel 每轮取受策略裁剪的 schema，作为 provider `tools` 传入 |
| 工具执行、幂等、审计、成本记录集中于 surface | `clipwright/tool/surface.py:345-436,439-457` | 保留执行入口；补 trace/span/call id 与权限策略上下文 |
| dangerous 工具不进普通 surface | `clipwright/tool/surface.py:27-29,107-119` | 保留；dangerous tool-call 必须转执行网关审批状态机 |
| `LLMService.with_tools` 已支持多轮发 schema、执行 call、回填 result | `clipwright/services/llm.py:641-668,711-784` | 作为实现参考并提升成支持 trace/token/cancel 的通用 turn driver |
| context loader 有优先级裁剪和来源记录 | `clipwright/services/context_loader.py:1-12,68-148` | 保留作为唯一上下文装配 seam，补持久化摘要与重要事实检索 |
| 报告 append-only，运行时写盘 | `clipwright/services/stage_report.py:1-9,108-174` | 保留，报告 schema 从阶段绑定改为 AgentRun 通用元数据 |
| artifact 大对象按需读取 | `clipwright/tool/artifacts.py:1-5,47-105` | 保留不可变 artifact 机制及引用语义 |
| Task events append-only，前端 SSE 游标订阅 | `clipwright/services/task_events.py:1-10,32-59,91-105`；`clipwright/api/task.py:146-175` | 扩展为统一 RunEvent；旧 type 映射兼容一周期 |
| 检查点含 Task 并用 sidecar 保存黑板与 stage runs | `clipwright/services/agent_kernel.py:1017-1107` | 升级为 AgentRun、tool transcript、记忆索引游标的原子快照 |
| 真取消句柄 | `clipwright/services/agent_kernel.py:529-546,590-600` | 保留，并传递至 LLM、AgentRun、网关和子进程取消 token |
| 收尾有 final_timeline 守门 | `clipwright/services/agent_kernel.py:970-1009` | 保留为领域完成条件，不交给模型自我判定绕过 |
| 路由白名单与 contextvar 模型路由 | `clipwright/services/model_router.py:65-107,237-249` | 保留；AgentSpec 路由只可选择白名单 route |

### 1.6 pipeline_v2 的残留职责与删除阻塞

`pipeline_v2.py` 当前不能直接删。下列能力必须先归属到中立模块或明确退休，删除才不造成应用 import 崩溃或行为丢失：

| 残留职责 | 证据 | 改造去向 |
|---|---|---|
| 成片最终时间线 postprocess：类型 transform、validate_clip、署名轨；完成时产出 `final_timeline` | `pipeline_v2.py:1268-1307`；现有 PRD 对比 `docs/master-agent-prd-2026-10-26.md:71-89` | 抽到 `services/timeline_finalize.py`，Kernel 完成前调用确定性接口；其规则可由阶段适配器共享 |
| 质检失败自愈、问题归因、策略选择、重做闭包 | `pipeline_v2.py:1253-1266,1335-1386,1390-1429` | 核心依赖闭包归 `stage_closure` 或 `agent_policy`；模型可选是否调用重做，但领域闭包校验强制执行 |
| 插件 Agent DAG/依赖合并 | `pipeline_v2.py:1364-1386`；`agents/registry.py` 顶部注释称插件并入 DAG（由目标行引用核对） | AgentSpec manifest 声明 accepts/produces/dependencies；统一注册表与策略验证 |
| Persona、Category Plugin、RAG 初始化与上下文注入 | `pipeline_v2.py:1553-1628` | `task_bootstrap.py`，作为 Task 创建后的 context/资源装配，不承担执行顺序 |
| Stage 输入模型构建 | `pipeline_v2.py:1630` 起及 `_dispatch` `2031-2112` | 保留 `stage_inputs` 为 AgentAdapter 输入转换器；只由对应 AgentSpec adapter 使用 |
| 模型路由、预算、熔断、Agent hooks、LLM 记账 | `pipeline_v2.py:1714-1830,1892-1912`；文件总长 2197 行 | 分拆为 `model_router`、budget policy、hook runner、统一 LLM telemetry，不让执行 DAG 存活 |
| PipelineState/steps/checkpoint/persist | `pipeline_v2.py:2114-2144` 等 | 只保留旧数据读适配器；新任务统一 Task/AgentRun event store |
| pipeline_id 最终结果读取 | `api/task.py:261-309` 调 `pipeline_capability.last_result`；`services/golden_capture.py:113` 同样 import（grep 命中）；`api/pipeline.py:784,843` | 迁至 `services/task_results.py`，定义 TaskResult 对外快照和 golden capture 来源 |
| 老 API 执行/诊断/重试/管理 | `api/pipeline.py:342,616,772,835,872,919,1127` 等 | Task API 或只读兼容端点；按 §4 路由表逐项处理 |
| 老测试仍直 import orchestrator | `tests/test_pipeline_e2e.py`、`tests/test_pipeline_capability.py`、`tests/clipwright/test_pipeline_*.py`、`tests/test_supervisor_*.py` 等（grep 结果） | 迁行为断言至 Kernel/AgentRun 测试；存储历史 fixture 可改为序列化兼容测试 |

已检索到生产 import 至少包括 `api/pipeline.py` 模块级 import、`pipeline_capability.py`、`api/requirements.py:500,646`、`api/task.py` 的旧结果读取和 `golden_capture.py`。具体应以删除前全仓 `rg`/grep 再确认；本文不声称这些残留已拆除。

### 1.7 前端现状

| 观察 | 证据 | 目标影响 |
|---|---|---|
| TaskStore 将 Plan/PlanStep 折叠成 UI 状态，显式依赖 plan/replan/step_start/tool_result/step_end | `J:\Clipweight-Client\src\stores\taskStore.ts:49-97,134-162` | 改为 RunEvent projection；UI 展示动作流，不要求有步骤表 |
| 任务运行 tab 同时呈现 PlanPanel、阶段列表、事件流、子 agent 轮询和 artifact | `src/features/copilot/CopilotTaskRunTab.tsx:17-51,54-103` | 统一显示 run timeline、工具调用、AgentRun、审批和结果；阶段仅作为领域标签 |
| 子 agent 状态取 `/api/pipeline/{taskId}/subagents` 并 3 秒轮询 | `src/features/copilot/RunningSubagents.tsx:1-6,25-45` | 迁至 task run events + `/api/task/{id}/runs` 快照，SSE 为主，轮询仅恢复快照 |
| Copilot store 把 `subagent` 与 `stage` 当两种 tab kind | `src/stores/copilotStore.ts:14-21,23-35`；`CopilotDock.tsx:43-58` | 收敛成通用 AgentRun detail tab；旧 kind 做数据迁移 |
| thinking UI 消费 `thinking` event | `CopilotTaskRunTab.tsx:93`、`ThinkingDisclosure.tsx`（见文件路径） | 保留并用 trace/span/call 关系分组，provider 无 reasoning 时明确为空而非模拟 |

---
## 2. 目标架构

### 2.1 组件关系

```text
Task API
  └─ AgentKernel.run(TaskRun)
       ├─ ContextAssembler.load(run, memory_refs, tool_history, limits)
       ├─ LLM Turn Driver(model, messages, surface.schemas(policy))
       │    ├─ assistant(tool_calls[]) → PolicyGate → ToolSurface / AgentRunRuntime
       │    ├─ tool_result(call_id, structured_result) → next model turn
       │    └─ assistant(final) → CompletionGate → TaskFinish
       ├─ AgentRunRuntime(spec, explicit_inputs, artifact_refs)
       │    ├─ AgentAdapter(stage/worker/plugin) → same lifecycle
       │    └─ ArtifactStore / ReportStore / MemoryWriter
       ├─ ExecutionGateway (dangerous only; approval required)
       ├─ BudgetGuard / CancellationToken / CheckpointStore
       └─ TraceStore + RunEvent stream + EvaluationRecorder
```

### 2.2 Kernel 统一 tool-use loop

#### 2.2.1 输入与循环

Kernel 对每个模型轮次组装一份消息上下文，调用支持 tools 的 provider 接口：

- `tools = surface.schemas(policy=run.tool_policy)`，含普通工具与 `delegate_agent` 等元工具的 schema；schema 按 RunSpec 授权、可用性、当前预算动态裁剪。
- assistant 若给出 tool_calls，逐个经统一 call dispatcher 校验/执行；支持并行 call 仅限 `parallel_safe=true` 且共享资源声明无冲突，否则串行执行。
- 每个 call 的执行结果按 provider 协议（OpenAI `role=tool` / Anthropic `tool_result`）回填原 conversation，保留 id/name/args/result/error。
- 模型可连续多轮调用工具，任意轮可停止工具调用并提交 final answer；Kernel 不将选择转成步骤数组。
- 每轮重新计算动态工具可见集，不能把上轮已撤销权限继续暴露；工具定义的 version/hash 写入 trace。
- Plan 可选地保留为只读 `initial_hypothesis`，用于用户预览、估算或 resume 提示。它不作为循环游标、不决定下一个 tool call；`plan_only` 语义是模型给建议，不执行，不是正式 run 必需阶段。

#### 2.2.2 关键代码骨架（目标形状，非现状）

```python
async def run_loop(run: TaskRun) -> TaskFinish:
    messages = await context.assemble(run)
    while not run.terminal:
        limits.check_before_model(run)
        schemas = surface.schemas(policy=run.tool_policy)
        response = await llm.turn(messages, tools=schemas, route=run.route)
        trace.model_response(run, response)
        if not response.tool_calls:
            finish = completion_gate.validate(run, response.content)
            if finish.complete:
                return await kernel.finish(run, finish)
            messages.append(protocol.assistant(response))
            messages.append(protocol.user(completion_gate.feedback(finish)))
            continue
        messages.append(protocol.assistant(response))
        for call in scheduler.order(response.tool_calls, schemas):
            limits.reserve_call(run, call)
            decision = await policy.before_call(run, call)
            if decision.pending_approval:
                await approval.pause(run, call, decision)
                return await kernel.paused(run)
            result = await dispatcher.execute(run, call, decision)
            trace.tool_result(run, call, result)
            messages.append(protocol.tool_result(call, result))
        await checkpoint.save(run, messages)
    return run.finish
```

实现约束：骨架只表达状态转移；不得因 `tool_calls` 被归一化为 `PlanStep` 后再进入旧 `_run_step`。工具循环可复用 `LLMService.with_tools` 的协议逻辑，但应拆出回合回调，支持 checkpoint、SSE、token usage、每 call policy、重启 replay 与 tool-call id 幂等。

#### 2.2.3 预算与终止

| 限额 | 建议默认 | 计数口径 | 达限行为 |
|---|---:|---|---|
| `max_model_turns` | 40 | 每次 provider 请求一次；tool result 回填后再请求计新轮 | 不再发模型请求，进入收尾并说明未完成项 |
| `max_tool_calls` | 100 | 按实际开始执行计；拒绝的危险调用单独计 denied_calls | 阻止新增调用，结果作为系统消息让模型总结；不可越闸 |
| `max_agent_runs` | 12 | 每个 AgentRun 创建一次，重试也计一次 | 不创建新 run，已有 run 可按取消/回收处理 |
| `max_depth` | 2（默认 1） | root 深度 0；值由 policy 限定，不可由模型提高 | 深度满时 delegate 工具返回结构化 refusal |
| `max_wall_sec` | 3600 | 单调时钟，跨模型、工具、子 run 累计 | 取消 in-flight run，写收尾三件套 |
| `budget_usd` | 配置缺省沿现有产品策略；上线前必须给可见 unknown-cost 规则 | 已结算金额 + 保守预留额 | costly 调用先 reserve；余额不足拒绝，未知成本按配置保守上限预留 |
| 单工具超时 | schema/policy 分档 | tool call wall time | 取消 adapter 子进程/请求，输出 timeout result |
| 最大连续无效轮 | 3 | 无工具且 final 不满足完成 schema，或重复相同无效 call | 系统反馈后仍无进展则 `needs_human` 收尾 |

终止条件（满足一条才结束）：

1. 模型返回结构化 `TaskFinish` 且 CompletionGate 验证必需产物与不变量成立。
2. 用户显式取消，Run 状态为 cancelled，保留完成/未完成清单。
3. 预算/回合/工具/墙钟闸触发，进入 `budget_exhausted`/`limit_reached`，非成功状态。
4. 必要人工审批等待，状态 paused/awaiting_approval，不伪报完成。
5. 不可恢复错误或策略拒绝，终态 failed/refused，附证据、已完成产物和阻塞原因。

创作任务 CompletionGate 必须检查可用 final timeline/artifact、timeline validator、目标时长/用户明确约束、渲染产物状态；模型文本“完成”不能覆盖不变量失败。允许任务定义 `completion_contract`，例如 research 任务不需要 timeline。

### 2.3 动作前策略门与工具调用

工具动作统一经过 `PolicyGate`，返回 `allow | deny | approval_required | budget_wait`：

| 等级 | 典型动作 | 执行方式 | 事件 |
|---|---|---|---|
| read | 查状态、读报告、检索本地索引 | 直接执行，幂等 | `tool_call`, `tool_result` |
| write_local | 写工作区文档/产物，非最终覆盖 | 校验 workspace root、相对路径、幂等键后执行 | call/result + artifact/doc_write |
| costly | TTS、视觉 LLM、素材下载、整片渲染 | 预算预留、成本上界/未知说明、幂等 | `budget_reserved`, call/result, usage |
| dangerous | shell/python、系统命令、外发/执行类 | 不出现在普通 schemas；经 ExecutionGateway 风险评估与人工审批；批准后仍在网关沙箱执行 | `approval_request/result`, gateway span |

`surface.schemas()` 目前依据 capability matrix 与工具注册表生成 schema（`tool/surface.py:107-152,198-205`）；改造后入口签名扩展为 `schemas(run_context, policy, provider_protocol)`，返回 provider 所需格式，并携带内部 metadata（tier、cost estimate、parallel safety、idempotency、workspace scope）。模型看到的 schema 不包括内部可信凭据。

危险工具目前已有执行网关意图，但 `surface.execute` 的拒绝提示仍指向 `/api/pipeline/{pid}/exec`（`tool/surface.py:363-375`）；删除 pipeline 路由前必须将策略门的 approval continuation 迁至 `/api/task/{task_id}/approvals/{approval_id}`，并验证拒绝/审批/恢复可回放。

### 2.4 AgentRun 统一契约

AgentRun 是单次有目标的 Agent 执行单元，可由主 Kernel 工具调用、人工操作或内部规则创建。阶段 Agent、调研 worker、插件 agent 都是 AgentSpec 的不同配置/Adapter，不是不同生命周期实现。

#### 2.4.1 `AgentSpec` 字段

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---:|---|
| `spec_id` | `str` | 是 | 稳定标识；如 `video.material.v1`，不可用显示名称作权限判断 |
| `version` | `str` | 是 | 契约与 prompt 版本，写入 trace 和结果 |
| `display_name` | `str` | 是 | UI 展示名 |
| `purpose` | `str` | 是 | 目标能力说明，不得包含动态秘密 |
| `input_schema` | JSON Schema | 是 | 显式输入，验证后形成运行快照 |
| `output_schema` | JSON Schema | 是 | 结构化结果；通用 envelope + domain payload |
| `tool_policy` | `ToolPolicy` | 是 | allow/deny tiers/tool ids、scope、cost cap、审批档位 |
| `adapter` | `str` | 是 | 注册的实现适配器 id，如 `agent.structure`、`llm.worker`；不是 prompt 中可控的 import path |
| `model_route` | `ModelRouteSpec` | 否 | provider/model/role/preference；只能通过白名单解析 |
| `limits` | `RunLimits` | 是 | timeout、turn/tool、token/cost、output size、concurrency |
| `max_depth` | `int` | 是 | spec 可声明上限，实际不得超过父 run 与全局策略最小值 |
| `retry_policy` | `RetryPolicy` | 是 | 最大次数、可重试类别、backoff、是否换路由；不得隐式重复副作用 |
| `review_policy` | `ReviewPolicy` | 是 | `none|on_dangerous|always|domain_gate`，审核由共用 ApprovalService 执行 |
| `capabilities` | `list[str]` | 是 | `accepts/produces` tags，仅用于验证/检索，不表示顺序指令 |
| `report_template` | `str` | 是 | 输出通用 markdown report 模板 id，runtime 负责落盘 |
| `enabled` | `bool` | 是 | 注册状态；禁用 spec 不得被模型工具面看到 |

#### 2.4.2 `AgentRun` 字段

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---:|---|
| `run_id` | `str` | 是 | 全局唯一，幂等创建时返回既有 run |
| `task_id` | `str` | 是 | 根任务归属 |
| `parent_run_id` | `str \| None` | 否 | root 为 null；子 run 建立 trace parent |
| `trace_id` | `str` | 是 | W3C trace id 或等价 128-bit id，全链路不变 |
| `span_id` | `str` | 是 | 本 run span；子工具/模型调用另建 child span |
| `spec_id/version` | `str` | 是 | spec 快照键；运行时持久化 schema 与 policy hash |
| `status` | enum | 是 | `queued/running/awaiting_approval/paused/succeeded/failed/cancelled/timed_out/refused` |
| `inputs` | object | 是 | 经 schema 验证后的显式 inputs；大型值只存 ArtifactRef |
| `input_artifacts` | `ArtifactRef[]` | 是 | 带 sha256、schema、bytes、workspace scope |
| `tool_policy_snapshot` | `ToolPolicySnapshot` | 是 | 创建时冻结授权范围；之后撤权可收紧，不可扩张 |
| `depth` | `int` | 是 | 由 parent + 1 推导，客户端不可信 |
| `attempt` | `int` | 是 | 同一逻辑 run 的尝试号；副作用必须按 idem key 保护 |
| `created_at/started_at/ended_at` | timestamp | 是 | UTC；统一后端时间来源 |
| `deadline_at` | timestamp | 否 | 相对 timeout 的绝对截止点，resume 不重置总墙钟 |
| `cancel_requested` | bool | 是 | 协作取消信号，adapter/tool 都需轮询/响应 |
| `usage` | `RunUsage` | 是 | model calls/tokens/cost/unknown cost/tool calls/agent runs/wall |
| `checkpoint_ref` | `str \| None` | 否 | transcript + tool-call cursor + memory cursor 的版本化快照 |
| `approval_id` | `str \| None` | 否 | await approval 时绑定唯一审批对象 |
| `failure` | `FailureReport \| None` | 否 | category/retryability/blocking point/evidence/attempts |
| `result_ref` | `str \| None` | 否 | `AgentResult` 持久化引用 |

#### 2.4.3 `AgentResult` 字段

| 字段 | 类型 | 必填 | 说明/约束 |
|---|---|---:|---|
| `run_id` | `str` | 是 | 结果归属 |
| `status` | 终态 enum | 是 | 与 AgentRun 终态一致 |
| `summary` | `str` | 是 | 给父 run 的短结论，字段有明确定长上限（建议 1000 字） |
| `data` | object | 是 | 通过 `output_schema` 验证的结构化领域输出 |
| `artifacts` | `ArtifactRef[]` | 是 | 不可变引用；每个引用含 id/path/hash/schema/size/preview |
| `report` | `ReportRef` | 是 | runtime 生成的 markdown 报告路径、seq、hash、章节索引 |
| `needs` | `Need[]` | 是 | 缺输入/外部决策项，`field/why/source_hint/required`；不自动补黑板值 |
| `degradations` | `Degradation[]` | 是 | 完成但质量折扣的记录 |
| `failure` | `FailureReport \| None` | 是 | 错误时必填；成功时 null |
| `usage` | `RunUsage` | 是 | 子 run 自身精确归因；父 run 汇总引用，不用全进程差值 |
| `model_route` | `ModelRouteActual` | 是 | 实际 provider/model/route id，不得只记请求值 |
| `tool_calls` | `ToolCallSummary[]` | 是 | call id、tool id、status、duration、cost、result ref |
| `started_at/ended_at` | timestamp | 是 | 可计算持续时间 |
| `quality` | `QualityMetrics \| None` | 是 | 领域评分/验证器输出，与模型主观自评区分 |

#### 2.4.4 示例 schema 形状（目标）

```python
class AgentRunCreate(BaseModel):
    spec_id: str
    inputs: dict[str, Any]
    input_artifacts: list[ArtifactRef] = []
    parent_run_id: str | None = None
    idempotency_key: str
    limits: RunLimits | None = None  # 只能更严格，不能高于 spec policy

class AgentResult(BaseModel):
    run_id: str
    status: RunTerminalStatus
    summary: str
    data: dict[str, Any]
    artifacts: list[ArtifactRef] = []
    report: ReportRef
    needs: list[Need] = []
    degradations: list[Degradation] = []
    failure: FailureReport | None = None
    usage: RunUsage
    model_route: ModelRouteActual
    tool_calls: list[ToolCallSummary] = []
    quality: QualityMetrics | None = None
```

#### 2.4.5 Agent adapter 迁移

- 核心 `StructureAgent`、`MaterialAgent` 等作为 `AgentAdapter` 注册到 AgentSpec Registry；保留既有 `XInput/XOutput` 类型和 `stage_inputs`，Adapter 只负责输入/输出转换，不再自行拥有生命周期、审核、run tracker、报告写盘或独立成本统计。
- 调研 worker 以 `adapter=llm.worker` 注册临时 spec，显式提供 query/context artifact refs 与只读 tool policy；不把自由 system prompt 当成其唯一输入契约。
- 插件 Agent 需声明 input/output schema、工具策略和版本，未声明的旧插件进入兼容适配器并告警；不允许借由 `AgentSpec.adapter` 任意动态 import。
- `delegate_agent(spec_id, inputs, artifact_refs, limits?)` 是 Kernel 可见的工具；schema 从 registry 自动生成受限枚举/描述，返回 `AgentResult`。具体阶段只是 `spec_id=video.material.v1`，不是 `delegate(stage=...)` 的特殊分支。

### 2.5 记忆体系

记忆必须按生命周期与读写责任拆成三层。三层都不能成为“把整个历史塞进 prompt”的别名；每次读取都要有 source、版本、相关性和裁剪记录。

#### 2.5.1 working memory

- 范围：当前 Task/AgentRun 的消息 transcript 摘要、最近工具结果、当前目标、未完成 needs、预算余量、已确认用户约束。
- 存储：`AgentRun` checkpoint；原始大结果只保留 `ArtifactRef`，消息上下文保存 preview 和 hash。
- 写入者：Kernel 在每个 model turn、tool result、人工注入、审批结果后写；AgentRun adapter 只能通过返回结果提交，不可直接改父 run working memory。
- 读取者：当前 Kernel model turn；子 AgentRun 只收到父 run 显式传入的 input/artifact refs 和最小 briefing。
- 过期：run 终止后不再作为实时上下文；压缩为 episodic 摘要，原始 transcript 按保留期归档。
- 保护：上下文 assembler 必须保留用户硬约束、失败阻塞点、artifact hash、timeline 关键数值；裁剪时按优先级而不是按字符串尾部粗暴截断。

#### 2.5.2 episodic memory

- 范围：TaskEvent、AgentRunEvent、每次报告、审批、失败分类、重试变化、模型路由、成本和最终结果。
- 存储：事件 append-only 存储 + reports/INDEX.md + run/result 记录；事件 seq 单调递增，Mongo 旁路失败不能阻塞运行。
- 写入者：统一 EventStore/runtime；工具和 Agent 不直接写事件数据库。报告由 runtime 根据结构化结果落盘。
- 读取者：当前任务回放、诊断页、恢复流程、评估采集器；主 agent 只通过 `read_report/list_reports/read_run_events` 工具按需取摘要。
- 过期：热事件按任务上限保留；完整 transcript/工具参数按租户保留期归档；报告是证据，不能覆盖改写。
- 价值：用于回答“当时看到什么、调用了什么、为什么失败”，也用于 golden 对比；不能自动当作 semantic knowledge 供所有任务使用。

#### 2.5.3 semantic memory

- 范围：跨任务可复用的已验证知识、插件/工具能力描述、素材/模板元数据、用户确认偏好、失败修复模式和 artifact embedding/index。
- 存储：版本化知识条目 + artifact metadata/index；每条包含 provenance、source run、confidence、created/updated/expiry、scope。
- 写入者：MemoryWriter 只接受显式批准的 TaskResult/用户确认/评估通过样本；普通模型输出默认不自动升级为长期知识。
- 读取者：ContextAssembler 根据 query、scope、任务类型和工具权限检索 top-k，并把引用写入 `context_loaded.sources`；不直接注入未授权 workspace 内容。
- 过期：TTL、版本替换、用户删除、来源 artifact 失效时失效；低 confidence 条目只作候选，不能覆盖当前显式输入。
- 评价：记录检索命中率、引用后任务成功率、错误知识命中率；错误知识必须能按 provenance 批量撤回。

#### 2.5.4 统一记忆接口

```python
class MemoryStore(Protocol):
    async def write(self, layer: Literal["working", "episodic", "semantic"],
                    item: MemoryItem, *, actor: str) -> MemoryRef: ...
    async def search(self, layer: Literal["episodic", "semantic"],
                     query: str, *, scope: str, limit: int) -> list[MemoryHit]: ...
    async def expire(self, *, now: datetime) -> int: ...
```

接口不允许 `get_all()`；检索必须有 scope、limit 和 trace id。`context_loader.load` 迁移为 `ContextAssembler.assemble` 的一个 adapter，保留 `sources/trimmed/bytes/budget` 返回形状，并增加 `memory_refs`、`fact_ids`、`assembler_version`。

### 2.6 评估与进化

#### 2.6.1 指标

| 指标 | 定义 | 采集位置 | 目标用途 |
|---|---|---|---|
| 任务成功率 | CompletionGate 通过的任务数 / 有效任务数 | TaskResult/EvaluationRecorder | 不能用模型自报完成替代 |
| 成片质量 | timeline validator、渲染可用性、字幕/音频/时长及人工评分的组合 | QualityMetrics | 识别“流程完成但片子变差” |
| 工具决策质量 | 有效 tool call / 总 call；失败后变化参数/工具/Agent 的比例 | ToolCallSummary | 判断是否自主探索 |
| 步数 | model turns、tool calls、AgentRun 数量 | RunUsage | 与旧 Plan 步数对比，避免只看成功率 |
| 成本 | model/tool/AgentRun/渲染成本，unknown 单列 | Trace usage ledger | 预算和版本回归 |
| 重规划率 | 非初始假设后新增/变更 tool action 的任务比例 | event projection | 判断是否真实适应结果，不追求固定序列 |
| 上下文保持 | 长任务关键事实在第 N 轮仍可检索/被引用 | context audit | 验证 ≥30 步不退化 |
| 人工接管率 | approval、needs_human、用户重试占比 | event stream | 发现策略门或工具契约缺陷 |

#### 2.6.2 Golden 基线

- 建立至少 12 个 golden 任务：知识区长片、鬼畜快剪、数码评测、Vlog 各 3 个；每个固定输入 workspace snapshot、Persona、category、素材清单和预算。
- 旧基线保存 `pipeline_v2`/当前 Task 两种结果的关键指标，不把旧步骤序列当目标。
- 每个任务至少三次运行，记录成功率、质量、model turns、tool calls、AgentRuns、成本、墙钟、失败类别和最终 artifact hash。
- 结果比较采用同一 validator 和人工盲评；随机性通过固定温度/seed（provider 支持时）与三次均值/离散度表达。
- golden fixture 不含真实秘密，不把网络即时结果当唯一真值；外部检索工具用录制回放或固定响应。

#### 2.6.3 失败闭环

EvaluationRecorder 从失败 trace 提取 context、响应、tool calls、结果、failure 和人工判定；按 schema 误用、输入漏传、无效重复、预算、领域质量、上下文遗失聚类。评审确认根因后，将候选 prompt/few-shot 版本写入 `prompt_registry`，由 offline replay 在 golden/失败集比较成功率、成本、重规划率；通过门禁后小比例 canary，失败可按 trace 回滚。未经评估和 provenance 审核的知识不进入 semantic memory。

### 2.7 可观测性与可回放

#### 2.7.1 Trace/span 结构

- `trace_id`：一个 Task 全程不变。
- `task_span`：Task 生命周期。
- `agent_run_span`：每个 AgentRun 一个 child span，记录 spec/version/depth/parent。
- `model_span`：每次 provider 请求，记录 prompt hash、messages hash、tools hash、provider/model、route、开始/结束、token、cost、响应状态。
- `tool_span`：每个 tool call，记录 call id、tool name/schema version、参数摘要/hash、tier、policy decision、approval id、结果摘要/ref、duration、cost。
- `memory_span`：每次装载/检索/写入，记录 layer、query hash、命中 ids、裁剪和耗时。
- `checkpoint_span`：checkpoint 版本、cursor、恢复来源、hash。

敏感输入不直接写日志：prompt/message 原文按配置加密或只保存受控 artifact ref；审计需要可回放时由授权后的 replay worker 解密读取。

#### 2.7.2 回放

- `ReplayBundle` 至少包含 TaskRequest、AgentSpec 快照、tool schema hash、context source refs、model response（脱敏/录制）、tool result、事件 seq、artifact hash 和版本。
- replay 模式不执行真实 costly/dangerous 工具，使用录制 result；write_local 用临时工作区；每次替代调用明确 `replayed=true`。
- 回放必须能重建每个 decision point：输入 context、模型响应、policy 决策、工具返回和下一轮消息。
- replay 失败时报告缺少的 bundle 成员，不默默改用当前工具结果。

### 2.8 前端目标

- `TaskStore` 不再把 Plan 当状态源；新增 `runStore`，按 `RunEvent` projection 展示模型轮次、工具调用、AgentRun、审批、报告、artifact 和终态。
- `CopilotTaskRunTab` 保留任务控制、ThinkingDisclosure、EventFeed、TaskArtifacts，但 `PlanPanel` 改为可选的 `InitialHypothesisPanel`；默认主视图是动作时间线。
- `stage` 与 `subagent` tab 收敛成 `agent-run` tab，payload 统一 `{taskId, runId}`；旧 localStorage kind 在 hydrate 时映射。
- `RunningSubagents` 从 supervisor 轮询迁至 Task SSE 的 `agent_run_start/update/end`；恢复连接时仅调用一次 `/api/task/{id}/runs` 快照。
- 事件类型 union 与后端 `EVENT_TYPES` 同步；未知事件显示原始可折叠卡而不是丢弃，保证新事件可观察。
- UI 明确显示 tool policy/approval、成本 unknown、artifact/report 引用和失败原因；不把“调用阶段”渲染成固定进度条来暗示线性管线。
- `/api/task/{id}/events/stream` 继续作为 SSE 入口；新增 `/api/task/{id}/runs/{run_id}`、`/approvals`、`replay` 查询，老 trace 兼容层只读一个版本周期。

---
## 3. 数据与事件契约

### 3.1 RunEvent 信封

```python
class RunEvent(BaseModel):
    seq: int
    event_id: str
    type: str
    ts: datetime
    trace_id: str
    task_id: str
    run_id: str | None = None
    parent_run_id: str | None = None
    span_id: str | None = None
    causation_id: str | None = None
    actor: Literal["user", "kernel", "model", "tool", "agent", "gateway", "system"]
    message: str = ""
    detail: dict[str, Any] = {}
    schema_version: int = 1
```

`causation_id` 用于回答“这个 tool_call 是哪次模型响应产生的”；`span_id` 用于耗时/成本归因；`run_id` 让主 run 与子 run 在同一条流中关联。旧 `TaskEvent` 可由 adapter 转换，不能继续出现不带 trace/run 关联的生产事件。

### 3.2 事件类型

| 事件 | 产生时机 | 必填 detail |
|---|---|---|
| `task_start` | Task 创建 | request summary、trace_id、limits |
| `context_loaded` | 每次模型轮次前 | assembler_version、sources、memory_refs、trimmed、bytes、fact_ids |
| `model_request` | 发起 provider 请求 | model/provider、prompt_hash、tools_hash、turn、token_budget |
| `model_response` | provider 返回 | response_id、tool_call_ids、content_preview、usage、cost、finish_reason |
| `thinking` | reasoning 增量/完成 | delta、done、model_span_id；没有 reasoning 不发伪造事件 |
| `tool_call` | call 开始 | call_id、name、args_hash/brief、tier、policy、span_id |
| `approval_request` | 需要人工/网关 | approval_id、call_id、risk、reasons、expires_at |
| `approval_result` | 审核完成 | approval_id、decision、actor、reason |
| `tool_result` | call 完成 | call_id、status、summary、output_refs、failure、duration、cost |
| `agent_run_start` | 创建 AgentRun | spec_id/version、inputs refs、depth、limits |
| `agent_run_end` | AgentRun 终止 | AgentResult ref、status、usage、failure |
| `report` | 结果报告落盘 | report ref、summary、bullets、artifact refs |
| `artifact` | artifact 创建/引用 | id、schema、sha256、bytes、preview |
| `memory_read/write` | 记忆操作 | layer、refs、query_hash、provenance |
| `checkpoint` | checkpoint 保存 | version、cursor、path/ref、hash |
| `narration` | 模型/Kernel 对用户说明 | text、cause、next_intent |
| `gate_hit` | 限额/完成闸 | gate、usage、unfinished |
| `task_end` | Task 终态 | status、finish、usage、result_ref |

事件白名单在 `schema/task.py` 或新 `schema/run.py` 中维护；增加事件必须同步后端 schema、SSE adapter、前端 union、projection 和 replay parser。

### 3.3 ToolCallResult

```python
class ToolCallRecord(BaseModel):
    call_id: str
    tool_name: str
    schema_version: str
    args_hash: str
    args_preview: dict[str, Any]
    tier: PermissionTier
    policy_decision: str
    approval_id: str | None = None
    status: Literal["started", "ok", "error", "refused", "awaiting_approval", "cancelled"]
    output_ref: ArtifactRef | None = None
    output_preview: Any = None
    failure: FailureReport | None = None
    duration_ms: float = 0
    usage: RunUsage = RunUsage()
```

结果必须有可机器判断的 status，不能只返回截断文本；大输出写 artifact，模型下一轮拿 preview/ref，需要细节时显式 `read_artifact`。

### 3.4 输入与 artifact 规则

- AgentRun 输入必须在创建事件显式列出；运行时不得从全局黑板自动补齐必需字段。
- `ArtifactRef` 至少含 `artifact_id/path/sha256/bytes/schema/preview/created_by_run_id`，path 经 workspace 校验；大对象只给 ref。
- 黑板只保存领域真值索引和 owner；`delegate_agent` 只接受父模型显式 refs，禁止 `assemble(board)` 隐式注入。
- timeline 的 edit→animation→audio 单向规则由 validator/blackboard policy 强制；模型不能用任意 `by` 绕过。
- 报告保留四节格式，并增加 front matter：trace/run/spec/attempt/cost/model/artifacts。

### 3.5 状态与失败

```text
queued → running → succeeded|failed|cancelled|timed_out
                 ↘ awaiting_approval → running|refused|cancelled
                 ↘ paused → running|cancelled
```

状态变更只能由 RunRuntime 产生事件；重启按 checkpoint + append-only events 恢复，不能根据 UI 当前 plan 猜测。沿用 `FailureReport`（`schema/agent.py:72-88`），增加 `last_call_id`、`changed_since_previous_attempt`、`recovery_options` 和 `policy`。第二次相同工具/参数失败后必须改变 tool/args/spec/model 或收尾。

---
## 4. 拆除清单

### 4.1 必须删除或迁出的执行对象

| 对象 | 删除/保留 | 改成什么形状 | 验收 |
|---|---|---|---|
| `services/pipeline_v2.py` | 删除生产执行模块；历史读取另迁 | `timeline_finalize`、`agent_policy`、`task_results`、`telemetry` 等中立模块 | `clipwright/` 无生产 import；历史只读 fixture 可读 |
| `api/pipeline.py` 执行路由 | 删除 `run-async/run-batch/retry/cancel/result/status/diagnostics` 执行语义 | Task/Run API；保留一个版本周期的历史只读查询或迁到 `api/task.py` | 新任务不产生 pipeline_id；执行路由返回 410/不存在 |
| `services/pipeline_capability.py` | 删除 | `task_results.py` 提供结果快照；Kernel 无 `run_pipeline` 分支 | capability matrix、surface、旧 checkpoint 不再生成 run_pipeline |
| `capability_matrix.META_CAPABILITIES` 的 `run_pipeline` | 删除 | `delegate_agent`、`get_agent_run`、`list_agent_runs` 为 registry-backed tools | tool schema 中无 run_pipeline |
| `stage_capability.py` | 删除能力名解释层 | `AgentRunToolAdapter`：按 schema 调 AgentRunRuntime，不按 `delegate/run_stage` 分支 | 无 `_DISPATCH_CAPS/_QUERY_CAPS` 字符串解释 |
| `stage_subagent.py` | 拆成 adapter + runtime | 保留输入转换、artifact/report 适配；删除 `_RUNS/_IDEM` 独立生命周期 | 阶段和 worker 都创建 AgentRun |
| `supervisor/spawn.py` | 拆成 `agent_run_runtime.py` 和 `approval_service.py` | 保留 with_tools 适配、worker tools 策略；删除专属 tracker/registry 双账 | worker 不再有另一套结果/成本/审核协议 |
| `default_executor` | 删除 capability 字符串 if/elif | `ToolDispatcher` 只按注册 tool name/schema route；AgentRun 作为一个注册 tool | Kernel 没有 stage/pipeline/subagent 字符串分支 |
| `_run_step` | 删除计划表动作执行 | `run_loop` 消费模型 response.tool_calls，逐 call gate/dispatch/result | 删除后 A 仍能完成真实任务 |
| `PlanStep` 执行状态 | 降级/迁移 | `InitialHypothesis` 只读建议；`ToolCallRecord` 是事实 | 无 `next_step()` 生产调用 |
| `_after_step/_decide/_reflect` | 合并 | 每轮 model response 后继续 loop；规则只作 policy/completion gate | 无第二套 continue/replan/finish JSON 决策循环 |
| `_PLAN_SCHEMA/_DECIDE_SCHEMA` | 删除执行用途 | 保留可选 `InitialHypothesis` / `TaskFinish` schema；行动 schema 来自 tools | 主模型请求有 tools，不依赖 steps 数组 |
| `run_stage`/`delegate` 历史 alias | 兼容读取后删除生产生成 | checkpoint migrator 只将旧 step 转为 migration note；新系统使用 `delegate_agent` | 新事件/代码不生成旧名称 |

### 4.2 旧职责的迁移顺序

1. 先把 `last_result` 搬到 `services/task_results.py`，改 `api/task.py`、`golden_capture.py`、旧只读 API 的 import。
2. 把最终时间线处理搬到 `services/timeline_finalize.py`，写确定性 contract tests。
3. 把预算、熔断、模型记账和诊断策略分别接到 AgentRun runtime/telemetry，不从 `pipeline_v2` 复制一份新 orchestrator。
4. 把 `stage_inputs.assemble` 改为 Adapter 专用 `validate_and_decode(spec, inputs, refs)`，禁止读取 blackboard。
5. 把 `stage_merge.merge` 改为 artifact/result writer；只有领域 validator 能更新 timeline pointer。
6. 把阶段与 worker 的 tracker、registry、review 合并到 AgentRunStore/ApprovalService。
7. 切换 Task API 与前端为 RunEvent 后，再删除 pipeline 执行路由。
8. 最后删除 `pipeline_v2.py` 和直测旧 orchestrator 的测试文件；保留行为断言的新测试。

### 4.3 “删完还有没有计划表驱动痕迹”机械检查

以下命令必须在 M3 结束时满足：

```text
grep -R "next_step()" clipwright/                          # 0 个生产调用

grep -R "default_executor\|_run_step\|_after_step" clipwright/services/agent_kernel.py  # 0

grep -R "run_pipeline\|PipelineOrchestratorV2\|pipeline_capability" clipwright/       # 0（历史迁移脚本除外）
grep -R "stage_capability\|_DISPATCH_CAPS\|_QUERY_CAPS" clipwright/                  # 0

grep -R "if step.capability\|elif name ==.*structure" clipwright/services/             # 0 个主执行分支
```

代码审查还需人工确认：任何“把模型输出转成 list 再 for 执行”的新实现都算回归，即便名称改为 action、operation 或 task。

### 4.4 保留的确定性模块

不得因追求自由编排而删除：

- Timeline schema/validator、clip 时间合法性、轨道覆盖和 edit→animation→audio 版本单向规则；
- render queue/ffmpeg 子进程管理、输出路径和工作区安全校验；
- budget reserve/settle、unknown cost 计数、model route 白名单；
- dangerous ExecutionGateway、风险判定、人工审批、审计；
- artifact hash/大小限制/写入原子性、报告 append-only；
- checkpoint 版本迁移、取消 token、任务 ownership/authz；
- CompletionGate 和最终质量/必需产物检查。

这些是系统不变量，不是编排顺序。模型可以选择调用什么、何时调用、是否换工具；不能选择非法时间线、越权写盘、跳过预算或伪造完成。

---
## 5. 迁移分期

所有阶段结束条件都是：后端 4461 passed、前端 983 passed、该期新增契约/集成用例全绿；若基线因并行变更变化，必须在期初锁定新基线并记录，不得以“剩余失败与本期无关”结束。

### 5.1 M0 前置：契约、基线与影子记录（预计 3–4 人日）

**改动面**

- 新增 `schema/run.py`：AgentSpec/AgentRun/AgentResult/RunEvent/RunUsage/ArtifactRef/ToolCallRecord。
- 新增 AgentSpec registry、AgentRunStore、EventStore adapter、ApprovalService 接口；先由旧 stage/worker adapter 写双记录，不改执行行为。
- `surface.schemas` 增加 schema hash/metadata；`model_router` 增加 route actual 记录。
- `task_events` 扩展 trace/run/span/cause 字段，前端 types/projection 兼容未知 detail。
- 建立 golden fixture、录制 provider/tool result 和旧基线指标。
- 抽取 timeline_finalize、task_results 的接口并为旧实现加 adapter。

**验收**

- 旧路径全量绿；同一任务可生成完整 trace bundle，不改变结果。
- 阶段和调研 worker 均能被 adapter 描述为 AgentSpec，并看到相同 AgentResult envelope 的影子数据。
- `context_loaded` 可关联 trace_id/run_id；模型调用有 prompt/model/token/cost 字段，缺成本标 unknown。

**回滚**

- 仅关闭影子记录开关并恢复旧 event serializer；中立模块保留但不在生产 import。

### 5.2 M1 Kernel tool loop：可开关/影子（预计 7–9 人日）

**改动面**

- 新增 `AgentKernel.run_loop` 与 `LLMToolDriver`；模型请求真正传 `surface.schemas(...)` 到 `LLMService.with_tools`/统一 provider turn API。
- 新增 ToolDispatcher、PolicyGate、CompletionGate、budget reserve/settle、call-level checkpoint。
- `Plan` 改为可选 initial hypothesis；新增 `kernel_mode=plan|tool_loop|shadow`。shadow 运行 tool loop 但不执行真实副作用，使用录制结果并比较决策。
- delegate_agent 先包裹现有 stage_subagent，结果转换为 AgentResult；调研 worker 先包裹 spawn。
- 保留旧 `_run_step` 作为 plan mode adapter，明确不进入 tool_loop。
- 前端 TaskRunTab 增加 model turn/tool call/approval cards，不删除 PlanPanel。

**验收**

- provider 请求日志确认每轮 `tools` 非空，工具结果按 provider 原生协议回填。
- tool loop 能完成只读任务、显式 delegate 任务和一个录制真实选题；无 Plan 也能跑通。
- shadow 的 tool call 序列、成本、最终结果可与 plan mode 对比，失败原因可定位到 span。
- 取消、审批等待、预算闸和 checkpoint 在模型请求中间也能恢复。

**回滚**

- `kernel_mode=plan` 回到旧计划 adapter；保留 shadow 数据，停止真实 tool_loop 副作用。

### 5.3 M2 统一 AgentRun（预计 7–9 人日）

**改动面**

- 实现 AgentRunRuntime、AgentRunStore、统一 retry/cancel/timeout/review/usage/trace/report/artifact。
- 阶段 Agent 通过 `AgentAdapter` 注册 spec；调研 worker 通过 `llm.worker` spec；插件接入 schema 验证。
- `stage_capability`、`subagent_capability` 改成薄工具 adapter，全部返回 AgentResult。
- 阶段成本从进程快照差值改为每个 model/tool span usage；父 run 汇总子 run 引用。
- 统一事件为 `agent_run_start/update/end`；旧 `step_*` 只在兼容 projection 中产生。
- 统一深度、并发、工具策略、审核门；子 AgentRun 通过显式 artifact refs 传递输入。

**验收**

- 同一 input/output schema、同一事件顺序、同一审核服务、同一取消/超时/重试和报告路径覆盖 stage 与 research worker。
- 失败后主模型可以 `delegate_agent` 换 spec、换参数、换 tool policy，而不是被 stage 字符串强制同参 retry。
- 任意 AgentResult 都能被父 run 通过 result ref 和报告工具读取，且大产物不进入 prompt。
- 旧阶段结果可通过 migrator 恢复，不丢 artifact/report/cost。

**回滚**

- `agent_run_channel=legacy` 只切 adapter 实现，不恢复第二套新契约；M0 的影子事件保留用于诊断。

### 5.4 M3 删除编排残留（预计 5–7 人日）

**改动面**

- Task API 成为唯一新执行入口；pipeline 执行路由 410/删除，历史查询迁只读。
- 删除 `pipeline_capability` 执行分支、`stage_capability` 解释层、`default_executor` 字符串分支、`_run_step`/plan action loop。
- 删除/迁出 `pipeline_v2.py` 残余职责，更新 requirements 直调、golden capture、管理页和前端 API。
- TaskStore/runStore 改成事件 projection；Copilot 统一 AgentRun tabs；`RunningSubagents` 停止 pipeline endpoint 轮询。
- 删除旧计划执行测试，迁移有效断言到 kernel/run/timeline tests；更新文档和 import 图检查。

**验收**

- §4.3 机械 grep 全部通过；运行时无 pipeline 执行 import。
- A–D 以及 M1/M2 的 tool loop/统一契约回归全部绿；老 Mongo pipeline 数据仍只读可查。
- 前端不产生 pipelineId 业务依赖；task SSE 能渲染 tool/agent/approval/report/artifact。

**回滚**

- M3 前必须有可用 tag/备份；如需回退，只回退路由和 adapter import，不把旧执行入口重新暴露给生产默认配置。

### 5.5 M4 记忆与评估（预计 5–7 人日）

**改动面**

- MemoryStore 三层、semantic retrieval、provenance/TTL/撤回；ContextAssembler 记录检索证据。
- EvaluationRecorder、golden runner、replay bundle、指标面板和 prompt/few-shot candidate registry。
- 长任务压缩、关键事实 pin、重要 artifact 及报告引用保留策略。
- 前端补成本/步数/重规划/上下文来源/回放诊断视图。

**验收**

- ≥30 步任务通过关键事实保持测试；context_loaded 记录检索和裁剪。
- 每个 golden 选题至少 3 次前后对比；生成成功率/质量/步数/成本/重规划率报告。
- 失败样本能从 trace replay，候选 prompt 经过离线门禁后才能进入 canary。

**回滚**

- 关闭 semantic read/write 和 prompt candidate；working/episodic/trace 仍为运行必需，不能删除事件证据。

---
## 6. 验收标准

以下测试必须能证伪目标；“日志看起来像 agent”不算通过。每条都要有自动化测试或可复现录制样本。

### A：无 Plan 也能完成

- 场景：移除/置空 `InitialHypothesis`，给一个真实选题和固定 workspace；模型第一次请求只收到 tools/context，不收到步骤列表。
- 判据：任务至少完成 structure→material→edit 的真实垂直切片，CompletionGate 通过，产生 artifact/report；provider 请求中没有 `steps` 行动数组。
- 反例：任何代码在执行前调用 `Plan.next_step()`、fallback 生成固定六步或按数组 for-loop，则失败。

### B：失败后改变行动

- 场景：第一次工具返回 `tool_failed`，第二次同一目标要求模型可换工具、换参数或换 AgentSpec；录制 provider 强制给出改变后的 call。
- 判据：第二次 `ToolCallRecord` 至少一项 `tool_name/args/spec_id/model_route` 改变；若仍同参，必须经过策略说明并在第二次拒绝无限 retry。
- 反例：只把 `attempt += 1` 后重复同一 `PlanStep`，即失败。

### C：两类子 agent 同一契约

- 场景：分别启动 video.material AgentRun 与 research/read-only AgentRun。
- 判据：创建/状态/取消/超时/审核/结果/报告/成本/trace 通过同一 schema、同一 EventStore、同一 ReviewService；只有 adapter/spec/tool policy 有差异。
- 自动断言：同一 `AgentRunStore.get`、同一 `AgentResult.model_validate`、同一事件类型集合可处理两者。

### D：决策可解释可关联

- 场景：至少 5 个 model turn，包含一次失败和一次 delegate。
- 判据：每次 action 都能用 `trace_id + causation_id + span_id` 找到 `context_loaded`、thinking（若 provider 提供）和 tool_call/result；context detail 包含 sources/trimmed/budget，thinking 不伪造。
- 反例：只在前端显示一段无 step/call 关联的“正在思考”，或只记录 `tool_call` 事件名，不通过。

### E：长任务上下文不退化

- 场景：录制或真实执行 ≥30 个 tool/model turns，期间产生 ≥10 个 artifact、≥5 个失败/恢复、≥3 个用户硬约束。
- 判据：第 30 步仍能回答并自动断言所有 pin facts、最终 artifact ref、预算余量和未完成 needs；原始大对象不进入 prompt，压缩有 sources/trimmed 记录。
- 反例：依赖最近 12 条字符串而丢失硬约束、只给“更早 N 步已压缩”无可检索 episodic/semantic ref。

### F：成本与步数可比较

- 场景：同一 golden 选题，改造前后各跑 3 次，provider、输入、预算、温度固定。
- 判据：报告列出 success、quality、model_turns、tool_calls、agent_runs、wall_sec、input/output tokens、known/unknown cost、replan rate，并能打开每次 run trace。
- 统计：给出均值和范围；不能只挑最好一次。若质量下降超过预设门槛或成本/墙钟超过预算阈值，M1/M2 不准切生产。

### G：确定性领域闸

- Timeline 非法时间、轨道冲突、缺 final artifact、预算超限、dangerous 未审批、workspace 越界都必须阻止完成或调用；模型不能通过文本说服 CompletionGate。

### H：恢复与取消

- 在模型请求、工具执行、子 AgentRun、审批等待四种位置分别 pause/cancel；3 秒内状态可观测，in-flight adapter/ffmpeg 收到 cancellation token，无孤儿进程。
- 重启后从 checkpoint/replay cursor 继续，不重做已结算副作用；幂等 key 与 artifact hash 断言通过。

### I：前端事实源

- 断开 SSE 后按 seq 重连不重复事件；RunStore 由事件折叠出状态，不依赖 plan steps。
- 同一 run 的模型轮次、工具、agent、报告、审批均可展开；旧 pipeline history 只能只读显示。

### J：删除证明

- M3 运行 §4.3 grep、静态 import graph 和启动 smoke test；代码、文档、测试命名可保留历史 fixture 标记，但生产 import/路由必须为零。

---
## 7. 风险与不做

### 7.1 为什么完全自由编排会在视频任务中退化

视频制作不是任意工具集合：结构产出脚本和场景，素材依赖场景，剪辑依赖素材，动画/音频会修改时间线，质检依赖最终时间线。若把这些依赖完全交给模型记忆，常见退化包括：

- 先剪辑后找素材，模型用占位输入继续推进，错误在渲染时才暴露；
- animation/audio 重新覆盖 timeline，字幕、配音或 BGM 丢失；
- quality 报问题后只重试同一参数，或者只重做 edit 而不连带下游；
- 长任务中上下文被大文件和报告挤出，模型忘记用户硬约束；
- costly 工具重复调用，成本闸只在父 Task 统计而看不到子 run；
- “模型说完成”被误当成“有可渲染成片”。

因此目标不是无约束自由，而是“模型选择动作 + 确定性契约阻止非法动作”。工具的 accepts/produces/dependencies 可作为 schema 描述和输入提示，不能被编译成固定执行顺序；缺输入由工具返回 needs，模型自行选择补输入、换工具、改参数或询问用户。

### 7.2 必须保留确定性的地方

- **渲染与时间线合法性**：Pydantic/validator/ffmpeg 负责可执行性；模型不能直接写任意 final timeline 绕过检查。
- **预算闸**：调用前 reserve，调用后 settle；unknown cost 显式暴露；不可由模型自报金额。
- **dangerous 执行网关**：普通 surface 不暴露 dangerous schema；风险、审批、沙箱、审计统一处理。
- **artifact/report 证据**：hash、append-only、workspace path 和引用一致性由 runtime 保证。
- **权限与深度**：RunSpec 的 tool policy、task owner、max depth、并发和取消由 Kernel/Runtime 计算。
- **完成条件**：质量 validator、必需 artifact、用户硬约束和渲染状态是 CompletionGate 的确定性输入。

### 7.3 不做与风险监控

- 不做无限递归派生；默认深度 1、最多 2，每层独立预算；不做跨 Task CPU/GPU 抢占。
- 不把 thinking 当作审计真相；不自动把全部 episodic 写进 semantic；长期知识需 provenance、批准和撤回能力。
- 不重写渲染引擎/时间线模型；不以删文件代替执行路径验收；M1 不同时改 prompt、模型、工具集和 UI 以免指标不可归因。
- 隐藏编排：tool call 转步骤数组即由 A/J 阻断；schema 过大：按 policy 动态裁剪并看无效 call 率。
- 阶段退化：domain contract、artifact/hash、golden 质量门；时间线错误：validator/owner/version gate。
- 成本失真：parent-child usage 对账且 unknown 不记 0；上下文遗失：pin facts + episodic retrieval + E 测试。
- 取消孤儿进程：子进程 registry/3 秒测试；前端双事实源：M3 后 RunEvent 唯一。

---
## 8. 两周优先级

只有两周时，选择两项垂直切片，不铺开全量删除。

### 优先级一：Kernel 原生 tool-use loop + 统一 trace（约 6–7 人日）

**改动面**

- `agent_kernel.py` 新增 tool-loop mode，先不删旧 plan mode；把 `surface.schemas()` 传给 `LLMService.with_tools`，实现 call/result、context_loaded、thinking、tool_call/result、checkpoint 和预算/取消。
- `tool/surface.py` 增加 call id、trace/span、policy metadata；`llm.py` 抽回调而非新写第三套 provider 协议；`task_events/schema/task.py` 扩充 run 关联字段。
- 以 `read_file/list_reports/read_artifact` 和一个低风险写工作区工具作为最小 surface，先实现一个真实选题的 structure 或 research 垂直切片。
- 前端 `CopilotTaskRunTab` 增加 tool-call timeline，保留现有计划显示作为兼容信息。

**从不能到能**

- 从“主模型只能返回 steps，Kernel 按数组执行”变成“模型在真实请求中获得 tools，能根据结果继续选择动作”；即使计划为空也可完成一个小任务。
- 每次决定从无法复盘变成可以按 `context_loaded → thinking → tool_call/result` 定位。

**验收**

- A、D、H 的最小测试绿；provider 请求抓包/录制确认 tools 非空；同一任务失败后模型至少换一次参数或工具。

### 优先级二：阶段/调研统一 AgentRun vertical slice（约 5–6 人日）

**改动面**

- 新增 `schema/run.py`、`agent_run_runtime.py`、`agent_spec_registry.py` 的最小实现。
- 把现有 `stage_subagent.run_stage` 和 `supervisor.spawn_subagent` 包装为两个 adapter；共用 AgentRunStore、AgentResult、report/artifact writer、usage/trace/cancel。
- `delegate_agent` 和 `research_agent` 两个 tool schema 都进入 Kernel surface；保持阶段领域输入转换，但禁止 runtime 读黑板隐式补输入。
- 前端暂时把两类结果都映射为同一 AgentRun card，不做完整 tab 重构。

**从不能到能**

- 从“阶段子 agent 与调研 worker 各有状态/审核/结果协议”变成“父模型可用同一个工具和同一 Result 形状选择两种 AgentSpec”。
- 失败后能真正换 AgentSpec/工具策略，成本和审批进入同一个 trace，而不是父 Task 账本盲区。

**验收**

- C、B 的录制测试绿；同一 `AgentResult` schema 同时验证 stage/research；取消、timeout、approval 和 report 路径相同。

### 两周内明确不做

- 不在两周内物理删除 `pipeline_v2.py`；先完成 M0/M1/M2 的垂直证据。
- 不在两周内上线 semantic memory 自动写入；先记录 episodic/trace。
- 不同时重写所有 49+ 工具 schema；从最小高价值工具面开始，并用 coverage 指标扩展。

---
## 9. 附录：关键文件清单

### 9.1 后端现状与首批改动

| 文件 | 当前职责/证据 | 目标动作 |
|---|---|---|
| `clipwright/services/agent_kernel.py` | plan/run loop、executor、decide、checkpoint、cancel，尤其 `:602-681`、`:812-943` | 新 tool loop；Plan 降级；拆 runtime/limits/checkpoint |
| `clipwright/schema/task.py` | Task/Plan/PlanStep/TaskEvent/limits/usage，`:58-195` | TaskRun/RunEvent 兼容字段；InitialHypothesis/RunUsage |
| `clipwright/schema/agent.py` | FailureReport/AgentContext/各阶段输入输出，`:72-152`、`:155-376` | FailureReport 扩展；阶段 schema 由 AgentSpec adapter 引用 |
| `clipwright/schema/stage.py` | StageSpec/StageResult，`:32-84` | 迁为 AgentSpec/AgentResult adapter，不再独立生命周期 |
| `clipwright/services/llm.py` | `with_tools` 多轮协议，`:641-784` | 抽 `ToolTurnDriver`，加 trace/cancel/usage callbacks |
| `clipwright/services/model_router.py` | 白名单、route、`use_route`，`:65-107`、`:237-257` | AgentSpec route snapshot 与 actual attribution |
| `clipwright/tool/surface.py` | schema、tiers、execute、幂等/审计，`:107-205`、`:345-457` | policy-aware schemas、call id、gateway continuation |
| `clipwright/services/capability_matrix.py` | tool tier 与 META_CAPABILITIES，`:32-50`、`:146-246` | 删除 pipeline/stage meta；注册 delegate_agent/run query |
| `stage_inputs.py` / `stage_merge.py` | 输入装配与 timeline/白名单合并，`:17-195`、`:20-114` | 变成显式 decoder/domain writer，保留 validator |
| `stage_subagent.py` / `stage_capability.py` | 固定 stage 生命周期与能力名解释，`:271-500`、`:22-141` | Adapter + 通用 AgentRun tool，删除独立 runtime |
| `subagent_capability.py` / `supervisor/spawn.py` | worker 工具、审核、循环、tracker，`:29-209`、`:49-267` | 共用 ApprovalService/LLM adapter/runtime |
| `context_loader.py` / `stage_report.py` | 上下文裁剪、append-only 报告，`:1-148`、`:1-9,108-174` | ContextAssembler/ReportWriter，接 memory、保留四节 |
| `tool/reports.py` / `tool/artifacts.py` | 报告与 artifact 读写，`:1-229`、`:1-192` | 保留工具，扩 ReportRef/provenance |
| `task_blackboard.py` / `task_events.py` | timeline owner、append-only 事件，`:24-135`、`:1-148` | 领域缓存 + EventStore/RunEvent |
| `api/task.py` / `api/pipeline.py` | Task 与 pipeline 路由，`:115-333`、`:243-350` | Task 唯一执行；pipeline 仅历史只读 |
| `pipeline_v2.py` / `pipeline_capability.py` | 2197 行老编排、run_pipeline/last_result | 迁职责后删除 |

### 9.2 前端现状与首批改动

| 文件 | 当前职责/证据 | 目标动作 |
|---|---|---|
| `src/stores/taskStore.ts` | plan/step event projection，`:49-97,134-162` | run event projection；Plan 仅 hypothesis |
| `src/stores/copilotStore.ts` | stage/subagent tabs，`:14-35`、`:100-159` | 统一 agent-run tab，迁移旧 localStorage |
| `src/features/task/useTaskEvents.ts` | Task SSE 订阅，`:19-46` | RunEvent 解析/未知事件保留 |
| `src/features/copilot/CopilotTaskRunTab.tsx` | PlanPanel、阶段行、事件、子 agent，`:17-103` | action timeline、AgentRun cards、approval |
| `src/features/copilot/RunningSubagents.tsx` | pipeline endpoint + 3 秒轮询，`:1-46` | SSE run events + 快照恢复 |
| `src/features/copilot/CopilotDock.tsx` | requirements/task-run/subagent/stage tabs，`:33-73` | 统一 AgentRun detail |
| `src/features/copilot/ThinkingDisclosure.tsx` | thinking 展开 | 保留，按 span/turn 分组 |
| `src/features/task/EventFeed.tsx` | 任务事件渲染（由调用方使用） | 增 model/tool/agent/approval/report cards |

### 9.3 测试与交付

```text
cd J:\Clipwright && python -m pytest tests -q
cd J:\Clipweight-Client && npx tsc --noEmit && npm run test -- --run
# M3：grep PipelineOrchestratorV2|pipeline_capability|run_pipeline|next_step|_run_step
python -m clipwright.services.replay --bundle <bundle.json> --dry-run
```

- [ ] M0 schema/registry/trace/golden；M1 tools 原生传递、Plan 可去除，A/D/H 通过。
- [ ] M2 stage/research 同 AgentRun，B/C 通过；M3 无计划表执行和 pipeline 生产 import，J 通过。
- [ ] M4 三层 memory、golden 三次对比、replay/few-shot，E/F 通过。
- [ ] 每期后端 4461 passed、前端 983 passed（或重新锁定的新基线）全绿；所有目标项有代码、测试或 trace 证据。
