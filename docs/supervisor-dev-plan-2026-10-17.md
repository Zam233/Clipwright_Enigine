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
- 阈值：`≥70%` → 允许集收窄为 `cheap/std`，并触发一次 S4/S5 评估；`≥100%` → 拒绝派工、落诊断书、状态置 `FAILED(budget_exhausted)`。
- 配置：`pipeline_budget_usd`（PRD Q1 待定，先读 env，缺省不启用）。

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

### 4.10 SSE 事件（沿用现有 SSE-only 链路）

新增事件类型（`add_event` 的 `event_type` 字段）：
`ledger_entry`、`attribution`、`dispatch`、`diag_report`、`budget`。
前端按 `type` 分流渲染，无需新连接。

---

## 5. 分批计划

| 批次 | 内容 | 主要改动文件 | 退出标准 | 依赖 |
|---|---|---|---|---|
| **B0** | F9 契约层：`AgentOutputBase` + 6 类继承 + 4 处已知降级点 + 降级点盘点补漏 + 契约测试 | `schema/agent.py`、6 个 `agents/*.py`、`agents/base.py` | 6 个 FAIL 用例字段齐备；无静默 fallback（清单归零） | 无 |
| **B1** | F1 账本：单点埋设 + Mongo + `GET /ledger` + SSE `ledger_entry` | `services/episode_ledger.py`、`services/pipeline_v2.py`、`api/supervisor.py`、`main.py` | 任取失败管线可答「哪一步、什么降级、证据」 | B0 |
| **B2** | **影子秘书**：F2 归因 + F5 诊断书 + `shadow` 模式（只记录不派工） | `services/attribution.py`、`services/diagnosis.py`、`api/pipeline.py`(`/diagnostics` 改造) | 影子模式跑满 30 条失败管线，产出 PRD A1/A2/A4 的度量数据；执行行为与 `off` 完全一致 | B1 |
| **B3** | F3 策略 + F4 派工（`on` 模式）：`execute_order`、禁令、决策留痕 | `services/supervisor.py`、`services/pipeline_v2.py` | A3 用例通过：同策略不连续；失败管线优先走 S3/S5 | B2（且影子准确率达标） |
| **B4** | F8 模型路由：路由表 + `model_override` + 熔断键 `(stage, model)` + 记账 | `services/model_router.py`、`services/llm.py`、`services/pipeline_v2.py`、`config.py` | A9 留痕完整；A 模型失败不连坐 B 模型（构造用例） | B3 |
| **B5** | F6 预算止损 + F7 升级接管 | `services/budget_guard.py`、`api/supervisor.py` | A6 不越硬预算；接管后管线可继续且留痕 | B4 |
| **B6** | 回归与等价性：`off` 模式 A10、全量 pytest、`/runs` 与 `/diagnostics` 向后兼容 | `tests/` | `python -m pytest tests -q` 全绿；A10 逐字节比对通过 | B5 |

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

## 9. 待决问题（阻塞 B5/B4 的部分）

| 编号 | 问题 | 阻塞 |
|---|---|---|
| Q1 | 每管线硬预算金额与计费口径 | B5 |
| Q2 | 免费套餐是否开启秘书/路由 | B4（决定 tier 允许集） |
| Q4 | 单管线最大换模型次数、单次决策成本上限 | B3/B4 硬规则 |
| Q5 | 是否允许跨 provider（合规） | B4 三阶段 |
