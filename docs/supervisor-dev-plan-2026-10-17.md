# 主 agent 化 · 后端开发计划 v1.0

| 项 | 内容 |
|---|---|
| 分支 | `主agent化`（基于 `feat/pluginization` @ `8abfae6`） |
| 日期 | 2026-10-17 |
| 上位文档 | `docs/supervisor-agent-prd-2026-10-17.md`（PRD v0.2） |
| 范围 | PRD 的 F1–F9 后端侧；不含前端（见前端仓库 `docs/supervisor-frontend-dev-plan-2026-10-17.md`） |
| 一句话 | 把「盲质检 + 静态派工表」换成「账本 → 归因 → 派工 → 诊断」的**确定性**决策链，并让派工可以指定子阶段模型 |

---

## 1. 五条工程原则

1. **影子优先（shadow）**：先只观察不改执行。秘书先把诊断写出来，与真实结果对照，攒够准确率再允许它真的派工。
2. **单点埋设**：账本只在 `_run_agent` 一处写入，绝不在 6 个 Agent 内部各写一份——否则一定漂移。
3. **契约先行**：F9 先落地，账本才有内容。先建壳后建约定，账本会退化成「编排队代填的日志」。
4. **默认关闭**：`CLIPWRIGHT_SUPERVISOR_MODE = off | shadow | on`，默认 `off`。
5. **可回退**：`off` 时行为、耗时、成本与现状等价（PRD A10 用逐字节 timeline 比对）。

---

## 2. 现状锚点（要动的代码）

| 位置 | 现状 | 本计划改动 |
|---|---|---|
| `schema/agent.py:54/86/111/143/169/222/251` | 6 个输出类各自重复 `decision` + `error: Optional[str]` | 抽 `AgentOutputBase`，新增 `failure` / `degradations` 字段 |
| `services/pipeline_v2.py:1412-1452` | 决策落定后只记 token | 在此处**单点**写账本 |
| `services/pipeline_v2.py:628-737` `_self_heal_quality` | 循环骨架 + 静态 redo | 保留循环骨架，归因/策略外移至 supervisor |
| `agents/quality_agent.py:348-357` | error 类别 → 固定 redo_agent | 降级为「症状分类器」；redo 由归因层决定（本表保留为 fallback） |
| `services/pipeline_v2.py:1252-1304` `_build_input`、`1551-1632` `_dispatch` | 派工实际入口 | 复用为 `execute_order` 的执行通道（**不**新增派工通道） |
| `services/pipeline_v2.py:470-539` | `_circuit_breakers` 键 = agent 名 | 键扩为 `(stage, model)` |
| `services/llm.py:410`、`476` | 模型选择只有二元 `use_flash` | 增加 `model_override`（provider + model）透传 |
| `agents/base.py:126-137` `llm_or_fallback` | 失败只 `logger.warning` 后返回 fallback | 返回/登记降级原因（`degradations`） |
| `services/pipeline_v2.py:754-755` | 类型 transform 失败保留原时间线，只打日志 | 登记降级 |
| `api/pipeline.py:715-760` `/diagnostics/{pid}` | 自建一套失败定位逻辑 | 改为消费 diagnosis 服务，保持返回结构向后兼容 |
| `services/agent_bus.py` | artifact/events 只有 trace 用途，`get_events` 零消费者 | 不删；定位为账本的只读投影，禁止再写业务数据 |

> **不做**：不改 `_DEPS`（阶段顺序）、不改 `QualityInput`（盲审红线）、不删 `AgentBus`（有前端/诊断依赖，本期不动）。

---

## 3. 新增模块

```
clipwright/
├── schema/
│   ├── agent.py              # + FailureReport / Degradation / AgentOutputBase
│   └── supervisor.py         # 新：LedgerEntry / Attribution / DispatchOrder / DiagnosisReport / ModelRoute
├── services/
│   ├── episode_ledger.py     # 新：账本（进程内 + Mongo 落库）
│   ├── attribution.py        # 新：归因（规则优先，LLM 仅兜底）
│   ├── supervisor.py         # 新：秘书（策略选择 + 派工 + 决策留痕）
│   ├── model_router.py       # 新：模型路由表与选择
│   ├── budget_guard.py       # 新：预算与止损
│   └── diagnosis.py          # 新：诊断书（模板化，无 LLM）
├── api/
│   └── supervisor.py         # 新：账本/诊断/派工/路由表端点
└── config.py                 # + supervisor_mode / model_routes / pipeline_budget_usd
```

---

## 4. 模块设计

### 4.1 F9 契约层（`schema/agent.py`）

```python
class Degradation(BaseModel):
    kind: str                 # llm_timeout_fallback / material_placeholder / tts_default_voice / plugin_transform_failed / rag_unavailable
    detail: str               # 人话说明
    scope: str = ""           # scene:3 / clip:c_12 / track:video_main / global
    impact: str = ""          # 对成品的影响（"该段将出现占位图"）

class FailureReport(BaseModel):
    category: Literal["resource_missing", "input_insufficient", "model_incapable",
                      "timeout", "external_unavailable", "constraint_conflict",
                      "internal_defect", "unknown"]
    blocking_point: str = ""                   # 人话：第 3 段无可用素材
    location: dict[str, Any] = {}              # {"scene_id": "...", "clip_id": "...", "track": "..."}
    attempts: list[dict[str, Any]] = []        # [{"n": 1, "changed": "换素材源"}]
    retryable: Literal["retry_same", "needs_upstream", "needs_human", "permanent"] = "retry_same"
    needs: list[dict[str, str]] = []           # [{"from_stage":"structure","field":"material_refs","why":"..."}]
    evidence: list[dict[str, Any]] = []        # [{"kind":"asset_query","ref":"pexels:q=..."}]

class AgentOutputBase(BaseModel):
    decision: AgentDecision = AgentDecision.PASS
    error: Optional[str] = None                # 兼容保留
    failure: Optional[FailureReport] = None
    degradations: list[Degradation] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_failure_on_fail(self):
        if self.decision == AgentDecision.FAIL and self.failure is None:
            raise ValueError("decision=FAIL 必须提供 FailureReport")   # 仅 dev 抛错
        return self
```

- **6 个输出类改为继承 `AgentOutputBase`**，删除各自重复的 `decision`/`error`。
- **生产兜底**：`DEV_STRICT` 关闭时，`FAIL + failure=None` 自动补 `FailureReport(category="unknown")` 并计入告警指标（`ledger_missing_failure_total`），避免因历史代码路径直接崩管线。
- **降级点盘点**（B0 必做）：以
  - `grep -n "except Exception" clipwright/agents clipwright/services`
  - `grep -n "fallback\|_fallback" clipwright/agents`
  产出清单，逐处判定「此处降级是否需要登记」。已知 4 处见 §2 表。
- **验收**：契约测试对 6 个 Agent 各构造一次 FAIL，断言 `failure` 七字段齐备；`degradations` 非空时账本必有一条。

### 4.2 F1 账本（`services/episode_ledger.py`）

```python
class EpisodeLedger:
    def __init__(self, pipeline_id: str): ...
    def record(self, *, stage: str, attempt: int, input_digest: str,
               decision: str, failure: FailureReport | None,
               degradations: list[Degradation], warnings: list[str],
               usage: dict, model: str, duration_ms: int) -> None: ...
    def entries(self, stage: str = "") -> list[LedgerEntry]: ...
    def summary(self, max_chars: int = 4000) -> str:      # 给秘书的摘要，结构化裁剪
    def degradations(self) -> list[Degradation]: ...
    def attempts(self, stage: str) -> int: ...
```

- **唯一写入点**：`pipeline_v2._run_agent` 在决策落定后调用 `ledger.record(...)`；`_finalize_timeline` 再补记插件 transform 降级。
- **落库**：Mongo 集合 `pipeline_ledger`，索引 `{pipeline_id: 1, ts: 1}`；与 state 一并进程内保存，供同步读。
- **与 trace 分工**：trace 是**事件流**（前端 SSE、排障），账本是**结构化的尝试记录**（给决策方）。不合并。
- **裁剪策略**：摘要按「最近失败优先 → 降级优先 → 其余折叠为一行」组装，超 4000 字截断并标注 `_[已截断 n 条]_`。
- **验收**：任取历史失败管线，`GET /ledger` 能答出「哪一阶段第几次尝试、什么降级、证据在哪」。

### 4.3 F2 归因（`services/attribution.py`）

**规则优先**（确定性、可测，覆盖大多数情形）：

| 规则 | 条件 | 结论 |
|---|---|---|
| R1 上游资源缺失 | 账本中某上游阶段有 `resource_missing` 降级，且其 `scope` 与症状位置相交 | 责任归该上游阶段，标记 `unrepairable` 候选 |
| R2 上游失败未消化 | 上游阶段有 FAIL 但管线继续（非致命路径） | 责任归上游 |
| R3 输入不足 | 某阶段 `retryable=needs_upstream` | 责任归 `needs[0].from_stage` |
| R4 无证据 | 账本中无相关降级/失败 | 落到静态类别表（`quality_agent.py:348-357`） |

**LLM 兜底**（仅当 R1–R3 冲突或全部不命中）：
- `llm.structured_output(use_flash=True, output_schema=AttributionRuling)`，超时 15s，失败即回退 R4。
- 输入只有 `ledger.summary()` + 质检 issues，**不含**任何可执行权限。

**输出**：`Attribution(primary_stage, upstream_chain, evidence_refs, confidence: "high|mid|low", unrepairable: bool)`。
- `confidence=low` 时**不派工**，直接升级（S6/人工），避免「自信地重做错阶段」。

### 4.4 F3 策略（`services/supervisor.py`）

```python
STRATEGIES = ["S1_same", "S2_model", "S3_upstream", "S4_accept_degraded", "S5_new_path", "S6_abort"]
```

- 选择依据：`attribution` + `ledger.attempts(stage)` + 预算余量 + 是否 `unrepairable`。
- **硬禁令**：同一 `(stage, strategy)` 连续出现即拒绝，强制换一档或升级。
- 每次决策写一条 `SupervisorDecision`（进账本 + 前端 SSE）。
- **验收**：构造「重做 edit 三次无效」，第 2 轮必须改走 S3/S5（PRD A3）。

### 4.5 F4 派工（复用 `_dispatch`）

```python
async def execute_order(self, state, order: DispatchOrder, ledger, bus) -> PipelineStep:
    assert order.target_stage in CORE_AGENT_NAMES          # 目标阶段白名单
    self._assert_order_safe(order)                         # 不得夹带阶段顺序变更
    input_data = self._build_input(order.target_stage, result_data, persona_config, plugin,
                                   extra_params={**ctx.extra_params,
                                                "_model_route": order.model,
                                                "_quality_issues": order.issues,
                                                "_dispatch_id": order.id})
    return await self._run_agent(state, order.target_stage, input_data, ctx, bus)
```

- **为什么不复用 `run_sub_agent`**：`services/_subagent_body.txt` 走 `AgentRegistry.get()`，而 `agents/registry.py:52` 明确禁止核心 6 名注册——该通道**派不了核心阶段**。想改会造成插件体系语义污染（`CORE_AGENT_NAMES` 冲突检查失效）。
- **安全校验**：派工单只能指定「重做哪个核心阶段」，不得指定执行顺序、不得跳过阶段、不得改 `_DEPS`。

### 4.6 F8 模型路由（`services/model_router.py`）

```python
class ModelRoute(BaseModel):
    provider: str; model: str; tier: Literal["cheap", "std", "strong"]
    price_in_per_1k: float = 0; price_out_per_1k: float = 0
```

- **配置**（`config.py` 新增 `model_routes: dict[str, list[ModelRoute]]`，按阶段配 2–3 档）：
  ```jsonc
  {"material": [{"provider":"anthropic","model":"claude-haiku-x","tier":"cheap"},
                {"provider":"anthropic","model":"claude-sonnet-4-6","tier":"std"}],
   "edit":     [{"model":"claude-sonnet-4-6","tier":"std"},
                {"model":"claude-opus-x","tier":"strong"}]}
  ```
- **选择算法**：`tier 允许集（由套餐/预算决定）→ 该 (stage, model) 失败历史 → 时延窗口 → 取最便宜可用`。
- **透传路径**：`DispatchOrder.model` → `ctx.extra_params["_model_route"]` → 各 Agent 构造 `LLMService` 时读取 → `generate(model=..., provider=...)`。
  - `services/llm.py` 需新增：`LLMService(provider=..., model_override=...)`，在 `generate`/`structured_output`/`with_tools` 中优先使用 override，其次 `use_flash`，最后默认。
- **熔断键改造**：`_circuit_breakers` 的键由 `agent_name` → `f"{agent}::{provider}/{model}"`；`_check_circuit_breaker` / `_record_agent_failure` 全部接收 `(agent, model)`。**这是 F8 的前置条件**，否则 A 模型失败会连坐 B 模型。
- **记账**：`llm_tracker.record_llm_call` 已有 `model` 字段，直接写入实际使用的 model；子代理路径（`_attributed_name`）已支持复合名，无需新建计量。
- **留痕**：每次选择写 `{stage, model, reason, prev_model, est_cost}` 进账本（PRD A9）。

### 4.7 F6 预算（`services/budget_guard.py`）

- 数据源：`llm_tracker` 累计（已有 input/output token 与 model）。
- **预算为可选配置**（PRD F6 / §12 已定⑥）：`pipeline_budget_usd` 留空 = 无上限——不降级、不阻断、不返回「预算不足」，只记账。
- 配置后阈值：`≥70%` → 允许集收窄为 `cheap/std`，并触发一次 S4/S5 评估；`≥100%` → 拒绝派工、落诊断书、状态置 `FAILED(budget_exhausted)`。
- 配置来源：设置项（运行时可改），不再依赖 env；`budget_enabled` 由「是否填了金额」推导。

### 4.8 F5 诊断书（`services/diagnosis.py`）

- **模板化，无 LLM**（P1 就能交付）：输入 `ledger + attribution + decisions + budget` → 输出固定结构。
- 结构：`conclusion / problems[] / evidence[] / attempted[] / suggestions[] / cost`。
- `suggestions[]` 每项带 `action`（`retry_with_model` / `redo_stage` / `accept_degraded` / `manual`），前端据此渲染按钮。
- `/diagnostics/{pid}` 改为消费本服务，**保留旧字段**（`stage`/`reason`/`suggestion`）以免前端断裂。

### 4.9 API（`api/supervisor.py`）

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/pipeline/{pid}/ledger` | 账本（支持 `?stage=`） |
| GET | `/api/pipeline/{pid}/attribution` | 最近一次归因结论 |
| GET | `/api/pipeline/{pid}/diagnosis` | 诊断书（F5） |
| GET | `/api/pipeline/{pid}/decisions` | 秘书决策序列（调度面板数据源） |
| POST | `/api/pipeline/{pid}/dispatch` | 人工改派工单（F7） |
| POST | `/api/pipeline/{pid}/accept-suggestion` | 一键执行建议 |
| GET/PUT | `/api/supervisor/routes` | 路由表读写（管理员） |
| GET/PUT | `/api/supervisor/mode` | `off/shadow/on` 运行时切换 |

权限：沿用 `_enforce_pipeline_owner`；`/supervisor/routes` 与 `/mode` 限管理员。

**v1.1 追加端点**（子代理列表/取消、执行日志与授权、`auto-mode`、worker 审核）：见 §11.2–§11.4，本表不再重复。

### 4.10 SSE 事件（沿用现有 SSE-only 链路）

新增事件类型（`add_event` 的 `event_type` 字段）：
`ledger_entry`、`attribution`、`dispatch`、`diag_report`、`budget`。
v1.1 追加：`subagent_start` / `subagent_progress` / `subagent_end` / `exec`（见 §11.2、§11.4）。
前端按 `type` 分流渲染，无需新连接。

---

## 5. 分批计划

| 批次 | 内容 | 主要改动文件 | 退出标准 | 依赖 |
|---|---|---|---|---|
| **B0** | F9 契约层：`AgentOutputBase` + 6 类继承 + 4 处已知降级点 + 降级点盘点补漏 + 契约测试 | `schema/agent.py`、6 个 `agents/*.py`、`agents/base.py` | **A11**（6 个 FAIL 分支字段齐备）＋ **A12**（降级路径全覆盖、清单归零） | 无 |
| **B1** | F1 账本：单点埋设 + Mongo + `GET /ledger` + SSE `ledger_entry` | `services/episode_ledger.py`、`services/pipeline_v2.py`、`api/supervisor.py`、`main.py` | 任取失败管线可答「哪一步、什么降级、证据」 | B0 |
| **B2** | **影子秘书**：F2 归因 + F5 诊断书 + `shadow` 模式（只记录不派工） | `services/attribution.py`、`services/diagnosis.py`、`api/pipeline.py`(`/diagnostics` 改造) | 影子模式跑满 30 条失败管线，产出 **A1/A2/A4** 的度量数据；执行行为与 `off` 完全一致 | B1 |
| **B3** | F3 策略 + F4 派工（`on` 模式）：`execute_order`、禁令、决策留痕 | `services/supervisor.py`、`services/pipeline_v2.py` | **A3**（同策略不连续）＋ **A13**（下游申报触发上游重做） | B2（且影子准确率达标） |
| **B4** | F8 模型路由：路由表 + `model_override` + 熔断键 `(stage, model)` + 记账 | `services/model_router.py`、`services/llm.py`、`services/pipeline_v2.py`、`config.py` | **A9**（留痕五字段）＋ **A18**（跨 provider 生效且记账不阻断）＋ 熔断不连坐 | B3 |
| **B5** | F6 预算（**可选，未配置=无上限**）+ F7 升级接管 | `services/budget_guard.py`、`api/supervisor.py` | **A6** 两段（配置时止损 / 未配置不得被拒）；接管后管线可继续且留痕 | B4 |
| **B6** | 回归与等价性：`off` 模式、全量 pytest、`/runs` 与 `/diagnostics` 向后兼容 | `tests/` | **A10**（逐字节等价）＋ **A5**（成功路径成本增量=0，A/B 各 20 条）＋ pytest 全绿 | B5 |
| **B7–B9**（v1.1 追加） | 子代理观测 → 动态子代理 → 执行网关（F12/F10/F11） | 见 §11 | **A14**（B7）／**A17·A19·A22·A23**（B8）／**A15·A16·A20·A21·A24–A26**（B9） | B3（秘书能派工之后） |

> **顺序不可压缩的两处**：B0 必须在 B1 前（否则账本没内容）；B2 的 shadow 必须在 B3 前（否则拿生产流量试归因规则）。

---

## 6. 测试计划

| 类型 | 内容 | 位置 |
|---|---|---|
| 契约测试 | 6 个 Agent FAIL 必填 failure；`QualityInput` 不含账本字段（红线 A7） | `tests/test_agent_output_contract.py` |
| 单测 | 归因 4 条规则逐条命中/不命中；策略禁令；模型路由选择（预算/历史/时延） | `tests/test_attribution.py`、`test_supervisor_policy.py`、`test_model_router.py` |
| 集成 | shadow 回放 30 条历史失败管线，产出准确率报表 | `tests/test_supervisor_shadow.py` |
| 等价性 | `off` 模式与基线同输入 → timeline 逐字节一致 | `tests/test_supervisor_off_equivalence.py` |
| 成本 | A/B 各 20 条同输入管线，比 token 账单 | 手工脚本 `scripts/bench_supervisor_cost.py` |

---

## 7. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 契约层改动面大（6 个 Agent + 20 处降级点），易引入回归 | B0 独立成批、单独 PR、全量回归必须先绿再进 B1 |
| LLM 归因不稳定 | 规则优先；`confidence=low` 不派工；shadow 期量化准确率后再开 `on` |
| 熔断键改造影响既有熔断语义 | 键带 model 但保留 agent 维度查询接口；`off` 模式下仍按 agent 聚合 |
| 预算单位与计费口径未定 | `budget_guard` 加 `enabled` 开关，未配置时不生效 |
| 时间线回归 | A10 逐字节等价性测试卡门禁 |

**总开关**：`CLIPWRIGHT_SUPERVISOR_MODE=off` 时，账本仍可写（成本近零），归因/派工不运行。

---

## 8. 提交与 PR 约定

- 分支：`主agent化`；每批（B0–B6）单独提交、单独 PR 到 `feat/pluginization`（跟随现有批次习惯），避免一个巨型 PR。
- 提交前缀：`feat(supervisor):` / `fix(supervisor):` / `test(supervisor):` / `docs(supervisor):`。
- 每批结束同步更新：`docs/services_overview.md`（新服务）、`docs/api_reference.md`（新端点）、`docs/workflow.md`（决策链）、本文件勾选进度。

---

## 9. 待决问题（与 PRD §12 保持一致）

| 编号 | 问题 | 阻塞 |
|---|---|---|
| Q3 | 是否允许用户手动改派工单（人工接管深度） | B5（F7 范围） |
| Q4 | 单管线最大换模型次数、单次决策成本上限 | B3/B4 硬规则 |
| Q6 | 诊断书是否暴露内部阶段名 | B2（诊断书文案） |

> 已定项（不阻塞开工）：免费套餐移出本期；跨 provider 开放且记账不阻断；命令风险由独立 LLM 判定（规则只升不降 + fail-safe）；自动模式用户开关；并发上限入设置、子代理开销不设独立上限；预算/单价在设置中配置、**不填即无上限**；带执行权限的 worker 需「独立 LLM 判风险 → 人工审核」。

---

## 10. 执行进度

### B0 — 已完成（提交 `400b30b`）

| 交付 | 位置 | 验证 |
|---|---|---|
| `Degradation` / `FailureReport` / `AgentOutputBase` | `schema/agent.py` | 7 个输出类继承；契约测试 8 条通过 |
| 两种 FAIL 的语义分离 | `AgentOutputBase._require_failure_on_execution_fail` | 质检「判定失败」不强制 failure；「执行失败」宽松补 `unknown`+计数、严格抛错 |
| 降级登记（单点埋设，ContextVar） | `services/degradation.py` | `degradation_scope` + `note_degradation`，Agent 零改动 |
| 失败构造带 failure | `agents/base.py::build_error_output` | 覆盖 5 个 Agent 的异常出口 |
| LLM 兜底登记 | `agents/base.py::llm_or_fallback` | 超时/失败两条分支分别登记 |
| 分镜回退登记 | `structure_agent.py`（超时回退 + 无有效场景回退） | 2 处 |
| 编排层作用域接入 | `pipeline_v2._run_agent`（`degradation_scope` 包 `_dispatch`，去重后并入输出 + trace 事件） | 降级不再止步于 Agent |
| 后处理降级 | `pipeline_v2._finalize_timeline`（transform 失败 / 类型违规） | 后处理降级进 `shared_data["degradations"]` |
| 全量回归 | `python -m pytest tests -q` | **3198 passed, 3 skipped, 1 xfailed** |

### 降级点盘点（B0 初筛，B1 逐处判定）

| 统计口径 | 数量 | 说明 |
|---|---|---|
| `agents/*.py` 中的 `except` 块 | 95 | 初筛清单，非全部需登记 |
| `pipeline_v2.py` 中的 `except` 块 | 32 | 主要为落库/上报的防御性兜底，多数不需登记 |
| `llm_or_fallback` 调用点 | 6 | 已在 `llm_or_fallback` 内部统一接线，**调用点无需改** |
| `_fallback_*` 辅助函数 | 1（`_fallback_scenes`） | 已接线 |
| `agents/*.py` 中 `logger.warning` | 39 | 判定「是否意味着产物打折」的候选面 |
| 当前 `note_degradation` / `_append_finalize_degradation` 调用点 | 7 | B0 交付 |

**B1 处置原则**：只有「静默改变产物内容」的路径需要登记（占位素材、默认音色、保留原时间线、丢弃片段、降级模型）；落库失败、遥测失败、日志类 `except` **不登记**，避免账本被噪声淹没。

### B1 — 已完成

| 交付 | 位置 | 验证 |
|---|---|---|
| `LedgerEntry` 数据契约 | `schema/supervisor.py` | 与 PRD §8 字段对齐；`failure` / `degradations` 直接复用 F9/B0 的模型 |
| 账本（内存 + Mongo 旁路） | `services/episode_ledger.py` | 每阶段 attempt 递增、失败/降级入账、`summary()` 可截断、注册表有界（200） |
| 单点埋设 | `pipeline_v2._run_agent` | 只有一处 `arecord`；Agent 内部零改动；Mongo 写走线程池不阻塞事件循环 |
| 后处理降级入账 | `pipeline_v2`（首跑 + retry 两处） | `stage="postprocess"` 独立条目 |
| 账本端点 | `api/supervisor.py` + `main.py` | `GET /api/pipeline/{pid}/ledger`（支持 `?stage=`，内存未命中回查 Mongo，未知管线返回 `found=false` 而非 404） |
| SSE 事件 | `add_event(..., "ledger_entry")` | 前端可按 type 分流 |
| 测试 | `tests/test_episode_ledger.py` | **9 passed** |

### B2 — 已完成

| 交付 | 位置 | 验证 |
|---|---|---|
| 归因（规则 R1–R4） | `services/attribution.py` | R1 资源缺失 → 不可修复；R3 上游申报；R2 需症状参照（不硬判）；R4 类别表兜底（低置信） |
| LLM 兜底 | `attribution.attribute_with_llm` | 仅规则全不命中且有证据时调用；`use_flash`、超时 15s、失败回退规则结论并标 `source=fallback` |
| 诊断书（模板化，无 LLM） | `services/diagnosis.py` | conclusion/problems/evidence/attempted/suggestions/cost；不可修复时给「接受降级」，重试过的给「换模型」 |
| 影子模式 | `config.supervisor_mode = off/shadow/on`（默认 off） | `_shadow_analyze` 在 `run()` 与 `run_from_agent()` 末尾调用；off 时零开销；**只写 shared_data + trace，不改执行** |
| 配置元数据 | `config_schema.py`（新增两行 `_ROWS`） | Settings 覆盖面测试要求每个字段都有 UI 元数据，已补齐（挂 `pipeline` 组） |
| 端点 | `api/supervisor.py` | `GET /{pid}/attribution`（`?llm=true` 才启用兜底）、`GET /{pid}/diagnosis`；内存未命中回查 Mongo |
| SSE | `add_event(..., "attribution" / "diag_report")` | 前端按 type 分流 |
| 测试 | `tests/test_attribution_diagnosis.py` | **23 passed**（含端点与 LLM 兜底的两条反例：规则命中不调 LLM、兜底失败回退） |

### B3 — 已完成

| 交付 | 位置 | 验证 |
|---|---|---|
| 策略选择 S1–S6 | `services/supervisor.py::choose_strategy` | 纯函数：不可修复→S4；无归因→S6；首次→S1（有上游链→S3）；第二次→S2（无换模型预算→S5）；三次以上→S5 |
| 同策略禁用（G2/A3） | 同上 `_escalate` | 同阶段同策略已用过即沿阶梯升级，阶梯用尽→S6 转人工；S3 重复时回落到症状阶段 |
| 决策留痕 | `services/supervisor.py::record_decision` | 写 `shared_data["decisions"]`（仅留 50 条）+ trace 事件 `dispatch` |
| 派工执行 | `pipeline_v2.execute_order` | 复用 `_run_agent`；核心阶段白名单校验（非核心直接 ValueError）；`_order_id` / `_quality_issues` 注入上下文 |
| 自愈接线 | `pipeline_v2._decide_for_heal` + `_self_heal_quality` | `off` 零开销；`shadow` 只记录；`on` 时 S4 接受降级、S6 止损、目标阶段改写 `redo_agent` |
| 测试 | `tests/test_supervisor_strategy.py` | **18 passed**（含禁用升级、阶梯耗尽、派工门禁与 `_run_agent` 复用） |

### B4 — 已完成

| 交付 | 位置 | 验证 |
|---|---|---|
| 路由表与选择 | `services/model_router.py` | 按阶段偏好档位（选材/音频偏便宜、结构/剪辑偏标准强）+ 价格排序；`budget_ratio≥0.7` 时禁止升级；失败模型自动跳过 |
| 白名单（共用） | `model_router.allowed_providers / is_route_allowed` | 显式 `model_whitelist` 精确匹配；否则按「主/闪 provider + 已配置 llm 源」放行。**B9 的 risk_judge 复用同一份** |
| risk_judge 路由 | `model_router.risk_judge_route` | 显式 `risk_judge_model` 必须过白名单（否则忽略并告警）；未配置时取最便宜 cheap 档 |
| 跨 provider 凭据 | `model_router.resolve_provider_credentials` | 角色绑定 → 同 provider 源 → settings；取不到就回退默认，绝不串用别的 key |
| LLM 覆盖（单点埋设） | `services/llm.py::_routed_client_and_model` | 无路由零变化；同 provider 只换 model；跨 provider 用 `_construct_client`（缓存）；`generate` / `ask` / `structured_output` 全部生效 |
| 熔断键 (stage, model) | `pipeline_v2._breaker_key / _failed_models_for` | A 模型失败不连坐 B 模型；已熔断的模型会从路由候选中剔除 |
| 账本记账 | `_run_agent` | `LedgerEntry.model` 在无 usage 时回落到路由 key（A9/A18 留痕） |
| 配置 | `config.py` + `config_schema.py` | `model_routes` / `model_whitelist` / `risk_judge_model`（留空=不路由，行为与改造前一致） |
| 测试 | `tests/test_model_router.py` | **21 passed**（含跨 provider 构建、无凭据回退、白名单拒绝） |

### B5 — 已完成

| 交付 | 位置 | 验证 |
|---|---|---|
| 预算（可选闸） | `services/budget_guard.py` | `pipeline_budget_usd` 留空 → `level=unlimited`、`ratio=None`、`is_blocked=False`（只记账不阻断）；配置后 70% `warn` / 100% `blocked` |
| 成本口径 | 同上 `_cost` | token 折算货币；单价优先 `model_prices`，缺失回退 `model_routes` 价格；未知模型按 0 计（宁可少算不阻断） |
| 子代理开销 | `snapshot.subagent_tokens / subagentSpentUsd` | 按 `host:sub` 复合名单列（PRD §7.3 要求可见） |
| 预算接入路由 | `pipeline_v2._choose_route_for` | 传 `budget_ratio`，≥70% 时路由只给 cheap/std |
| 预算接入决策 | `pipeline_v2._decide_for_heal` | `is_blocked` → `choose_strategy(budget_exhausted=True)` → S6 止损 |
| 预算接入派工 | `pipeline_v2.execute_order` | 耗尽时抛 ValueError（API 映射 409） |
| 预算端点 | `GET /api/pipeline/{id}/budget` | 形状对齐前端 `BudgetSnapshot` |
| 人工改派 | `POST /api/pipeline/{id}/dispatch` | 核心阶段白名单；预算耗尽 409；记录 `manual_orders` + trace，返回 `requires_retry` |
| 接受降级 | `POST /api/pipeline/{id}/accept-suggestion` | 置 `accepted_degraded` + 记一条 S4 决策（mode=manual） |
| 测试 | `tests/test_budget_guard.py` | **14 passed** |

### B6 — 已完成（机制层等价性）

| 交付 | 位置 | 验证 |
|---|---|---|
| `off` 模式不做路由 | `tests/test_supervisor_off_equivalence.py` | 即使配了 `model_routes`，`_choose_route_for` 仍返回 None |
| `off` 模式不做决策 / 不写归因诊断 | 同上 | `_decide_for_heal` / `_shadow_analyze` 均不改 `shared_data` |
| 零 LLM 增量（A5 机制保证） | 同上 | 把 `LLMService.structured_output` 打桩成抛错，归因/诊断仍正常（纯函数） |
| 账本旁路不受开关影响 | 同上 | `off` 下账本照记（B1 是观测层） |
| 默认值 | `config.supervisor_mode` | 默认 `off`（改造后默认行为与改造前一致） |
| 待人工验证 | A10 逐字节 timeline 比对 / A5 A/B 20 条账单 | 需真实管线跑批（脚本见 §6 测试计划） |

### B7 — 已完成

| 交付 | 位置 | 验证 |
|---|---|---|
| 子代理节点契约 | `schema/supervisor.py::SubagentNode` | snake_case，与前端类型一一对应（含 children 树） |
| 追踪器 | `supervisor/tracker.py` | 状态机 pending→running→done/failed/cancelled；有界注册表（200）；取消回调；`tree()` 父子嵌套；`snapshot()` 按状态计数 |
| 事件 | tracker `_event` | `subagent_start / subagent_progress / subagent_end`（复用 trace/SSE） |
| 端点 | `api/supervisor.py` | `GET /{pid}/subagents`（空返回不是 404）、`POST /{pid}/subagents/{sid}/cancel`（不存在 404 / 不可取消或已结束 409） |
| 测试 | `tests/test_subagent_tracker.py` | **10 passed** |

> B7 单独看是"空跑"——还没有东西会创建子代理。它是 B8 的地基：`spawn` 直接调 `tracker.register()`，前端 F6 可并行开工。

### B8 — 已完成

| 交付 | 位置 | 验证 |
|---|---|---|
| 临时 worker 注册表 | `supervisor/worker_registry.py` | 按管线归属、有界 200；`SUPERVISOR_TOOLS` 全集与 `EXEC_TOOLS` 标记；**不写 AgentRegistry**（A17） |
| 派生 | `supervisor/spawn.py::spawn_subagent` | 工具子集校验（越权即拒）、星型拓扑（`host_agent` 存在即拒，A19）、预算闸 |
| 两级审核门 | `spawn.py` + `review.py` | 带 `run_shell/run_python` → 独立 LLM 判风险 → `pending_review`，**未过门不实例化**（A22）；`high/critical` 需 `confirm_high_risk`（A22） |
| 独立判定 | `supervisor/review.py::assess` | **输入白名单**（只取 role/prompt/tools/timeout/max_turns/command/cwd/workspace），非白名单键丢弃（A23）；失败 fail-safe `high`（A26） |
| 执行 | `spawn._run_worker` | 走 `LLMService.ask`（可带 `use_route`）；成功/失败都落 tracker + registry；失败不影响管线 |
| 端点 | `api/supervisor.py` | `POST /{pid}/subagents`、`GET /api/supervisor/workers/pending-review`、`POST /api/supervisor/workers/{wid}/review`、`GET /api/supervisor/tools` |
| 并发上限 | `spawn.py` + `config.max_concurrent_subagents` | 运行中（`pending/running`）达上限即抛 `SpawnError`，**0 = 不限**；额度随 `tracker.finish/cancel` 自动释放（A14） |
| 模型自由 | `spawn_subagent(provider=, model=)` | 子代理模型**由主代理自由指定**，允许跨 provider（`use_route` 覆盖，不走路由表的档位选择）；不传则继承管线路由。provider 打错时该 worker 计 `failed`，不影响管线 |
| 测试 | `tests/test_subagent_spawn.py` | **17 passed**（含未过门不执行、high 需二次确认、输入隔离、fail-safe、并发上限两例） |

### B9 — 已完成

| 交付 | 位置 | 验证 |
|---|---|---|
| 确定性规则 | `supervisor/shell_policy.py` | 硬底线（读凭据 / 不可逆系统破坏，**不经 LLM、不可授权**）；危险种子只用于**上调**等级（A25） |
| 执行网关 | `supervisor/gateway.py` | 硬底线 → 棘轮 → 独立判定 → `max()` → 分流（low 直跑 / med 自动模式可免 / high 与 critical 需授权 / critical 二次确认） |
| 沙盒 | 同上 | cwd 可指定；环境变量剥离 `*_API_KEY/TOKEN/SECRET/PASSWORD/...`；墙钟超时并 kill；输出截断 4000 字符 |
| 审计 | `ExecRecord` + trace | 每条执行（含被拒/超时）都有记录：命令、cwd、风险等级、理由、是否自动模式、退出码、耗时、输出摘要 |
| 审核队列 | `gateway.pending_approvals / approve / reject` | high/critical 未带 `confirm_high_risk` 会被放回队列（A22 同语义） |
| Python 执行 | `gateway.run_python` | 脚本落 `{cwd}/.supervisor/scripts/{uuid}.py`（无 `cwd` 时落系统临时目录，**不写进程 cwd**），用 `"{sys.executable}" -I` 子进程执行（隔离用户 site-packages）；`risk_text` 走同一套「硬底线→棘轮→独立判定→max」链 |
| 端点 | `api/supervisor.py` | `GET /exec`、`GET /exec/pending-approvals`、`POST /exec/{aid}/approve|reject`、`GET/PUT /exec/auto-mode` |
| 测试 | `tests/test_exec_gateway.py` | **26 passed**（硬底线 5 类、棘轮上调、自动模式只减免 med、密钥剥离、审计字段、`run_python` 三例含落盘位置、禁网与放开、端点） |

### B1 起的前置条件

- F9 已提供 `failure` / `degradations`，账本（B1）只需把 `_run_agent` 单点处的结果落成 `LedgerEntry`；
- `step.result` 已自动携带 `failure` / `degradations`（`model_dump`），**无需新增 RPC 或额外通道**。

---

## 11. v1.4 追加：执行能力与动态子代理（B7–B9）

> 对应 PRD **v1.4** 的 F10（动态子代理）/ F11（执行能力）/ F12（子代理观测）。
> **顺序刻意排成 B7 观测 → B8 派生 → B9 执行网关**：先能看见、再能派活、最后才给 shell。

### 11.1 新增模块

```
clipwright/
├── supervisor/                    # 新包，与 services/ 里的秘书逻辑分开
│   ├── worker_registry.py         # 临时 worker 规格与句柄（内存，按 pipeline 归属）
│   ├── review/judge.py            # 独立 LLM 风险判定（命令 + worker 共用；输入只含 spec/命令，A23/A24）
│   ├── spawn.py                   # E1–E3：派生 / 查询 / 列出 / 取消子代理
│   ├── gateway.py                 # E4–E5：执行网关（独立判定 → 授权 → 沙盒 → 审计）
│   └── shell_policy.py            # 确定性规则：危险种子棘轮（只升不降）+ 两类硬底线
├── api/supervisor.py              # 扩：子代理列表·状态·取消、执行日志、授权、auto-mode、worker 审核
└── config.py                      # + max_concurrent_subagents（设置项，默认 6）
                                   # + exec_timeout_sec / exec_allow_network / auto_mode（默认 false）
                                   # + pipeline_budget_usd / model_prices（**可空：空=无上限**）
```

### 11.2 B7 子代理观测（F12）

- 数据源：现有 `trace.add_event(pid, agent, type, summary, detail, parent_agent, depth)` 已支持父子与深度 —— 新增事件类型 `subagent_start / subagent_progress / subagent_end`，不新建通道。
- 端点：`GET /api/pipeline/{pid}/subagents`（树）、`POST /api/pipeline/{pid}/subagents/{sid}/cancel`。
- 退出标准：并发 3 个子代理状态实时、取消后并发额度立即释放（PRD A14）。

### 11.3 B8 动态子代理（F10）

- `WorkerRegistry.register(spec) -> worker_id`，**按 pipeline 归属**；管线结束 `gc()`，不写 `AgentRegistry`。
- 复用路径：核心 6 阶段走已有的 `execute_order`；插件 agent 走 `AgentRegistry.get`。
- 临时 worker：`WorkerSpec` → 动态构造的 `BaseAgent` 子类实例（system_prompt / 工具子集 / model_spec 由闭包注入）。
- **工具集校验**：`worker.tools ⊆ SUPERVISOR_TOOLS`，构造期强校验，运行期再校验一次。
- **深度**：worker 内调用 `spawn_subagent` 直接抛 `SubagentDepthError`（对应 PRD A19）。
- **执行权限审核门（A22/A23）**：worker 工具集含 shell/python 时，`spawn` 前必须过两级门——
  1. `review/judge.py`：用**独立会话 + 独立模型**（不复用主代理上下文，请求体只含 `WorkerSpec`）输出 `{risk, reasons[], 建议}`；判定失败按 `high` 处理（fail-safe）。
  2. 人工审核：`WorkerRegistry` 记录 `pending_review`，`spawn` 返回「待审核」而非实例化；`POST /workers/{wid}/review` 批准后放行。`risk=high` 要求 `confirm_high_risk=true`。
  3. 未过门的 worker 不得实例化（单测直接断言）；两级结论 + 审核人 + 时间入账本。
- 退出标准：能派「素材可用性核查员」查第 3 段是否占位图；管线结束后 `WorkerRegistry` 为空（A17）；带 shell 的 worker 未审核时 spawn 失败。

### 11.4 B9 执行网关（F11）

```
run(kind, cmd, *, timeout, cwd) →
  1. 硬底线检查（确定性规则，不经 LLM）→ 读凭据 / 不可逆系统破坏 → 直接拒并记账
  2. shell_policy.ratchet(cmd) → 命中危险种子时记录「最低风险底线」
       （删/移/覆盖既有文件、装包、起服务、改权限、网络下载后执行）
  3. risk_judge.assess(cmd, cwd, workspace_brief)
       # 独立会话 + 独立模型（设置项 risk_judge_model，默认 cheap 档，与主代理解耦）
       # **判定模型必须命中 provider 白名单**（共用主代理的白名单，不另开通道）
       # 输入只含命令/cwd/workspace 说明（A24 断言字段白名单）
       # 超时默认 5s（可配）；串行命令逐条判定，不批处理
       # 输出 {risk_level: low|med|high|critical, reasons[], danger_explanation}
       # 失败（超时/非法 JSON/schema 不符）→ 一律 high（A26 fail-safe）
  4. final_risk = max(判定等级, 规则底线)        # A25：规则只升不降
  5. 分流：low → 执行；med/high → 需用户授权（auto_mode 仅减免 med）；
           critical → 需授权 + 二次确认
  6. asyncio.create_subprocess_shell(cwd=workspace, env=白名单, 墙钟上限, 输出截断)
  7. 写 ExecRecord（风险等级 / 理由 / 危险说明 / 是否自动模式）入账本
     + add_event(pid, "supervisor", "exec", ...)
  8. 超时/被杀 → 记 timeout，返回已捕获的部分输出
```

- **关键分工**：「危不危险」由**独立 LLM 请求**判定（按用户决定），但三条约束保证「被说服」不会直接变成「被授权」：判定输入隔离、规则只升不降、判定失败一律升 `high`。
- `run_python`：脚本落 `workspace/.supervisor/scripts/{uuid}.py`，用 `python -I` 子进程执行（隔离用户 site-packages）；默认禁网，`exec_allow_network=true` 才放开。
- **硬底线（仅两类，任何模式不可授权）**：① 读取凭据（`.env` / `~/.ssh` / keychain）；② 不可逆系统破坏（格式化系统盘、`shutdown`/`reboot`、删除系统级注册表项）。
- 并发子代理上限读设置项 `max_concurrent_subagents`（默认 6）；子代理开销**不设独立上限**，全额计入管线预算。
- **不得**把 shell 注册进 `ToolRegistry`——`StructureAgent` 的 `with_tools` 走 `list_agent_callable()`，会把 shell 一起暴露给阶段 Agent。
- 退出标准：PRD A15 / A16 / A20 / A21 / A24 / A25 / A26 通过。

### 11.5 测试

| 类型 | 内容 |
|---|---|
| 单测 | `shell_policy.ratchet` 危险种子逐条命中：删/移/覆盖既有文件、装包、起服务、改权限、下载后执行；`rm -rf ./x` 与 `rm -rf /` 的差异 |
| 单测 | 硬底线两类各 5 条样本：任何 `auto_mode` 下都被拒 |
| 单测 | `risk_judge`（命令）：输入白名单只含命令/cwd/workspace 说明（A24）；非法 JSON 与超时（5s）→ `high`（A26）；LLM 判 `low` 但命中种子 → 升 `med`（A25）；串行逐条判定不批处理 |
| 单测 | gateway 的截断、环境变量剥离（断言子进程读不到 `*_API_KEY`） |
| 集成 | 派生 → 执行 → 回收；并发上限按设置生效；取消；深度拒绝 |
| 集成 | 独立判定必须产出理由与危险说明；字段缺失时按 `high` 处理（fail-safe） |
| 集成 | `auto_mode` 开/关：开时 `med` 不弹窗、`high` 仍弹；开关状态入账 |
| 集成 | 带 shell 的 worker：未审核 spawn 必须失败；`risk=high` 不带 `confirm_high_risk` 必须拒绝（A22） |
| 单测 | 独立判定的请求体只含 `WorkerSpec`，断言不含主代理对话/理由字段（A23） |
| 单测 | 预算未配置时：任何派工都不得返回「预算不足」；配置上限时才触发止损（A6 两段） |
| 兼容 | `off` 模式下 gateway 不注册任何工具：断言 `ToolRegistry.list_agent_callable()` 不含 exec 类 |

### 11.6 风险

| 风险 | 缓解 |
|---|---|
| shell 被阶段 Agent 看见 | 只注册进 `SUPERVISOR_TOOLS`，不进 `ToolRegistry`；测试断言兜底 |
| LLM 把危险命令说成「只读排查」 | 独立判定输入隔离（无主代理辩解）+ 规则只升不降 + 判定失败 fail-safe 到 `high` |
| 自动模式被长期开着遗忘 | 开关状态常驻显示 + 每次开启写账本 + 会话结束自动复位为关 |
| 临时 worker 无限派生 / 吃掉预算 | 并发上限可配（默认 6）；子代理开销不设独立上限但全额计入管线预算，成本条单列显示 |
| Python 逃逸 | 文档层面明确「审计边界 ≠ 安全边界」；不可信代码一律要求容器；默认禁网 |


