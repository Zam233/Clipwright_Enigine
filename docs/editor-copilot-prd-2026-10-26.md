# 附录（已合并）：`run_stage unknown` 现场复现记录

> **本文不是独立 PRD。** 编辑器副驾驶的三条需求（副驾驶标签化 / 编辑器内运行 / 思考可见）
> 已于 2026-10-26 并入 `master-agent-prd-2026-10-26.md`，**唯一真值以 master 为准**。
>
> 本文件只保留一段**历史复现记录**（下面 §1.4 原文），因为 master §19.5 引用了它作为证据。
> 其余章节已删除（避免两份设计文档漂移）；需要设计细节请看 master：
>
> | 原章节 | 现在的位置（master） |
> |---|---|
> | §0 一页结论 / §2 目标与非目标 | §19 引言 + §0.1 需求登记表（需求 8/9/10） |
> | §1.1 副驾驶是单面板 | §19.1 现状事实 |
> | §1.2 活动被关在 `/task/{taskId}` | §19.1 / §19.3 |
> | §1.3 思考拿不到 | §19.1 / §19.4 |
> | **§1.4 unknown 链路（含 1.4.1–1.4.3）** | **保留在本文件**（master §19.5 摘要引用） |
> | §3 方案 A 副驾驶标签化 | §19.2 |
> | §4 方案 B 编辑器内运行 + 子 agent 下钻 | §19.3 |
> | §5 方案 C 思考可见（含 5.3 文档标签、5.4 目录选择） | §19.4 / §19.6 |
> | §6 方案 D 失败可见（D1–D7） | §19.5 |
> | §7 数据契约 | §17.12 |
> | §8 验收 | §19.7（U1–U14） |
> | §9 分期 / §10 测试计划 / §11 风险 | §19.8 / master §12 / §16.9 |

- 版本：v1.2（2026-10-26，瘦身为附录）
- 关联：[master-agent-prd-2026-10-26.md](master-agent-prd-2026-10-26.md)（唯一真值）、[workspace-prd-2026-10-24.md](workspace-prd-2026-10-24.md)

---

## 1.4 `run_stage 未成功：unknown（retryable=retry_same）` 的真实链路（已定位）

用户现象：`结束：粗剪（failed）` → `已完成 2 步，未完成 4 步；结束原因：run_stage 未成功：unknown（retryable=retry_same）`

逐层追到的四个事实：

1. **结论文案只由 category + retryable 拼出来**，不含任何原文错误：
   `agent_kernel.py:248-272`——`failure = outcome.get("failure")`，`category` 缺失时默认 `"unknown"`，
   `conclusion = f"{step.capability} 未成功：{category}（retryable={retryable}）{tail}"`，
   而 `tail` 依赖 `evidence[0]`，**evidence 为空时 tail 为空**。
2. **`unknown` 是 `_failure_from_output` 兜底出来的**：
   `stage_subagent.py::_failure_from_output`——Agent 只给了 `decision=fail` + `error`（没给结构化
   `failure`）时，构造 `FailureReport(category="unknown", blocking_point=error)`；
   `retryable` 用 dataclass 默认值 `retry_same`。
3. **原文错误确实存在，但没进 evidence**：`StageResult.error` 与 `blocking_point` 里有
   `str(payload["error"])`，而 `FailureReport.evidence` 是空的 → 上面第 1 条拼不出 tail。
4. **重试策略把错误彻底吃掉**：`retryable == "retry_same" and attempt < 2` → 用**同样的输入**重跑同一阶段，
   第二次仍失败（attempt=2 不再重试）→ 直接 `finish` 收尾，整条任务停在第 3 步，
   后面 animation/audio/quality 全部"未完成"，且用户拿不到可执行信息。

**这不是"某个 filter 写错了"，而是三件事同时缺**：
(a) 失败原因没有一路带到用户面前；(b) `retry_same` 在"输入不足/环境问题"上没有意义；
(c) 失败后没有"换成 replan（补上游 / 换模型 / 降级）"的路径，只能收尾。

### 1.4.1 已复现：不是 material 失败，是 material「假成功」

用 `stage_subagent.run_stage` 真跑（scenes 为空的工作区，不触发任何 LLM）得到与用户日志**完全一致**的形态：

```
material: status=ok  writes=['candidate_clips']  summary='候选素材 0 条'  failure=None
edit    : status=failed  error=''  summary='轨道 0 条 / 片段 0 个'
          failure.category=unknown  retryable=retry_same
          failure.blocking_point='edit 阶段失败'  failure.evidence=[]
```

两个"假成功"源头：

| 位置 | 行为 |
|---|---|
| `agents/material_agent.py:741-746` | `scenes` 为空时返回 `decision=PASS, candidate_clips=[]`——**素材阶段在"没场景"时也报成功** |
| `agents/material_agent.py:748-760` | 无注册素材源时返回 `PASS` + 每场景占位候选（`note="无注册素材源，将使用文字占位"`）——同样是 PASS |

真正失败的是剪辑：`agents/edit_agent.py:319-325`

```python
if not scenes:
    return EditOutput(decision=AgentDecision.FAIL,
                      timeline=Timeline(id=..., width=..., height=..., fps=...),
                      edit_notes=["场景列表为空"])     # ← 既没有 error，也没有 failure
```

全文件只有这一处 `decision=FAIL`（其余异常走 `run_stage` 的异常分支，会被归成
`timeout/model_incapable/internal_defect`），所以**编辑阶段出现 `unknown` 的唯一来源就是这条**：
它只把人类可读原因写进 `edit_notes`，而 `_failure_from_output` 不读 `edit_notes`。

### 1.4.2 附带发现：失败的空时间线仍然进了黑板

复现里 `edit` 的 `writes=['edit_notes','timeline']`——失败时返回的是一个**空壳 Timeline**
（0 轨道 0 片段），却被 `stage_merge` 当成正常产物写进黑板。后果比 `unknown` 更危险：
下游 `animation/audio/quality` 会以为"有 timeline"继续跑，最终交付一条空片，且失败点被推得更远。
→ 修复要求：阶段 `decision=FAIL` 时**不得合并 timeline**（或只合并 `path` 为空的占位并标 degraded）。

### 1.4.3 想确认你那次的具体成因，看这三处

1. 事件流里 `structure` 是否带 `degradation: scenes_fallback`（`structure_agent.py:558-567`：LLM 没产出可用场景 → 模板化分镜兜底；topic 与 script_text 都空时模板也可能为空）；
2. `material` 的 `step_end.summary` 是否为「候选素材 0 条」，以及是否出现「无注册素材源，将使用文字占位」；
3. `edit` 的 `step_end` 是否只有 `edit_notes=['场景列表为空']` 而没有 error——若是，本节的复现就是你的现场。

> 修复不是"把 unknown 换成别的词"，而是：**错误原文必须出现在任务结论里**，
> 并且**可重试性由真实原因决定**（`input_insufficient → 先派上游`、`model_incapable → 换模型`、
> `timeout → 加墙钟或降规模`、`policy_refused → 不重试直接报`）。
> 对应修复项见 master §19.5（D1–D7）、验收见 master §19.7 与 §16.10.5。
