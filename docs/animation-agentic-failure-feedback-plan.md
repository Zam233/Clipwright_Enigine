# 动画 Agentic 失败反馈与自修复改造计划

- 状态：Plan
- 目标：失败不是日志，而是 Agent 可读、可定位、可行动的一等数据
- 范围：AnimationAgent、Agentic loop、MGGenerator 出口、validator/lint/QA、前端活动流

## 1. 设计原则

1. **失败是一等数据**：成功和失败使用同一个 `Observation` schema，不允许只有 `error: string`。
2. **可定位**：每个失败必须带 `scope`、`location`、`expected`、`observed`、`fix_hint`。
3. **可行动**：每个失败必须映射到 `repair_mg` / `generate_mg` / `ask_user` / `abort`。
4. **不可静默 final**：存在 blocking failure 时，Agent 不能 final。
5. **不设拍脑袋数字**：决策不再手写 JSON + `max_tokens`；改用 provider 原生 tool calling。运行预算由显式 `RunPolicy` 配置，不在 prompt 里写“40 字/1200 token”这类魔法数。
6. **证据可追溯**：原始 prompt/response、tool args、failure 详情、artifact 全部落盘。
7. **失败不可变**：每次 observation append-only，修复产生新 observation，旧失败保留。
8. **终态明确**：`success / needs_user / capped` 三选一；capped 必须带 unfinished，不得伪装成功。

## 2. 统一契约

```python
@dataclass(frozen=True)
class Failure:
    code: str                  # MG_FONT_UNREGISTERED
    category: str              # schema|layout|typography|lint|render|qa|decision
    severity: str              # blocking|warning
    scope: str                 # tool|element|field|document|artifact
    location: dict             # {"element_index":0,"path":"elements[0].y","bbox":[...]}
    expected: Any
    observed: Any
    fix_hint: str
    retryable: bool
    evidence_ref: str | None

@dataclass(frozen=True)
class Observation:
    observation_id: str
    tool: str
    status: str                # ok|failed|partial|blocked
    artifact_ref: str | None
    summary: str
    failures: tuple[Failure, ...] = ()
    next_actions: tuple[str, ...] = ()
    raw_ref: str | None = None
```

`success` 不是模型自报，而是：

```python
status == "ok" and not any(f.severity == "blocking" for f in failures)
```

## 3. 失败分类与定位

### 决策层

- `DECISION_TOOL_CALL_MISSING`
- `DECISION_UNKNOWN_TOOL`
- `DECISION_INVALID_ARGS`
- `DECISION_PROVIDER_UNSUPPORTED`

不再解析自由文本 JSON；改用 provider `tools` + tool_calls。

### MG 结构

- `MG_JSON_PARSE`：raw_ref + 首个非法位置
- `MG_SCHEMA_INVALID`：JSON Pointer 路径
- `MG_KEYFRAME_PROP_UNSUPPORTED`：element_index、keyframe_index、prop
- `MG_SHAPE_MISSING`：element_index
- `MG_EASING_UNSUPPORTED`：element_index、value、allowed[]
- `MG_FONT_UNREGISTERED`：style.font_family、family、allowed[]
- `MG_HTML_LINT`：lint code、file、line、message

### 布局

- `MG_LAYOUT_MULTILINE_OVERLAP`：
  - element_index_a/b
  - y_a/y_b
  - height_a/height_b
  - min_gap
  - suggested_y[]
- `MG_LAYOUT_CARD_LABEL_OFFCENTER`
- `MG_LAYOUT_ARROW_HEAD_MISSING`
- `MG_LAYOUT_DUPLICATE_TEXT`

### QA

- `QA_TEXT_TOO_SMALL`
- `QA_TEXT_READ_TIME_SHORT`
- `QA_MOTION_WEAK`
- `QA_PHONE_SAFE_AREA`

每个都带 element_index、字段、当前值、阈值来源。

## 4. Tool 返回改造

所有动画 Tool 返回 `Observation`，不是裸 dict/异常：

- `generate_mg`
- `validate_mg`
- `repair_mg`
- `qa_mg`
- `render_preview`
- `inspect_lint`

`generate_mg` 内部：

```text
LLM raw -> parse -> schema validate -> lint -> semantic -> observation
```

每一步失败都追加 Failure，不提前吞掉。

`repair_mg` 输入：

```json
{"mg_def": {}, "failure_ids": ["f1","f2"], "failures": [...]}
```

输出新 Observation，并记录 `repaired_failure_ids`。

## 5. Loop 改造

```text
call tool
→ normalize Observation
→ FailureLedger.append
→ if blocking:
      planner 选择 repair/regenerate/ask
   else:
      final 允许
```

硬规则：

- `final` 前检查 ledger 无 blocking；
- `repair_mg` 必须引用具体 failure_id；
- 同一 failure fingerprint 连续无变化 → `needs_user`，不空转；
- 预算来自 `RunPolicy`：
  - `max_cost_usd`
  - `deadline_sec`
  - `max_tool_calls`（任务配置，非 prompt 数字）
- 预算耗尽 → `capped` + `unfinished[]`，不得 `success=true`。

## 6. 决策改造

从：

```text
LLM 输出自由 JSON {kind,name,args,reason}
```

改为：

```text
LLM tools=[generate_mg, validate_mg, repair_mg, qa_mg, ask_user, final]
→ provider tool_calls
→ schema 校验 args
```

好处：

- 不再有 JSON 文本截断；
- 不再需要 `max_tokens`/reason 长度上限；
- args 由 schema 约束；
- 未知 tool/坏 args 变成结构化 `DECISION_*` failure。

如果 provider 不支持 tool calling：

- 该 provider 标记 `unsupported`；
- 回退到受控 JSON schema，但失败必须记录 `DECISION_PROVIDER_UNSUPPORTED`，不能假装正常。

## 7. 03 / 05 的修复路径

### 03

Failure：

```json
{
  "code": "MG_FONT_UNREGISTERED",
  "location": {"path": "style.font_family", "family": "SFMono-Regular"},
  "expected": ["ui-monospace","monospace","sans-serif"],
  "observed": "SFMono-Regular",
  "fix_hint": "移除未注册 family 或改用 allowed 字体"
}
```

Agent：

```text
repair_mg(failure_ids=["font"])
→ 改 font_family
→ validate_mg
```

### 05

Failure：

```json
{
  "code": "MG_LAYOUT_MULTILINE_OVERLAP",
  "location": {"elements": [0,1,2], "path": "elements[*].y"},
  "expected": {"min_gap": 252, "suggested_y": [170,422,674]},
  "observed": {"y": [170,326,482], "font_sizes": [120,120,180]}
}
```

Agent：

```text
repair_mg(failure_ids=["multiline"])
→ 改 y/字号
→ validate_mg
```

## 8. 可观测性

- 每次 observation 写入 blackboard；
- 前端活动流显示：tool、status、failure code、scope、element；
- QA 面板按 blocking/warning 分组；
- 保留 raw_ref，不把长错误截断进 prompt；Agent 可按需读取。
- 决策上下文只放 summary + failure 列表，不塞完整 HTML。

## 9. 测试

- 每个 failure code 的 parser 单测；
- 03 fixture：必须产生 `MG_FONT_UNREGISTERED`；
- 05 fixture：必须产生 `MG_LAYOUT_MULTILINE_OVERLAP` 且 suggested_y 正确；
- final 门禁：blocking failure 时禁止 final；
- 预算耗尽：capped + unfinished；
- provider tool calling 成功/不支持；
- 前端活动流展示 failure scope；
- 5 个样片：0 blocking 或明确 needs_user。

## 10. 实施顺序

1. contracts + failure taxonomy + ledger；
2. 改 MGGenerator 出口为 Observation；
3. semantic/lint/qa 结构化；
4. 改 tool adapters；
5. 改 loop + final gate + planner；
6. 改决策为 native tool calling；
7. 前端活动流/QA 面板；
8. 03/05 回归 + 5 样片；
9. 全量 E2E；
10. 文档与提交。

## 11. 验收

```text
03 不再因字体静默失败
05 给出 y/字号修复建议并被 Agent 使用
任何 blocking failure 都不能 final
Agent 能引用 failure_id 修复
无魔法 max_tokens/reason 上限
```
