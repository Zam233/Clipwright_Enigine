# PRD：主 Actor 化 —— 移除 6 阶段管线，创作全走主 Agent + 阶段子 Agent

- 版本：v1.0（2026-10-26）
- 仓库：`J:\Clipwright`（后端）、`J:\Clipweight-Client`（前端）
- 分支：`feat/master-agent`
- 关联文档：[workspace-prd-2026-10-24.md](workspace-prd-2026-10-24.md)（工作区）、[pluginization-plan-2026-09-10.md](pluginization-plan-2026-09-10.md)、[structure.md](structure.md)、[workflow.md](workflow.md)

---

## 0. 一页结论

| 你要的 | 结论 | 关键点 |
|---|---|---|
| ① 完全移除 pipeline | **可行，但不能先删** | 6 阶段管线现在仍是唯一"能把片子做出来"的路径。必须先把"主 Agent 派阶段子代理"接通并验收，再拆管线；否则会出现"能规划、做不出来"的真空期 |
| ② 主 Agent 拉起动画/素材/质检等子 Agent 并拿回结果 | **能，缺三块基建** | 缺 ①**黑板书**（阶段之间的数据交换台）②**阶段子代理契约**（现在的 `spawn_subagent` 只能派"裸 LLM 只读调研员"，扛不动阶段 Agent）③**能力目录**（主 Agent 现在根本看不见 `spawn_subagent`） |
| ③ 修上轮实测的 bug | **7 条，均已定位到文件行** | 视觉解读名存实亡（三击）最重；其余 6 条为一致性/落点/接口语义问题 |

**核心判断**：这件事的难点不在删 2191 行 `pipeline_v2.py`，而在**把 pipeline 里那套"输入装配 + 结果合并 + 自愈重跑"的隐性知识，搬到主 Agent 可控的显式契约里**。搬歪了就会退化成"主 Agent 乱派、子 Agent 拿不到输入、时间线互相覆盖"。

预计 4 期、约 18–22 人日（含测试与回归）。

---

## 0.1 需求登记表（十条，逐字保留，唯一真值）

> 本 PRD 是**唯一真值**。此前分散在 `editor-copilot-prd-2026-10-26.md` 的前端三条（8/9/10）
> 已并入 **§19**（原文件保留为历史复现记录，冲突以本文为准）。
> 每条都给出「设计章节」与「验收编号」——**没有验收编号的需求视为未定义**。

| # | 需求（用户原话） | 设计章节 | 验收 |
|---|---|---|---|
| 1 | 主 agent 应当**每次执行前**知道先前上下文 | §17.6 决策上下文协议、§17.11 逐跳上下文契约、§18.2 `decide` 循环 | M16、M23、M25 |
| 2 | 子 agent 应当**回传文字性内容**，同时在工作区生成 markdown 给主 agent 读（不删除） | §17.3 汇报协议、§18.3 写入方与触发点 | M14、M15、M24、M29、M30 |
| 3 | 创意简报 / 规划书应当在工作区有 markdown，并且主 agent 应该去读 | §17.4 需求阶段文档、§18.3 触发点 | M19、M25 |
| 4 | 完善主 agent 和子 agent 的**工具集** | §17.5 两套工具清单（含"刻意不给"）、§18.4 落点表 | M26、M27、M32 |
| 5 | 前端**工作区应当能自选目录** | §17.8、§18.5 端到端链路、§19.6 前端 | M21、M22、M31 |
| 6 | PRD 没有实现**真正的现代 agent 工具**那样的主/子 agent 化 | §16.10 决策回路（成功路径也决策）、§16.12、§18.2 | M10、M12、M13、M6–M9 |
| 7 | 规划/执行工具过程中**先文字输出要做的事与所思所想**再执行；一个阶段完成后**阶段性向用户报告成果**；主 agent 根据需要拉起子 agent；**agent run fail 时错误先报主 agent，由它决定下一步** | §16.3 叙述（narration/intent/thinking）、§16.4 阶段汇报、§16.5 失败回流决策 | M8、M10、M6、M7 |
| 8 | **副驾驶标签页可活动**：可添加、可关闭；**只有需求对话不可关闭** | §19.2 副驾驶标签化 | U1–U4 |
| 9 | 主 agent 的一切都在**编辑器页**显示/运行，不再跳 `/task/{id}`；含日志、工具、Skill、召唤子 agent；下方显示**当前运行的子 agent**，点击在右侧**新增标签页**；不得跳出编辑器 | §19.3 编辑器内运行与子 agent 下钻 | U5–U9 |
| 10 | 回复 LLM 后出现**「正在思考」下拉**，点击**流式**输出思考内容；规划/执行工具前**先文字输出要做的事** | §19.4 思考与叙述（`thinking` 帧 + `ThinkingDisclosure`） | U10–U12、M8 |

**编号约定**：`M*` = 后端口径的验收（§16.10.5、§17.9、§18.6）；`U*` = 前端口径的验收（§19.7）。

---

## 1. 现状事实（全部有代码证据）

### 1.1 双轨已经存在，主 Agent 侧是"半成品"

| 事实 | 证据 |
|---|---|
| `proceed` 已双轨：`agent_mode=on` 走 Task（主 Agent），否则走老 6 阶段管线 | `clipwright/api/requirements.py`（`if str(settings.agent_mode).lower() == "on": return await _proceed_as_task(...)`） |
| 运行时 `agent_mode = on`（store 覆盖生效） | `J:\Clipwright\settings.json` = `{"agent_mode": "on"}`；`settings_store.apply_all()` 后读到 `'on'` |
| 主 Agent 能力面只有 **56** 项，且**不含任何编排能力** | 实测 `clipwright.services.agent_kernel._capability_catalog()`：`'spawn_subagent' in catalog == False`、`'run_pipeline' == False`、`'list_subagents' == False` |
| 根因：`build_surface()` 只收 `kind=="tool"` 的条目 + Skill | `clipwright/tool/surface.py:123`（`if e.kind == "tool"`）；`META_CAPABILITIES`（`clipwright/services/capability_matrix.py:141-174`，含 `run_pipeline`/`spawn_subagent`/`run_shell`/`run_python`）从未进入目录 |
| 执行侧其实通：Kernel 有专门的派发分支 | `clipwright/services/agent_kernel.py:75-79` → `services/subagent_capability.py` → `supervisor/spawn.py` |

**后果**：主 Agent 有能力派子代理，但规划器看不见，所以从不主动派；只有两种它决定不了的情况会派——质检低置信度时系统自动派（`pipeline_v2.py:1464 _spawn_diagnostician`）、人工打 API。

### 1.2 6 阶段管线的真实体量

| 模块 | 规模 | 说明 |
|---|---|---|
| `clipwright/services/pipeline_v2.py` | **2191 行** | 编排核心：`run()`、DAG 执行、自愈重跑、检查点、熔断、预算、诊断、派工单 |
| `clipwright/api/pipeline.py` | **1241 行 / 20 条路由** | 对外的管线 API（run / run-async / result / cancel / runs / orders / ledger / attribution / diagnosis …） |
| `clipwright/services/requirements_service.py` | **2151 行** | 需求对话 + 规划书；**内含 3 处直接构造并执行阶段 Agent**（`StructureInput` :1841、`MaterialInput` :1040、`AnimationInput` :1124），绕过了 `PipelineOrchestratorV2` |
| `clipwright/services/pipeline_capability.py` | — | 把整条管线包装成 `run_pipeline` 能力（`agent_kernel.py:71-74`） |
| 前端 | `api/pipeline.ts` 12 处、`api/supervisor.ts` 16 处、`admin/PipelineAdminPage.tsx` 6 处、`features/agent/AgentPanel.tsx` 5 处、`useSupervisorEvents.ts` 2 处、`HomePage.tsx` 1 处 | 管线 ID 已渗透进 API 客户端、事件订阅、管理页、需求面板 |

**重要事实**：`pipeline_v2.py` **不负责渲染**（全文件无 `RenderService`/`render_timeline` 调用）。管线产出 `final_timeline`，成片由 `api/render.py` 独立发起。**所以移除管线不会连带干掉渲染链路** —— 这是本次改造最大的运气。

### 1.3 阶段 Agent 的真实契约（要搬走的东西）

| 阶段 | 输入 | 输出 | 输入端装配点 |
|---|---|---|---|
| structure | `StructureInput`（persona_config / rag_context / creative_brief / production_plan） | `script_skeleton`, `scenes` | `pipeline_v2.py:2036` |
| material | `MaterialInput`（script_skeleton / material_plugin_config） | `candidate_clips` | `pipeline_v2.py:2047` |
| edit | `EditInput`（script_skeleton / candidate_clips） | `timeline`, `edit_notes` | `pipeline_v2.py:2057` |
| animation | `AnimationInput`（timeline / visual_config / image_assets） | `timeline`, `animation_plan` | `pipeline_v2.py:2068` |
| audio | `AudioInput`（timeline / audio_config） | `timeline`, 字幕/配音轨 | `pipeline_v2.py:2081` |
| quality | `QualityInput`（timeline / constraints） | `issues`, `redo_agent` | `pipeline_v2.py:2090` |

数据流全靠一个**隐式黑板** `state.shared_data` + `AgentBus`：
- 合并语义在 `pipeline_v2.py:1312-1333 _merge_agent_result`：非控制键（排除 `timeline/agent_name/decision/error/status`）并入 `result_data`；`timeline` 以 `bus.set_artifact("timeline")` 为准。
- 自愈语义在 `pipeline_v2.py:1336 _check_quality` + `:1357 _downstream_closure`：质检给出 `redo_agent` 时，按依赖闭包把下游一起重做（`edit` 重做必须连带 `animation`/`audio`，否则旁白/BGM 静默丢失）。
- 依赖图：`AgentDAG.get_execution_plan()` / `_DEPS`。

**这三段语义是管线最有价值的部分**，必须原样进主 Agent 的执行契约，否则"主 Agent 自由规划"会稳定地做出比管线更差的片子。

### 1.4 现在的子代理扛不动阶段 Agent（4 个硬伤）

| 硬伤 | 证据 | 影响 |
|---|---|---|
| 只能给"裸 LLM + 只读工具子集" | `services/subagent_capability.py:29-33`（`READONLY_TOOLS`）、`:130-132`（非空只读子集） | 阶段 Agent 需要结构化输入 + 写产物，派出去只会瞎猜 |
| 没有结构化输入通道 | `supervisor/spawn.py:191-227`（只有 `role` + `system_prompt` + 工具） | `MaterialInput` 这类 Pydantic 契约无法传 |
| 没有共享黑板 | `Task`（`schema/task.py:152-188`）没有 `shared` 字段；只有 pipeline 的 `state.shared_data` | 子代理之间无法交接 `script_skeleton`/`candidate_clips`/`timeline` |
| 结果只回文本 + 成本 | `supervisor/spawn.py:244-254`（`output` 截断 2000 字） | 时间线对象、产物路径、降级清单全丢 |

附加限制（可保留，但要明说）：星型拓扑深度固定 1（`spawn.py:64-69`）、并发上限 6（`spawn.py:93-106`）、带 `run_shell`/`run_python` 需两级审核门（`spawn.py:123-140`）。

---

## 2. 目标与非目标

### 2.1 目标（可验收）

- **G1 一条创作路径**：创作入口只有一个——主 Agent（Task）。6 阶段管线不再是可执行路径，`run_pipeline` 能力下线。
- **G2 阶段即子代理**：7 个 Agent（requirements / structure / material / edit / animation / audio / quality）都能被主 Agent 作为**阶段子代理**派发，输入结构化、输出结构化。
- **G3 结果回流**：每次派发返回 `StageResult`（产物指针 + 摘要 + 降级 + 成本 + 失败分类），主 Agent 据此决定继续/重派/换阶段/收尾。
- **G4 自愈能力不退化**：质检 `redo_agent` + 下游闭包重做，语义与 `_downstream_closure` 一致。
- **G5 可观测**：任务事件流是唯一事实来源（`services/task_events.py`），前端能看到每个阶段子代理的输入摘要、输出、产物、成本、状态。
- **G6 上轮实测 bug 全修**（见 §7），且视觉解读要么真的看图，要么如实报 `degraded`。

### 2.2 非目标

- **N1 不做递归派生**：阶段子代理不得再派子代理（深度固定 1）。星型拓扑保留。
- **N2 不删历史数据**：Mongo 里的 `pipelines` 集合、`projects` 老布局、已有渲染产物一律保留（只读兼容）。
- **N3 不动渲染链路**：`api/render.py` 与渲染队列不改（只加"从工作区取真值"的可选参数）。
- **N4 不做多任务抢占调度**：并发仍按 task 维度排队，不做跨 task 的 CPU/GPU 抢占。

---

## 3. 目标架构

### 3.1 两层结构

```
                    ┌──────────────────────────────────────────┐
  用户一句话 ───────▶│ 主 Agent（AgentKernel · Task）             │
                    │  plan → 派发 → 观察 → 反思 → 收尾           │
                    └───────┬──────────────────────────────────┘
                            │ run_stage(stage, …)   # 同步；无 spawn_stage
        ┌───────────┬───────┼────────┬───────────┬────────────┐
        ▼           ▼       ▼        ▼           ▼            ▼
   structure   material   edit   animation     audio      quality
        │           │       │        │           │            │
        └───────────┴───────┴────────┴───────────┴────────────┘
                            │  读/写
                   ┌────────▼─────────┐
                   │ TaskBlackboard   │  script_skeleton / scenes /
                   │ (每 task 一份)    │  candidate_clips / timeline / issues
                   └────────┬─────────┘
                            │ 产物落盘
                   ┌────────▼─────────┐
                   │ 工作区 workspace/ │  materials assets renders cache logs
                   └──────────────────┘
```

### 3.2 新增：`TaskBlackboard`（黑板书）

pipeline 的 `shared_data` 私有化在 `PipelineState` 里，主 Agent 侧没有。新增：

`clipwright/services/task_blackboard.py`

```python
class TaskBlackboard:
    """按 task_id 存放阶段间共享数据；进程内 + 检查点，不写 Mongo 大字段。"""
    def get(task_id) -> dict
    def put(task_id, key, value, *, by: str) -> None      # 记录 by=哪个阶段写的
    def merge(task_id, data: dict, *, by: str) -> list[str]  # 返回被覆盖的键（审计用）
    def snapshot(task_id, keys: Iterable[str] | None = None) -> dict
    def reset(task_id) -> None
```

**键契约（与阶段契约同源，禁止随意新增）**

| key | 生产者 | 消费者 | 形状 |
|---|---|---|---|
| `creative_brief` | requirements | 全部 | `dict` |
| `production_plan` | requirements | structure / material / edit / animation / audio / quality | `dict`（含 `scenes`） |
| `script_skeleton` | structure | material / edit | `dict` |
| `scenes` | structure | material | `list[dict]` |
| `candidate_clips` | material | edit | `list[dict]` |
| `timeline` | edit → animation → audio（**单向加厚，只允许下游覆盖上游**） | 下一阶段 / 渲染 | `Timeline` 的 dict |
| `visual_config` / `audio_config` / `constraints` | persona/category 插件 / 请求 | animation / audio / quality | `dict` |
| `issues` / `redo_agent` | quality | 主 Agent（决定重派） | `list[dict]` / `str` |
| `final_timeline` | 主 Agent 收尾时写入 | 工作区 `timeline.json` / 渲染 | `dict` |

写入纪律：
1. 每次 `put` 记 `by`（阶段名），事件流里可见"谁改了时间线"；
2. `timeline` 只允许**下游覆盖上游**（edit → animation → audio），倒序写被拒绝并记 `degradation`；
3. 值超过 `MAX_BLACKBOARD_BYTES`（默认 2MB）时不进黑板，只存"工作区文件指针 + sha256"（防 Mongo/内存被时间线打爆）。

### 3.3 新增：阶段子代理契约

`clipwright/schema/stage.py`（新）

```python
StageName = Literal["structure","material","edit","animation","audio","quality",
                    "requirements"]        # requirements 仅"规划/改写"用途

class StageSpec(BaseModel):
    stage: StageName
    args: dict[str, Any] = {}              # 阶段特定参数（见 §3.4）
    provider: str = ""                     # 空 = 继承默认
    model: str = ""                        # 空 = 继承默认
    timeout_sec: float = 900.0             # 阶段是真干活，不是问答
    max_turns: int = 8
    dry_run: bool = False
    idem_key: str = ""                    # 幂等（重派同一阶段时复用结果）

class StageResult(BaseModel):
    stage: StageName
    status: Literal["ok","degraded","failed","refused","cancelled","needs_human_review"]
    # 产物：指针优先，值其次
    artifacts: dict[str, Any] = {}         # {"timeline": {"path": ..., "sha256": ..., "inline": {...}?}}
    summary: str = ""                      # ≤500 字，给主 Agent 决策用
    writes: list[str] = []                 # 本次写进黑板的 key
    degradations: list[dict] = []          # 复用 services/degradation.py 的 kind/detail
    issues: list[dict] = []                # quality 专用
    redo_agent: str = ""                   # quality 专用
    cost_usd: float | None = None          # None = 未知（不伪造 0）
    duration_ms: int = 0
    failure: FailureReport | None = None   # 复用 schema/agent.py 的失败分类
```

### 3.4 输入装配器（把 pipeline 的隐性知识搬成显式函数）

`clipwright/services/stage_inputs.py`（新，**逻辑从 `pipeline_v2._dispatch` 原样搬家**）

```python
def assemble(stage: StageName, *, task: Task, blackboard: dict,
             override: dict | None = None) -> BaseModel:
    """按 §1.3 的表格构造 XInput；缺关键键时抛 StageInputMissing（不猜）。"""
```

规则：
- `structure` 需 `creative_brief`/`production_plan`（可从 `task.request.extra` 取）；
- `material` 需 `script_skeleton`；`edit` 需 `script_skeleton` + `candidate_clips`；`animation`/`audio`/`quality` 需 `timeline`；
- **缺键 → `StageInputMissing`**，回报主 Agent `failure.category="input_insufficient"`，并给出"缺哪个键、该先派哪个阶段"的建议（不是一句报错）；
- `override` 允许主 Agent 显式覆盖（例如把用户新要求塞进 `constraints`）。

### 3.5 结果合并器

`clipwright/services/stage_merge.py`（新，移植 `pipeline_v2.py:1312-1333`）

```python
def merge(stage, result: StageResult, blackboard: TaskBlackboard, *, task_id) -> list[str]:
    """非控制键并入黑板；timeline 走单向加厚校验；返回写入的键。"""
```

控制键沿用 `{"timeline","agent_name","decision","error","status"}`。

### 3.6 能力面改造（让主 Agent 看得见、用得对）

**只有一条阶段派发能力**（`run_stage`，S1 后改名 `delegate`），**不存在 `spawn_stage`**——
理由见 §18.8：阶段是同步真干活，结果必须直接回主 agent；"spawn（派出去不管）"这种语义
会让数据绕过主 agent，是这次要消灭的东西。

| 能力 | 作用 | 与旧的关系 |
|---|---|---|
| `run_stage`（→ `delegate`） | 派一个**阶段子代理**并**同步等结果** | 新增，唯一派发通道 |
| `get_stage` / `list_stages` | 查状态/列已派阶段 | 语义同 `get_subagent_status`/`list_subagents`，但按 stage 维度 |
| `spawn_subagent` | 保留：只读调研员（核查员/检索员） | 不变 |
| `run_pipeline` | **下线**（§4） | 弃用 → 拒绝 → 删除 |

`capability_matrix.META_CAPABILITIES` 增补上述能力；`tool/surface.build_surface()` 改为**同时收 `META_CAPABILITIES`**（这是 §7-bug-6 的修法，顺带把 `run_pipeline` 从目录里摘掉）。

### 3.7 自愈：由主 Agent 执行"重派闭包"

质检返回 `redo_agent=edit` 时，主 Agent 不做新的推理，而是调用一个**确定性能力**：

```
replan_closure(redo_agent) -> ["edit","animation","audio"]
```

即把 `_downstream_closure`（`pipeline_v2.py:1357-1380`）搬到 `services/stage_closure.py`，主 Agent 用它生成重派计划。这样"该连带重做谁"不靠 LLM 记性，靠代码保证。

---

## 4. 移除 6 阶段管线的执行方案

### 4.1 分四期，禁止一步到位

| 期 | 目标 | 关键开关 | 验收 |
|---|---|---|---|
| **P1 影子期** | 新通道上线，管线保留；同一需求可双跑对比 | `settings.agent_mode=on` + 新增 `settings.stage_channel="agent"` | 用 5 个真实选题双跑：主 Agent 产出 vs 管线产出，人工判定不劣于管线（分辨率/时长/字幕/配音四项） |
| **P2 切换期** | `proceed` 只走 Task；`run_pipeline` 能力**拒绝**并给替代路径 | `settings.pipeline_enabled=false`（默认） | `POST /api/pipeline/run` 与 `run_pipeline` 能力均返回 `refused` + 明确替代说明；前端不再产出 pipelineId |
| **P3 拆除期** | 删前端调用点、删 API 路由、删 `pipeline_v2` 的执行入口 | — | 前端 `tsc` + `vitest` 绿；后端全量绿；`grep` 无 `pipelineId` 业务依赖 |
| **P4 清账期** | 删文件、删测试、文档改口、Mongo 只读化 | — | `pipeline_v2.py` 不在 import 图内；老数据仍可读（只读接口保留） |

**回滚**：P1/P2 用 `pipeline_enabled` 一键回滚；P3 用 git revert（此期不动 `pipeline_v2.py`）；P4 一旦合并不再回滚（前一期已灰度验证）。

### 4.2 删除清单（P3/P4 执行，P1/P2 只加不改）

| 对象 | 动作 | 风险 |
|---|---|---|
| `api/pipeline.py` 的 `run` / `run-async` / `result` / `cancel` | 删除（保留 `runs`/`ledger`/`attribution`/`diagnosis` 只读一段） | 前端 `homePage.launch` 曾依赖 `/run-async`；已迁 Task |
| `services/pipeline_capability.py` | 删除，`capability_matrix` 摘掉 `run_pipeline` | Kernel 退化计划回落（`agent_kernel.py:204-209`）必须改成"派 structure" |
| `requirements_service.py` 三处直调 Agent（:1040/:1124/:1841） | 改为 `run_stage`（同一契约，去掉重复实现） | 这三处是"管线之外的第二条路"，删不干净就会留下两套行为 |
| `pipeline_v2.py` | P4 删除（先把 §3.4/3.5/3.7 的逻辑搬走） | **必须搬走**：输入装配、结果合并、下游闭包、熔断、检查点 |
| 前端 `admin/PipelineAdminPage.tsx` | 改造成「任务诊断页」（数据源换 `task` 事件流） | 管理页是运维入口，不能直接删 |
| 前端 `api/pipeline.ts` 12 处调用 | 收敛到 `taskApi` + `supervisorApi` | `useSupervisorEvents` 订阅的 pipeline 事件需映射到 task 事件 |
| `proceed` 的双轨分支 | 删掉 `agent_mode != on` 分支（`settings.agent_mode` 可保留为"是否允许降级到单 Agent"的兼容开关，但创作路径只有一条） | 用户可能把 agent_mode 关掉；关掉时应明确报"当前不支持无主 Agent 创作"而不是静默走管线 |

### 4.3 保留但降级的东西

- `AgentBus`：阶段子代理内部仍用它做产物/事件，但**不再作为跨阶段真值载体**（真值在黑板书）。
- `PipelineState` → 改名 `TaskRunState`，只保留"步骤/用量/事件"三件事，`shared_data` 迁到黑板。
- Mongo `pipelines` 集合：只读，前端历史页可看，不再写入新记录（或双写 `tasks`）。

---

## 5. 子代理扩展细节

### 5.1 拓扑与并发

- 深度固定 1：阶段子代理的 `extra_params.host_agent` 必须非空，任何再派生直接拒绝（沿用 `spawn.py:64-69`）。
- 允许主 Agent **并行派**互不依赖的阶段（例如 `material` 与"BGM 候选"这类只读任务）；有依赖的阶段由 `StageInputMissing` 强制串行。
- 新增 `settings.max_concurrent_stage_workers`（默认 3，与 `max_concurrent_subagents=6` 分开——阶段 worker 吃 CPU/ffmpeg，不能按轻量 worker 算）。

### 5.2 工具与写权限

| worker 类型 | 工具集 | 审核门 |
|---|---|---|
| 阶段子代理（structure/material/edit/animation/audio/quality） | 该阶段 Agent 自己的能力集（`BaseAgent.list_agent_tools()` 现状）+ 只读工具 | **不需要**（它们是内部可信阶段，且写范围被限制在工作区） |
| 调研子代理（`spawn_subagent`） | 只读子集 | 不需要 |
| 执行子代理（`run_shell`/`run_python`） | 执行工具 | 两级审核门（不变） |

写权限的边界：阶段子代理的写操作**只能落在 `workspace_root` 内**（`materials/` 除外——那是用户区）。用 `security.assert_allowed_path` + workspace 前缀双重校验；越界立即 `failed` 并记 `policy_refused`。

### 5.3 墙钟、取消、预算

- 每阶段墙钟默认 900s（`StageSpec.timeout_sec`），超时 → `failure.category="timeout", retryable="retry_same"`，主 Agent 可换模型重派一次。
- 取消：`tracker.cancel` → `cancel_cb` → `task.cancel()`（沿用 `spawn.py:318-325`）；阶段子代理内部的长任务（ffmpeg）需在 `CancelledError` 时终止子进程，不得留下孤儿。
- 预算：`budget_guard.is_blocked(pid)` 在派发前检查；阶段内 LLM 调用沿用 `record_llm_call(agent_name=f"stage:{stage}")`，账本单列"阶段开销"。

### 5.4 模型指定（含修 bug）

- `StageSpec.provider/model` 生效路径与现状一致（`ModelRoute` + `use_route`）。
- **必须补**：`is_route_allowed()` 校验。不在白名单的 `provider/model` → `refused`（`failure.category="policy_refused"`），**不许静默回退默认模型**（现在的行为是只打一行 warning 就用默认模型，等于欺骗用户）。
- 继承语义保持：留空 = 继承当前默认模型，并在 `StageResult` 里**回填实际使用的 provider/model**（让用户看得见到底用了谁）。

### 5.5 失败分类与主 Agent 决策表

| `failure.category` | `retryable` | 主 Agent 该做什么 |
|---|---|---|
| `input_insufficient` | `needs_replan` | 先派缺失的上游阶段（依 §3.4 建议） |
| `timeout` | `retry_same` | 重派一次（可换更快的模型） |
| `model_incapable` | `retry_other_model` | 换模型重派 |
| `tool_failed` | `retry_same` | 看 `evidence.last_tool_call` 决定换工具或降级 |
| `budget_exhausted` | `needs_human` | 停并告知用户预算 |
| `policy_refused` | `permanent` | 不重试，报告并请求人工 |
| `pipeline_failed` → 改 `stage_failed` | `needs_human` | 附证据收尾 |

### 5.6 结果回流的可见性

每次 `run_stage` 产生三类事件（复用 `task_events.emit`）：`step_start`（含 stage + 输入摘要）、`tool_call`、`step_end`（含 `StageResult.summary` + `artifacts` + `cost_usd`）。前端 TaskPage 的阶段卡片直接消费。

---

## 6. 能力目录修复（P0，一行级）

**问题**：`_capability_catalog()`（`agent_kernel.py:279-289`）用 `build_surface()`，而 `build_surface()` 只收 `kind=="tool"` + Skill，`META_CAPABILITIES` 从未进入 → 主 Agent 看不见编排能力。

**修法**：

```python
def _capability_catalog() -> str:
    from clipwright.tool.surface import build_surface
    from clipwright.services.capability_matrix import META_CAPABILITIES
    rows = [f"- {e.name}（{e.tier}）: {e.description[:60]}"
            for e in build_surface() if e.available][:60]
    rows += [f"- {m['name']}（{m['tier']}）: {m['notes'][:80]}" for m in META_CAPABILITIES]
    return "\n".join(rows) or "（能力目录不可用）"
```

**提示词同步**（`agent_kernel.LLMPlanner.plan` 的 `prompt`）：补一段"编排能力用法"——`run_stage` 的 `stage` 取值、`args` 该填什么、`spawn_subagent` 什么时候用（只读调研）、失败时的 `failure.category` 含义。**目录给了名字不给参数说明，模型照样填错**。

**验收**：`'run_stage' in _capability_catalog()` 为 True（且目录里**没有** `spawn_stage`——它不该存在），目录项数 ≥ 60；plan 请求体里能看到编排能力的 args 说明。

---

## 7. Bug 修复清单（上轮实测，7 条）

| # | 问题 | 落点 | 修法 | 验收 |
|---|---|---|---|---|
| B1 | 视觉解读**名存实亡**：产出文件名伪标签（`tags=['封面图','横版']`，`summary='文件: 封面图.png (640x360)'`） | `content_vision.py:320`（`content_index_visual_llm=False` 直接跳过）、CLIP 权重拉不下来、`vision.py:267` 文件名兜底 | ① 打开 `content_index_visual_llm`；② `vision_llm_model` 指向**多模态**模型（当前回退到 `deepseek-v4-pro`，纯文本看图必失败）；③ CLIP 权重改为可配置 `content_index_clip_model_path`（离线目录）并允许 off | 同一图片的 `vision.summary` 不含文件名与 `WxH` 字样；含画面元素描述；`evidence["visual.description"]` 以 `llm/` 开头 |
| B2 | **假阳性 status**：tags 来自文件名/尺寸却报 `ok` | `workspace_index.py:126-133` | tags 只来自兜底来源时返回 `status="degraded"`，并带 `evidence.source="filename"` | 单测：无模型可用时 `_vision_for(...)["status"] == "degraded"` |
| B3 | **中文路径读不了图**：`cv2.imread` 报 `can't open/read file` | `content_vision._detectors` / `vision.py` 读图处；抽帧名用中文 `stem` | 读图改 `cv2.imdecode(np.fromfile(path, np.uint8), ...)`；抽帧文件名改 `sha8_i.jpg` | 中文名素材的 `vision.tags` 与英文名素材一致（同一图） |
| B4 | `meta.index.provider` **硬编码 `"transformers"`**，与实时 `settings.vision_provider='llm'` 不一致 | `workspace.py:234` | 写元数据时取 `settings.vision_provider`；`status()` 返回实时值 + `snapshot` 原值两份 | 面板显示的 provider == `settings.vision_provider` |
| B5 | **下载落点不存在**：`materials/{video,image,audio}` 建好了但没人往里写；`video_download` 落 `tempfile.mkdtemp()` 且全仓库无调用者 | `tool/video.py:270-274` | ① `video_download` 默认落 `workspace/materials/<kind>/`（无工作区才回退临时目录）；② 补一个真正调用它的入口（素材检索命中 → 下载 → 落盘 → 触发索引） | 调用一次下载工具后，`GET /api/workspace/{id}/materials` 出现该文件且 `status=indexed` |
| B6 | **能力目录缺编排能力**（主 Agent 看不见 `spawn_subagent`/`run_pipeline`） | `agent_kernel.py:279`、`surface.py:123`、`capability_matrix.py:141-174` | 见 §6 | `'spawn_subagent' in catalog` 为 True |
| B7 | **模型白名单不生效**：子代理 `/阶段` 指定越权 provider 时静默回退默认模型 | `supervisor/spawn.py:201-207`（无 `is_route_allowed`） | 派发前校验，越权 `refused`；`StageResult` 回填实际 provider/model | 单测：`provider="不存在的源"` → `status="refused"`，且不产生 LLM 调用 |
| B8 | `GET /api/workspace/{id}/events` 是 SSE 长连接，普通 GET 会挂死（实测挂 120s） | `api/workspace.py` 事件端点 | 拆成两个：`GET .../events?since=`（**返回 JSON 数组**）+ `.../events/stream`（SSE） | 普通 GET 100ms 内返回；SSE 端点仅在被显式订阅时挂起 |
| B9 | 面板不展示视觉标签/摘要，用户看不到"模型看到了什么" | `WorkspaceMaterialsPanel.tsx:149-154` | 展示 `vision.tags` / `vision.summary` / 来源（`llm` / `clip` / `filename`），`degraded` 用警示色 | 截图级验收：面板出现画面描述文本 |
| B10 | 成品不进工作区 `renders/`（PRD 宣称"项目一切都在工作区"） | `services/render.py` 输出路径未接 `workspace_root` | render 入口接受 `workspace_root`，产物写 `workspace/renders/`；无工作区回退全局 `renders/` | 渲染完成文件出现在 `workspace/renders/`，`workspace/status()` 计数正确 |
| B11 | `logs/` 目录建了没人写 | 无写入方 | 阶段子代理/渲染的日志 tee 到 `workspace/logs/<date>.log`，按 `logs_keep_days=7` 清理 | 跑一次任务后 `logs/` 非空 |

> 说明：B1–B5、B8–B11 来自 2026-10-26 的在线实测（启动后端 + 真实 ffmpeg/API）。修复必须带单测，不接受"改完看着对"。

---

## 8. 数据契约变更

### 8.1 新增

| 对象 | 文件 | 说明 |
|---|---|---|
| `TaskBlackboard` | `services/task_blackboard.py` | 见 §3.2 |
| `StageSpec` / `StageResult` | `schema/stage.py` | 见 §3.3 |
| `assemble()` | `services/stage_inputs.py` | 见 §3.4 |
| `merge()` | `services/stage_merge.py` | 见 §3.5 |
| `replan_closure()` | `services/stage_closure.py` | 见 §3.7 |
| `stage_subagent.py` | `services/stage_subagent.py` | `run_stage/get_stage/list_stages` 的实现（无 spawn_stage） |

### 8.2 变更

| 对象 | 变更 | 兼容 |
|---|---|---|
| `Task`（`schema/task.py`） | 新增 `stages: list[StageResult]`、`blackboard_keys: list[str]` | 老检查点反序列化时缺省为空 |
| `capability_matrix.META_CAPABILITIES` | 增 `run_stage`/`get_stage`/`list_stages`（**不加 `spawn_stage`**）；`run_pipeline` 标 `deprecated` 后删除 | — |
| `settings` | 新增 `stage_channel`、`pipeline_enabled`、`max_concurrent_stage_workers`、`content_index_clip_model_path` | 均带默认值，不改 `config_schema.MISSING_META` 校验 |
| API | `POST /api/task/{id}/stage`（手工派阶段，运维/调试用）、`GET /api/task/{id}/stages` | 新增，不动老端点 |
| API（废弃） | `POST /api/pipeline/run`、`/run-async`、`/result` | P2 起返回 `410 Gone` + 替代说明 |

---

## 9. 验收标准

| # | 场景 | 判据 |
|---|---|---|
| A1 | 单入口 | 前端不存在"一句话出片（管线）"入口；`POST /api/pipeline/run` 返回 410；`run_pipeline` 不在能力目录 |
| A2 | 主 Agent 会派阶段 | 一次真实创作的任务事件流里出现 ≥4 次 `run_stage`，且阶段顺序满足 `AgentDAG` 依赖（structure→material→edit→animation→audio→quality 的子序列） |
| A3 | 输入装配正确 | 每个阶段 `step_start.detail.input_summary` 里，必需键齐（material 有 `script_skeleton`，edit 有 `candidate_clips`，animation 有 `timeline`） |
| A4 | 结果回流可用 | 主 Agent 的 `reflect` 输入里能看到上一阶段的 `StageResult.summary + artifacts`（不是只有一句话） |
| A5 | 时间线不互相覆盖 | 倒序写 `timeline`（audio 之后 edit 再写）被拒绝并记 `degradation`；最终 `final_timeline` 的 `clips[].subtitle` 存在 |
| A6 | 自愈不退化 | 构造 `quality` 返回 `redo_agent=edit`，主 Agent 重派闭包 = `[edit, animation, audio]`（与 `_downstream_closure` 一致），且最终成片有配音/字幕 |
| A7 | 预算/成本可见 | `task.usage.cost_usd` == 各阶段 `cost_usd` 之和（未知成本单独计数，不记 0） |
| A8 | 取消真的停 | 派发中 `POST /api/task/{id}/cancel` → 3s 内状态 `cancelled`，无残留 ffmpeg 子进程 |
| A9 | 越权模型被拒 | 传未白名单 provider → `refused` + `policy_refused`，无 LLM 调用 |
| A10 | 视觉解读是真的 | 一张真实照片 → `vision.summary` 含画面元素，`status=ok`，`evidence` 为 `llm/` 或 `clip:` |
| A11 | 假阳性被消灭 | 模型不可用 → `status=degraded` + `source=filename`（不是 `ok`） |
| A12 | 中文路径 | 中文名图片/视频的视觉结果与同内容英文名一致 |
| A13 | 下载落工作区 | 调用下载入口后 `materials/<kind>/` 出现文件且 `indexed` |
| A14 | 回归基线 | 后端 `pytest tests -q` 全绿；前端 `tsc --noEmit` + `vitest run` 全绿；同一选题的成片与管线时代对比，时长误差 ≤2%、字幕/配音不缺 |

---

## 10. 分期与工作量

| 期 | 内容 | 人日 | 交付物 |
|---|---|---|---|
| **P0** | §6 能力目录 + §7 的 B1–B4、B6–B9、B11（与主 Agent 化解耦，可先落地） | 3 | 主 Agent 能看见并派子代理；视觉解读变真 |
| **P1** | §3.2–3.5 基建（黑板/阶段契约/装配/合并）+ §3.6 `run_stage` 通道 | 6 | 主 Agent 可派 6 个阶段并拿回结构化结果（管线仍在） |
| **P2** | §3.7 自愈闭包 + `pipeline_enabled=false` 切换 + 前端 Task 事件流接入 | 5 | 创作只走主 Agent；管线 API 返回 410 |
| **P3** | §4.2 拆除（API/前端/requirements_service 三处直调） | 4 | 前端无 pipelineId 依赖 |
| **P4** | 删 `pipeline_v2.py` + 文档改口 + 测试基线重写 | 3 | 通过 A1–A14 |
| **合计** | | **21** | |

可并行：B5/B10（下载/渲染落工作区）与 P1 无耦合，可插空做。

---

## 11. 风险与不做

| 风险 | 说明 | 缓解 |
|---|---|---|
| **质量回退** | 管线里有大量"踩出来的经验"（自愈闭包、熔断、检查点、降级登记），主 Agent 自由规划容易丢 | P1 只做"能力对齐"，规划提示词里给显式阶段序列建议；A2/A6 卡死 |
| **成本上升** | 主 Agent 多轮 plan/reflect + 阶段子代理各自的 LLM 调用 | 预算闸默认开（`TaskLimits` 四道闸）；`run_stage` 合并一步，减少一轮 reflect |
| **时间线被写坏** | 多个阶段写 `timeline` | 单向加厚校验 + `by` 审计 + 黑板书 2MB 上限转指针 |
| **工作区与渲染脱节** | 成片落全局 `renders/`（B10） | B10 与 P1 同期做，别留到 P4 |
| **老数据不可读** | Mongo `pipelines` 集合停写 | 只读接口保留一个版本周期，前端历史页只读 |
| 不做 | 递归派生、跨 task 抢占调度、远程渲染改造、多租户配额 | — |

---

## 12. 测试计划

| 层 | 内容 |
|---|---|
| 单测 | `test_task_blackboard.py`（写/覆盖/超限转指针/倒序写拒绝）、`test_stage_inputs.py`（缺键报 `input_insufficient`）、`test_stage_merge.py`（控制键排除、timeline 单向）、`test_stage_closure.py`（闭包 == `_downstream_closure`） |
| 契约测试 | `test_subagent_spawn.py` 扩展：`is_route_allowed` 拒绝路径；`StageResult` 字段完备性（schema 冻结） |
| 能力面 | `test_agent_capability_catalog.py`：`run_stage` 在目录内、`spawn_stage` **不在**；`run_pipeline` 在 P2 后不在 |
| 端到端 | 3 个选题（知识区长片 / 鬼畜快剪 / Vlog）跑真实任务：断言 A2–A7；记录成本与耗时基线 |
| 前端 | TaskPage 阶段卡片渲染 `StageResult`；`useTaskEvents` 消费 `step_end`；`tsc` + `vitest` |
| 视觉链路 | 4 张真实图 + 2 段视频：断言 A10/A11/A12；模型不可用时不得 `ok` |
| 回归 | 后端全量（当前基线 4393 passed）；前端 970 passed |

---

## 13. 实施记录（2026-10-26）

> **读法注意**：本节记录的是 P0–P4 的**历史落地**（代码可跑、有测试）。
> §16（决策回路）、§17（文档协议/工具集）、§18（审计后修订）是**其后追加的设计，尚未实现**。
> 两者冲突时以 §18 为准，不要把设计读成"已完成"。

### 13.1 已落地

| 期 | 需求 | 状态 | 落点 |
|---|---|---|---|
| P0 | 能力目录含编排能力（B6） | ✅ | `agent_kernel._capability_catalog` 合并 `META_CAPABILITIES`，且过滤「已下线」项；`capability_matrix` 补 `get_subagent_status/list_subagents/cancel_subagent/run_stage/get_stage/list_stages/replan_closure/validate_stage_plan` |
| P0 | 视觉解读真看图（B1/B2/B3） | ✅ | `content_vision.label_shot` 改为 **clip → llm → 本地分类器** 顺序（LLM 优先，避免文件名伪标签混入）；`imread_unicode()` 修中文路径；`vision.py` 三个分支回填 `source`；`workspace_index._vision_for` 用 `source` 判定 `ok/degraded` |
| P0 | provider 硬编码（B4） | ✅ | `Workspace.ensure` 写实时 `settings.vision_provider`；`status()` 再按实时值纠正并保留 `provider_stale` |
| P0 | 工作区事件端点（B8） | ✅ | `GET /api/workspace/{id}/events` 改为 **JSON 立即返回**；SSE 移到 `/events/stream` |
| P0 | 模型白名单（B7） | ✅ | `supervisor/spawn.py` 派发前 `is_route_allowed` 校验，越权直接 `SpawnError` |
| P0 | logs/ 空目录（B11） | ✅ | `Workspace.append_log/cleanup_logs`；`scan_and_index` 落扫描流水 |
| P0 | 面板展示视觉结果（B9） | ✅ | `WorkspaceMaterialsPanel` 新增「画面（视觉解读）」列 + 来源标识 + degraded 高亮 |
| P0 | 下载落工作区（B5） | ✅ | `video_download(workspace_root=)` 默认落 `materials/<kind>/`，无工作区才回退临时目录 |
| P0 | 成片落工作区（B10） | ✅ | `RenderRequest.workspace_root` + `_render_out_path()`；`allowed_media_roots()` 加入 `workspace_dir` |
| P1 | 黑板 | ✅ | `services/task_blackboard.py`：`by` 归属、时间线单向覆盖、2MB 转指针 |
| P1 | 阶段契约 | ✅ | `schema/stage.py`（`StageSpec`/`StageResult`）、`services/stage_inputs.py`（缺键 `StageInputMissing` + 建议）、`services/stage_merge.py`（控制键排除、audio 顺带定稿 `final_timeline`） |
| P1 | 阶段执行通道 | ✅ | `services/stage_subagent.py`（`run_stage/run/run_inline/get_stage/list_stages`）+ `services/stage_capability.py`（`run_stage/get_stage/list_stages`）+ kernel 分支 |
| P2 | 自愈闭包 | ✅ | `services/stage_closure.py`：`closure/edit → [animation, audio]`、`replan_plan`、`validate_plan`；能力 `replan_closure`/`validate_stage_plan` |
| P2 | 黑板防绕过 | ✅ | 写 `timeline` 的 `by` 必须是 `TIMELINE_STAGE_ORDER` 里的生产者，未知/匿名一律拒绝（否则 `new_rank=99` 会绕过单向校验） |
| P2 | trace 鉴权 | ✅ | `/api/pipeline/trace/{id}` 的 task 分支**先**做 `_enforce_pipeline_owner`（该函数已支持 task id），再取事件——兼容层不放宽鉴权 |
| P2 | 管线下线 | ✅ | `settings.pipeline_enabled=False`（默认）；`pipeline_capability.run` 拒绝并给 `run_stage` 替代；`POST /api/pipeline/run-async` → **410**；`proceed` 单轨（`agent_mode!=on` 且管线下线 → 409 明确报错） |
| P2 | 退化计划 | ✅ | 规划失败退化为 **6 个 `run_stage`**（依赖序），不再回头打管线 |
| P3 | 三处直调 Agent | ✅ | `requirements_service` 的 material/animation/structure 三处改走 `stage_subagent.run_inline`（不再各自 `new Agent()`） |
| P3 | 兼容层 | ✅ | `GET /api/pipeline/trace/{id}` 与 `/trace/stream/{id}` 接受 `task_*`（映射 `task_events`），前端无需为两条链路写两套订阅 |
| P3 | 前端口径 | ✅ | 管理页标题改「执行诊断（老管线只读）」；`proceedResult` 仍优先 `task_id` |

### 13.2 与 PRD 的差异（必须知道）

1. **P4 的物理删除没有执行**：`pipeline_v2.py`（2191 行）与 `/api/pipeline/*` 仍在仓库里。
   原因不是"没做完"，而是删除它们会连带打断三件事：`api/pipeline.py` 模块级 import、
   前端 `admin` 页的数据源、以及 ~30 个测试文件的基线。PRD 原话就是「P4 需要测试基线重写」。
   **执行路径已经切断**（能力拒绝 + 端点 410 + 退化计划 + proceed 单轨），运维语义上管线已下线；
   物理删除建议单独一个 PR，只做删除与测试清理，不再夹带行为变更。
2. **`requirements` 不作为阶段子代理真跑**：`STAGE_NAMES` 里有它，但 `stage_inputs.assemble`
   只返回参数包（需求阶段走 `requirements_service` 的对话链路）。派发它会得到 `dry_run` 语义。
3. **`run_inline` 不写黑板**：预览/局部修补（换素材、重做动画、重新翻译）保留"无副作用"语义，
   只复用统一的实例化与墙钟通道。
4. **成本记账是估算**：阶段成本用进程级 `llm_usage_snapshot()` 差值 × 价目表；拿不到就是
   `None + cost_unknown=True`（不伪造 0）。
5. **视觉依赖外部多模态**：当前 `vision_llm_model=deepseek-chat`（DeepSeek flash）。
   模型不支持图像时会退到 `degraded`（`source=filename`），而不是假装成功。
6. **`pipeline_enabled=true` 仍是可用后门**：灰度对比与回滚用；生产默认 false。

### 13.3 验收结果

| # | 场景 | 结果 |
|---|---|---|
| A1 | 单入口 | ✅ `agent_mode=on` 时 `run-async` 转 Task（200，返回 `task_*`）；`agent_mode!=on` 且 `pipeline_enabled=false` 时 **410**；`run_pipeline` 不在能力目录；`proceed` 单轨（无通道时 409 明确报错）；`retry` 也受闸（410） |
| A2 | 主 Agent 会派阶段 | ✅ 目录含 `run_stage`；退化计划即 6 阶段序列（`test_stage_closure_p2`） |
| A3 | 输入装配 | ✅ 缺键 → `input_insufficient` + `needs[{from_stage}]` |
| A4 | 结果回流 | ✅ `StageResult`（产物/摘要/writes/成本/失败分类/实际模型） |
| A5 | 时间线单向 | ✅ 倒序写被 `BlackboardError` 拒绝（单测） |
| A6 | 自愈不退化 | ✅ `closure("edit") == ["animation","audio"]`，quality 永不入闭包 |
| A7 | 成本可见 | ✅ 无用量 → `None + cost_unknown`（不伪造）；有差值才折算 |
| A8 | 取消 | ✅ `wait_for` + `CancelledError` → `status=cancelled` |
| A9 | 越权模型被拒 | ✅ `spawn` 与 `run_stage` 双路径都 `policy_refused` |
| A10/A11/A12 | 视觉真读画面 / 不假阳性 / 中文路径 | ✅ 实测 `summary=橙色背景中央有一个白色正方形。`、`source=llm`；模型**真失败**时 `degraded`（独立校验发现过一次假阳性，已修 + 加回归用例）；`imread_unicode` 修中文路径 |
| A13 | 下载落 materials | ✅ 单测断言落 `materials/video/` |
| A14 | 回归 | 后端见 §13.4；前端 `tsc` 干净 + 970 passed |

### 13.4 回归

- 新增测试：`tests/test_master_agent_p0.py`（16 条）、`tests/test_stage_subagent.py`（17 条）、
  `tests/test_stage_closure_p2.py`（10 条）；`tests/test_subagent_spawn.py` 补白名单用例。
- `tests/conftest.py` 默认 `HF_HUB_OFFLINE=1`：模型权重都在本机缓存，但加载时会先发 HEAD 探网，
  网络抖动导致 RAG/CLIP 用例随机失败（实测 SSL `UNEXPECTED_EOF_WHILE_READING`）。测试不该依赖外网。
- 老管线相关用例（`test_pipeline_capability` / `test_batch_a_callchain` / `test_pipeline_category_default`）
  显式 `pipeline_enabled=True` 固定其被测语义，避免被 P2 的下线闸误伤。

---

## 14. 独立校验（2026-10-26，两个互不通气的 subagent）

按"开发者不自证"的原则，交付后派了两个独立代理：**A 只读代码审计**（不跑测试，逐行读码）、
**B 行为验证**（只跑命令/探针，不改仓库源码）。结论如下。

### 14.1 B 的验收表（真实执行）

| A# | 结论 | 实测证据 |
|---|---|---|
| A1 | 通过 | `run-async`：off+disabled→410；on→200 `task_id=task_5c85f41dae`；目录含 `run_stage/spawn_subagent`、不含 `run_pipeline`（68 行） |
| A2/A3/A4 | 通过 | 假 Agent 跑 structure→material→edit：黑板 `{script_skeleton,scenes,candidate_clips,timeline}`，owner 依次 structure/material/edit |
| A5 | 通过 | edit→audio→edit 第三次被拒（`writes=[]`、owner 仍 audio）；未知阶段报「不是时间线生产者」 |
| A8 | 通过 | `sleep(1)` + `timeout=0.05` → `failed/timeout/retry_same` |
| A9 | 通过 | `spawn` 抛 `SpawnError`、`run_stage` 返回 `refused`，均无 LLM 调用 |
| A10 | 通过 | 中文名图 `status=ok source=llm summary='橙色背景中央的白色长方形'`；两图描述明显不同 |
| **A11** | **不通过（已修）** | 模型真失败时仍报 `ok/source=llm` + 文件名兜底 `llm/filename` |
| A12 | 通过 | `imread_unicode('橙色方块.png')` shape=(360,640,3) |
| A13 | 通过 | 落 `<root>/materials/video/demo_clip.mp4` |
| A14 | 通过 | 6 个测试文件 86 passed |

A11 的根因：`analyze_image(provider="llm")` 在 LLM 抛错时**内部**回退成
`_fallback_analyze`（`source=filename`），而 `label_shot` 只判 `description` 非空，
于是把兜底当成了模型输出。**修法**：`label_shot` 判 `source`（缺失时按 `model`
推断，`model=filename` 视为兜底），只有真看过图才置 `source=llm`；
并新增回归用例 `test_vision_not_false_positive_when_model_fails`。

另一条附带发现（重要）：`vision_llm_model` 会被**角色绑定**（`role="vision"`）覆盖，
实测实际生效的是 `deepseek-flash`；所以"改 `vision_llm_model` 来模拟模型不可用"这条
PRD 手段无效。多模态确实在跑（`OpenAIChat initialized (model: deepseek-flash)`）。

### 14.2 A 的问题清单与处置

| # | 问题 | 处置 |
|---|---|---|
| P1-1 | `proceed` 幂等在 task 路径失效：幂等键绑 `pl_xxx`，task 只登记在 `api.task._RUNNING` → 双击建两个 Task、双倍扣费 | ✅ 修：`_proceed_idem_hit` 补查 `_RUNNING`；`_proceed_as_task` 返回前把键重绑到 `task.id` |
| P1-2 | 黑板 2MB「转指针」只有读端没有写端：超大时间线被静默丢弃，下游报「缺 timeline」 | ✅ 修：`stage_merge` 超限先落盘再存 `{path,sha256,bytes}`；`merge()` 返回 `(written, errors)`，合并失败把 `status` 降为 `degraded` 并记 `blackboard_merge_failed` 降级 |
| P2-1 | 时间线单向校验可被**未知 by / 空 by** 绕过（`get("",99)` 恒最大） | ✅ 修：`by not in TIMELINE_STAGE_ORDER` 一律拒绝（含匿名），补两条单测 |
| P2-2 | `StageResult.status` 词表漂移（`needs_human_review` → `dry_run`） | ⏳ 保留现状并记录：阶段是同步真跑，没有"待审"态；kernel 的 `needs_human_review` 分支由 `spawn_subagent` 使用，未删 |
| P2-3 | 越权模型用 `constraint_conflict` 而非 `policy_refused`；`FAILURE_CATEGORIES` 缺新类别 | ✅ 修：统一 `policy_refused`；`FAILURE_CATEGORIES` 增补 `policy_refused/budget_exhausted/cancelled/invalid_input/not_found/stage_failed` |
| P2-4 | 阶段成本按默认模型计价；继承默认模型时不回填实际 provider/model | ✅ 修：`_cost_from_usage(route)`；留空时用 `_default_route()`（角色绑定后的实际值）回填 |
| P2-5 | `assemble` 的 override 对 `creative_brief/production_plan` 无效 | ✅ 修：先看 override/黑板，再回落到 `request.extra` |
| P2-6 | `creative_brief/production_plan` 实际走 `request.extra`，不在黑板 | ⏳ 文档口径已按实现修正（§3.2 键表标注"由 requirements 写入 request.extra，经 assemble 注入"），不改实现（黑板给阶段间产物用） |
| P2-7 | `POST /api/pipeline/retry/{id}/{agent}` 未受 `pipeline_enabled` 闸，可从残留结果重跑老管线 | ✅ 修：加 410 闸口 |
| P2-8 | 13.1 写「run-async → 410」，实际 `agent_mode=on` 时转 Task 返回 200 | ✅ 文档已改口（§13.1 与 A1 表述修正）：410 只在 `agent_mode!=on` 且管线下线时出现 |
| P2-9 | CLIP 离线路径 `content_index_clip_model_path` 未实现 | ✅ 修：新增设置项 + `local_files_only`；`config_schema` 同步 |
| P2-10 | CLIP 命中时不再跑 LLM → `summary` 常为空 | ✅ 修：`label_shot(want_description=True)`（工作区索引传 True），CLIP 命中时也补一句画面描述 |

### 14.3 A 判定为「合理妥协」的项

PRD §13.2 的 #1（P4 物理删除留单独 PR）、#2（requirements 不真跑）、#3（`run_inline` 无副作用）、
#4（成本估算）、#5（视觉依赖外部多模态、降级如实）——独立审计认定为合理，不属于漏做。

### 14.4 修完后的回归

- **后端全量：4436 passed / 4 skipped / 1 xfailed（0 failed）**
  （中途一次出现 `test_content_audio` 2 条失败，复跑 57 passed、`tests/clipwright` 单跑 3963 passed、
  全量复跑 0 failed —— 判定为 ffmpeg/CPU 争用下的偶发，非逻辑问题）。
- **前端**：`tsc --noEmit` 干净；`vitest run` 112 files / **970 passed**。
- 新增/加强的用例：`test_vision_not_false_positive_when_model_fails`（A11 假阳性）、
  `test_merge_spills_huge_timeline_to_pointer`（P1-2 指针写端）、
  匿名/未知 `by` 写时间线被拒（P2-1）、`test_kernel_accumulates_stage_cost_into_task_usage`（A7）、
  `test_blackboard_rejects_unknown_timeline_producer`（P2-1 补充）。
- 被本批改动波及的老用例（`test_pipeline_capability` / `test_batch_a_callchain` /
  `test_pipeline_category_default` / `test_pipeline_runs`）显式 `pipeline_enabled=True` 固定被测语义。

---

## 15. P4 收尾状态与机械删除清单

### 15.1 本轮已完成的清理（P4 的"清理与文档"部分）

| 项 | 说明 |
|---|---|
| 死代码清理 | `requirements_service` 三处 `from ...agents.* import XAgent` 与 `agent = StructureAgent()` 全部删除（执行已收归 `stage_subagent.run_inline`） |
| 双份语义对齐 | `pipeline_v2._downstream_closure` 保留**插件感知**版本，并在 docstring 指明与 `stage_closure.closure` 同源；新增 `test_core_deps_match_pipeline_dag` 逐字比对核心依赖（P4 删管线后自动 skip） |
| 文档 | 本文档 §13/§14/§15；`docs/api_reference.md`（工作区事件拆 JSON/SSE）；`docs/frontend-backend-parity.md` §8；`AGENTS.md`（本期改进 + 回归期望值 4436） |

### 15.2 物理删除清单（未执行，单独 PR 做）

> 为什么不在本轮做：`pipeline_v2.py` 被 `api/pipeline.py` **模块级 import**，
> 而 `api/pipeline.py` 又被 `main.py` 挂载；同时 `last_result()` 被 `api/task.py`、
> `golden_capture.py`、`api/pipeline.py` 三处引用。一次性删除会让应用起不来，
> 且需要同步重写约 30 个测试文件（PRD §4.1 P4 原文：测试基线重写）。
> 执行路径已全部切断（能力拒绝 / 端点 410 / 退化计划 / proceed 单轨 / retry 410），
> 所以这一步是**纯机械清理**，不含行为变更。

按顺序执行（每步跑一次全量，绿了再下一步）：

1. **把 `last_result` 搬进中立模块**：`services/pipeline_results.py`（只保留
   `_RESULTS` + `remember/last_result/clear_results`），改 `pipeline_capability`、
   `api/pipeline.py`、`api/task.py`、`golden_capture.py` 的 import。
2. **删 `services/pipeline_capability.py`**，同步 `capability_matrix` 移除 `run_pipeline`
   条目与 `DRY_RUN_CAPABLE` 里的引用；`agent_kernel.default_executor` 的
   `run_pipeline` 分支删除（退化计划已不产生它）。
3. **删 `api/pipeline.py` 的执行类路由**：`run-async`、`run-batch`、`retry`、`cancel`、
   `result`、`status`、`diagnostics`；保留只读 `runs`/`tasks`/`trace*` 一个版本周期
   （前端管理页用），或把它们整体迁到 `api/task.py` 后删除。
4. **前端**：`src/services/api/pipeline.ts` 收敛到 `taskApi`/`supervisorApi`；
   `admin/PipelineAdminPage.tsx` 改名为执行诊断页并只读 `task` 数据源；
   `features/agent/AgentPanel.tsx` 的 `pipelineApi.cancel/getResult` 换 `taskApi`。
5. **删 `services/pipeline_v2.py`**（此时应已无 import：`grep -r "pipeline_v2"` 为空）。
6. **重写测试基线**：删除 `tests/test_pipeline_e2e.py`、`tests/clipwright/test_pipeline_*.py`、
   `tests/clipwright/test_batch_a_callchain.py` 等直测管线的文件；
   把仍有效的**行为断言**（自愈闭包、降级登记、预算熔断、检查点）迁到
   `tests/test_stage_*.py` 的等价用例上，**不要直接删掉覆盖**。
7. **Mongo**：`pipelines` 集合转只读（不再写入），前端历史页保留一个版本周期。

验收：`grep -rn "PipelineOrchestratorV2\|pipeline_capability\|run_pipeline" clipwright/` 为空；
全量测试绿；A1–A14 不回归。

---

## 16. 就地改造：把"主 agent 驱动的 pipeline"改成主 agent（不设双通道）

> 用户裁定：**不要两个独立的 V1/V2，就在现有版本上改。** 本节取代先前所有"并行双通道"的设想：
> 只保留**一套**能力、一套数据流，改完即生效，回归全绿才算完成。
>
> **2026-10-26 追加修订**：两个独立模型（DeepSeek flash / GLM flash）交叉审查后一致判定
> 「按 §16.2–§16.7 改完仍是"数据过手的 pipeline"」。缺的不是数据流，而是**决策回路**。
> 修订内容见 **§16.10–§16.12**，其中 §16.10 的 P0 项**前置**于 S1。

### 16.0 原则

1. **一套实现**：不新增并行能力名（不保留"旧语义 `run_stage` + 新语义 `delegate`"并存）；
2. **改的是三处 pipeline 痕迹**（数据流、顺序、反思粒度），不是重写编排层；
3. 每个小步都跑全量回归（当前基线 4437 passed），**不留红着的中间态**。

### 16.1 判据回顾与现状核对（为什么必须改）

| 判据 | 现状证据 | 结论 |
|---|---|---|
| A 的产物应回主 agent，由主 agent 传给 B | `stage_subagent.py:253` 读黑板 → `:262` `assemble()` 自动装配；`stage_inputs.py:83` `{**board, **override}` | ❌ 数据绕过主 agent |
| 顺序应由主 agent 决定 | `agent_kernel.py:177`「…必须按顺序」；`:224-233` 退化计划固定 6 步 | ❌ 顺序写死 |
| 失败应由主 agent 决策 | `agent_kernel.py:238-272` `reflect` 是**纯规则**（只 retry/finish），没有 LLM 判断 | ❌ 主 agent 没在决策 |

### 16.2 数据流就地改造：`run_stage` 改成 `delegate`（同一能力，改语义）

**能力统一为 `delegate`**（`capability_matrix` 只留一条，`run_stage` 改名；`spawn_stage` 已于本次直接删除）：

```python
delegate(agent, brief, inputs, expect=None, timeout_sec=None, provider="", model="")
```

| 项 | 现状 | 改成 |
|---|---|---|
| 输入来源 | `assemble(board)` 自动拼 | **只用 `inputs`**；`stage_inputs` 退役为 `validate(agent, inputs) -> list[need]`，只校验不填值 |
| 缺输入 | 报 `StageInputMissing`（读黑板后才报） | `status="needs"` + `needs=[{field, why, from}]`，由主 agent 决定补什么 |
| 产物去向 | `stage_merge.merge()` 写黑板 | **只回主 agent**：`outputs={name: {artifact, schema, bytes, preview}}`，写工作区不可变产物文件 |
| 黑板 | 阶段间数据通道 | 降级为"主 agent 主动记忆"：仅 `remember/recall`，**任何 assemble/validate 不得读它** |
| 自动合并开关 | `StageSpec.merge=True` 默认 | **删除该字段**（没有"自动"这回事） |

产物引用（content-addressed，不可变）：

```
outputs.timeline = {"artifact":"art_91c2",
                    "path":"<ws>/cache/artifacts/art_91c2.json",
                    "sha256":"…", "schema":"Timeline", "bytes":184320,
                    "preview":{"tracks":3,"clips":12,"duration_sec":61.4}}
```

主 agent 在下一跳里显式引用：`inputs={"timeline": {"artifact": "art_91c2"}}`，运行时就地解析成对象。
**这就是"数据过主 agent 的手"的可执行含义**：不传就不在。

### 16.3 叙述：先说要做什么、再动手

四处，全部落在现有事件流里（`schema/task.py::EVENT_TYPES` 增加 `narration` / `intent` / `thinking` / `report`）：

| 时机 | 事件 | 文本来源 | 示例 |
|---|---|---|---|
| 规划产出后 | `narration` | `_PLAN_SCHEMA` 每步新增 `say` 字段（`say` 给用户看，`why` 给审计看） | "先弄清素材库里有什么，再决定怎么剪。" |
| 每次能力调用**前** | `intent` | `step.say` → 否则模板（"{agent}：{brief 摘要}"） | "准备调用 material：为 12 个场景找候选素材。" |
| 反思后改计划 | `narration` | reflect 输出的 `say` | "素材是空的——先去工作区看看有没有可用文件，再决定补素材还是改结构。" |
| LLM 思考 | `thinking` | provider 的 `reasoning_content` 增量（无则不发） | 流式思考内容 |

实现要点：
- `_PLAN_SCHEMA` 增加 `say`（string，≤120 字），规划提示词要求"每步先用一句话说明你要做什么、为什么"；
- `_run_step` 在 `executor(...)` **之前** `emit("intent", ...)`；`_reflect` 返回新计划后 `emit("narration", ...)`；
- 阶段 Agent 内部工具的 reasoning 走现有 `_push_llm` 通道映射成 `thinking`（不新增 LLM 调用）。

### 16.4 阶段汇报：每完成一步，向用户报告成果

新增 `report` 事件（**不额外调 LLM**，由结构化结果生成）：

```python
emit(task.id, "report", headline, {
   "stage": "material", "status": "ok",
   "bullets": ["候选素材 0 条", "12 个场景中 0 个命中，未命中改为文字占位"],
   "artifacts": [{"name":"candidate_clips","artifact":"art_71ab","preview":"0 条"}],
   "cost_usd": 0.012, "duration_ms": 1840,
})
```

文案由 `stage_report.py`（纯函数、可单测）生成：
- headline：`"{阶段中文名}完成：{核心指标}"`（如「素材完成：12 个场景，候选 0 条」）；
- bullets：`summary` 拆句 + 每个产物的 `preview` + 降级条目（≤5 条）；
- 失败：headline 用 `"{阶段}没做成：{一句话原因}"`，bullets 放 `failure.evidence` 前 3 条。

前端（§19.3）以"汇报卡"渲染，与工具行区分——**用户读的是这段话，不是事件 JSON**。

### 16.5 失败回流：主 agent 真的做决策

**现状**：`reflect` 是纯规则（`agent_kernel.py:238-272`）——`retry_same` 盲重试一次，其余直接 `finish`。
主 agent 从未"根据手上的工具决定下一步"。

**改成：规则护栏 + 一次轻量 LLM 决策**（`use_flash`，只在失败时发生）：

```
输入：failure{category, retryable, needs, evidence} + 当前计划剩余步骤
     + 可用能力清单（manifest: accepts/produces）
     + 手上已有产物清单（artifacts: name → preview）
输出：{action: replan|retry|finish, say, steps?}
```

规则护栏（先跑，命中就不花模型）：
1. `policy_refused` / `permanent` → `finish`；
2. `needs[].from` 是已知 agent 且不在剩余计划里 → 直接 `replan` 插入补派步（并 `emit narration`）；
3. 同一 agent 同参连续失败 2 次 → 禁止再 `retry`（必须 `replan` 换参/换人/收尾）。

LLM 决策只在护栏未命中时调用，且必须给出 `say`（为什么这么决定），随 `narration` 推给用户。

配套：`tool_result` 的 `detail` 必须带 `failure` 全文（category/retryable/needs/evidence）——
这是 §16.5 能成立的前提，也是 §19.5（D1/D3）的同一处修复（原始复现记录见 `editor-copilot-prd-2026-10-26.md` §1.4）。

### 16.6 删掉写死顺序（就地两处）

| 位置 | 改成 |
|---|---|
| `agent_kernel.py:177` 提示词 | 换成"能力清单 + 依赖提示"：`edit 需要 scenes 与 candidate_clips；没有就先 delegate material`。**给信息，不给命令** |
| `agent_kernel.py:224-233` 退化计划 | 改"探测两步"：① `delegate("probe", "列工作区素材与已有场景", {})`；② 据结果再决定；仍无法决策 → `finish` 并说明"需要用户补充信息" |

### 16.7 就地迁移顺序（一个版本，4 小步，每步全量绿）

| 步 | 改动 | 完成判据 |
|---|---|---|
| S1 | 能力改名 `run_stage` → `delegate`（`spawn_stage` 已删）；`inputs` 改为显式参数；`stage_inputs` 改 `validate` | 测试与文档里不再有 `run_stage`；**黑板不再被 assemble 读取**（M1/M4 转绿） |
| S2 | `stage_merge` 退役：payload → artifacts（写工作区 + 引用），删 `StageSpec.merge` | `grep "bb.put" clipwright/services/stage_*` 为空；黑板只剩 `remember/recall` |
| S3 | 叙述与汇报：`_PLAN_SCHEMA.say`、`intent`/`narration`/`report`、`stage_report.py` | 真实任务事件流里：每个 `tool_call` 前有 `intent`，每个 `step_end` 后有 `report` |
| S4 | 失败决策：reflect 护栏 + LLM 兜底 + `tool_result.failure` 全量 | M2/M6/M7 转绿 |

> 顺序理由：S1 先让数据流改道（架构分水岭），S2 拆掉黑板依赖，S3/S4 才有干净落点。
> 每步都不新增并行 API，改完旧路径即消失。

### 16.8 验收（今天必挂，改完必须全绿）

| # | 测试 | 现状 |
|---|---|---|
| M1 | 清空黑板，只靠 `delegate(inputs=...)` 显式传参跑通 structure→material→edit | ❌ `assemble` 只从黑板取 |
| M2 | `material` 返回"候选 0 条"→ 主 agent 改派（换 brief 重找 / 探测工作区 / 问用户），不让 `edit` 报 unknown | ❌ 固定顺序照跑 |
| M3 | 合法非典型顺序：`quality` 提前做约束预检 → `structure` → … | ❌ 缺键即失败 |
| M4 | A 的产物不经主 agent 出现在 B 的输入里时，测试能发现（断言 B 的 inputs 全部来自本次调用） | ❌ 输入来源不可追溯 |
| M5 | 计划步骤在不同选题下不完全相同 | ❌ 恒 6 步 |
| M6 | 失败结论含原文 + 建议（`category｜blocking_point｜建议`） | ❌ `unknown（）` |
| M7 | `retry_same` 同参连续失败 2 次后不再原地重试，必须 replan 换参/换人 | ❌ 只重试一次然后收尾 |
| M8 | 每个 `tool_call` 前有 `intent`；每个 `step_end` 后有 `report`；有 reasoning 时 `thinking` 流式出现 | ❌ 三者都不存在 |
| M9 | 回归：后端全量绿、前端 `tsc` + `vitest` 绿；`run_stage` 名称在代码与文档中不再出现 | ❌ 待 S1 |

### 16.9 风险与护栏

| 风险 | 缓解 |
|---|---|
| 主 agent 要自己拼 `inputs`，容易拼错 | `validate()` 立刻返回 `needs`（带 from 建议）——错误代价一轮，不是白跑 |
| 叙述/汇报让事件流变长 | 一步一条 `report`；`thinking` 前端默认折叠；`narration` ≤120 字 |
| reflect 走 LLM 增成本/延迟 | 只在失败时走；先跑三条护栏；`use_flash`；同一任务同一错误最多一次 LLM 决策 |
| 产物文件堆积 | 写 `<ws>/cache/artifacts/`，走既有 `cleanup_cache()`（`workspace_cache_max_gb`） |
| 自由度上升导致不可复现 | 每次 `delegate`/`report` 都留事件与产物引用；评估看"成片指标 + 步骤差异"，不追求步骤相同 |

---

### 16.10 审查后修订：缺的是决策回路（P0，前置）

两个独立审查（DeepSeek flash / GLM flash，互不通气）给出同一结论，且各自独立定位到同一批证据：

| 共同发现 | 证据 | 性质 |
|---|---|---|
| **成功路径上主 agent 零决策**：`plan` 一次出 1–6 步，之后 `reflect` 对成功只返回 `{action:continue}`（自述"不额外花模型调用"） | `agent_kernel.py:238-249` | 决定性缺口：改完仍是"一次性计划 + 线性哑跑" |
| **主 agent 从未拿到工具 schema**：执行器按字符串硬分支，目录只是 `name（tier）: 描述` 文本；真正的 tool loop 只在子代理（`spawn.py:226-240`）与阶段 Agent 内部（`agents/base.py:246`） | `agent_kernel.py:68-99`、`:314-336`；`surface.py:198-205`（schema 已就绪，只差接线） | 主子颠倒：主 agent 反而没有 function calling |
| **观察回路把结论扔了**：`_brief` 对 dict 只回键名；`_observe` 只留标量与 ≤120 字串，`StageResult.summary`（≤500）被丢弃 | `agent_kernel.py:582,607-636,774-783` | **A4 当前为假**：reflect 无料可用，§16.5 的 LLM 决策会空转 |
| **`emit` 对未知事件类型直接抛错**：`EventError`，而 `EVENT_TYPES` 里没有 `narration/intent/thinking/report` | `services/task_events.py:39-41`；`schema/task.py:28-44` | S3 第一行必须先加 `EVENT_TYPES`，否则一步都发不出去 |
| **S1 改名会打断在飞任务**：checkpoint 存 `task.model_dump()`，内含 `capability="run_stage"`；重启后执行器无该分支 → 被工具面拒绝 | `agent_kernel.py::_checkpoint`、`surface.py:375` | 需要旧名→新名的加载时映射 |
| **`needs` 生产了但没人消费** | `stage_subagent.py:440` 产出，`reflect`/`stage_capability` 均不读 | A3「据此先派」未实现；且字段名 `from_stage` 与 PRD 的 `from` 不一致，需冻结 |
| **状态全在内存**：`_TASKS/_CONTROL`、黑板、`_RUNS/_IDEM`、owners；checkpoint 不含黑板 | `agent_kernel.py:36-38`、`task_blackboard.py:40-41`、`stage_subagent.py:41-44`、`api/task.py:26` | 重启后黑板空 → 全部 `input_insufficient` |
| **取消不真停**：只置 `_CONTROL` 标记，主循环在步间才查；执行中的 900s 阶段与 ffmpeg 子进程不受影响 | `agent_kernel.py:417-428,485-488`、`api/task.py:201-210`、`tool/video.py:54` | A8「3s 内停、无孤儿」现状不成立 |
| **两套子代理机制并存**：`spawn_subagent`（只读、并发 6、两级审核、真 `with_tools`、成本入 `llm_tracker`）vs `stage_subagent`（可写、无审核、无并发限、成本靠进程级 usage 差值、不入账） | `spawn.py:49-156` vs `stage_subagent.py:93-111` | 预算闸对阶段失效；语义与记账分家 |
| **失败仍写坏产物**：先 merge 后判失败，空壳 timeline 入库 | `stage_subagent.py:377-394` | 下游基于空片继续跑（§19.5 / 附录 §1.4.2 已复现） |

#### 16.10.1 修订一：给主 agent 装真正的决策回路（替代"一次性规划"）

```
循环：观察 → 决策 → 派发 → 再观察
  ① observe：把上一步的 {summary, artifacts[].preview, failure, cost} 结构化写进 context_digest（不再截断丢弃）
  ② decide ：LLM(with_tools) 看到 —— 能力清单（含 args schema）+ 手上 artifact 清单 + 剩余预算
             → 输出 {say, action: delegate|finish, agent?, brief?, inputs?, expect?}
  ③ dispatch：执行 delegate；回到 ①
```

- **`plan` 降级为"初始假设"**：只用来给第一步一个起点，不再决定全程；
- **成功也要决策**：`material` 返回「候选 0 条」这种"假成功"必须被 ② 看见并改派（M2 因此可测）；
- **成本分档**：`decision_policy = auto|always|step0`（默认 `auto`）= 只读步骤走规则快通道，写/花钱步骤必过 LLM；决策统一 `use_flash`；
- 复用现成件：`surface.schemas()`（已实现）喂 `LLMService.with_tools`；`with_tools` 的 executor 已支持工具循环（`spawn.py:226` 在用）。

#### 16.10.2 修订二：修观察回路（前置，否则决策无料）

- `step.result_brief = StageResult.summary`（而不是键名）；
- `_observe` 增加 `summary` / `artifacts[].preview` / `failure` 三个**不截断**字段（长度上限按字段类型给，不再一刀切 120 字）；
- `context_digest` 保留最近 12 步全量 + 更早的压缩计数（已有机制，只是要保证 summary 不被压掉）。

#### 16.10.3 修订三：前置与兼容（S0）

| 项 | 内容 |
|---|---|
| S0-1 | `EVENT_TYPES` + 前端 union + 回放器同时加 `narration/intent/thinking/report`（顺序：后端 schema → 前端类型 → emit 调用点） |
| S0-2 | checkpoint 加载时做 `capability` 旧名映射：`run_stage`（及历史 `spawn_stage`）→ `delegate`；缺失旧名不影响历史任务续跑 |
| S0-3 | 冻结 `needs` 字段名：`{field, why, from_stage}`（实现已是 `from_stage`，PRD 改为一致）；`reflect` 必须消费它 |
| S0-4 | artifacts 落到 `<ws>/artifacts/`（**不放 cache/**），或 `cleanup_cache` 跳过被引用的 artifact id；读端校验 sha256 |

#### 16.10.4 修订四：运行时闭环（主 agent 自由度的必要配套）

- **真取消**：`AgentKernel` 自己持有 asyncio.Task（现在句柄在 api 层），`cancel` → cancel 当前步 → 终止当前步登记的子进程（复用 `services/render.py::_ACTIVE_PROCS` 的模式）；
- **状态持久化**：黑板、`_RUNS`、owner 一并进 checkpoint；重启后 `resume` 能接着跑（否则重启=重来）；
- **阶段预算**：阶段派发前 `budget_guard.is_blocked`，阶段成本写 `llm_tracker`（`agent_name=f"stage:{stage}"`），与 `spawn_subagent` 用同一本账；
- **收尾守门**：`_finish` 前校验 `final_timeline` 存在且非空，否则不允许宣称完成（防"漏派 audio 也算完成"）。

#### 16.10.5 验收修订（原 M1–M9 里不可判定的重写）

| # | 修订后判据 |
|---|---|
| M2 | `material` 无可用素材时必须**有信号**（`degradation` 或 `needs`，见 copilot D7）；② 的决策日志里出现"改派"（新的 delegate 调用参数与失败前不同） |
| M3 | `quality` 若保留前置预检，必须支持"无 timeline 的约束预检"模式；否则本项移出验收（当前 `QualityInput.timeline` 必填，做不到） |
| M5 | 改为可判定：3 次真实运行中，**至少 1 次**步骤序列 ≠ 固定 6 步（顺序或数量有差异），且成片指标不劣于基线 |
| M8 | 分档：`thinking` 仅在 provider 返回 `reasoning_content` 时断言（否则断言"显示未提供"）；`intent`/`report` 为硬断言 |
| **M10** | 每一步之后都能看到一次决策（`narration` 或 `decision` 事件），成功步骤也不例外 |
| **M11** | 新增四类事件后，`emit` 不再抛 `EventError`（单测覆盖 `EVENT_TYPES` 完整性） |
| **M12** | 重启后端后 `resume` 能继续（黑板/阶段结果不丢） |
| **M13** | 取消后 3s 内停，且 `ps` 无孤儿 ffmpeg |

### 16.11 实施顺序（修订后）

```
S0（前置，1 人日）：EVENT_TYPES + checkpoint 旧名映射 + needs 字段冻结 + artifacts 目录
S1（3 人日）：决策回路（observe→decide(with_tools)→dispatch）+ 观察回路修复
S2（2 人日）：delegate 数据过手（inputs 显式 + validate + artifact 引用，黑板降级）
S3（2 人日）：叙述/汇报（intent/narration/report，report 只作 step_end 派生视图）
S4（2 人日）：失败决策（护栏 + needs 消费 + failure 全文入事件）
S5（1.5 人日）：运行时（真取消/状态持久化/阶段预算/收尾守门）
```

> 与 §16.7 的差别：**S1 从"改数据流"提升为"装决策回路"**——两个审查都指出，数据流改道不改变"哑执行"的本质。

### 16.12 两个模型共同的"只改三件事"

1. **每步 observe → LLM decide**（没有它，delegate 只是把 pipeline 的接线换了个名字）；
2. **delegate + artifact 引用落地**，并修观察回路让决策有料；
3. **真取消 + 状态持久化**（否则主 agent 自由度的代价是"卡住 900s 且账单不可控"）。

---

## 17. 工作区文档协议：子 agent 写 markdown 汇报，主 agent 读/改

> 用户追加要求（2026-10-26）：子 agent 不能只回结构化 JSON，还要**在工作区写一份 markdown 汇报**
> 自己的工作进度（**不删除**），交给主 agent 阅读；主 agent 要有 read / edit 工具去读改这些文档；
> 需求阶段的创意简报与规划书同样落成 markdown。本节定义这套协议。

### 17.0 需求映射（用户五条 → 设计落点 → 验收）

| # | 用户要求（原话） | 设计落点 | 验收 |
|---|---|---|---|
| 1 | 主 agent **每次执行前**应当知道先前上下文 | §17.6 决策上下文协议 + §17.11 逐跳上下文契约（`context_loaded` 事件留痕） | M16、M23 |
| 2 | 子 agent 应当回传**文字性内容**，**同时**在工作区生成 markdown 给主 agent 读 | §17.3 汇报协议：`StageResult.report_path` + `summary` + 三节必填 markdown（append-only） | M14、M15、M24 |
| 3 | 创意简报 / 规划书应当在工作区有 markdown，且主 agent 应该去读 | §17.4：`docs/brief.md`、`docs/plan.md`（改版出 `plan-vN.md`）+ 每跳必读 | M19、M25 |
| 4 | 完善主 agent 与子 agent 的**工具集** | §17.5 工具清单（主/子各一套，含档位、权限范围、缺口） | M26、M27 |
| 5 | 前端工作区应当能**自选目录** | §17.8：`workspace_roots` 白名单 + `GET /api/workspace/roots` + 04 步选择器 | M21、M22 |

### 17.1 先回答五个问题（现状核对）

| # | 问题 | 现状（代码证据） |
|---|---|---|
| 1 | 主 agent 每次执行前知道先前上下文吗？ | **只在最初规划时读一次，且被截断**。`LLMPlanner.plan` 调 `context_bundle(task)`（`agent_kernel.py:146`），而 `context_bundle` 只输出「每步 `[status] title（brief）`+ ≤80 字 facts」（`:275-303`）；`default_executor(task, step)` 只拿到 `step.args`，**执行前不再读任何上下文**。PRD §16.10.1 的 decide 回路会补上"每步读 digest"，但**读的仍是压缩文本**，不是文档 |
| 2 | 子 agent 回传文字性内容吗？ | **只有一个 `summary` 字段（≤500 字），且在观察层被丢掉**（`_observe` 只留标量与 ≤120 字串，`agent_kernel.py:607-636`）。工作区里**没有任何 markdown 汇报**（`workspace.py` 只写 `project.json`/`timeline.json` + `logs/*.log`） |
| 3 | 创意简报 / 规划书有 markdown 吗？ | **没有**。它们只活在 `session.user_inputs` 与 `task.request.extra`（`stage_inputs.py:87` 读 `extra.creative_brief`），从不落盘；`write_project_files` 只写 JSON |
| 4 | 主 agent 有 list/manage subagent 的工具吗？ | **有，但不完整**：`list_subagents`/`get_subagent_status`/`cancel_subagent`（`subagent_capability.py`，管 `spawn_subagent` 那套）+ `list_stages`/`get_stage`（管阶段那套）。**没有** `wait`、`list_artifacts`、`read_artifact`、`list_reports`/`read_report` |
| 5 | 前端工作区能自选目录吗？ | **不能**。`POST /api/workspace` 的 `root` 必须位于 `settings.workspace_dir` 内（`api/workspace.py::_check_root_allowed`），04 步只展示路径 |

> 已具备的地基（好消息）：`read_file`（read）、`write_file`（write_local）、`list_dir`（read）已在主 agent 工具面里；
> `allowed_media_roots()` 已包含 `workspace_dir`（B10 时加的），所以"主 agent 读写工作区文件"不需要新的权限管道，
> 缺的是**约定、edit 工具、报告封装、以及把文档内容送进决策上下文**。

### 17.2 目录与命名（append-only，永不删除）

```
<workspace>/
├── docs/
│   ├── brief.md              # 需求 Agent：创意简报（可被主 agent 编辑）
│   ├── plan.md               # 需求 Agent：制作规划书（当前版）
│   └── plan-v2.md, plan-v3.md…  # 每次大改生成新版本，plan.md 为最新软链/副本
├── reports/
│   ├── INDEX.md              # 自动生成：每次报告的 时间/阶段/状态/一句话结论/链接
│   ├── 01-structure-1.md
│   ├── 02-material-1.md
│   ├── 03-edit-1.md
│   ├── 03-edit-2.md          # 重跑 → 新序号，**不覆盖**旧报告
│   └── …
└── artifacts/                # 结构化产物（不可变，引用式）
```

规则：
1. **append-only**：已写出的 report 文件禁止删除/覆盖；重跑只新增 `-<attempt>.md`；
2. 报告文件名由系统生成（`<序号>-<stage>-<attempt>.md`），子 agent 不自选路径，避免越权与踩名；
3. `reports/INDEX.md` 由系统维护（`list_reports` 的数据源），主 agent 不需要 glob 猜文件名；
4. 文档只允许写在 `docs/` 与 `reports/` 下；`artifacts/` 只读（不可变）。

### 17.3 子 agent 汇报协议（必须写，写不出就失败）

每次 `delegate` 结束（成功/失败/needs 都要写），runtime 生成报告骨架，子 agent 填充：

```markdown
---
stage: material
status: ok            # ok | degraded | failed | needs
attempt: 2
duration_ms: 18400
cost_usd: 0.012
artifacts: [candidate_clips]
needs: []
---

## 我做了什么
给 12 个场景各找 3 个候选；先查工作区素材库（18 个文件），再用外部源补齐 4 个场景。

## 结果如何
- 命中 8/12 场景；4 个场景无匹配 → 已标记 `unmatched_scenes: [4,7,9,11]`
- 候选总计 21 条，其中 6 条分辨率 < 720p（已降权）

## 发现的问题
- 工作区素材多为竖屏，与 16:9 画幅不匹配；建议结构阶段考虑竖屏或补素材
- 无 BGM 可用（materials/audio 为空）

## 给下一位
剪辑阶段请优先用 `asset_89d…`（1080p 横屏，时长 6.2s）；
场景 4/7/9/11 需要文字占位或补素材。

## 产物
- candidate_clips → artifacts/art_71ab（21 条，preview: 21）
```

硬约束：
- **`## 我做了什么` / `## 结果如何` / `## 给下一位` 三节必填**（缺失则该次派发记 `degraded`，并把缺失项写进报告）；
- 报告由 runtime 写盘（子 agent 只产出文本，避免它拿到写文件权限后乱写）；
- 写盘失败 → `degraded` + 事件 `degradation(kind=report_write_failed)`，但**不阻断**阶段结果。

### 17.4 需求阶段的文档

`requirements` 阶段产出两份 markdown（同样 append-only，改动生成新版本）：

| 文件 | 内容 | 谁写 | 谁能改 |
|---|---|---|---|
| `docs/brief.md` | 创意简报：主题/受众/时长/风格/素材偏好/禁忌 | requirements Agent（经运行时落盘） | 主 agent（`edit_file`）+ 用户（对话改写后生成新版本） |
| `docs/plan.md` | 制作规划书：场景表、节奏、动画意图、配音要求 | 同上 | 同上 |

- 对话里确认/修订单后，**写新版本 `plan-vN.md` 并把 `plan.md` 更新为最新副本**（保留历史，便于回溯"为什么这么剪"）；
- 主 agent 在决策时可以 `read_file("docs/plan.md")` 精读，而不是靠 `production_plan` 那个 dict 的压缩摘要。

### 17.5 工具清单（主 agent / 子 agent 各一套）

#### 17.5.1 主 agent（编排者）工具集

| 工具 | 档位 | 作用 | 状态 | 权限范围 |
|---|---|---|---|---|
| `read_file` | read | 读任意工作区文本（报告/文档/JSON） | 已有 | `<ws>/**` |
| `write_file` | write_local | 新建文件（**不覆盖已有报告**） | 已有（补约束） | `<ws>/docs|reports` 下**新文件**；`reports/*` 覆盖一律拒绝 |
| `edit_file` | write_local | 行级替换（`old_string` 唯一命中） | **新增** | 仅 `docs/*.md` + 本任务未定稿的 `reports/*.md` 的"补记"段落 |
| `list_dir` | read | 列目录 | 已有 | `<ws>/**` |
| `glob` | read | 按模式找文件（`reports/*.md`） | **新增** | `<ws>/**` |
| `grep` | read | 在文档里搜关键词（"分辨率""BGM"） | **新增** | `<ws>/docs|reports` |
| `list_reports` | read | 读 `reports/INDEX.md` → 最近 N 条（seq/stage/status/headline/path/cost） | **新增** | 只读 |
| `read_report` | read | `read_report(seq\|path, max_chars≤8000)`；超限提示"指定小节" | **新增** | 只读 |
| `list_artifacts` | read | 产物清单 `name → {artifact, schema, bytes, preview}` | **新增** | 只读 |
| `read_artifact` | read | 按需读产物（先 preview，再显式 `full=true`） | **新增** | 只读 |
| `delegate` | costly | 派一个子 agent（`agent/brief/inputs/expect`） | **改造自 `run_stage`** | 见 §16.2 |
| `list_stages` / `get_stage` | read | 查已派阶段的运行表（`delegate` 化后保留） | 已有 | 只读 |
| `list_subagents` / `get_subagent_status` / `cancel_subagent` | read / write_local | 管理"调研子代理"（`spawn_subagent` 那套） | 已有 | 只读/取消 |
| `wait_for_stage` | read | 派出去并行、稍后收（同步语义仍是默认） | **新增（可选）** | 只读 |
| `replan_closure` / `validate_stage_plan` | read | 自愈闭包、计划合法性校验 | 已有 | 只读 |
| `inject`（人） / `pause` / `cancel` | — | 用户插话与任务控制（API 层，非工具） | 已有 | — |

**刻意不给主 agent 的工具**：`run_shell` / `run_python`（dangerous，必须走执行网关两级审核）、
`delete_file`（报告不可删）、`write_file` 覆盖 `reports/*`（append-only）。

#### 17.5.2 子 agent（执行者）工具集

子 agent **不写文件**（报告由 runtime 落盘），它只拿到执行本阶段所需的工具 + 一个"汇报"出口：

| 工具/出口 | 作用 | 状态 |
|---|---|---|
| 该阶段 Agent 自身能力集（`BaseAgent.list_agent_tools()`：素材检索、ffmpeg 剪辑、TTS、抽帧校验…） | 真正干活 | 已有 |
| `read_file` / `list_dir` | 读自己的输入与工作区素材清单（**只读**） | 新增（限 `<ws>/docs|materials|artifacts`） |
| **`report_progress(text)`** | 向"汇报缓冲"追加一段文字（`我做了什么/结果如何/发现的问题/给下一位`），结束时由 runtime 写成 markdown | **新增** |
| **`need(field, why)`** | 声明缺什么输入（替代乱猜/自动兜底） | 新增（落 `needs[]` 与报告） |
| `emit_artifact(name, value, schema)` | 登记结构化产物（返回 `artifact` 引用） | 新增（由 runtime 写盘） |

**刻意不给子 agent 的工具**：`write_file` / `edit_file`（防它改别人的报告或 docs）、
`delegate`（深度固定 1，不得再派）、`run_shell`（除渲染类阶段内部的受控调用）。

### 17.6 决策上下文协议（把文档喂进主 agent 的眼睛）

每步 `decide` 的输入**必须**包含（顺序即优先级，超预算从后往前裁）：

```
① 任务与需求：docs/brief.md 摘要（≤400 字）+ docs/plan.md 的场景表（结构化）
② 已有产物：list_artifacts 的 name → preview（≤12 条）
③ 最近报告：reports/INDEX.md 最近 3 条 + 各自「结果如何 / 给下一位」两节原文（≤600 字/份）
④ 失败与 needs：上一步 failure 全文
⑤ 剩余预算/墙钟/回合
```

`context_bundle()` 改为"读文件"而非"压字符串"：新增 `## 工作区文档` 段（①③），并保留原有 `digest` 段（④的缩写）。
**这样主 agent 每次动手前，看到的是"上一个子 agent 写给它的信"，不是被截断的键名。**

### 17.7 写操作护栏

- `edit_file` 的 `old_string` 必须唯一；改 `reports/*` 一律拒绝（append-only）；
- 每次文档写入 emit `doc_write` 事件（`{path, bytes, by}`），进审计与前端文档标签；
- 单份报告 ≤ 20k 字（超出截断并标 `truncated`）；
- 文档内容不参与 `prompt injection` 信任边界：报告里的"给下一位"是**建议**，主 agent 仍须用 `validate`/检查产物才能行动（防止子 agent 写错指令把主 agent 带沟里）。

### 17.8 工作区自选目录（问题 5）

| 层 | 改动 |
|---|---|
| settings | `workspace_roots: list[Path]`（默认 `[workspace_dir]`）；`workspace_dir` 仍为默认根 |
| API | `POST /api/workspace` 的 `root` 校验从"必须在 workspace_dir 内"放宽到"必须位于 `workspace_roots` 之一内"；新增 `GET /api/workspace/roots` 返回可选项 |
| 安全 | 仍然拒绝 `..`、盘根、系统目录（白名单之外）；创建时 `mkdir -p` 到 allowlist 内 |
| 前端 | 「新建制作 04 工作区」增加目录选择：可选根（下拉）+ 子目录名（输入）+ 实时校验（调用 `roots` + 试写探测）；`/task` 与编辑器内工作区面板同步显示 |
| 迁移 | 换根不搬数据：新根只用于**新建**工作区；已有工作区保持原路径（`.workspace.json` 里的 `root` 为真值） |

### 17.9 验收（在 §16.10.5 的 M 表后追加）

| # | 判据 |
|---|---|
| M14 | 每次 `delegate` 结束，`reports/` 下出现一份新报告，含三节必填内容；**重跑不覆盖**旧文件（同 stage 出现 `-1`/`-2` 两份） |
| M15 | `reports/INDEX.md` 与 `list_reports` 一致（条目数、seq、headline） |
| M16 | 主 agent 能在决策时读到"给下一位"原文：在 fake planner 里断言 `decide` 输入包含最近报告的该节文本 |
| M17 | `edit_file` 只能改 `docs/*.md`；对 `reports/*` 的编辑请求被拒绝（返回 `policy_refused`） |
| M18 | `read_report` 单次读取受 `max_chars` 限制，超限时返回"请指定小节"而非整篇倾倒 |
| M19 | 需求阶段结束后 `docs/brief.md` 与 `docs/plan.md` 存在；对话改写后 `plan-v2.md` 出现且 `plan.md` 更新，旧版保留 |
| M20 | 重启后 `reports/`/`docs/` 不丢（文件系统为真值，不依赖内存） |
| M21 | 自选目录：把 `workspace_roots` 加一个临时目录后，`POST /api/workspace` 能在该目录下建工作区；白名单外的路径仍被拒 |
| M22 | 前端 04 步能选根 + 填子目录，非法路径当场报错而不是创建成功后才失败 |
| **M23** | **每次** `decide` 之前都有一条 `context_loaded` 事件，`detail` 列出本次装载了哪些来源（brief/plan/artifacts/reports/failure）与各自字节数；缺 `docs/plan.md` 时事件里显式标 `missing` 且任务不静默继续 |
| **M24** | `StageResult` 里同时有 `summary`（文字）与 `report_path`（markdown 真路径）；两者内容一致（同一份汇报的两种载体），且 `report_path` 指向的文件可被 `read_report` 读到 |
| **M25** | 主 agent 在**每一步** decide 的输入里都能看到 `docs/plan.md` 的场景表（不只是首次规划）；断言方式：fake planner 记录每次 decide 的 prompt，三次 decide 的 prompt 都含场景表标题 |
| **M26** | 工具清单落地：`edit_file`/`glob`/`grep`/`list_reports`/`read_report`/`list_artifacts`/`read_artifact` 全部出现在 `_capability_catalog()` 里且可被 `default_executor` 执行（每个补一条冒烟测试） |
| **M27** | 越权拒绝：子 agent 调 `write_file`/`edit_file`/`delegate` 一律 `policy_refused`；主 agent 调 `delete_file` 不存在此工具；`write_file` 覆盖 `reports/*` 被拒 |

### 17.10 实施顺序（插进 §16.11）

```
S0  前置（EVENT_TYPES / checkpoint 旧名映射 / needs 冻结 / artifacts 目录）
S1  决策回路（observe→decide(with_tools)→dispatch）+ 观察回路修复
S2  delegate 数据过手（inputs 显式 + validate + artifact 引用，黑板降级）
S2.5 工作区文档协议（本节）：reports/docs 落盘 + edit_file/list_reports/read_report + decide 读文档
S3  叙述与汇报（intent/narration/report；report 事件即"报告已写"的指针）
S4  失败决策（护栏 + needs 消费 + failure 全文）
S5  运行时（真取消/状态持久化/阶段预算/收尾守门）
S6  工作区自选目录（settings + API + 前端 04 步）
```

> S2.5 的位置很关键：**它必须早于 S4**。因为"失败后决定下一步"靠的是读上一份报告里
> 「发现的问题 / 给下一位」，而不是靠 failure 里的三个枚举字段。

### 17.11 逐跳上下文契约（回应要求 1）

**要求**：主 agent **每次执行前**都要知道先前上下文——不是"首次规划时读一次"。

契约（每步 `decide` 前强制执行，`context_loaded` 事件留痕）：

| 步骤 | 动作 | 失败时 |
|---|---|---|
| 1 | 读 `docs/brief.md` 摘要（≤400 字）+ `docs/plan.md` 场景表 | 文件缺失 → 事件标 `missing`，并把"需求文档缺失"写进 decide 输入（主 agent 可决定先补需求/继续） |
| 2 | `list_artifacts()` → `name→preview`（≤12 条） | 空则写"暂无产物" |
| 3 | `list_reports(limit=3)` → 取每份「结果如何 / 给下一位」原文（≤600 字/份） | 无报告（第 1 步）则跳过 |
| 4 | 上一步 `failure` 全文（category/retryable/needs/evidence） | 无失败则跳过 |
| 5 | 剩余预算/墙钟/回合数 | — |
| 6 | 组装成 `decide` 的 user prompt；**总长受 `context_budget_chars`（默认 6000）约束**，超限按 ⑤→④→③→②→① 倒序裁剪，并记录 `trimmed` 清单 | — |

两条纪律：
1. **不允许"记得住"**：任何未写进 §17.2 文档或 §17.6 输入的历史，都不算上下文（防止主 agent 凭残缺记忆决策）；
2. **可审计**：`context_loaded` 事件带 `sources[]`（来源、字节数、是否 trimmed），出问题时能回答"它当时到底看到了什么"。

### 17.12 数据契约增补

| 对象 | 变更 |
|---|---|
| `schema/stage.py::StageResult` | 新增 `report_path: str`（报告真路径）与 `report_seq: int`；`summary` 保留（与报告同源的短摘要） |
| `schema/task.py::EVENT_TYPES` | 新增 `narration`、`intent`、`thinking`、`report`、`doc_write`、`context_loaded` |
| `settings` | 新增 `workspace_roots: list[Path]`、`context_budget_chars: int = 6000`、`report_max_chars: int = 20000` |
| `services/stage_report.py`（新） | 报告模板渲染 + `INDEX.md` 维护 + append-only 校验（纯函数为主，便于单测） |
| `services/context_loader.py`（新） | §17.11 的装载与裁剪实现；返回 `(text, sources[], trimmed[])` |
| `tool/` 新增 | `edit_file`、`glob`、`grep`（限文档目录）、`list_reports`、`read_report`、`list_artifacts`、`read_artifact`；子 agent 侧 `report_progress`、`need`、`emit_artifact` |

---

## 18. 审计后修订：状态口径、执行主体、端到端链路

> 独立审计（2026-10-26，第三个模型）对 §17 的判定：**五条需求在 PRD 层面已覆盖，但"不足以开工"**。
> 三条理由（P0）：① §17.11 依赖尚未实现的 `decide` 循环，需求 1 没有执行主体；
> ② 报告/文档的写入方与触发点没写清（requirements 不是正常阶段、`StageResult` 无 `report_path`）；
> ③ 自选目录只改 `_check_root_allowed` 不够——`Workspace.ensure` 仍用默认根，媒体/文件白名单不会跟着放宽。
> 外加一条文档级矛盾：§13 写"已落地/验收通过"，而 §16/§17 写"待实施"。本节逐条收口。

### 18.1 状态口径（先把话说清）

| 类别 | 含义 | 包含 |
|---|---|---|
| **已实现（代码可跑）** | 有代码、有测试，`grep` 可验证 | §13.1 的 P0–P3 全部；§14 的修复；`run_stage` 阶段派发（含黑板）；`read_file/write_file/list_dir` |
| **设计（本 PRD 提出，未实现）** | 有契约与验收，代码待写 | §16.10 决策回路、`delegate`、`report_path`、`reports/docs` 文档协议、§17.5 新增工具、`workspace_roots`、§17.11 `context_loaded` |
| **刻意的历史记录** | 已完成但被后续修订覆盖 | §16.2–§16.7（被 §16.10 修订）、§17.5 原表述（被 §18.3 细化） |

**读法约定**：§13/§14 是**历史落地记录**；§16/§17/§18 是**下一步的实现契约**。
两者冲突时以 §18 为准——不得把"设计"读成"已完成"。

### 18.2 需求 1 的执行主体（`decide` 循环落到代码）

```python
# clipwright/services/agent_kernel.py（改造点）
async def run(self, task: Task) -> Task:
    while not task.terminal:
        ctx = context_loader.load(task)            # §17.11：每跳装载 + 裁剪，emit context_loaded
        decision = await self._decide(task, ctx)   # LLM(with_tools)；成功路径也调用
        if decision["action"] == "finish":
            break
        step = _steps_from(decision)[0]
        task.plan.steps.append(step)
        await self._run_step(task, step)           # 失败 → 下一次循环带着 failure 再 decide
```

- 新增 `LLMPlanner.decide(task, context_text) -> {action, say, agent?, brief?, inputs?, expect?}`；
  现有 `reflect`（纯规则，`agent_kernel.py:238-272`）**降级为护栏快通道**，只在
  `policy_refused/permanent/needs 命中` 时短路，其余交 `decide`；
- `context_loader.load()` 是**唯一**的上下文入口（禁止别处再拼 prompt），返回
  `(text, sources, trimmed)`，并 `emit("context_loaded", ...)`；
- 缺 `docs/plan.md`/`docs/brief.md` 时 **不静默继续**：写入 `sources[].missing`，
  并在 text 里显式一句"需求文档缺失（可先补需求或按当前 brief 继续）"。

### 18.3 需求 2/3 的写入方与触发点（谁写、何时写、失败怎么办）

| 产物 | 写入方（唯一） | 触发点 | 失败处理 |
|---|---|---|---|
| `reports/<NN>-<stage>-<attempt>.md` | `stage_report.write_report()`（runtime，**不是**子 agent） | `stage_subagent.run_stage()` 的 `finally`：拿到 `summary`/`status`/`artifacts`/`needs`/`cost` 后写盘 | 写盘失败 → `StageResult.status="degraded"`、`report_path=""`、`emit("degradation", kind=report_write_failed)`；阶段结果本身仍返回 |
| `reports/INDEX.md` | `stage_report.write_report()` 内**原子重写**（tmp + `os.replace`） | 同上，写报告后立即更新 | 失败只 degrade，不影响报告本体 |
| `docs/brief.md` / `docs/plan.md` | `workspace.write_docs(brief, plan)` | **`requirements_service` 在"规划书生成/修订成功"之后**调用（这是唯一触发点；`requirements` 不是 `delegate` 阶段，`stage_inputs` 不参与） | 写盘失败 → 会话响带 `docs_error`，任务可继续但 `context_loaded` 会标 `missing` |
| `docs/plan-vN.md` | 同上，版本号自增 | 每次"修订规划书" | 保留旧版，不覆盖 |

`StageResult` 增补（与 §17.12 一致，这里冻结语义）：

```python
report_path: str = ""     # 报告真路径；写盘失败时为空字符串（不是 None）
report_seq: int = 0       # 序号，与 INDEX 的 seq 一致
summary: str = ""         # ≤500 字，与报告「结果如何」同源的短摘要
```

**INDEX.md 的定位（回应"是否又变回共享状态"）**：它只是**目录**，不是数据通道——
1. 子 agent **不得**读 INDEX，也不得读别人的报告（工具面里没有）；
2. 主 agent 拿 `list_reports()` 得到条目后，**必须** `read_report()` 才拿到内容；
3. INDEX 不参与 `validate/assemble`，不用来"自动补输入"。
   → 数据仍然只经主 agent 的手：读哪份、读哪节、传给谁，都是它显式决定。

**并发与一致性**：同一 workspace 的报告序号用 `mkdir` 原子占位（`reports/.seq` + `O_CREAT|O_EXCL`）自增；
INDEX 原子重写；两进程并发写同一 seq 时后者失败重试一次，仍失败则用 `seq+1`。

### 18.4 需求 4 的工具落点（逐项给模块/档位/分支）

| 工具 | 归属模块 | `TIER_MAP` 档位 | `default_executor` 分支 | 权限 |
|---|---|---|---|---|
| `edit_file` | `tool/fs_tools.py`（新增类） | `write_local` | 新分支（与 `write_file` 同族） | 仅 `docs/*.md` + 未定稿报告补记段 |
| `glob` | `tool/fs_tools.py` | `read` | 走 surface 默认 | `<ws>/**` |
| `grep` | `tool/fs_tools.py` | `read` | 走 surface 默认 | `<ws>/docs|reports` |
| `list_reports` | `tool/reports.py`（新） | `read` | 走 surface 默认 | 只读 |
| `read_report` | `tool/reports.py` | `read` | 走 surface 默认 | 只读，`max_chars≤8000` |
| `list_artifacts` | `tool/artifacts.py`（新） | `read` | 走 surface 默认 | 只读 |
| `read_artifact` | `tool/artifacts.py` | `read` | 走 surface 默认 | 只读 |
| `wait_for_stage` | `services/stage_capability.py` | `read` | 与 `get_stage` 同分支 | 只读 |
| `delegate` | `services/stage_capability.py`（`run_stage` 改名） | `costly` | 改造现有分支 | 见 §16.2 |
| 子 agent 侧 `report_progress` / `need` / `emit_artifact` | `services/stage_subagent.py` 内部通道 | — | 不进程工具面（runtime 内注入） | 只写自己的报告缓冲/needs/artifacts |

**目录截断问题**：`_capability_catalog()` 现在 `rows[:60]`（`agent_kernel.py:322`）。改两类分组 + 上限提到 120：
`## 编排与文档类`（必须全量，含上述新工具）+ `## 执行类（按需展开）`；本机 token 不紧张，不做激进截断。

### 18.5 需求 5 的端到端链路（不只改一个校验）

```
settings.workspace_roots: list[Path]（默认 [workspace_dir]）
  → security.allowed_media_roots() 遍历 roots（不只是 workspace_dir）
  → GET /api/workspace/roots → 前端下拉
  → POST /api/workspace{root} → 校验在 roots 内 → Workspace(project_id, root=...)  ← 关键：ensure 必须接受并沿用传入 root
  → <root>/<slug>-<id>/{docs,reports,artifacts,materials,assets,renders,cache,logs}
  → 渲染落点 _render_out_path(workspace_root=...) 已支持（B10）
  → 文件 API / 素材白名单 / 资产白名单 同步放开（走 allowed_media_roots 即可）
```

**硬要求**：`Workspace.ensure()` 现在只用 `_default_root()`（`workspace.py:214-217`）——必须改成
"显式 root 优先、`.workspace.json` 次之、最后才 `_default_root()`"，否则 API 收下 root 也白搭（审计 P0-3 原话）。

### 18.6 新增验收（M28–M32）

| # | 判据 |
|---|---|
| M28 | "给下一位"进入 decide 输入时被标记为 `untrusted_suggestion`（报告原文不改，但 prompt 里有标签）；主 agent 若据此行动，必须先 `read_artifact`/`validate`（测试断言：直接照做会被 `validate` 拦下并记录） |
| M29 | 报告写盘失败 → `StageResult.status=degraded`、`report_path=""`、事件 `degradation(report_write_failed)`，阶段结果不丢 |
| M30 | 并发写同一 workspace（两任务或同任务重跑）→ 序号不冲突，INDEX 条目数 == 报告文件数 |
| M31 | `Workspace.ensure(root=...)` 真的落在传入 root 下（不是默认根）；`allowed_media_roots()` 含该 root；渲染落点跟随 |
| M32 | 目录截断修复后，`_capability_catalog()` 里能搜到 §17.5 列出的全部新工具名（逐个断言） |

### 18.7 与 §13 的关系（消除矛盾）

§13.1 表格下方追加一行注记：

> 注：§13 记录的是 P0–P4 的**历史落地**。§16（决策回路）与 §17/§18（文档协议、工具集、
> 自选目录）是**其后追加的设计**，尚未实现；读本文档时不要把设计当作已完成。

### 18.8 为什么没有 `spawn_stage`（本次已从代码删除）

**结论：不需要，也不应该有。子代理直接回传主 agent，就是唯一的形态。**

| 论点 | 说明 |
|---|---|
| "spawn" 的语义就是"派出去不管" | 一旦允许不等结果，主 agent 就没有"这一步的结论"可依据，数据又绕开了它。而这次改造的核心判据正是**数据必须过主 agent 的手**（§16.1） |
| 阶段是"真干活"，不是后台任务 | 一个阶段要跑 LLM + ffmpeg + ffprobe，几十秒到几分钟；主 agent 在这期间没有别的独立工作可做（下一跳依赖这一步的产物）。"并行"在没有第二个可并行任务时只是把复杂度变成轮询 |
| 别名会制造"两套机制"的错觉 | 审计已点出"两套子代理并存"是当前最严重的结构问题之一（`spawn_subagent` vs 阶段派发）。再留一个 `spawn_stage`，等于把同一个坑挖深一层 |
| 真需要并行时也有干净写法 | 用"多个 pending 的 delegate + `wait_for_stage`/`list_stages` 收"（§17.5 已列 `wait_for_stage`），而不是引入 fire-and-forget。届时 `delegate` 加一个 `async=true` 参数即可，**不需要第二个能力名** |

**本次实际动作**（代码已改，相关测试 49 + 17 条通过）：
- `capability_matrix.META_CAPABILITIES` 删除 `spawn_stage` 条目；
- `agent_kernel.default_executor` 分支从 `("run_stage","spawn_stage","get_stage","list_stages")` 收为 `("run_stage","get_stage","list_stages")`，注释写明"不存在 spawn_stage"；
- 文档同步：§3.6 能力表、§6 验收、数据契约、测试计划、§16.2、§16.11 全部去掉该名；
- 实测：`_capability_catalog()` 含 `run_stage`、**不含** `spawn_stage`，目录 67 行。

**连带的设计约束**（写给后续实现者）：
1. `delegate` 必须**同步返回** `DelegationResult`；不允许"返回一个 id、稍后再问"作为默认路径；
2. 将来若加 `async=true`，返回的仍是 `DelegationResult{status:"pending"}`，且**必须**经 `wait_for_stage` 收敛到同一形状——不新增第二种结果契约；
3. `spawn_subagent`（只读调研员）保留但**不与阶段派发合并**：它定位是"外部信息核查"，有独立的两级审核门与并发限制；两者共用同一本成本账（`llm_tracker`）即可。

---

## 19. 前端：副驾驶 · 编辑器内运行 · 思考可见（需求 8/9/10）

> 本节把原先独立的 `editor-copilot-prd-2026-10-26.md` 的三条前端需求**并入本文**，成为唯一真值；
> 原文件保留为历史复现记录（其中的 `edit` 空场景失败复现与 D1–D7 细节在这里保留摘要）。

### 19.1 现状事实（代码证据）

| 事实 | 证据 |
|---|---|
| 编辑器是固定 4 面板（assets / properties / timeline / agent），**agent 面板没有标签概念**；面板宽度用单一 `panelWidths.agent`，移动端 `mobilePanel` 是单值 | `src/layouts/EditorLayout.tsx:31-46`、`:14`、`:49,62,111-125`、`:310/:348` |
| 主 Agent 的活动被关在 `/task/{taskId}` 独立页：`proceed` 返回 `task_id` 后前端**跳转** | `src/features/agent/proceedResult.ts`；`src/pages/TaskPage.tsx:21-28`（EventFeed/PlanPanel/TaskArtifacts/TaskSubagents/TaskToolbar） |
| 事件面后端已就绪：SSE + JSON，事件类型覆盖工具/阶段/降级/审批 | `clipwright/api/task.py:156-175`；`schema/task.py:28-45` |
| "思考"拿不到：`reasoning_content` 只在非流式日志里用过一次；requirements SSE 只有 `status/delta/result` | `services/llm.py:377-380`；`requirements_service.py::stream_chat` |
| token 级流式能力已存在 | `services/llm.py:522-571`（`stream_generate`） |
| 规划/工具执行前**没有**"先说话"环节 | `agent_kernel.py::LLMPlanner.plan` 直接返回 JSON 计划；`default_executor` 直接执行 |

### 19.2 副驾驶标签化（需求 8）

**数据模型**（`src/stores/copilotStore.ts`，新增）：

```ts
type CopilotTabKind = 'requirements' | 'task-run' | 'subagent' | 'stage' | 'workspace' | 'docs' | 'plan';
interface CopilotTab {
  id: string;          // 'requirements' 固定；'subagent:<id>'；'stage:<name>'；'task:<id>'；'docs:<path>'
  kind: CopilotTabKind;
  title: string;
  closable: boolean;   // requirements 恒 false
  payload?: { taskId?: string; subagentId?: string; stage?: string; path?: string };
}
// state: tabs[] / activeId / openTab / closeTab / reorder；持久化 localStorage（按 projectId 分键）
```

规则（逐条可测）：
1. `requirements` **永远存在、永远第一、不可关闭**：不渲染关闭按钮、`×` 快捷键无效、`closeTab('requirements')` 是 no-op；
2. 关闭当前激活标签 → 焦点回落到 `requirements`；
3. 同 `id` 不重复添加（再次点击 = 聚焦）；
4. 上限 8 个，超出提示"先关掉一个"；
5. 刷新后恢复（`requirements` 恒存在）。

组件：`CopilotDock`（容器）+ `CopilotTabBar`（`+` 菜单 / 关闭 / 中键关闭 / 拖拽排序）+ 按 `kind` 渲染内容。
既有 `AgentPanel` **内部不改**，被包进 `kind:'requirements'` 的内容区（降低回归面）；`EditorLayout` 右侧从渲染 `<AgentPanel/>` 改为 `<CopilotDock/>`，复用 `panelWidths.agent`。

### 19.3 编辑器内运行 + 子 agent 下钻（需求 9）

**硬要求：不再跳 `/task/{taskId}`。**

- `proceedResult.ts` 只写 store（`taskId` 进 `copilotStore`），不 `navigate`；`/task/$taskId` 路由保留一个版本周期（老书签），页面内提示"该流程已并入编辑器"；
- "任务运行"标签（`kind:'task-run'`）= 复用现有展示件重组，不重写：
  - 顶部状态条（状态/时长/花费，复用 `TaskToolbar` 的展示逻辑）→ `PlanPanel` → `EventFeed`（全量日志、工具、Skill、阶段行）→ 阶段时间线（每 stage 一行：summary/writes/cost）→ 底部 `RunningSubagents` → `TaskArtifacts`；
- SSE 单实例：由 `CopilotDock` 持有 `useTaskEvents`，切标签**不断流**、关标签不重建连接（验收 U8）；
- **日志/工具/Skill/召唤子 agent 一律只在这个容器里出现**，不提供任何"去 /task 看详情"的路径。

**子 agent 下钻**：
- 底部 `RunningSubagents` 横向 chip：`role`、`status`（running/idle/pending_review/done/failed）、`elapsed`、`cost`；
- 点击 chip → `openTab({id:'subagent:'+id, kind:'subagent'})` → 右侧新增标签，显示该子 agent 的事件时间线、`role`、输入摘要、输出结论（≤2000 字）、成本/tokens、`failure.category`；
- **只在点击时开**（绝不自动开，防标签爆炸）；子 agent 结束后标签保留（便于复盘），关闭不影响其运行。

### 19.4 思考与叙述（需求 10 + 需求 7 的前端部分）

后端通道（与 §16.3 一致，事件类型需先进 `EVENT_TYPES`）：

| 帧/事件 | 触发 | 内容 |
|---|---|---|
| `thinking` | provider 返回 `reasoning_content` 增量（无则不发） | `{delta, done?}` |
| `intent` | 每次 `delegate` / 工具执行**之前** | `{text, step_id, agent}`（文本来自 `PlanStep.say`，缺省模板句） |
| `narration` | 规划产出后、反思改计划后 | `{text, after_step?}`（"我打算这么做，因为…"） |
| `report` | 每个阶段/子 agent 完成后 | `{headline, bullets[], artifacts[], cost_usd, duration_ms}` |

前端交互：
- 回复 LLM 后先渲染一条 **`正在思考 ▾`** 折叠条（`ThinkingDisclosure`）；点击展开 → 订阅 `thinking` **流式追加**（等宽、自动滚底、可复制）；结束变 `思考了 N 秒 · 展开`；
- provider 不返回 reasoning → 显示"该模型未提供思考内容"（不空转、不报错）；
- 执行工具前，事件流插入一条斜体 `intent` 行（"准备：…"），紧接其后才是工具行；
- `report` 用**汇报卡**渲染（标题 + ≤5 条要点 + 产物预览 + 成本），与工具行区分——用户读的是这段话，不是 JSON。

### 19.5 失败可见与可恢复（需求 7 的后端配套，摘要）

复现结论（详细证据见 `editor-copilot-prd-2026-10-26.md` §1.4）：
`material` 在 scenes 为空 / 无素材源时返回 **PASS**（`material_agent.py:741-760`），
`edit` 因 scenes 为空返回 **FAIL 但不写 error/failure**（`edit_agent.py:319-325`，全文件唯一 FAIL），
于是 `_failure_from_output` 兜底成 `unknown` + `retry_same` → kernel 盲重试一次后收尾
→ 用户看到 `run_stage 未成功：unknown（retryable=retry_same）`。附带：失败时返回的**空壳 timeline 仍被写入黑板**。

必改（D1–D7）：

| # | 位置 | 改动 |
|---|---|---|
| D1 | `stage_subagent._failure_from_output` | 无结构化 `failure` 时把原文错误写进 `evidence`，不只写 `unknown` |
| D2 | 同上 | 按错误文本保守归因（缺输入/超时/磁盘/模型），`retryable` 跟随类别（`input_insufficient → needs_upstream`） |
| D3 | `agent_kernel._reflect(finish)` | 结论三段式：`类别｜阻塞点｜建议`，`evidence` 为空用 `blocking_point` 兜底 |
| D4 | `agent_kernel._reflect(retry)` | `retry_same` 仅确实可重试时重跑一次；`input_insufficient` → 走 `stage_closure` 自动补上游 |
| D5 | `edit_agent.py:319-325` | 空场景失败带 `failure=FailureReport(category="input_insufficient", needs=[{from_stage:"structure", field:"scenes"}])` |
| D6 | `stage_subagent` / `stage_merge` | `decision=FAIL` 时**不合并 timeline**（空壳时间线入库会让下游基于空片继续跑） |
| D7 | `material_agent.py:741-760` | scenes 空 → `FAIL(input_insufficient)`；无素材源 → `PASS` 但显式登记 `degradation`（让上游能判定"素材为空"） |

### 19.6 前端工作区（需求 5 的前端部分）

- **目录选择**（新建制作 04 步）：根下拉（`GET /api/workspace/roots`）+ 子目录名输入 + 试写探测；非法路径当场报错，不等到创建后；
- 换根只影响**新建**工作区；已有工作区按 `.workspace.json` 的 `root` 显示（不搬数据）；
- **文档标签**（`kind:'docs'`）：列 `reports/INDEX.md` + `docs/*.md`，只读渲染（markdown → HTML），顶部显示 `stage/status/attempt/cost/artifacts`，正文按四段折叠；`doc_write` 事件到达时自动追加一条；
- 前端**不允许编辑文档**（编辑权在主 agent 的 `edit_file` 与用户的对话改写）。

### 19.7 验收（U1–U12）

| # | 判据 |
|---|---|
| U1 | 需求对话标签无关闭按钮；`closeTab('requirements')` 后 `tabs` 仍含它且 `activeId` 回落 |
| U2 | 添加/关闭"任务运行""工作区""子 agent""文档"标签，集合与焦点正确；刷新后恢复 |
| U3 | 同 id 重复添加只聚焦；超过 8 个提示且不新增 |
| U4 | 关闭非激活标签不影响当前激活标签 |
| U5 | `proceed` 成功后 **URL 不变**（仍在 `/editor`），"任务运行"标签自动打开并开始收事件 |
| U6 | 事件流可见：阶段 `step_start/step_end`、工具 `tool_call`（`kind=skill` 打标）、`degradation`、`gate_hit`；页面无任何"去 /task 看详情"提示 |
| U7 | 底部出现运行中子 agent chip；点击新增 `subagent:<id>` 标签并显示其事件/输出/成本；关闭标签不影响其运行 |
| U8 | 切换/关闭标签 10 次，`events/stream` 连接数恒为 1（不重连风暴） |
| U9 | 未点击的 subagent **不会**被自动开标签 |
| U10 | 出现"正在思考 ▾"；展开后 `thinking` 文本流式追加；结束后显示耗时并可再次展开 |
| U11 | provider 不返回 reasoning → 显示"该模型未提供思考内容"，无报错 |
| U12 | 每个工具/阶段调用前有一条 `intent` 行；每次 `step_end` 后有一条 `report` 汇报卡 |
| U13 | 04 步目录选择：合法路径创建成功，非法/不可写路径当场报错（不产生半成品工作区） |
| U14 | 文档标签只读；点击报告条目能渲染四段式内容；`doc_write` 到达时列表自动追加 |

### 19.8 前端分期（并入总排期）

| 期 | 内容 | 人日 | 依赖 |
|---|---|---|---|
| F1 | `copilotStore` + `CopilotDock` + `CopilotTabBar`（需求 8） | 3 | 无（可立刻做） |
| F2 | 任务运行标签 + 不跳转 + 子 agent chip/标签（需求 9） | 4 | 后端 `EVENT_TYPES` 就绪（S0-1） |
| F3 | `ThinkingDisclosure` + `intent`/`narration`/`report` 渲染（需求 10、7 前端） | 3 | 后端 S3 事件 |
| F4 | 文档标签 + 04 步目录选择（需求 5 前端） | 2 | 后端 S2.5 / S6 |

> 顺序上 **F1 与后端 S0/S1 可并行**（前端纯壳，不依赖 decide 循环）；
> F2 必须等 S0-1（`EVENT_TYPES` 加类型，否则 `emit` 抛 `EventError`）；
> F3 等 S3；F4 等 S2.5/S6。

### 19.9 事件 → UI 契约（冻结字段名，前端不再靠猜）

**数据源只有两处**，不引入第三个：

| 数据 | 来源 | 刷新方式 |
|---|---|---|
| 任务事件（日志/工具/Skill/阶段/叙述/汇报/思考） | `GET /api/task/{id}/events/stream`（SSE） | 由 `CopilotDock` 持一条连接，切标签不断流 |
| 子 agent 运行状态（chip 列表、成本、状态） | `GET /api/pipeline/{taskId}/subagents`（pid 可为 task id，已有） | **事件驱动 + 3s 兜底轮询**：收到 `step_start/step_end` 立即拉一次；无事件时空转 3s 拉一次（仅在 task 非终态时） |

逐类事件的渲染映射（**字段名以本节为准**，后端 emit 时必须带上）：

| 事件 `type` | 前端行/控件 | 必须读取的 `detail` 字段 | 备注 |
|---|---|---|---|
| `task_start` | 状态条出现 "运行中" | `{mode}` | — |
| `plan` / `replan` | 计划区（`PlanPanel`）+ 一条 `narration` 卡 | `{steps[], why}` | replan 额外显示"新增 N 步" |
| `narration` | 正文档落（斜体块） | `{text, after_step?}` | 用户可读的"我打算…因为…" |
| `intent` | 事件流一行（斜体，"准备：…"） | `{text, step_id, agent}` | 必须在对应工具行**之前** |
| `step_start` | 阶段行开始 | `{stage?, capability, agent?, args_keys?}` | `stage` 缺省时用 `capability` 作标题 |
| `tool_call` | 工具行 | `{step_id, args, tier_hint, kind?}` | `kind==="skill"` → Skill 图标；否则工具图标 |
| `tool_result` | 工具行结果（可展开） | `{status, brief, error, duration_ms, failure?}` | `failure` 全文进展开区（D3） |
| `thinking` | **思考折叠条**（不进行流，单独控件） | `{delta, done?}` | 与当前 step 绑定；新 step 开始时重置 |
| `report` | **汇报卡** | `{headline, bullets[], artifacts[], cost_usd, duration_ms}` | 一步一张，紧跟在 `step_end` 之后 |
| `degradation` | 黄色警示行 | `{kind, detail}` 或数组 | 可折叠多条 |
| `gate_hit` / `approval_request` | 阻断提示 + 操作按钮 | 事件自带字段 | 复用现有审批组件 |
| `artifact` | 产物条目（`TaskArtifacts`） | `{name, artifact, preview}` | 点击 → 文档/产物标签 |
| `doc_write` | 文档标签列表追加一条 | `{path, bytes, by}` | 不弹窗，只更新列表 |
| `context_loaded` | 默认**不显示**（仅调试模式显示在"决策"折叠区） | `{sources[], trimmed[], bytes}` | 用于回答"它当时看到什么" |
| `step_end` | 阶段行结束（状态/成本/摘要） | `{stage?, status, summary?, writes?, cost_usd?, duration_ms?}` | `summary` 是阶段报告的短摘要 |
| `task_end` | 状态条收尾 + 收尾卡（三件套） | `{status, reason, completed[], unfinished[]}` | 失败时 reason 必为三段式（D3） |

**兼容规则**：前端对未知 `type` 一律降级为"系统行"（灰字 + 原始 JSON 折叠），**不许白屏**；
后端新增事件类型必须先加 `EVENT_TYPES`（`S0-1`），否则 `emit` 抛 `EventError`。

### 19.10 阶段标签与子 agent 标签的分工（`kind:'stage'` 补全）

两种"子代理"在 UI 上**不是同一个东西**，必须分开呈现：

| | 阶段（stage） | 调研子代理（subagent） |
|---|---|---|
| 触发 | `delegate`/`run_stage` | `spawn_subagent` |
| 数据源 | 任务事件（`step_start/step_end` + `report`） | `/api/pipeline/{id}/subagents`（tracker 快照） |
| 在编辑器里 | **阶段行**（任务运行标签内的阶段时间线）；点击 → `kind:'stage'` 标签，内容 = 该阶段历次 `report` 卡 + 产物 + 成本 + 写入的键 | **chip**（底部 `RunningSubagents`）；点击 → `kind:'subagent'` 标签，内容 = 该 worker 事件/输出结论/成本/tokens/failure |
| 关闭标签 | 不影响运行 | 不影响运行 |

`kind:'stage'` 标签内容（此前只声明了 kind，没有内容规范，现补）：
1. 顶部：`stage`、最近一次 `status`、`attempt` 次数、累计成本、最后一次 `duration_ms`；
2. 中部：该 stage 的**历次报告卡**（按序号倒序，`report` 落盘后以 `read_report` 取内容）；
3. 底部：它产出/写入的产物（`artifacts[].name + preview`）与 `writes`（黑板/文档键）。

### 19.11 多任务与项目切换（此前未定义）

| 场景 | 规则 |
|---|---|
| 同一项目再次 `proceed` | 已有 `task-run` 标签时**复用它**（换 `taskId` 并清空旧事件），不新开第二个"任务运行"标签 |
| 切换到另一个项目 | `copilotStore` 按 `projectId` 分键持久化：切项目后标签集合随之切换；原项目的 `task-run` 标签留在该项目的键里（回来还在） |
| 同一项目并发两个任务 | 本期**不支持**（`TaskLimits` 与工作区都是单任务语义）；若检测到第二个 `taskId`，`task-run` 标签顶部提示"已有任务在运行，先停止或等待"，不并行订阅两条 SSE |
| 任务已终态 | 标签保留（可复盘），SSE 连接关闭，按钮变"重新运行" |
| 老书签 `/task/{id}` | 页面顶部提示"该流程已并入编辑器"，提供"在编辑器中打开"按钮（写入 store 后 `navigate('/editor')`） |

### 19.12 用户改需求的两个入口（此前含糊）

- **对话入口（主）**：需求对话标签 = 现有 `AgentPanel`/`ReviewPanel`，用户用自然语言改简报/规划书 → 后端生成 `docs/plan-vN.md`；
- **文档入口（辅）**：`docs` 标签**只读**展示 `brief.md`/`plan.md`，顶部提供"用对话修改"按钮 → 聚焦需求对话标签并预填"请修改规划书：…"；
- **前端永不直接编辑文档**（编辑权在主 agent 的 `edit_file`），避免用户与主 agent 同时改一份文件产生竞争。

### 19.13 新增验收（U15–U18）

| # | 判据 |
|---|---|
| U15 | 未知事件 `type` 不白屏：渲染为"系统行"+ 可展开原始 JSON |
| U16 | 阶段行点击开 `kind:'stage'` 标签，内容为该阶段历次 `report` 卡 + 产物 + writes；同一阶段重跑后标签内出现两张卡（不覆盖） |
| U17 | 同项目再次 proceed 复用同一个 `task-run` 标签（不新增第二个）；切项目再切回来，标签集合恢复且不丢 task |
| U18 | 并发第二任务被拦：`task-run` 顶部提示"已有任务在运行"，SSE 连接数仍为 1 |

---

## 20. 实施记录（2026-10-26 第二批）

> 本轮按 §16.11 / §17.10 / §19.8 开工，交付了 **S0 + S1(部分) + S2.5 + S6(后端) + F1**，
> 其余明确未做（下表逐项列出，避免又出现"设计当已完成"）。

### 20.1 已实现（代码可跑，有测试）

| 期 | 内容 | 落点 | 验收 |
|---|---|---|---|
| S0 | `EVENT_TYPES` 增加 `narration/intent/thinking/report/doc_write/context_loaded` | `schema/task.py` | M11 |
| S0 | `StageResult.report_path/report_seq` | `schema/stage.py` | M24 |
| S1 | `_brief` 优先用 `summary`；`_observe` 保留 summary/writes/artifacts preview/failure 细节（不再一刀切 120 字） | `agent_kernel.py` | A4/M10 |
| S1 | `_run_step` 在 executor **之前** emit `intent`；`step_end` 之后 emit `report` | `agent_kernel.py` | M8/U12 |
| S1 | `LLMPlanner.decide` + `AgentKernel._decide`：护栏（policy_refused/permanent → finish；`needs.from` 缺则自动补派）→ 轻量成功步走规则 → 其余走 LLM；异常退回 `_reflect`；注入式 fake planner 直接走规则 | `agent_kernel.py` | M10 |
| S1 | `context_loader`（新）：brief/plan 场景表/产物 preview/最近 3 份报告「结果如何·给下一位」/failure → 文本 + 预算裁剪 + `sources/trimmed`；缺文档显式写"⚠ 需求文档缺失" | `services/context_loader.py` | M16/M23/M25 |
| S2.5 | `stage_report`（新）：报告 md（四节 + front-matter）、`INDEX.md`、`.seq-N` 原子占号、并发重建 | `services/stage_report.py` | M14/M15/M30 |
| S2.5 | `run_stage` 落报告并回填 `report_path/report_seq`；写失败 → `degraded` + `report_write_failed` 降级 + `degradation` 事件 | `stage_subagent.py` | M24/M29 |
| S2.5 | 工作区 `docs/ reports/ artifacts/` 目录；`write_docs()`（`plan-vN.md` 用 `O_EXCL` 占位防撞名）；`ensure(root=...)` | `services/workspace.py` | M19/M31 |
| S2.5 | 工具 `list_reports` / `read_report` / `edit_file`（注册 + 档位；`edit_file` **只允许 `docs/*.md`**） | `tool/reports.py` | M18/M26/M27 |
| S2.5 | 需求文档落盘唯一触发点：`_proceed_as_task` 调 `ws.write_docs(brief, plan)` | `api/requirements.py` | M19 |
| S6 | `settings.workspace_roots/context_budget_chars/report_max_chars/agent_decide_policy`；`allowed_media_roots` 纳入 roots；`GET /api/workspace/roots`（含试写探测）；`create` 把 root 传给 `ensure`；已存在且 root 不同 → 明确报错 | `config*.py`、`security.py`、`api/workspace.py` | M21/M31 |
| F1 | `copilotStore`（requirements 不可关 / 去重 / 上限 8 / 按 projectId 持久化 / 脏数据修正回写）、`CopilotTabBar`、`CopilotDock`，`EditorLayout` 右侧与移动端改渲染 `<CopilotDock/>` | `src/stores/copilotStore.ts`、`src/features/copilot/*` | U1–U4 |

回归：后端 **4448 passed / 4 skipped / 1 xfailed**；前端 **979 passed**（`tsc` 干净）。
新增测试：`tests/test_docs_protocol.py`（11 条）、`src/stores/copilotStore.test.ts`（6 条）、`src/features/copilot/CopilotTabBar.test.tsx`（3 条）。

### 20.2 未实现（第二批之后更新的清单）

| 项 | 现状 | 依赖 |
|---|---|---|
| ~~S2 `delegate` 完全改名~~ | **部分完成**：`StageSpec.inputs` 显式传参 + `stage_inputs.validate` 已落地（给了 inputs 就**不读黑板**，缺则返回 `needs`）；能力名仍是 `run_stage`（改名待做） | 改名需同步 checkpoint 旧名映射与 44 处测试 |
| ~~S3 思考流~~ | **基础完成**：`llm.take_last_reasoning()` 取 provider 的 `reasoning_content`，kernel 在 `_run_step`/`_decide` 后 emit `thinking`；前端 `ThinkingDisclosure` 可展开。**token 级流式未做**（目前是整段 done 帧） | provider 的 stream chunk 若带 reasoning 再做增量 |
| ~~S4 D5/D6/D7~~ | **完成**：edit 空场景失败带 `input_insufficient + needs`；失败阶段的空壳 timeline 不再入库；material `scenes=[]` → FAIL，无素材源 → PASS + `degradation` | — |
| S5 运行时 | 真取消（kernel 持 `_TASK_HANDLES` + `cancel()` 打断协程）✅；收尾守门（创作流程无 `final_timeline` 不算完成）✅；**状态持久化（黑板/runs 进 checkpoint）未做** | 需扩 checkpoint 结构 |
| ~~F2 任务运行标签~~ | **完成基础版**：`CopilotTaskRunTab`（工具栏 + 思考条 + 计划 + 阶段行 + 事件流 + 子 agent chip + 产物）；`proceedResult` 不再跳 `/task`，改为开标签 | — |
| `subagent` / `stage` / `docs` 标签 | `subagent` 仍是占位（可先看任务运行里的产出）；`stage`/`docs` 占位 | docs 需要 `GET /api/workspace/{id}/reports`（未做） |
| **F4 04 步目录选择 UI** | 后端 `roots` 端点已就绪，前端下拉未做 | — |
| `run_pipeline` 条目 | 仍在 `capability_matrix`（`_capability_catalog` 已过滤），留作回滚开关 | 物理删除随 P4 |

### 20.3 独立 code review（第三轮）与处置
审阅结论：**"不能开工交付"**——因为 F2/F3/F4 仍是占位、且存在 1 个 P0 + 若干 P1。已修：

| 审阅项 | 处置 |
|---|---|
| P0 报告失败只改 status、不发事件；root 为空时跳过降级 | ✅ 补 `degradation(report_write_failed)` 事件 |
| P1 `INDEX.md` 并发可能丢行 | ✅ 写完回读文件数，不一致重建（最多 3 次） |
| P1 `plan-vN.md` 无锁可能撞名 | ✅ `O_CREAT|O_EXCL` 占位 |
| P1 `edit_file` 只拒 `reports/`，能改 artifacts/materials/根目录文本 | ✅ 收紧为**只允许 `docs/*.md`** |
| P1 缺 `plan.md` 只标 missing 后静默继续 | ✅ 上下文里显式写"⚠ 需求文档缺失，可先补需求或确认继续" |
| P1 已有工作区时忽略传入 root | ✅ 根不同直接 `WorkspaceError`（"换根只对新建生效"） |
| P2 脏 localStorage 规范化后未回写 | ✅ `bindProject` 时回写 |

未修（记录在案）：`run_pipeline` 仍在 `capability_matrix`（`_capability_catalog` 已过滤，留作回滚）、成功轻量步跳过 LLM 决策（`agent_decide_policy=auto` 的设计选择，`always` 可全量判）。

### 20.4 第二批（继续未实现清单）

| 项 | 落点 | 验收 |
|---|---|---|
| **S4 D5**：edit 空场景失败带 `failure(input_insufficient, needs=[scenes@structure])`，并把原因写进 `error` | `agents/edit_agent.py` | M6 |
| **S4 D6**：阶段失败且时间线为空壳 → **不合并进黑板**（`stage_merge`） | `services/stage_merge.py` | A5/M6 |
| **S4 D7**：material `scenes=[]` → FAIL(`input_insufficient`)；无素材源 → PASS + `degradation(material_placeholder)` | `agents/material_agent.py` | M2 |
| **S2 步进**：`StageSpec.inputs` 显式传参 + `stage_inputs.validate`；给了 inputs 就不读黑板，缺键返回 `needs`；artifact 引用可解析成对象 | `schema/stage.py`、`stage_inputs.py`、`stage_subagent.py`、`stage_capability.py` | M1/M3 |
| **S5 真取消**：kernel 持 `_TASK_HANDLES`，`cancel()` 直接打断协程（不再只置标记） | `agent_kernel.py` | A8 |
| **S5 收尾守门**：创作流程（plan 里有 run_stage）无 `final_timeline` → 不算 done，reason 写明 | `agent_kernel._finish` | A5 |
| **产物落盘**：`persist_artifacts` 把 ≥512B 的产物写 `artifacts/art_xxxx.json`，结果里只留 `{artifact,path,sha256,preview}` | `stage_subagent.py` | M26 |
| **工具**：`list_artifacts` / `read_artifact`（支持 `json_path` 取子树、200KB 上限） | `tool/artifacts.py`（新） | M26/M32 |
| **S3 思考**：`llm.take_last_reasoning()`；kernel 每步/每次决策后 emit `thinking` | `services/llm.py`、`agent_kernel.py` | M8/U10 |
| **F2 任务运行标签**：`CopilotTaskRunTab`（工具栏/思考条/计划/阶段行/事件流/子 agent chip/产物）+ `proceedResult` 不再跳 `/task` | `src/features/copilot/*`、`proceedResult.ts`、`AgentPanel`/`ReviewPanel` | U5–U9 |
| **前端事件类型**：`TaskEventType` 与 `EVENT_CN` 补齐 6 类 | `src/types/task.ts`、`features/task/labels.ts` | U15 |

第二批回归：后端 **4448+ passed**（含新增断言）、前端 **992 passed**（新增 `CopilotTaskRunTab.test.tsx` 4 条 + 原有 9 条），`tsc` 干净。

### 20.5 第三批（把 §20.2 的剩余清单清零）

| 项 | 做法 | 验收 |
|---|---|---|
| **能力改名 `delegate`** | 矩阵只广告 `delegate`；`stage_capability` 认 `delegate`（新）与 `run_stage`（旧名兼容）；kernel 分支两者都收；**检查点加载时把 `run_stage`/`spawn_stage` 映射成 `delegate`**（`_load_checkpoint`），在飞任务续跑不被卡死 | M9 |
| **thinking 真流式** | requirements 对话走的是 `stream_generate`：在 `_stream_gathering_llm` 里逐块转发 `chunk.reasoning_content` → `chat(on_reasoning=…)` → `stream_chat` 发 `{"type":"reasoning"}` 帧；前端 `AgentPanel` 收帧追加到 `liveReasoning`，"正在思考 ▾"可展开看增量 | U10/U11 |
| **docs 端点** | `GET /api/workspace/{id}/reports`、`.../reports/{seq}`、`.../docs`（brief/plan 只读） | U14/M19 |
| **docs / stage / subagent 三个标签** | `CopilotDocsTab`（报告索引 + docs 列表 + 只读正文）、`CopilotStageTab`（按 stage 过滤报告）、`CopilotSubagentTab`（从 supervisor 快照取状态/成本/输出） | U7/U16 |
| **04 步父目录选择** | HomePage 拉 `GET /api/workspace/roots`，下拉选父目录（不可写禁选）→ `create({root})`；后端 `ensure(root=)` 语义修正为**父目录**，实际目录仍是 `<父目录>/<slug>-<id>` | U13/M21/M31 |
| **状态持久化** | checkpoint 旁写 `<task>.state.json`（黑板 + 阶段运行表 + 归属）；`_load_checkpoint` 顺带恢复（幂等，已有数据不覆盖）；`stage_subagent.import_runs()` 恢复阶段表 | M12/M20 |
| **`run_pipeline` 摘除** | 从 `META_CAPABILITIES` 删除条目（`coverage.by_kind.pipeline == 0`）；`pipeline_capability` 代码保留作为回滚开关，P4 物理删除 | A1 |
| 回归 | 后端 **4464 passed / 4 skipped**（新增 `test_docs_protocol` 3 条：reports/docs API、旧名兼容）；前端 **983 passed**；`tsc` 干净 | — |

> 至此 §20.2 里"未实现"的条目全部关闭；唯一保留的是 **P4 物理删除**（`pipeline_v2.py` / `api/pipeline` 执行路由）
> 与 `pipeline_capability` 的代码本体——它们作为回滚开关存在，按 §15.2 的七步清单单独 PR 处理。
