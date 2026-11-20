# 阶段 Agent 自主循环与交接协议 —— 对抗性评审报告

## 0 评审信息

| 项目 | 内容 |
|---|---|
| 评审对象·PRD | `docs/stage-agents-agentic-loop-prd-2026-11-20.md` · v0.2 · 784 行 |
| 评审对象·Plan | `docs/stage-agents-agentic-loop-coding-plan-2026-11-20.md` · v1.0 · 810 行 |
| 后端基线 | `J:\Clipwright` · `9d8b236a2de33b07b12b24ba3bd4f9be730ba7e6`（分支 `ref_zam_subagents_agentic_loop`，自查 `git rev-parse HEAD`） |
| 前端基线 | `J:\Clipweight-Client` · `06946c27fa917858693ebe1427e873c1c47d923d` |
| 评审角色 | 独立技术评审员（红队），未参与两份文档写作 |
| 评审范围 | ① 两份文档的事实断言（逐条打开文件核对）；② PRD §4/§5/§7/§9/§11 的契约与计划设计；③ Plan 28 个任务的覆盖度、依赖、规模、回滚、测试；④ 两文档互一致性；⑤ 双仓现状与目标的落差 |
| 方法 | 只读静态审查：`read` 全文 + `Select-String` 定点取行 + `git rev-parse`。**未运行任何测试、未启动服务、未 import 应用**。凡标“无法核实”的是我确实没读到的点，不是推测 |
| 事实核验规模 | 抽查 63 处 `file:line` 断言，逐条回原文；其中 41 条列入 §3（PRD 25 / Plan 16），其余用于支撑 §2 证据列与 §4/§5 的逐行核对 |
| 一句话结论 | **有条件可实施**：方向与现状审计基本可信，但目标契约有 2 处硬缺口（`brief` 字段、`dry_run` 第二映射点）、依赖图有 1 处环、唯一 Adapter 归属同名含混——这 4 条不先定，B01 派工后必然返工 |

## 1 总评

| 结论 | 判定 |
|---|---|
| 可实施性 | **有条件可实施** |
| 是否建议进入 B01 派工 | **建议，但须先完成 §8 的 F1–F4 四项文本修订**（均为文档内改动，不改技术方案）。F1/F2/F3 不修就派工，会导致 Schema 冻结返工与排期错序；F4 不修则 B03 与既有 `agent_run._run_stage` 同名打架 |
| 风险等级 | 中高：删改面横跨 schema / 编排 / 报告链 / 双仓前端，且 PRD 自我声明“不做旧数据兼容”，一次性切换没有中间态 |

**三条核心理由（之所以是“有条件”而非“可实施”）**

| # | 理由 | 关键证据 |
|---|---|---|
| 1 | **现状审计可信度高，可实施性不来自“方案对不对”，而来自“契约没闭口”**。PRD 对五个阶段“非现代 agent”的诊断逐条可核实（`with_tools` 单发、PV 自检 2/4 轮、quality 无决策循环、`_map_status` 吞 `dry_run`、`rounds=len(calls)+1`），这部分我未找到事实性错误。问题集中在它给出的*目标*契约：`AgentResult` 字段表与伪代码不一致，且漏掉现存字段 | §2 P0-01、P0-02；`schema/run.py:69`（`brief`）、`schema/run.py:24-25`（`RunStatus` 无 `dry_run`） |
| 2 | **Plan 的任务切片质量高于其契约表质量**。28 项拆分、S/M/L 与 26 人日算术自洽（10×L+18×M），逐阶段 Gate 矩阵可执行；但 §2「前后端契约变更总表」的 producer/consumer 列有多处把“目标”写成“现状”，且个别行号指错函数。契约表若被当作派工依据，会把改造点指到错的地方 | §2 P1-06、P1-05、P1-09；`agent_kernel.py:548` 实为 `_recover_upstream_needs`，被当作 8 个契约点的通用消费者 |
| 3 | **删改面与“同版切换”承诺之间缺一条可达路径**。PRD 明令不支持双写、不做历史兼容，同时又要求“双仓同批”，但前端受影响面在 Plan 里没有全部落到任务（`TaskRunDetail` 新字段无任务明确负责新增），而 `next_for/preview/headline` 的消费点分布在 2 个仓 6 个文件以上 | §2 P1-09、P1-08；`CopilotDocsTab.tsx:110`、`CopilotStageTab.tsx:52`、`context_loader.py:374`、`stage_report.py:216-233`、`agent_kernel.py:2377-2381` |

### 1.1 改动面分布（用于判断“有条件”的代价）

| 受影响面 | 后端落点 | 前端落点 | 变更性质 | 不可逆性 |
|---|---|---|---|---|
| 结果契约 | `schema/{run,stage,agent}.py` | `types/task.ts`、`eventSchema.ts` | 删 `StageResult`/7 个 `XOutput` 返回签名，改语义 | **高**：wire 变更，无 v1 解码 |
| 执行入口 | `agent_run`、`stage_subagent`、`agent_loop`、`agent_kernel`、`tool/surface` | — | 收敛为唯一 `drive` + 唯一 Adapter | **高**：三个入口同时动 |
| 报告链 | `stage_report`、`context_loader`、`tool/reports`、`api/workspace`、`progress`、`session_projection` | `workspace.ts`、`CopilotDocsTab`、`CopilotStageTab`、`SessionTimeline` | 删四节解析与 `preview/next_for/headline` | 中：正文仍可读，索引语义重建 |
| 事件 | `agent_run._emit_run`、`task_events`、`agent_kernel` 5 处 `task_end` | `eventSchema`、`taskStore`、`EventFeed`、4 个 run 组件 | detail 扩字段 + 新增 `subagent_round` | 中 |
| 评估 | `evaluation`、`golden_eval`、`api/eval` | `eval.ts`、`EvalPanel` | 统计口径变更（capped 单列、0 成本保留） | 中：历史可比性丢失 |
| Agent 实现 | 7 个 agent + `animation/agentic*` | — | 循环重写（非搬移） | **高** |
| 提示词 | `prompts/*` | — | 搬移 + 新增 `stage_loop.py` | 低（逐字快照可回滚） |

结论：7 个面里 4 个“不可逆或高成本”，而两文档承诺的是**一次性切换、无中间态**。这个分布本身就要求更高的前置契约精度——恰好是当前最薄的一环。

### 1.2 评审局限（与 PRD 同口径自我声明）

- 未运行测试、未启动服务、未 `import` 应用；本文所有“状态”判断来自源码静态阅读与既有用例的文本。
- 未验证 provider 实际能力（`tool_result_style`/`vision_message_format` 的运行时行为）、生产环境开关取值、真实数据分布。
- 前端只做静态阅读，未跑 `tsc --noEmit` / `vitest`；前端行号取当前工作树，实施时会漂移。
- §2 的 P0 判定是“按文档实施时的高概率冲突”，不是实测崩坏结论；每条都给了可复核的 `file:line`，读者可自行推演。
- 工作树中这两份文档与 `personas/Zam_copy/` 等为未提交状态，我按工作树现状核对。

**不建议的做法**：不要按 Plan §4.2 的 W1–W6 直接开工。先把 §8 的 4 条文本修订 + 2 条契约裁决（`brief`、`dry_run`）落进 PRD/Plan，再冻结 B01。

## 2 结论性问题表

P0 = 会导致实施方向错误或双仓联调必炸；P1 = 实现前应修；P2 = 可后补。按严重度排序。

| ID | 严重度 | 类别 | 位置(文档:行) | 问题 | 证据(file:line) | 建议修法 |
|---|---|---|---|---|---|---|
| P0-01 | P0 | 契约 | PRD:218-229、244-280；Plan:94、96、105 | 目标 `AgentResult` 字段表**没有 `brief`**，而伪代码声明 `extra="forbid"`。现 `AgentResult.brief` 是重放与前端详情的数据源：`_IDEM` 复用后回填、前端 `run.brief` 直接渲染。按此表冻结 schema，重放语义与 RunDetail 一起断 | `clipwright/schema/run.py:69`（`brief: str = ""`）；`services/agent_run.py:477-478`（`result.brief = spec.brief`）；`J:\Clipweight-Client\src\features\task\TaskRunDetail.tsx:134`（`run?.brief`） | PRD §5.1 增一行 `brief`（必填键、可空串、语义=本次派发原始要求、重放快照来源）；Plan §2 增同一行；B01 的 `test_terminal_matrix` 加“带 brief / 空 brief”两例 |
| P0-02 | P0 | 契约 | PRD:221、242；Plan:95 | 文档只承认 `agent_run._map_status` 一处把 `dry_run` 变 `ok`。实际**第二处**在 `stage_subagent.run`：`dry_run` 进 `status:"ok"` 分支后对外返回 `status="ok"`，`output` 里才带 `dry_run`。只改 `_map_status` 不足以让 Kernel 看到 `dry_run` 终态。另：现 `RunStatus` 字面量根本没有 `dry_run`，只加字段不改枚举会 ValidationError | `services/stage_subagent.py:335-340`；`services/agent_run.py:688-696`（`"dry_run": "ok"` 在 695）；`clipwright/schema/run.py:24-25`（8 值，无 `dry_run`）；`services/stage_subagent.py:62`（`StageResult.status` 有 `dry_run`） | PRD §5.1 补一句“`dry_run` 的现有丢失点共 2 处（`_map_status`、`stage_subagent.run` 的 ok 分支）”；Plan B03 验收加断言“surface 与 `AgentResult` 两层的 `dry_run` 都不被折成 ok”；B01 扩 `RunStatus` 并删 `pending/running` 时同步 `_LIVE` 与 `list_runs` 的 `live` 标记 |
| P0-03 | P0 | 一致性 | Plan:139、485、517、533、570、626、648 | 依赖图有环/顺序冲突：§3.1「主路径」把 `X01` 排在 `F01/F02/F03/F04` **之后**（`…→F01/F02/F03/F04→X01→X02`），但 F01 依赖“B01、B09 的 fixture；X01”，F03 依赖 X01，F04 依赖 F03；同时 §4.2 表与甘特图把 X01 排 W1–W3（在 F 之前）。执行时按主路径做则 F01 无 fixture 可贴，按排期做则主路径不成立 | Plan:139（主路径）；Plan:485（F01 依赖 X01）；Plan:517（F03 依赖 X01）；Plan:570（X01“冻结后解锁 F01/F03”）；Plan:626/648（X01 在 W1） | 把 X01 拆成 X01a「fixture 起草（W1，随 B01）」与 X01b「双仓 fixture 联验（M0 冻结后）」；主路径改为 `B01→X01a→B02→…→F0x→X01b→X02`；甘特图同步 |
| P0-04 | P0 | 可行性 | PRD:669；Plan:180、188 | “唯一阶段领域 Adapter”命名自相矛盾：PRD 写 `stage_subagent._run_stage` 为唯一 Adapter，但仓内**已有** `stage_subagent.run_stage`（现执行入口）与 `agent_run._run_stage`（结果转换层，line 553）。Plan B03 又同时要改 `stage_subagent.py:281,428` 与 `agent_run.py:553,688`。三者同名不同义，实施时极易落成“两个入口”，而这正是两文档重复禁止的结果 | `services/stage_subagent.py:312`（`run`）、`:428`（`run_stage`）；`services/agent_run.py:553`（`_run_stage`）；文档 PRD:669、Plan:180 同段 | 先在 PRD §8 与 Plan B03 各加一句命名裁决：新 Adapter 定名 `stage_adapter.run_stage`（或明确“复用 `agent_run._run_stage` 并收窄为纯转发”），并写明 `stage_subagent.run`/`run_stage` 的最终去留（删除 or 窄转发），Plan B03 验收加“全仓只有一个 Adapter 符号”的静态断言 |
| P1-05 | P1 | 事实 | Plan:114 | `task_end.detail` 的 producer 写成 `agent_kernel.py:2119`。实测 2119 是 `if gate_reason == "dry_run_preview"` 分支，与 `task_end` 无关；`task_end` 共 **5 处** emit，形状各不相同（成功/失败/取消/另两条终态），逐处改造才是工作量主体 | `services/agent_kernel.py:928`、`:1122`、`:1393`、`:1653`、`:1690`（5 处 `emit(..., "task_end", ...)`）；`:2119`（dry_run 分支） | Plan §2 该行 producer 改为 `agent_kernel.py:928,1122,1393,1653,1690`；并在 B08 任务里列出“5 处 task_end 逐一补 completed/unfinished/terminal_reason/escrow”，验收加“取消路径的 task_end 与成功路径同形” |
| P1-06 | P1 | 契约 | Plan:95-105（8 行） | 「后端 consumer」列把 `agent_kernel.py:548` 当作 `status`、`outputs`、`writes/details`、`needs`、`issues`、`degradations`、`to_tool_payload` 等 8 个契约点的通用消费者。实测 548 是 `_recover_upstream_needs`（只补 needs），不是通用读取点。该列因此不可作为改造清单，会漏掉真正的消费者（如 `surface.py:846` 之后的 `to_tool_payload` 消费、`agent_kernel` 的 loop 结果解析） | `services/agent_kernel.py:548`（`async def _recover_upstream_needs`）；对照 `tool/surface.py:846`（`await agent_run.run(...)` 后才拿到结果） | §2 表「后端 consumer」逐行重查；无法定位就先写“待定位（B0x 内确认）”，不要复用同一行号；把“consumer 未核实”纳入 X01 的 fixture 联验前置 |
| P1-07 | P1 | 可行性 | PRD:433；Plan:404 | 计划把“覆盖前序程序化调用轨迹”归因于 `run_tool_loop` 开头的重置（`agents/base.py:486`），并让 B17“删除 `last_tool_calls` 重置”。删 486 **不会**解决问题：真正覆盖发生在 532/538/541，且落盘条目只有 `{"tool","input"}`——**没有 `status` 字段**。因此 `tool_failure_degradation` 用 `str(c.get("status") or "")` 得到 `""`，被当作非失败（`""` 在白名单里），失败率闸对这一段完全失明 | `agents/base.py:486`（重置）、`:503`（`{"tool","input"}`）、`:532/538/541`（覆盖）；`services/stage_subagent.py:192`（`not in ("success","ok","")`）；`services/stage_subagent.py:672`（`skill_calls + last_tool_calls`） | Plan B17 改为：在 541 处**合并**而非替换（`self.last_tool_calls = prev + calls`），并给 `calls` 补 `status/duration_ms/seq_in_run`；验收加“程序化 3 次 + 循环 2 次 → total=5 且失败率可算” |
| P1-08 | P1 | 漏项 | PRD:663（§7.4 静态扫描）、PRD:643-650（§7.3 清单） | 下游清单与静态扫描规则都只覆盖 `build_sections/_section_text/next_for/section`。漏掉两类：① `stage_report.list_reports` 自身用首个 `# ` 行产 `headline`（216-221），属同一条解析链；② `agent_kernel._preview_of` 读 `value["preview"]`（artifact ref 的 preview，与报告 `preview` 同名词不同义）。按现规则做静态门禁，既会漏 ① ②，又会在 `intent_apply.report.preview`、`/api/animation/preview` 上产生误报 | `services/stage_report.py:216-221`、`:228-233`；`services/agent_kernel.py:2377-2384`；误报面：`services/intent_apply.py:145`、`api/animation.py:177` | §7.4 静态规则改为“限定在 `services/stage_report.py`、`services/context_loader.py`、`tool/reports.py`、`api/workspace.py` 四文件的符号级引用”，并对 `headline`、`preview` 加“命名空间限定”说明；§7.3 增一行 consumer=`agent_kernel._preview_of`（保留语义，不删） |
| P1-09 | P1 | 一致性 | Plan:101、117、532；PRD:641 | 把目标字段写成“现有落点”：`TaskRunDetail.tsx:134` 实际读的是 `run?.brief`，而前端 `TaskRunDetail` 接口**没有** `report_seq/report_state/handoff_summary/cost_unknown/trace_state/tool_calls_total/schema_version/terminal_reason`。Plan 把这些字段的“前端 consumer”指到不存在的字段上，F01–F05 的验收标准里也没有“新增这些 DTO 字段”的明确任务归属 | `J:\Clipweight-Client\src\types\task.ts:345-370`（字段清单，缺上述键）；`:303-318`（Summary 有 `report_path`，无 `report_seq`）；`src\features\task\TaskRunDetail.tsx:134` | Plan §2 增列“前端需新增字段”标记（`target-only`）；F04 任务描述补“`types/task.ts` 新增 8 个 nullable 字段 + 四个状态各自的渲染分支”，验收加“缺键 → 协议错误态，不渲染空” |
| P1-10 | P1 | 一致性 | Plan:788-800；Plan:622-633 | 排期与工作量不自洽：① 26 人日 ÷ 90 人日容量（3 人×30 工作日）≈29% 利用率，§4.2 却按满 6 周排；② §8.2 自称“关键路径下限 ≈10.5 人日”，10.5 人日 < 15 工作日，却据此断言“M1 不可能压缩到 3 周以内”——自相矛盾；③ §8.1 列 `S=0`，而 §3.1 仍定义 S≈0.25–0.5 的档位未用 | Plan:788（合计 26）、Plan:622-631（W1–W6）、Plan:800（10.5 人日与 3 周断言）、Plan:136（S 定义）、Plan:785（S=0） | 二选一：要么承认排期由“维护窗口 + 人力可用性”而非工作量决定（写明 6 周是日历约束），要么把 §8.2 的关键路径重算并给出 M1 的真实天数；同时删掉 `S=0` 那行或补“本轮无 S 任务”说明 |
| P1-11 | P1 | 漏项 | PRD:223-224、284-291；Plan:99 | `needs` 与 `redo_request` 的关系只各给一行字段定义，没有状态机：同一终态能否同时带 `needs` 与 `redo_request`？`redo_request.target_stage` 与 `need.from_stage` 冲突时以谁为准？Kernel 收到 `failed + redo_request` 时是重派还是先补 needs？§5.2 的表只给 `status → Kernel 行动`，没给 `redo_request` 的触发与幂等条件 | PRD:223（`needs[]` 为唯一缺项真相）、PRD:224（`redo_request` 组成）、PRD:284-291（映射表无 redo 行）；Plan:99（同行） | PRD §5.2 增一张 3×3 判定表（有/无 needs × 有/无 redo_request × status），并规定“redo_request 只在 `quality`/`edit` 的 `failed` 且 `base_artifact_sha` 可校验时产生，其余路径禁发”；Plan B19 验收引用该表 |
| P1-12 | P1 | 一致性 | Plan:656-668；PRD:20-21、602 | 同一个动画开关出现三个名字：现网 `CLIPWRIGHT_ANIMATION_AGENTIC_LOOP`（env，请求时读）、Plan B14 回滚点写 `animation.agentic_loop.enabled`、Plan §4.3 表写 `animation_agentic_loop`。另外 `stage_loop.*.enabled` 这一族点分开关没有任何读取层落点（现配置为 `settings` 属性 + `os.environ` 两种风格混用），Plan 未说落在哪个模块 | `agents/animation_agent.py:2181`、`:2184`（`os.environ.get("CLIPWRIGHT_ANIMATION_AGENTIC_LOOP")`）；Plan:360、Plan:668；`services/agent_loop.py:353`（`getattr(settings, "agent_loop_max_turns", …)` 风格） | Plan 增一小节「开关读取层」：统一 `settings.stage_loop_<stage>_enabled`（与现 `agent_loop_max_turns` 同风格）并把 `CLIPWRIGHT_ANIMATION_AGENTIC_LOOP` 标为 legacy alias；B14 回滚点、§4.3 表、§6.2 风险表三处名字对齐 |
| P1-13 | P1 | 测试 | PRD:62、63、536、595；Plan:422、438 | 回归用例路径写错目录，4 处会 file-not-found：`tests/test_audio_agent_llm.py`、`tests/test_audio_uploaded_dub_alignment.py`、`tests/test_quality_agent_content.py`、`tests/test_quality_semantic_qa.py` 实际都在 `tests/clipwright/` 下。按文档抄命令即失败，而这两条正是 audio/quality 的“不许退化”门禁 | `tests/clipwright/test_audio_agent_llm.py`、`tests/clipwright/test_audio_uploaded_dub_alignment.py`、`tests/clipwright/test_quality_agent_content.py`、`tests/clipwright/test_quality_semantic_qa.py`（`Get-ChildItem tests -Filter "*audio*"` 在根目录为空） | PRD §2.1 audio/quality 行、§6.4/§6.5 验收、Plan B18/B19 的回归行统一加 `clipwright/` 前缀；§10/§5.1 的命令块逐条扫一遍目录 |
| P1-14 | P1 | 可行性 | PRD:446；Plan:412 | edit 的完成门里放了 `dub_duration_problems(tl, run.inputs)`（实测配音时长），但**阶段顺序上 edit 在 audio 之前**（`PLAN_STAGES`：structure→material→edit→animation→audio→quality），此时不存在配音产物。同段 `seed` 又要求 `run.inputs["edit_profile"]`。这条门要么永远跳过（形同虚设），要么逼 edit 读 audio 的旧产物（与 §8“常态禁止音频读旧 timeline 定稿”方向相反） | `services/stage_subagent.py:379-380`（阶段顺序）；`agents/edit_agent.py:281-304`（edit 输入不含配音）；PRD:446（gate 代码）、PRD:673（“常态禁止音频读旧 timeline 定稿”） | PRD §6.3 把 `dub_duration_problems` 从 edit 的 gate 移到 audio（或改为“若上游显式提供已实测配音时长才启用，否则记 `unverified`”）；Plan B17 验收删掉对应该断言，B18 补 |
| P2-13 | P2 | 一致性 | PRD:644；Plan:113、769 | `agent_kernel._emit_report` 的现状判断两文档不一致：PRD 写“本仓搜索未确认生产调用”，Plan 直接写“无调用点”并列入删除清单。我实测全仓只有定义、零调用——Plan 是对的，PRD 过于保守。此类不一致会让“删死码”变成需二次确认的阻塞项 | `services/agent_kernel.py:2412`（定义，唯一出现处，全 `clipwright/tests` 检索无调用） | PRD §7.3 该行改为“全仓零调用点（`grep -r _emit_report clipwright tests` 仅定义）”；保留“发布前再跑一次静态检索”的前置条件 |
| P2-14 | P2 | 事实 | PRD:307、§6 各工具面表 | PRD §6 开头的规则写“能力名必须能在 `CAPABILITY_ALIASES` 或工具 `capability` 自声明里找到”。实测 `black_frame_detect`、`audio_silence_detect` 是工具名（无 `capability` 声明、别名表也没有），`content_query`/`get_type_spec`/`read_file`/`image_generate` 同理，靠 `resolve_name` 的工具名兜底才可用。规则句与它自己的表不一致 | `tool/capabilities.py:27-71`（别名表全量）；`tool/quality.py:19`、`:83`（仅 `name` + `permission_tier`，无 `capability`）；`tool/capabilities.py:117`（`candidates()`） | §6 规则句改为“能力名优先；仅当无能力别名时按工具名兜底（`resolve_name`），本表标出哪些走兜底”，并给 `black_frame_detect`/`audio_silence_detect` 标注“工具名兜底” |
| P2-15 | P2 | 事实 | PRD:235 | `RequirementsOutput` 的证据行写成 `schema/agent.py:324（324–342）`，但 324 是 `RequirementsInput`，`RequirementsOutput` 在 334。§6.1 又正确引 334，前后不一致 | `clipwright/schema/agent.py:324`（`class RequirementsInput`）、`:334`（`class RequirementsOutput`） | PRD:235 改为 `schema/agent.py:334`；同类“类头/字段行”混引的还有 `schema/agent.py:295`（类头 295、`exclude=True` 在 300），一并校正 |
| P2-16 | P2 | 漏项 | PRD:22、145；两文档任务表 | PRD §4 正确指出“提示词实际是 `clipwright.prompts` 包”，但**前端仓 `AGENTS.md` 不变量 #23 仍写死 `clipwright/prompts.py`**。两份文档都把这句当作“可核对的现状依据”，却没有“同步 AGENTS.md”的任务。不变量与代码互斥时，后续 agent 会按文档写错 | `J:\Clipweight-Client\AGENTS.md:164`（`## 23. 提示词只有一处：clipwright/prompts.py`）；`clipwright/prompts/`（实为包：`__init__.py`/`agents.py`/`loop.py`/`engine/`） | 在 Plan §7.1/§7.2 文件清单加 `J:\Clipweight-Client\AGENTS.md`（改 #23 文案）与 `AGENTS.md`（后端补 #13/#21/#23 或注明“见前端仓”）；归入 B14 或 X02 的收尾项 |
| P2-17 | P2 | 一致性 | PRD:642；`stage_subagent.py:365` | 进度分母写“分母为 6”，来源是 `PLAN_STAGES`（6 项，不含 requirements）。但 `STAGE_NAMES` 是 7 项，报告目录里若出现 requirements 报告，`len({stage})` 可以到 7 > 6，出现 `7/6` | `services/stage_subagent.py:365-375`（`_stage_progress_counts`）、`:379-380`（`PLAN_STAGES` 6 项）；`schema/stage.py:21-23`（`STAGE_NAMES` 7 项） | PRD §7.3 该行补“分母取 `PLAN_STAGES`，且 distinct stage 需与 `PLAN_STAGES` 求交后再计数”；B08 验收加“出现 requirements 报告时 completed 不超过 total” |
| P2-18 | P2 | 漏项 | Plan:114；PRD:681 | 事件里已经在泄露输入值：`agent_run_start.detail` 带 `spec.brief[:200]`，而 Plan 的该行默认值写“禁止泄露输入值”。不是大事故，但“契约默认值”与现实现相反且无人认领 | `services/agent_run.py:390-395`（`"brief": spec.brief[:200]`） | Plan §2 该行改“输入键；brief 摘要按 §11 数据保留策略裁剪或哈希”，并在 X01 的 fixture 里体现脱敏后的 brief 形状 |
| P2-19 | P2 | 测试 | Plan:746-747、PRD:747 | 测试迁移点只写“从 `test_stage_subagent.py:183`（183–309）迁移”，但没有“旧用例必须先红”的要求；而 `dry_run`/`needs` 的现有断言（若存在）会在契约变更后静默变成恒真。同类问题：`test_docs_protocol.py` 四节断言被替换时无“旧断言删除清单” | `tests/test_stage_subagent.py:183-309`（文档所指区段）；`tests/test_docs_protocol.py:81-206`；Plan:230、Plan:749 | Plan §5 增“契约变更测试纪律”：① 每个被替换的旧断言在 PR 里列 diff；② 新用例必须能在旧实现上失败（红）后再实现；③ 禁止 `pytest.mark.skip` 兜底（§5.3 已有此句，扩到全部任务） |

### 2.1 严重度分布与修复顺序

| 严重度 | 条数 | 修复窗口 | 未修后果 |
|---|---:|---|---|
| P0 | 4 | **B01 派工前**（全部是文档文本改动，预计 0.5 人日） | P0-01/P0-02 → 契约冻结返工；P0-03 → F 组无 fixture 可贴；P0-04 → 落地成两个执行入口 |
| P1 | 10 | B01–B05 期间（随任务附带修） | 改造点指错位置、验收断言写不出、排期失真 |
| P2 | 7 | 可后补，但 P2-16/P2-13 建议随 X02 收尾 | 文档与不变量互斥，后续 agent 按错文档施工 |

建议修复顺序：`P0-01 → P0-02 → P0-04 → P0-03 → P1-06 → P1-09 → P1-05 → 其余`。理由：字段先闭口（否则 B01 的 fixture 白做），再定 Adapter 归属（否则 B03 的 PR 描述会误导），最后修排期（排期可随时改，不影响代码）。

### 2.2 每条 P0 的最小验证方式（评审者视角）

| ID | 修完之后怎么证明“真的修好了” |
|---|---|
| P0-01 | 用修好的 `AgentResult` 对一个带 `brief` 的真实 run 做 `model_validate`，期望无 `extra_forbidden`；再对空 `brief` 做一次，期望不报错；前端 `TaskRunDetail` 接到同一 JSON 能渲染出 brief |
| P0-02 | 构造 `StageResult(status="dry_run")`，断言 `agent_run._map_status("dry_run") == "dry_run"` **且** `stage_subagent.run(...)["status"] != "ok"`；再断言 `RunStatus` 字面量包含 `dry_run` |
| P0-03 | 对 Plan §3.1 的主路径做一次拓扑排序脚本（读 §4.2 表也行），断言不存在“某任务在其依赖之前被排入”的边；人工确认 F01 依赖的 fixture 在 W2 之前已冻结 |
| P0-04 | `Select-String -Pattern "def run_stage"` 与 `"def _run_stage"` 各跑一次，只应命中一处（窄转发除外），且该处的 docstring 写明“唯一阶段领域 Adapter” |

## 3 事实核验记录

方法：对文档中每个 `file:line` 断言，用 `read` / `Select-String` 回到原文件取该行及其上下文，逐条判定。**没有一条是凭印象给的**；“无法核实”只在确实读不到时使用（本次 0 条）。共列 41 条（PRD 25 / Plan 16），覆盖 §5 抽查清单的全部重点项。

| # | 文档位置 | 断言原文摘要 | 核实结果 | 实际证据(file:line) | 影响 |
|---|---|---|---|---|---|
| 1 | PRD:28 | “阶段名已列入枚举，实际阶段工厂未登记” | 属实 | `schema/stage.py:17-23`（`StageName`/`STAGE_NAMES` 含 `requirements`）；`services/stage_subagent.py:34-41`（`_STAGE_DEPENDENCIES` 只有 6 项） | 支撑“requirements 不是真阶段”的判定；B03 的“工厂可达”是真实缺口 |
| 2 | PRD:315 | “`agent_for` 对 requirements 直接抛错（87）” | 属实 | `services/stage_subagent.py:87-91`（`raise ValueError(f"阶段 {stage} 没有对应的 Agent")`） | 属实；但该行为是**有意**的设计（注释写明“requirements 由需求服务处理”），PRD 未区分“缺陷”与“裁定” |
| 3 | PRD:315 | “`run_stage` 另有 `stage != "requirements"` 特判（516）” | 属实 | `services/stage_subagent.py:513-519`（三元表达式，requirements 走 `{"topic":…}` 字典） | 属实；这正是“工厂缺项被迫打补丁”的证据 |
| 4 | PRD:242、45 | “`_map_status` 把 `dry_run` 映射成 `ok`（688-696）” | 部分属实 | `services/agent_run.py:688-696`（`:695 "dry_run": "ok"`）✓；但 `services/stage_subagent.py:335-340` 另有一处把 `dry_run` 折成 `status:"ok"` 返回，且 `schema/run.py:24-25` 的 `RunStatus` 无 `dry_run` 字面量 | 修一处不够 → P0-02 |
| 5 | PRD:609 | “`build_sections` 把结果折成四节；`render_report` 写信封+四节” | 属实 | `services/stage_report.py:58`（docstring 逐字“我做了什么 / 结果如何 / 发现的问题 / 给下一位”）、`:100-105`（返回四键）、`:108`（`render_report`） | 四节名与文档完全一致，删改范围可精确圈定 |
| 6 | PRD:609 | “`preview` 最多 300 字，`next_for` 最多 600 字” | 属实 | `services/stage_report.py:232`（`[:300]`）、`:233`（`[:600]`） | 数字准确，可作 API 契约基线 |
| 7 | PRD:609 | “`_section_text` 在找不到标题时返回空串” | 属实 | `services/stage_report.py:239-243`（`start < 0` → `return ""`） | 支撑“自由正文 → 假空态”的静默失效论证 |
| 8 | PRD:609 | “`context_loader` 在两者中优先用 `next_for`，并非两节都完整装载” | 属实 | `services/context_loader.py:374`（`m.get("next_for") or m.get("preview")`）、`:150`（`_reports_digest(root, LIMITS["reports"])`）、`:362`（定义） | 属实；改自由正文后此处必然空 → §7.3 的 S 级判定成立 |
| 9 | PRD:639 | “`ReadReportTool` 按 seq 查索引、`section` 用 `_section_text`；`max` 实现为下限” | 属实（前端部分除外） | `tool/reports.py:137-141`（`section` → `_section_text`；`limit = max(200, …)` 无上限）；`:119`（`list_reports(root, limit=500)`）；但前端 `workspaceApi.report` 无 `section` 参数（`J:\Clipweight-Client\src\services\api\workspace.ts:244-252`） | “前端也传 section”的假设不成立 → Plan F01 的“删 section 分支”实际是“不存在” |
| 10 | PRD:644 | “`_emit_report` 定义里仍查 `output.artifacts`；本仓搜索未确认生产调用” | 部分属实 | `services/agent_kernel.py:2412`（定义）、`:2431`（`output.get("artifacts")`）✓；全仓 `clipwright`+`tests` 检索 `_emit_report` **只有定义一处**，确认零调用 | “未确认”过保守 → P2-13 |
| 11 | PRD:145 | “当前没有独立 `clipwright/prompts.py`，实际包用 `__init__.py` 导出、`agents.py`/`loop.py`/`engine/`” | 属实 | `clipwright/prompts/`（`__init__.py` 11.7KB、`agents.py`、`loop.py`、`chat.py`、`material.py`、`mg.py`、`type_forge.py`、`engine/`） | 属实；但前端仓 AGENTS.md #23 仍写 `prompts.py`（P2-16） |
| 12 | PRD:433 | “`run_tool_loop` 开头会重置 `last_tool_calls`（486），因此该轮会覆盖前序程序化调用轨迹” | 部分属实 | `agents/base.py:486`（重置）✓；真正覆盖在 `:532`/`:538`/`:541`，且 `:503` 只写 `{"tool","input"}`、无 `status` | 修 486 无效 → P1-07 |
| 13 | PRD:237 | “`material_notes` 被 schema 标成 exclude（`schema/agent.py:206`）” | 部分属实 | `schema/agent.py:206`（类头）、`:213`（`material_notes: … exclude=True`） | 行号差 7 行，不影响结论；同类 `AudioOutput` 引 295 实为类头，`exclude` 在 300 |
| 14 | PRD:303 | “`issues` 字段集无 `QualityIssue.message/location`” | 属实 | `schema/agent.py:320-321`（schema 有 `message`/`location`）；`schema/run.py:127-131`（投影白名单只有 severity/category/clip_id/reason/retry/scene_title） | 原因判断准确：不是 schema 缺字段，是**投影丢字段** |
| 15 | PRD:65 | “`run_loop` 用 `len(calls)+1` 估算轮次，批量调用会偏离真实模型轮数” | 属实 | `services/agent_loop.py:720`（`"rounds": len(calls) + 1`）；`:624`/`:674`（`max_tool_rounds=min(_rounds_cap, STRUCTURAL_TOOL_ROUNDS=24)`） | 属实；`iterations` 语义改为“实际决策轮”必须同步改此处 |
| 16 | PRD:642 | “`_stage_progress_counts` 数工作区最近 200 报告的 distinct stage，分母为 6” | 属实 | `services/stage_subagent.py:365-375`（`len({m["stage"] …})`，`list_reports(limit=200)`，`len(PLAN_STAGES)`）；`:379-380`（`PLAN_STAGES` 6 项） | 属实；但与 `STAGE_NAMES` 7 项不一致 → P2-17 |
| 17 | Plan:94 | “`schema/run.py:55`（AgentResult）、`agent_run.py:327`（run）” | 属实 | `schema/run.py:55`（`class AgentResult`）；`services/agent_run.py:327`（`async def run`） | 锚点正确 |
| 18 | Plan:95 | “`agent_run.py:465`（`agent_run_end`）” | 属实 | `services/agent_run.py:465-476`（`_emit_run(task, "agent_run_end", …)`） | 锚点正确；但 detail 里 `degradations` 已是计数（473），与 PRD §8 一致 |
| 19 | Plan:97 | “`stage_subagent.py:755`（writes/details）” | 属实 | `services/stage_subagent.py:750`（`writes=writes`）、`:755-760`（`details={…}` 白名单） | 锚点近似精确；details 白名单里**包含** `audio_config_internal`，是隐私风险点（PRD 未点出） |
| 20 | Plan:100 | “`stage_subagent.py:712`（degradations）” | 属实 | `services/stage_subagent.py:712-719` | 锚点正确 |
| 21 | Plan:101 | “`stage_subagent.py:772`（报告落盘）” | 属实 | `services/stage_subagent.py:772`（注释）→ `:782`（`build_sections`）→ `:791`（`write_report`）→ `:799`（`result.report_path`） | 锚点正确；`doc_write` 在 `:820-821`（`bytes: 0`，与 PRD 一致） |
| 22 | Plan:102 | “`stage_subagent.py:672`（tool_calls）、`:832`（dump）” | 属实 | `:672`（`stage_tool_calls = skill_calls + last_tool_calls`）、`:832`（`_dump_tool_calls`）、`:847`（`reports/tool_calls/<stage>-<attempt>.json`） | 锚点正确；“内存 200 条删减”在 `agents/base.py:336-337` |
| 23 | Plan:103 | “`stage_subagent.py:663`（tokens）” | 部分属实 | `:663` 是 `usage_after = llm_usage_snapshot()`；token 差值在 `:668-671` | 差 5 行；无实质影响 |
| 24 | Plan:104 | “`agent_run.py:335`/`:362`（replayed）” | 部分属实 | `:362`（`cached.replayed = True`）✓；`:335` 是 `base_id = spec.idem_key or f"run_…"`，不是 `replayed` 的落点 | `replayed` 的 producer 只应写 362 |
| 25 | Plan:114 | “`agent_kernel.py:2119`（`task_end.detail`）” | **错误** | `:2119` 为 `if gate_reason == "dry_run_preview":`（终态映射分支）；`task_end` 实际 emit 于 `:928`、`:1122`、`:1393`、`:1653`、`:1690` | 事件改造会漏 4 处 → P1-05 |
| 26 | Plan:116 | “`tool/reports.py:101`（ReadReportTool.execute）、`stage_report.py:239`（`_section_text`）” | 属实 | `tool/reports.py:101`（`async def execute`）、`services/stage_report.py:239`（`def _section_text`） | 锚点正确 |
| 27 | Plan:117 | “`api/task.py:623`/`:659`（runs 列表/详情）” | 属实 | `api/task.py:623`（`@router.get("/{task_id}/runs")`）、`:659`（`…/runs/{run_id}`）；`:511`（`tool-calls`） | 锚点正确；列表返回 13 个键（`636-654`），与前端 `TaskRunSummary` 12 键基本对齐 |
| 28 | Plan:120 | “`evaluation.py:146`（task_summary）、`golden_eval.py:112`（run_many）” | 属实 | `services/evaluation.py:146`、`:68`（`record_run`）；`services/golden_eval.py:112`（`run_many`）、`:47`（`run_once`） | 锚点正确 |
| 29 | Plan:340 | “`agents/base.py:81`/`:242`/`:463`；`agent_kernel.py:1185`/`:1760`/`:2006`” | 属实 | `agents/base.py:81`（`execute`）、`:242`（`call_tool`）、`:463`（`run_tool_loop`）；`kernel:1185`（`_on_tool_call`）、`:1760`（`_enter_closeout`）、`:2006`（`_gate_hit`） | 锚点全部正确，是 Plan 里质量最高的一组 |
| 30 | Plan:188 | “`run_skill_round` 外围轮从 `stage_subagent.py:607` 收到统一预算” | 部分属实 | `:611`（`if getattr(agent, "run_skill_round", None)`）、`:613-616`（`asyncio.wait_for(..., timeout=min(60.0, timeout_eff))`） | 行为描述准确（确有 60s 上限）；行号应为 607-619 |
| 31 | Plan:356 | “`animation_agent.py:1927`/`:1987`/`:2018`” | 属实 | `:1927`（`async def _generate_mg_via_agentic`）、`:1987`（`for obs in reversed(result.observations)`）、`:2018-2019`（“视觉循环回到 Agent”注释） | 锚点正确；开关 env 在 `:2181`/`:2184` |
| 32 | Plan:452 | “`schema/agent.py:206`（取消 `material_notes` 隐式 exclude）” | 部分属实 | `:206` 是类头，`exclude=True` 在 `:213` | 差 7 行；无实质影响 |

| 33 | PRD:433 | “edit 的唯一工具轮是 `run_tool_loop(max_rounds=2, max_calls=4)`，结果只追加一行 notes” | 属实 | `agents/edit_agent.py:995-1004`（`if _pv_project and _pv_llm_on:` → `max_rounds=2, max_calls=4` → `notes.append(f"PV 自检：…")`） | 描述精确；该轮正是“轨迹被覆盖”的触发点 |
| 34 | PRD:489 | “`audio_config_internal` 被 `exclude=True`（`schema/agent.py:295`）” | 部分属实 | `:295` 是类头、`:300` 是 `exclude=True` 字段 | 差 5 行；但 `stage_subagent.py:755-760` 的 details 白名单又把它放回去 → 两处口径相反（G7） |
| 35 | PRD:601 | “Material 参考 `tests/clipwright/test_material_agent.py:201`” | 属实（文件） | `tests/clipwright/test_material_agent.py` 存在；201–329 为候选/失败重搜用例区段（行号未逐行核） | 文件定位正确；行号属“同区间合理”而非精确锚点 |
| 36 | PRD:307 | “能力名必须能在 `CAPABILITY_ALIASES` 或工具 `capability` 自声明里找到” | 部分属实 | `tool/capabilities.py:27-71`（别名表 27 项）；`tool/quality.py:19`、`:83` 只有 `name`+`permission_tier`，无 `capability` | 规则句与 §6 表不符：表里 6 个“能力名”实为工具名，靠兜底解析（P2-14） |
| 37 | PRD:22 | “后端 `AGENTS.md:59`（59–94）” | 属实 | `AGENTS.md:59` 是不变量 1；1–12 覆盖 59–95 | 锚点正确；但 PRD:307 引的“不变量 19”在后端文件不存在（只有 1–12、24–26） |
| 38 | PRD:22 | “前端 `AGENTS.md:73`（73–76、123–145、164–180）” | 属实 | `J:\Clipweight-Client\AGENTS.md:73`（#13 渲染边界）、`:123`（#21 终止机制）、`:164`（#23 提示词）、`:178`（#24 起） | 三处引用全部命中；#23 内容过时（P2-16） |
| 39 | Plan:326 | “新增 `tests/test_agent_loop.py::test_multi_call_observe_revise_final` 等” | 属实（文件存在） | `tests/test_agent_loop.py` 存在，可承载新用例 | 新增用例名与既有用例不冲突（文件内无同名） |
| 40 | Plan:564 | “X01 复用 `src/features/task/eventSchema.test.ts:57`” | 属实 | 该行为既有用例 `it('agent_run_end 解析 failure / needs / report_path')` | 复用点选得准：正是事件 detail 契约的现成断言 |
| 41 | PRD:637 | “文档事件当前 `bytes=0`” | 属实 | `services/stage_subagent.py:820-821`（`{"path": path, "bytes": 0, "by": stage, "seq": seq}`） | 属实；`bytes=0` 是占位值，前端 `EventFeed` 目前不展示 → 改真值需同步前端 |

**核验结论**：现状证据（file:line）质量高——41 条中 29 条完全属实、11 条“部分属实”且均为行号偏移 ±7 行内或“类头 vs 字段行”的混引、仅 1 条**错误**（#25：Plan:114 的 `task_end` 锚点）。真正的问题不在“证据造假”，而在**把目标态写成现状**（§2 P1-09）与**同一行号复用给多个契约点**（P1-06）。

## 4 PRD 专项审查

### 4.1 §4 目标架构

| 审查点 | 结论 | 证据/理由 |
|---|---|---|
| “唯一 AgentLoop” 是否有唯一的调用形状 | **不成立**。架构图里 `Kernel` 同时连到 `Loop`（直接）与 `Runner`（经 ToolSurface 派发），`Runner → Stage → Loop`。同一张图表达了两条进入 `drive` 的路径，而 §4.1 正文说“Kernel（Task Goal/Plan/closeout）、5 个阶段、调研 worker、animation MG 都调用它，只替换 LoopPolicy”。两条路径的差异（谁建 RunContext、谁持预算租约）没有画出来 | PRD:98-117（mermaid）、PRD:149-150 |
| `LoopPolicy` 接口是否闭口 | **不闭口**。`drive` 伪代码只调用 `policy.seed`（154）与 `policy.gate`（168、184）。但 §6.2 的 `StructurePolicy` 定义了 `on_gate_rejected`（391-393）并称“关键：拒绝反馈必须回到模型可见的观察里”——**`drive` 从不调用它**，伪代码在 172 行直接把 `gate_rejected` 观察写进 ledger。同理 `RequirementsPolicy.budget()`（335-336）在 `drive` 里也没有对应调用点（用的是 `run.budget`）。接口成员（seed/gate/budget/on_gate_rejected）与调用点不对齐，实现者只能猜 | PRD:152-195（drive）、PRD:321-336（RequirementsPolicy）、PRD:380-393（StructurePolicy） |
| 闸门顺序是否可实现 | **部分可实现**。顺序声明为 `cancel → closeout/只读面 → 轮数 → 调用数 → 墙钟 → 预算预留`，但伪代码只在循环顶（158）与每个 call 前（177）各查一次 `run.gate.hit()`，把六个闸合成一个布尔；而 §4.1 明确“闸门顺序固定，不可由 policy 改写”。要么 `hit()` 内部按序返回首个命中原因（文档未说），要么伪代码漏了分级 | PRD:158、177、209 |
| `finalize` 只执行一次是否有并发保证 | **未定义**。伪代码注释写“staging + CAS + run_id 幂等”，但取消路径（156-157）与 `internal_defect`（207）都直接 `return LoopExit` 而不进 `finalize`——与 §8“早退/取消/拒绝/熔断/成功均通过组装器”冲突。这是两处文本的正面矛盾 | PRD:156-159、207 vs PRD:126、Plan:38 |
| 删除测试（“删掉共用循环就会散架”）是否可判 | 可判但无验收断言。§4 说“用删除测试验证抽象”，§10 测试表没有对应用例 | PRD:128、PRD:743-751 |

### 4.2 §5 数据契约的缺口

按“字段/语义 → 缺口 → 影响”列。前 4 条是我认为必须在冻结前定稿的。

| # | 缺口 | 具体表现 | 影响 |
|---|---|---|---|
| C1 | `mode` 字段不存在 | PRD:220 要求“inline 同样生成 run_id，用宿主会话/局部运行 scope 归属，明确 `mode=inline` 并禁止写生产 Task 黑板”，但 §5.1 的 22 行字段表与 254-280 的伪代码里**没有 `mode`**。Plan §2 也没有 | inline 运行无法被下游识别，`禁止写黑板` 只能靠调用点自觉 → B03 的验收“inline 不写生产 Task 黑板”没有可断言的字段 |
| C2 | `brief` 字段缺失 | 见 §2 P0-01 | 重放与 RunDetail 断链 |
| C3 | `needs` 与 `redo_request` 无关系定义 | 见 §2 P1-11 | Kernel 双入口，重复派发风险 |
| C4 | `outputs` 大对象引用只给了名字 | 全文只说“大数据用 `ArtifactRef`”，没有 `ArtifactRef` 的结构（是否 `{artifact,path,sha256,bytes,preview}`）、写谁、何时失效、读回哪个 API、GC 策略。现实现里 `_preview` 恰好**丢 path/sha**（`schema/run.py:200-206`），说明“引用化”这件事今天就不完整 | 报告与 run 查询只能看到 preview 字符串，跨 run 复用产物无 sha 可校验 → §7.4 的 `artifact_sha` 校验无数据基础 |
| C5 | `report_sha256` 无 producer | 字段列在 §5.1、Plan §2，但全仓无任何 hash 计算；`write_report` 只返回 `(path, seq)`（`stage_report.py:155-173`） | 需要新增计算点，且要决定 hash 覆盖 front-matter 还是不含（否则每次重写 INDEX 都变） |
| C6 | `dry_run` 的“校验结果”放哪未定 | §5.2 写“预检返回输入来源与校验结果”，§6.6/§5.1 未说落在 `outputs` 还是 `details`；`details` 又被定义为“非权威展示诊断” | 预检结果会落到非权威字段，前端无法稳定读取 |
| C7 | `iterations` 语义与事件 `round` 不同源 | `iterations` 定义为“实际决策轮数”，但现 `agent_loop` 事件用 `rounds=len(calls)+1`（`agent_loop.py:720`），`subagent_round` 又是另一处计数（PRD:163） | 三处计数不同源，验收“iterations 正确”无法写断言 |
| C8 | `events` 的 `schema_version` 与 payload 版本未分家 | §7.2 说“Markdown 可带 `schema_version/run_id/report_seq`”，§8 说事件 detail 用“版本字段”，Plan §1.3 说 `schema_version=2` 同时出现在 AgentResult、事件 detail、WorkspaceReport | 三处共用一个版本号意味着任一字段变更都要跳大版本，实际不可行；应区分 `wire_version`（结果）与 `report_version`（正文信封） |

### 4.3 §7 取消固定格式：下游清单自查（重点抽查）

我按 §7.4 要求的“定向扫描”口径，独立扫了一遍 `build_sections` / `_section_text` / `next_for` / `headline` / `preview` / `section` / `INDEX.md` / `api/*` / 前端 DTO 与组件。结果与 PRD §7.3 的 15 行清单对比：

| 消费者 | PRD §7.3 是否列出 | 实情 | 结论 |
|---|---|---|---|
| `services/stage_report.py` `build_sections`/`render_report`/`_section_text`/`_parse_front_matter` | 是（S） | `:53`/`:108`/`:239`/`:180` 均在 | 覆盖 |
| `services/context_loader.py:_reports_digest` | 是（S） | `:374` 读 `next_for` 优先 | 覆盖 |
| `tool/reports.py` `read_report(section)` | 是（S） | `:137-141`；且**前端 client 无 section 参数** | 覆盖但前端侧描述不实（P1-09 同源） |
| `api/workspace.py` 两个端点 | 是（S） | `:175-213`；`:210` 返回绝对 `path` | 覆盖 |
| `services/progress.py` 时间线 | 是（S） | `:80-94` 读 `progress.md` 的 `- \`` 行 | 覆盖 |
| `services/stage_report.update_index` 七列 | 是（S） | `:249-294`，`headline` 列来自列表 | 覆盖 |
| 前端 `workspace.ts` / `CopilotDocsTab` / `CopilotStageTab` | 是（S） | `workspace.ts:88,93`；`CopilotDocsTab.tsx:110`；`CopilotStageTab.tsx:52` | 覆盖，三处消费方式与文档描述一致 |
| `services/session_projection.py` + `SessionTimeline.tsx` | 是（S） | `session_projection.py:42`（`doc_write→artifact`）；`SessionTimeline.tsx:389` | 覆盖 |
| `services/agent_run._emit_run` / `_emit_report` | 是（S） | `agent_run.py:465-476`；`agent_kernel.py:2412` | 覆盖 |
| `services/evaluation.py` / `golden_eval.py` / `api/eval.py` | 是（V） | `evaluation.py:146`、`golden_eval.py:112`、`api/eval.py:70` | 覆盖 |
| 测试 `test_docs_protocol` / `test_progress_index` / `test_tool_dedupe` | 是（S） | 三个文件均存在 | 覆盖 |
| **`services/stage_report.list_reports` 的 H1→headline 解析** | **否**（只在 609 行被描述为机制，未进 §7.3 表） | `:216-221` 是“无标题就空”的根因，属同一条解析链 | 漏；且它是“假空态”最关键的一处 |
| **`services/agent_kernel._preview_of`** | **否** | `:2377-2384` 读 `value["preview"]`，但这是 **artifact ref 的 preview**，与报告 `preview` 同名词不同义 | 漏（属于“不该删但要标注”的一类）。按现静态规则做门禁会误删或误报 |
| **`services/intent_apply.py` 的 `report.preview`** | 否 | `:145` 等 10 处，是另一个命名空间的 list | 不需要改，但静态扫描会命中 → §7.4 规则必须限定文件集 |
| **`INDEX.md` 的仓外/运维消费者** | 承认未验证 | 文档已在 §7.3 首段声明“代码外引用和部署脚本实际用法未验证” | 可接受，但“停写前需确认”没有对应任务（Plan 也没有） |
| **`api/task.py` 轨迹端点对 `stage+attempt` 的回落** | 是（在 §8 轨迹 API 行） | `api/task.py:511-597` 按 stage/attempt 找文件，未命中回落最新 | 覆盖，但它是**§7 之外**的破坏点，放在 §8 容易被漏读 |

**结论**：§7.3 的清单是这两份文档里做得最扎实的一张表（15 行里 11 行可逐条核实且描述准确）。两处实质漏项（`list_reports` 的 headline 解析、`_preview_of`）都源于同一个错误——把“符号级删除”当成了“文档链删除”，而 `headline`/`preview` 这两个词在仓里跨了三个命名空间。

### 4.4 §9 计划与 §11 风险

| 审查点 | 结论 |
|---|---|
| §9 的 9 个任务（P0-1…P2-2）是否可作派工单位 | **不可直接派工**：没有规模、没有人日、没有明确 owner，粒度为“一批”（P0-2 一次改 6 个文件 + 3 个仓外路径）。它的价值是切换顺序（P0→P1→P2），不是任务分解。Plan 的 28 项才是可派工单位，两者粒度差 3 倍以上 |
| §9 是否覆盖前端 | 只在 P0-3 的“文件与改动”列里一句“前端 Workspace/Task DTO、Copilot/Task/评估消费者”。没有独立前端里程碑 → 与 Plan 的 F01–F05（5 项、4.25 人日）严重不对称，说明这份 PRD 不是按双仓项目写的 |
| §9.1 一次性切换的 4 步是否可执行 | 第 2 步“按 workspace、task、版本把旧报告目录…移到只读归档”可执行；第 3 步“探针包含运行成功、缺输入、无报告、分页读取、SSE、INDEX、黑板、前端下钻和评估摘要”是 9 个探针但没有通过判据（谁判、阈值多少）；第 4 步“双仓回退旧版本”与“v2 目录隔离”依赖运维脚本，而两文档都没有脚本任务 |
| §11 风险表的覆盖面 | 6 行（成本时延/非确定性/重复调用/权限插件/收口预算/切换索引）都是真风险，但**全部是“单点失败”视角**，缺并发、缺契约版本演进、缺数据保留与隐私、缺回滚演练；见 §7 |
| §11“待拍板”5 项 | 5 项都给了建议默认值，这是本文档少见的“敢下结论”处。但第 2 项（未知金额 costly 是否允许执行）与 Plan §9 第 2 项重复且默认值一致 ✓；第 1、5 项与 Plan §9 第 1、5 项一致 ✓；第 3 项与 Plan 第 3 项一致 ✓；第 4 项口径不同（PRD 只说“由运维确定”，Plan 给“≥30 天 / 2 小时窗口”）→ 单边定义，见 §6 |

### 4.5 §6 五阶段方案的实现前提（逐条查“伪代码能不能落地”）

§6 是 PRD 篇幅最大的一节，但它给的 `LoopPolicy` 伪代码依赖若干**尚不存在**的输入与函数。逐条查：

| 阶段 | 伪代码依赖 | 现状 | 落地缺口 |
|---|---|---|---|
| requirements | `validate_creative_brief(brief)` 标注 `schema/requirements.py:48` | `:48` 是 `class CreativeBrief`；模块里只有 `brief_field_names()`（`:164`）与 `brief_prompt_template()`（`:169`），**没有** `validate_creative_brief` | 需新增函数（PRD §6.1 已在工具面标 `brief_validate` 待登记，但 gate 里直接当既有函数调用 → 读者会以为存在） |
| requirements | `run.inputs["brief_revision"]`、`run.inputs["brief_draft"]` | `RequirementsInput`（`schema/agent.py:324-332`）字段为 `context/topic/script_text/reference_materials/persona_id/category_plugin_id`，无上述键 | B16 必须扩 `RequirementsInput`，而 Plan 的 B16 文件清单只有 `requirements_agent.py`、`stage_inputs.py`、`requirements_service.py`，**没有 `schema/agent.py`** |
| structure | gate 用 `run.inputs["target_duration_s"]`、`run.inputs["script_text"]` | `StructureInput`（`:155-175`）无 `target_duration_s`；时长来自哪（`script_skeleton`/`spec.args`）未定义 | 同上：需扩输入并写清来源，否则 `duration_problems` 无参数 |
| structure | `scenes_validate` 能力 | 待登记（PRD:408 自述） | 需封装 `_validate_scenes` + 覆盖/时长校验；这是“把循环外校验变成门”的关键，工作量被 Plan（B15 记 M）低估 |
| edit | `dub_duration_problems` | 见 §2 P1-14：阶段顺序不支持 | 必须移出门或降级为 `unverified` |
| audio | `run.inputs["dub_mode"]`（`upload/tts/none`） | `AudioInput`（`:282-293`）无此键；现实现按 `audio_config_internal`/`settings` 分支 | 需新增输入键并写清与配置的优先级 |
| quality | `run.ledger.coverage([...])` | 现 `AgentResult` 无 ledger 概念；`coverage` 字段 `QualityOutput` 也没有（`schema/agent.py:344-`） | `quality_evidence` 待登记 + `coverage` 新增字段，两处都要落 |
| quality | `vision_score` 来自 `tool/stubs.py:28`（工具名 `vision_llm`） | 文件名 `stubs.py`，说明是桩实现；PRD 标 `costly` | 需确认它是真能力而非占位，否则“仅配置允许时进入工具面”会给出一个假能力（与 AGENTS.md 不变量 26 的“不许替 engine 宣称能力”同类风险） |
| animation | 证据三元组 `clip_id + artifact_sha + 背景哈希` | 背景哈希的算法与存放位置未定义（`agentic_runtime.py` 里只有 final 守卫） | B14 需补“哈希怎么算、存哪、何时失效” |

> 汇总：§6 的 5 段伪代码里，有 4 段依赖的输入键（`brief_revision`/`target_duration_s`/`dub_mode`/`coverage`）在现有 schema 中不存在，而两份文档都没有“扩阶段输入”的独立任务。这是 §9/B16–B19 规模被低估的根因。

## 5 Coding Plan 专项审查

### 5.1 28 个任务是否覆盖 PRD 全部要求

逐条把 PRD 的硬要求映射到任务 ID（“—”＝无任务认领，即漏）：

| PRD 要求 | 位置 | 认领任务 | 判定 |
|---|---|---|---|
| `AgentResult` 唯一公开结果（含 `brief`/`mode`） | §5.1 | B01、B02、B03 | **部分**：B01 的字段清单抄的是 PRD §5.1，同样缺 `brief`/`mode`（§2 P0-01、§4.2 C1） |
| 七终态 + `dry_run` 不折 ok | §5.1、§5.2 | B01、B03 | **部分**：B03 验收只写“dry_run 不映 ok”，未覆盖 `stage_subagent.run` 的 ok 分支（§2 P0-02） |
| `to_tool_payload` 重写 | §5.3 | B02 | 覆盖 |
| 唯一 `drive` + 4 类调用方 | §4.1、§8 | B12、B13、B14、B16、B21 | 覆盖；但 `ResearchPolicy` 只有 B21 一项且排在最后（见 5.2 漏依赖） |
| `prompts/stage_loop.py` 单一来源 | §4 | B14、B15、B16 | **部分**：PRD §4 要求把本次涉及的 `DECIDE_SYSTEM`、动画修复、结构固定工具名、主循环纠错文案一并归位；B17/B18/B19 新增的阶段提示词无任务认领 |
| 五阶段改造方案 §6.1–6.5 | §6 | B16、B15、B17、B18、B19 | 覆盖（一一对应） |
| Material / Animation 迁移 | §6.6 | B20、B14 | 覆盖 |
| 自由正文 + 原子目录 + 索引 | §7.2 | B06 | 覆盖 |
| 报告读接口分页与 `section` 拒绝 | §7.2 | B07、B09 | 覆盖 |
| §7.3 下游清单 15 行 | §7.3 | B06–B11、F01–F05 | **部分**：`stage_report.list_reports` 的 headline 解析、`agent_kernel._preview_of` 无任务（§2 P1-08）；`TaskFinishSummary.tsx`、`AgentPanel.tsx` 出现在 Plan §7.2 文件清单却**无任务认领** |
| 进度与完成计数收据 | §7.3 | B08 | 覆盖 |
| 黑板 CAS / 输入冻结 / inline 消费 | §8 | B05 | 覆盖 |
| 轨迹 API 按 run 寻址 | §8 | B10 | 覆盖 |
| 事件形状与 DTO | §8 | B08、B10、F03、F04 | 覆盖 |
| 评估与 golden 口径 | §7.3 | B11、F05 | 覆盖 |
| §10 验收命令（含 golden_eval） | §10 | §5.1 命令块 | **漏**：PRD:733 的 `python -m clipwright.services.golden_eval …` 未出现在 Plan §5.1（674-680 只有 5 条），而 golden 是 B11 的验收载体 |
| 一次性切换与删除范围 | §9.1 | X02、B01–B11 的“删除”列 | 覆盖；§9.1 第 3 步的“9 个探针”在 X02 里没有逐项列出 |

**小结**：28 项对 PRD 的覆盖率为 12/16 完整、4/16 部分、0 完全缺失——结构上可用。缺口集中在三处：字段（`brief`/`mode`）、符号级（headline/`_preview_of`）、以及三个前端文件无人认领。

### 5.2 依赖图：环与漏依赖

| 边/节点 | 问题 | 判定 |
|---|---|---|
| `X01 → F01/F03/F04` 与主路径 `F01–F04 → X01` | 环（§2 P0-03） | **P0** |
| `B17` 依赖写 `B05、B12、B15` | B17 要改 `agents/base.py:483,497`（删 `last_tool_calls` 重置、改 `_executor` 归属），而 `base.py` 的 `execute→drive/finalize` 抽取属于 **B13**。漏 B13 会出现“在未定型的 Base 上改记账” | **P1 漏依赖** |
| `B21` 依赖写 `B02、B12、B13` | B21 的第三项改动是“移除 `agent_loop.py:308` 重复轮控分支”，其自述前提是“删除重复循环要等全部调用方迁完”。但全部调用方 = B14/B15/B16/B17/B18/B19，均不在依赖里，且 B21 被排在 W6 与它们同周 | **P1 自相矛盾**（要么补依赖，要么把“删除重复循环”拆到 M5） |
| `B12` 依赖 `X02（上线启用）` | 语义含混：开发可先做、启用要等窗口。§4.2 把 B12 排 W5（窗口后）与 §3.1“B12 可预作”不一致 | P2（表意不清） |
| `B05` 依赖 `B02、B03、B04` | 正确；但 B05 的验收要求“audio 未成功定稿 `final_timeline` 不报 ok”，涉及 `stage_merge.py:106` 的 audio 分支，而该分支的判定逻辑在 B18 才改 | P2（跨任务验收锚点错位） |
| `B11 → F05` | 正确 | — |
| 整体拓扑 | 除上述外无环；21 个后端任务的最长链为 `B01→B02→B03→B05→B17→B18→B19`（7 跳） | 可执行 |

### 5.3 契约总表与规模排期自洽性

**(a) Plan §2 契约表 vs PRD §5.1 字段表**

| 字段族 | PRD §5.1 | Plan §2 | 差异 |
|---|---|---|---|
| 身份（`schema_version/run_id/kind/task_id/trace_id/attempt/stage/role`） | 有 | 有 | 一致 |
| `status/terminal_reason/summary` | 有 | 有 | 一致 |
| `outputs/writes/details` | 有 | 有 | 一致 |
| `needs/failure/error` | 有 | 有 | 一致 |
| `issues/redo_agent/redo_hint/redo_request` | 有 | 有 | 一致 |
| `degradations/iterations` | 有 | 有 | 一致 |
| `report_path/report_seq/report_state/report_sha256/handoff_summary` | 有 | 有 | 一致（但 `report_sha256` 无 producer，C5） |
| `tool_calls/tool_calls_path/tool_calls_total/trace_state` | 有 | 有 | 一致 |
| `tokens/cost_*/duration_ms/provider/model/created_at` | 有 | 有 | 一致 |
| `replayed/replay_of/spec_ref` | 有 | 有 | 一致 |
| **`brief`** | **无** | **无** | **共同缺失** |
| **`mode`** | **无**（正文要求有） | **无** | **共同缺失** |

结论：两张表在字段集合上高度一致（这是 Plan 质量最好的一处），共同缺口恰好就是 §5.1 里最容易炸的两个（`brief`、`mode`）。

**(b) 规模与排期**

| 指标 | 文档值 | 我的核算 | 判定 |
|---|---|---|---|
| 任务数 | 28（B21/F5/X2） | 逐项数：21+5+2=28 | 一致 |
| 规模分布 | M=18、L=10、S=0 | 逐项数：L=B03/B04/B05/B06/B10/B12/B13/B14/F04/X02=10；M=18 | 一致 |
| 人日 | 13.5+12.5=26 | 18×0.75+10×1.25=26.0 | 算术一致 |
| 3 人 6 周 | §4.2 W1–W6=30 工作日 | 3×30=90 人日容量 ÷ 26 人日 ≈ 29% 利用率 | **不自洽**（差距 3.4 倍，未解释） |
| 关键路径 | §8.2“≈10.5 人日”并断言“M1 不可能 <3 周” | 10.5 人日 < 15 工作日 | **自相矛盾** |
| 1 人 / 2 人 / 3 人 | 6–7 / 6–7 / 6 周 | 2 人时工作量已可分半（≈13 人日 ≈ 3 周），却仍报 6–7 周 | 2 人、3 人两列实质上没有边际收益论证，只有断言 |

**(c) §4.2 排期表的可执行性**

| 周 | 声明 | 问题 |
|---|---|---|
| W1 | BE-A: B01→B02；BE-B: X01 | B02 依赖 B01 冻结，B01 是 M(0.75)，B02 也是 M(0.75) → 1.5 人日，W1 有 5 人日，BE-A 有 3.5 人日空档未分配 |
| W2 | BE-A: B03、B04（各 L=1.25） | 2.5 人日 vs 5 人日容量；BE-B 只有 B05（1.25）。两人合计 3.75/10 人日 |
| W3 | BE-A: B06、B07、B08；BE-B: B09、B10、B11 | 3.25 + 2.75 = 6/10 人日，是全表最满的一周 |
| W4 | “F04 联调字段修订；B12 预研”+ 维护窗口 | 维护窗口（X02，L）与 F04（L）同周，且 X02 定义“必须串行” → 该周实际只够一项 |
| W5–W6 | B12–B21 共 12 项 | 12 项 ≈ 11.5 人日 ÷ 2 人 ÷ 10 天 = 58% 利用率；与 W2 的 38% 形成明显波动 |

结论：总量对得上、日历对不上。若按 §8.2 的“关键路径”真值，3 人方案应在 3 周左右完成 M1；文档给 6 周却不说明多出来的 3 周是什么（人力可用性？维护窗口排期？）。这不是致命错，但会让“是否值得加人”的判断失真。

### 5.4 前端任务覆盖、回滚点、测试可落地性

**(a) 前端覆盖**

| 文件/组件 | 出现在 Plan | 任务认领 | 判定 |
|---|---|---|---|
| `services/api/workspace.ts`、`lib/render.ts` | §7.2 + F01 | F01 | 覆盖 |
| `CopilotDocsTab`、`CopilotStageTab` | F02 | F02 | 覆盖 |
| `eventSchema.ts`、`stores/taskStore.ts` | F03 | F03 | 覆盖 |
| `types/task.ts`、`TaskRunDetail`、`TaskToolTrace` | F04 | F04 | 覆盖 |
| `TaskRuns`、`CopilotTaskRunTab`、`CopilotSubagentTab`、`RunningSubagents` | F04“额外验收视图” | F04（同任务内联） | 覆盖但无独立验收用例行 |
| `EventFeed`、`SessionTimeline`、`services/api/eval.ts`、`EvalPanel` | F05 | F05 | 覆盖 |
| **`services/api/task.ts`** | §7.2 文件清单 | **—** | 漏（run 详情/列表 client 未认领） |
| **`TaskFinishSummary.tsx`** | §7.2 文件清单 + PRD §7.3 提及 | **—** | 漏 |
| **`AgentPanel.tsx:1779`**（质检摘要） | §7.2 文件清单 | **—** | 漏（PRD §7.3 明确要求“照顾前端 AgentPanel”） |

**(b) 回滚点可执行性**

| 任务 | 回滚点原文 | 判定 |
|---|---|---|
| B01 | 上生产前撤 B01 合同 | 可执行 |
| B03 | “切换前复旧 Adapter” | **不可执行**：旧 Adapter（`stage_subagent.run`/`run_stage`）在 B03 内已被改；无“保留双实现”的分支 |
| B05 | “上线后退回切换前黑板快照” | **不可执行**：没有任何任务负责建黑板快照，且 §8 要求“禁止从旧快照无感重发”，两者冲突 |
| B14 | `animation.agentic_loop.enabled=false` | **不可执行**：开关名不对（现为 `CLIPWRIGHT_ANIMATION_AGENTIC_LOOP`，见 §2 P1-12） |
| B15–B20 | `stage_loop.*=false` | **半可执行**：开关读取层未定义（P1-12），且 B15–B19 声明“关掉后仍走 v2 gate”，需要“旧生成路径 + 新门”的共存代码，无任务负责建 |
| X02 | 停新任务→保全→隔离→双仓回退 | 可执行（最高质量的一条） |

**(c) 测试可落地性**

| 项 | 判定 |
|---|---|
| 新增 `tests/clipwright/test_{requirements,structure,edit,audio,quality}_agent_loop.py` | 可落地，命名与既有 `tests/clipwright/test_*` 一致 |
| 新增 `tests/contracts/` | 目录当前**不存在**（`Test-Path tests/contracts` = false），需 B01/X01 新建；用例文件与 fixture 目录的层级（`tests/contracts/test_agent_result_v2.py` vs `tests/contracts/fixtures/`）在两处描述不一致 |
| fixture 落点 | **三处不一致**：X01 写前端 `src/services/api/contracts/agent_result_v2/*.json`；§5.3 写 `src/contracts/fixtures.ts`；§7.2 写 `src/contracts/{fixtures,contract.test,decode}.ts`。“同字节 fixture”与“TS 文件引用同一内容”是两种机制，且“构建期同步脚本或手动同步”没有归属 |
| 回归用例路径 | 4 处目录写错（§2 P1-13） |
| `tests -q` 已知长跑 | §5.1 的诚实说明（53% 附近无进展）值得保留，但“以 contracts + 子集 + 一次完整跑通记录为准”等于允许全量回归不常跑 → 与 AGENTS.md 的 CI 门禁（`python -m pytest tests -q`）冲突 |

### 5.5 §3.2 逐阶段 Gate 矩阵与 §4.3 灰度开关的核对

| 阶段 | Plan §3.2 要求“必须保留的业务回归” | 可核查性 | 缺口 |
|---|---|---|---|
| Requirements | CreativeBrief 关键字段、来源与冲突 | 字段可枚举（`schema/requirements.py:164 brief_field_names()`）；“来源与冲突”无数据源 | “来源已核对”在现 schema 里无对应字段 → 只能落在 `details`（非权威），§6.1 的 `source_unverified` 降级码无证据来源 |
| Structure | scenes 稳定 ID、正时长、脚本覆盖/目标时长、`material_refs` 可读 | 前三项有现成校验；`target_duration_s` 见 §4.5 | 对齐/接地/plan drift 在业务层完成（`structure_agent.py:750` 起），未进 gate 清单 → 改造后可能整体丢失 |
| Edit | timeline schema、源窗≤实测素材、配音时长、字幕溢出 | 前三项可行；配音时长不可行（§2 P1-14） | 同 P1-14 |
| Audio | TTS 授权、混音削波/时长、实测配音-字幕区间、样式继承 | **“TTS 授权”从哪读未写**：现实现依赖 `audio_config_internal` + provider 可用性 | 需明确授权来源（`RunSpec` 租约 / 显式设置 / 用户确认），否则“未授权不得 TTS”无法实现 |
| Quality | 技术 error 必阻断、必检证据与 timeline SHA 同版、坏语义未验 | 可核查；`timeline_sha` 需上游提供 | `timeline_sha` 的 producer 未定（edit/audio 提交时算还是 quality 自己算） |
| Material | 授权、下载登记、每场覆盖、候选真实存在 | 可核查（`candidate_clips` + 工作区登记） | 8 轮/16 动作/1800s 是“预发建议”，无基线数据 |
| Animation MG | 每 clip 同 SHA+背景 hash 的 HTML/帧/视觉与技术 QA | 见 §4.5 最后一行 | 背景 hash 无定义 |

**§4.3 灰度开关的问题**（除 §2 P1-12 的命名外）：

| 开关 | 声明行为 | 问题 |
|---|---|---|
| `stage_loop.structure.enabled` | off 时“走确定性/单发路径但仍经完成门与统一结果” | 需要“旧生成 + 新门”共存代码，无任务负责建；B15 的回滚点因此半不可执行 |
| `stage_loop.requirements.enabled` | off 时“只做结构化生成 + needs” | 与 B16 的“撤回新派发入口”耦合：一个开关承担了两个语义（是否走 drive / 是否开新入口） |
| `agent_loop.shared_driver` | off 时 Kernel 走既有 `_run_tool_loop` | 这是唯一一个“新旧两套实现并存”的开关，却恰好与 PRD §4“不允许第二套 while”正面冲突。要么承认过渡期存在两套，要么这个开关不存在 |
| `animation_agentic_loop` | 维持现网值 | 与 B14 的迁移目标矛盾：B14 把 MG 迁进公共 `drive` 后，旧 env 开关已无意义，但表里要求“维持现网值” |

### 5.6 排期表的可执行性结论

Plan §4.2 的 6 周表在“周 × 人”维度上有 3 处空档（W2 两人合计仅 3.75/10 人日）与 1 处超载（W4 同时排 F04 与串行的 X02）。这说明这张表是**按依赖关系画的**，不是按人力容量排的。作为派工依据可以接受（依赖是对的），作为工期承诺不可接受（见 §2 P1-10）。建议在 §4.2 表下加一句“本表按依赖排序，非人力容量排程；实际工期由 §8.2 的可用人力决定”。

## 6 两文档一致性

只列“有冲突 / 只有一边定义 / 名字不一致”的项；一致项（7 终态、`schema_version=2`、`report_state` 三值、`iterations` 语义、`stage` 在 research 为空、PRD §11 与 Plan §9 的 4 组待拍板默认值）不重复。

| # | 项 | PRD | Plan | 判定 | 处置 |
|---|---|---|---|---|---|
| I1 | 开关名族 | 只用“阶段开关”这种泛称；动画开关引 env `CLIPWRIGHT_ANIMATION_AGENTIC_LOOP`（:61） | 定义 `stage_loop.<stage>.enabled`、`agent_loop.shared_driver`、`animation_agentic_loop`；B14 回滚点又写 `animation.agentic_loop.enabled` | **冲突 + 单边** | 见 §2 P1-12；F7 |
| I2 | 里程碑编号 | §9 用 P0-1…P2-2（9 项） | §4.1 用 M0–M4（5 档），§3 用 B/F/X 编号 | **无映射** | 加一张 3 列映射表（PRD 分期 ↔ Plan 里程碑 ↔ 任务 ID 区间），否则进度汇报会出现两套口径 |
| I3 | 任务粒度 | 9 项“批”（如 P0-2 一次改 6 文件） | 28 项（粒度差约 3 倍） | **不对称** | 以 Plan 为派工单位，PRD §9 降级为“切换顺序说明”，在 PRD 顶部注明 |
| I4 | 删除清单 | §9.1 列 5 行，含 `report_text` 字典子节、`tool_calls_hint`、`stage/attempt` 轨迹匹配、`payload_of` 空 dict 回落、7 个 XOutput 签名 | §7.1 列 8 行，**缺**上述 5 项，**多** `_emit_report` | **不对齐** | Plan §7.1 补齐 5 项；`_emit_report` 回写 PRD（P2-13） |
| I5 | `needs` 的 producer | “`needs[]` 为唯一缺项真相，迁出 `FailureReport.needs`”（:223） | §2 needs 行 producer 写 `stage_subagent.py:520` —— 那正是 `StageInputMissing` → `StageResult.failure` 的构造点，即**旧路径** | **冲突** | Plan 该行 producer 改为“`ResultAssembler`（新）+ 删除 `_run_stage` 的 `failure.needs` 上浮（`agent_run.py:588-591`）” |
| I6 | `redo_request` 的组成 | 给了 5 个成员（scope/target stage/issue IDs/source run/base artifact+sha/typed overrides） | 只有“redo_agent 由 request 派生”，无成员定义 | **单边（PRD 独有）** | Plan §2 引用 PRD:224 的成员表，B19 的验收直接按 5 个成员断言 |
| I7 | `trace_state` 枚举 | 四值 `complete/partial/empty/unavailable`（:227） | 只用“empty≠unavailable”举例，无完整枚举 | **单边** | Plan §2 补枚举 |
| I8 | `cost_unknown` 语义 | “true 允许与已知部分并存”（:228） | “未知为 null；已知零为 0”（:103） | **表述冲突** | 统一成：“`cost_usd` 为已知部分合计（可为 0 或 null）；`cost_unknown=true` 表示合计不完整；二者并存合法” |
| I9 | `created_at` 的线格式 | 未提 | §1.3 要求“后端数值秒或显式 ISO，不依赖 JS 隐式解析” | **单边（Plan 更严）** | 回写 PRD §5.1，否则前端无法据此实现 |
| I10 | golden_eval 验收 | §10 给命令（:733） | §5.1 命令块无该命令 | **单边** | Plan §5.1 补第 6 条命令；归入 B11 验收 |
| I11 | 报告正文的 `schema_version` | “Markdown 可带 `schema_version/run_id/report_seq`”（:626） | §1.3 要求 `schema_version=2` **同时出现在 AgentResult、公开事件 detail 和 WorkspaceReport** | **过度合并** | 区分 `wire_version`（结果/事件）与 `report_version`（正文信封），见 §4.2 C8 |
| I12 | 阶段墙钟下限 | 只在 §2.1 提“阶段墙钟存在请求下限，允许 0 表示不设上限” | 28 项任务均未列“墙钟下限与 `stage_loop` 预算的关系” | **单边** | 在 B12 的“阶段预算执行”验收里显式写 `effective_stage_timeout` 与阶段预算的先后（现 `stage_subagent.py:587`） |

## 7 遗漏项（两者都没覆盖或覆盖不足）

| # | 遗漏项 | 现状证据 | 为什么必须补 |
|---|---|---|---|
| G1 | **并发与竞态模型** | `_RUNS`/`_IDEM` 是进程内 dict 无锁（`stage_subagent.py:44-47`）；`reserve_seq` 靠重试（`stage_report.py:37-50`）；黑板 CAS 只在提交时（`task_blackboard`）。两文档只说“原子提交”，从未定义“同一 task 能否并行跑两个阶段”“同阶段能否重入” | 新的 `drive` + RunStore 一旦允许多 run 并发，`run_id` 原子分配与黑板 revision 的耦合是首要炸点；没有并发模型就无法写并发测试 |
| G2 | **取消在飞动作的真实路径** | `agent_run._LIVE` 存了 `asyncio.Task` 句柄（`agent_run.py:412-414`）；`ask_user.cancel_owner` 存在（`agent_kernel.py:923-925`）；但阶段内 ffmpeg 子进程 / HTTP 请求的取消无定义 | PRD §4.1 说“取消在下一个动作前与 dispatch 前后三处检查”，只覆盖“还没发出的动作”。“已发出、正在跑”的动作（渲染 8 分钟）没有 kill 契约，用户点停止后会等到底 |
| G3 | **权限档位 fail-closed 发生在哪一层** | `BaseTool.permission_tier: str = ""`（`tool/base.py:60`）；`TIER_MAP` 已空（`capability_matrix.py:57-61`）；插件工具缺档位=不进工具面，内置工具缺档位=硬失败 | 两文档都写“缺档位 fail-closed”，但**注册时**还是**调用时**决定行为不同：前者让工具彻底消失，后者让调用返回错误。测试写法完全不同 |
| G4 | **事件契约版本化流程** | `tests/test_event_contract.py` 是既有门禁（`EVENT_TYPES` ↔ 前端 parser 键集合，见 AGENTS.md 验证清单）；本次要新增 `subagent_round` 并改 6 类 detail | Plan F03 只写“改 decoder”，没写“新增 `EVENT_TYPES` 条目 + 同步前端 parser 键集 + 更新 `test_event_contract.py`”。漏这步，`pytest tests/test_event_contract.py` 会红，而它在 CI 必跑 |
| G5 | **i18n / 语言** | 四节标题是硬编码中文（`stage_report.py:66-69`、`:101-104`）；前端 `CopilotDocsTab` 直接拼中文提示 | 取消固定格式后正文语言由模型决定（可能英文/中英混），而 UI 文案仍是中文；报告“无标题”合法化后，列表与详情没有任何语言回退约定 |
| G6 | **可观测性落地点** | Plan §6.3 列 7 个指标 + 阈值（`protocol_error_total`、`report_state_unavailable_ratio`…），但仓内观测只有 `task_events`（SSE，任务级）与 `logger` | “`protocol_error_total=0` 阻断发布”没有采集实现、没有存储、没有告警通道。这类“指标只存在于文档”的门禁，在维护窗口里没人能执行 |
| G7 | **数据保留与隐私** | `details` 白名单**包含** `audio_config_internal`（`stage_subagent.py:755-760`）；`agent_run_start.detail` 带 `brief[:200]`（`agent_run.py:392`）；`api/workspace.py:210` 返回报告**绝对路径** | PRD:240 明令“TTS 凭据、服务绝对路径、私有配置不进入报告或 SSE”，但现实现三处都在泄。迁移若照抄白名单，等于把裁定写在文档里、违反写在代码里 |
| G8 | **回滚演练** | X02 的回滚点写得最细（停写→保全→隔离→双仓回退），但**没有任何任务安排“切换前演练一次回滚”** | 一次性切换无中间态，演练是唯一能验证“回退清单真的可执行”的手段；不演练，回滚点是纸面承诺 |
| G9 | **文档与不变量同步** | `J:\Clipweight-Client\AGENTS.md:164` 的不变量 #23 仍写 `clipwright/prompts.py`；后端 `AGENTS.md` 只有 1–12 与 24–26，没有 13–23 | 两文档都引 AGENTS.md 作为“可核对依据”，却没安排更新它。AGENTS.md 自己写着“不更新文档的代码变更不应合入主干”，违反即返工 |
| G10 | **历史数据对账口径** | §9.1 只说“旧数据隔离只读、不回填”；Plan §5.5 第 2 步只说“备份” | 切换后 `evaluation` 的成功率口径会变（旧 run 不在新 store），但没有任何“切换前后指标可比性”说明；评估面板会出现“历史归零”而无人解释 |
| G11 | **运维脚本与目录隔离** | X02 与 §9.1 都依赖“移动目录、起版本探针、清客户端缓存”三个动作，但两文档都没有脚本任务；`清 localStorage run/event 缓存` 还依赖前端缓存 key 的命名约定，也没有任务 | 维护窗口里最容易出人祸的就是手敲路径；Plan §6.2 自己也承认“归档误删：维护窗口脚本路径拼错”，却没把脚本纳入交付 |
| G12 | **capped / escrow 的双仓展示** | PRD 多处引“终止 PRD:273”并新增 `capped` 独立终态；`TaskFinishSummary.tsx:89` 读 `closeout.report_path`，`escrow` 已有事件字段（`agent_kernel.py:1665`） | F04 的组件清单没有 `TaskFinishSummary`（§5.4a），capped 与 failed 的 UI 区分也没有验收 → 用户看到“失败”而非“预算耗尽”，与裁定相悖 |
| G13 | **报告被人工删除后的状态** | 报告 append-only，但 `list_reports` 靠扫目录（`stage_report.py:206`），用户可以删文件；§7.2 只规定“即使不存在报告，结构化终态仍要查询得到” | `report_state` 该是 `unavailable`（写过但读不到）还是 `not_applicable`（本就没写）没有判定规则，前端会二选一猜 → 直接决定“重试”按钮是否存在 |

遗漏项按风险排序：**G1（并发）> G2（取消在飞）> G7（隐私泄露）> G4（事件契约门禁）> G11（运维脚本）> G6（指标无采集）> 其余**。前三项任一在切换日暴露，都会出现“文档没写、测试没有、现场无法判”的局面。

## 8 建议的最小修改清单

按“改哪个文件哪一节 + 改什么”列出，共 10 条，均为文档内改动，可直接执行。F1–F4 是派工前**必须**完成的。

| # | 文件 · 位置 | 改什么 | 对应问题 |
|---|---|---|---|
| F1 | PRD §5.1 字段表 + §5.1 伪代码；Plan §2 表 | 增 `brief`（必填键、可空串、语义=本次派发原始要求、重放快照来源、前端 RunDetail 读取）、增 `mode: Literal["task","inline"]`（必填、inline 禁写黑板）；伪代码 `AgentResult` 两个字段各加一行 | P0-01、C1 |
| F2 | PRD §5.1 `status` 行 + §5.2；Plan B01/B03 验收 | 写明 `dry_run` 的**两处**丢失点（`agent_run._map_status:695`、`stage_subagent.run:335`）；`RunStatus` 扩入 `dry_run` 且移除 `pending/running` 时同步 `_LIVE`/`list_runs.live`；B03 验收加“两层都不折 ok” | P0-02 |
| F3 | Plan §3.1 主路径 + §4.2 表 + 甘特图 + 依赖列 | X01 拆 X01a（fixture 起草，随 B01）/ X01b（双仓联验，M0 后）；主路径改 `B01→X01a→B02→…→F0x→X01b→X02`；B17 依赖补 B13；B21 依赖补 B14–B19，或把“删重复轮控”移出 B21 | P0-03、§5.2 |
| F4 | PRD §8 首段；Plan B03 | 明确唯一阶段 Adapter 的**符号名与归属文件**（建议 `stage_adapter.run_stage`），写明 `stage_subagent.run`（dict 包装）与 `run_stage` 的终局（删 or 窄转发），加“全仓仅一个 Adapter 符号”的静态断言 | P0-04 |
| F5 | Plan §2 表 | 「后端 consumer」列逐行重查，删掉 `agent_kernel.py:548` 的 8 次复用；`task_end.detail` producer 改 `928/1122/1393/1653/1690`；前端列加 `target-only` 标记并列出 F04 需新增的 8 个 nullable 字段 | P1-05、P1-06、P1-09 |
| F6 | PRD §5.2；Plan B19 | 新增“needs × redo_request × status”判定表（3×3），规定 `redo_request` 的合法产生者（quality/edit 的 `failed` + 可校验 `base_artifact_sha`）与 Kernel 的优先级 | P1-11 |
| F7 | Plan §4.3；B14 回滚点；§6.2 风险表 | 开关收敛为一层：`settings.stage_loop_<stage>_enabled` + `agent_loop_shared_driver`，把 `CLIPWRIGHT_ANIMATION_AGENTIC_LOOP` 标 legacy alias；三处名字统一 | P1-12、I1 |
| F8 | PRD §7.3 表 + §7.4 静态规则 | §7.3 增两行：`stage_report.list_reports` 的 H1→headline（`:216-221`）、`agent_kernel._preview_of`（`:2377-2384`，保留语义）；§7.4 静态扫描限定在 4 个后端文件 + 3 个前端文件，并注明“artifact ref 的 `preview` 不在删除范围” | P1-08 |
| F9 | Plan §8.2 + §4.2 注 + §8.1 | 二选一：重算关键路径并给出 M1 真实天数，或明确“6 周是日历约束（人力/窗口）而非工作量推导”；§8.1 的 `S=0` 行补“本轮无 S 任务” | P1-10 |
| F10 | `AGENTS.md`（后端）、`J:\Clipweight-Client\AGENTS.md`；Plan §7.1/§7.2 文件清单 | 后端补 #13/#21/#23 引用（或注明“见前端仓”）；前端 #23 由 `clipwright/prompts.py` 改为 `clipwright.prompts` 包；把 `docs/{workflow,api_reference,frontend-backend-parity}.md` 与两份 AGENTS.md 一并列入 X02 收尾；同时把 4 处测试路径加 `clipwright/` 前缀 | P2-16、P1-13、G9 |

### 8.1 执行建议（谁在什么时机改）

| 时机 | 动作 | 责任 | 判据 |
|---|---|---|---|
| 派工前（0.5 人日） | F1、F2、F4、F3 四条落进文档 | PRD/Plan 作者 | §2.2 的四条最小验证方式全部能写出 |
| B01 冻结时 | F5、F6 随 schema 冻结一起改 | 后端契约 owner | Plan §2 表每行 producer/consumer 都能打开文件核到 |
| B03–B05 期间 | F7、F8 随实现附带 | BE-A | 开关名全仓只出现一种；静态扫描规则可执行 |
| X02 收尾 | F9、F10 | 运维 + 文档 owner | AGENTS.md 与代码一致；`docs/{workflow,api_reference,frontend-backend-parity}.md` 已更新 |

### 8.2 如果只允许改 4 处

若资源受限，**只做 F1、F2、F3、F4**。这四条全部是“文本裁决”，不改任何技术方案，却直接决定 B01 冻结是否要返工、F 组是否有 fixture、阶段执行入口是否唯一。其余 6 条都可在实施中顺带修，不会造成方向性返工。

**审查者签署**：本报告全部结论可回溯到 §3 的 41 条核验与 §2 的证据列。未对 PRD/Plan 的技术方案做重写建议——仅指出契约不闭口处与可执行性缺口。若需要，我可以对 §2 的 4 条 P0 逐条给出可直接粘贴的修订文本。




