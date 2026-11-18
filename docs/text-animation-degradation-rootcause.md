# 文字动画降级根因诊断（task_a3e78e2fda）

> 目的：解释 2026-11-15 这轮任务为什么在“最终时间线已产出、quality 0 error、降级台账 outstanding=0”的情况下仍然 failed，以及 text/mg 降级为什么重跑 3 轮仍不消失。  
> 证据来源：`workspaces/task_a3e78e2fda-8e2fda/reports/degradation_ledger.json`、`workspaces/proj_f4727a36c443-36c443/reports/43-animation-12.md`、`44-animation-13.md`、`45-animation-14.md`、`47-quality-14.md`、`agent-closeout.json`。

## 1. 结论先行

这轮 failed **不是单一“模型不努力”**，而是四层问题叠加：

1. **指标定义对“内容驱动长停留”不友好**：文字/MG 的关键帧覆盖率按整段时长算，长 clip 的合理 hold 被算成“动效弱”。
2. **生成侧没有“关键帧手术”能力**：文字/MG 重派只是重新生成同一段，LLM 仍然只做入场；没有确定性地补中段关键帧/放大位移。
3. **模板兜底仍然存在**：`mg_template_fallback` 从 `_fallback_generate()` 走内置模板；这与“只保留提示词、禁止模板”的约定冲突。
4. **任务终态状态机不一致**：台账已经 `outstanding=0`、收据齐全，但 `terminal_reason` 仍是 `blocked / degradation_unresolved`，closeout 直接按旧 gate 判 failed。

## 2. 逐项根因

### 2.1 `text_motion_weak`：18%–40% 覆盖率

**报告**：animation 第 12/13/14 次仍报；收据写“关键帧覆盖率 18%–40%，稳定卡在 45% 门槛下”。  
**代码路径**：`clipwright/agents/animation_agent.py`（覆盖率 = 最后一个内容关键帧 - clip 起点 / display_sec；阈值 0.45）。

根因：

- 文字 clip 的 `display_sec` 是**内容驱动**的，常见 8–30s（法律条文/长句要阅读时间）；
- LLM 的 text keyframes 往往只在 0–1.2s 做 opacity/scale 入场，之后就是 hold；
- 覆盖率分母是整段 8–30s，分子只有入场 1s → 10%–40%，必然低于 45%；
- 动画 Agent 的重派只是把同一段文字重新交给 LLM，**没有代码去把中段补上 0.5–1s 间隔的关键帧**；
- 提示词虽然写了“关键帧覆盖 ≥60%”，但这是软约束，LLM 不执行时没有确定性修复；
- 结果：第 1 轮 8 项、第 2 轮 6 项、第 3 轮 9 项，数字在变，根因不变。

**修复方向**：
- 判定改成“运动窗口 ≥0.30s”而不是整段覆盖率（已在当前代码改，但该轮报告是改前产物）；
- 或在生成后确定性补中段关键帧（例如每 0.8s 一个轻微 translate/scale 变化），而不是再调 LLM；
- 或者区分 `hold_intentional=true`，不把合理 hold 计入弱动效。

### 2.2 `mg_motion_weak`：最大位移 26/44px

**报告**：第 13 次 26px（1.4%），收据写 44px（2.3%）；要求 ≥4%（1920px ≈ 77px）。  
**代码路径**：`clipwright/animation/mg/type_generation.py`：

- `MOTION_MIN_COVERAGE=0.45`；
- `MOTION_MIN_SHIFT_RATIO=0.04`；
- `objective_motion_findings()` 用 `motion_report().max_shift / width` 计算；不达标给 critique 45 分，触发一次修复。

根因：

- LLM 生成的 MG 是“入场小偏移 + 静态展示”，`max_shift` 只有 26–44px；
- 修复 prompt 只要求“提高位移/铺关键帧”，但 LLM 重写后仍输出小偏移；
- MG 的客观门禁在 `_critique` 之后只给**一次**修复机会，修复仍不达标时接受原输出（第 13 次）或走模板（第 12 次）；
- 第 14 次“ok”是假通过：收据记录该次产物 sha256 与第 13 次完全相同，且没有再调用 MG 引擎，说明 gate 没有真正复跑这段。

**修复方向**：
- 生成后确定性放大主运动（translate/scale 到 ≥4%），或对 MG 元素做后处理；
- gate 在最终产物上复算，不能只信 method=ok；
- 长 MG 也应按“运动窗口”而非整段覆盖率判定。

### 2.3 `mg_template_fallback`：一处内置模板

**报告**：第 12 次“启动器功能流程链”使用内置模板。  
**代码路径**：`clipwright/animation/mg/generator.py::_fallback_generate()`：

- LLM 多次尝试 + 修复失败 → `_fallback_generate()`；
- `FallbackEngine.find_best_template()` 按描述匹配到内置 `templates/*.json`；
- 成功后 method=`fallback`，`fallback_template=tid`，并写 `mg_template_fallback` 降级。

这与“只保留提示词、禁止模板/预设”的用户约定**直接冲突**。后续 `type_generation` 里有“method 含 fallback 就拒绝”的校验，但这条路径仍可能先生成模板再被下游接受/登记；报告证明模板确实进了时间线。

**修复方向**：删除/关闭 `_fallback_generate`，失败时返回明确的 `llm_mg_failed_no_template`，不要用模板补位。

### 2.4 `llm_mg_failed_no_template`：4 段 LLM MG 未产出

**报告**：第 12 次 4 个 LLM 定制 MG 未产出，按“只保留提示词”策略跳过。  
**代码路径**：`clipwright/animation/mg/generator.py`：

- LLM 返回非法 JSON / 截断 / 校验失败 → `_note("llm_json_invalid" / "llm_json_truncated")`；
- 重试/修复失败 → `_fallback_generate` 或 `drawtext` 兜底；
- 本次选择 no-template 路径，所以只登记 `llm_mg_failed_no_template`，没有画面。

根因：

- 报告只记录“未产出”，没有保留具体 finish_reason/解析错误；从现有报告**无法确定**是 JSON 截断、字段非法还是 critique 失败；
- 4 段集中在“启动器功能流程链”这类长描述/多元素场景，LLM 输出更长，JSON 截断/结构错误概率更高；
- 没有 per-clip 的详细错误日志落报告，导致无法定向修。

**修复方向**：
- 报告里保留每段 `finish_reason/解析错误/缺失字段`；
- 对长 MG 拆小/限制元素数；
- 失败按 clip_id 重派，而不是整段跳过。

### 2.5 为什么 outstanding=0 但任务还是 failed

这是最关键的“状态机”问题。

证据：

- `degradation_ledger.json`：`"pending": []`，receipts 7 条（accepted），`updated_at=21:58:43`；
- `agent-closeout.json`：`terminal_reason="blocked"`，`blocker_code="degradation_unresolved"`，`completed` 含 final_timeline/scenes/...，`unfinished=[]`；
- `task_a3e78e2fda.json`：status=`failed`，terminal_reason=`blocked`。

代码路径：`clipwright/services/agent_kernel.py::_success_terminal_reason()`：

```python
if gate_reason == "degradation_unresolved":
    return TerminalReason.blocked, "degradation_unresolved"
```

- 降级守门先把 `gate_reason` 设成 `degradation_unresolved`，再要求写收据；
- 收据是在 gate 之后写入的，**但 `gate_reason` 没有随收据清空/重算**；
- 最终 `_success_terminal_reason` 仍看到旧的 `degradation_unresolved` → blocked → failed；
- 即使 `unresolved` 已经是空、`outstanding=0`，终态也不翻盘。

**修复方向**：
- 在最终收尾前重新计算 `unresolved`；如果 `outstanding=0` 且所有降级有 accepted/remediated 收据，应把 `gate_reason` 重算为 `tool_loop_completed`（achieved）或明确的 `degradation_accepted` 终态；
- 不能“先判死、后写收据”却仍按判死状态落盘。

## 3. 为什么重跑 3 轮没有修好

| 轮次 | 降级 | 实际动作 | 为什么没修好 |
|---|---|---|---|
| 第 1 轮 | 8 项 text_motion_weak | 重派 animation | 重新生成同段，LLM 仍只做入场 |
| 第 2 轮 | 6 项 text_motion_weak | 定向 clip 重派 | 仍未做关键帧手术；长 display_sec 分母不变 |
| 第 3 轮 | 9 项（含 mg_motion_weak） | 再重派 | MG 修复仍小位移；模板/LLM 失败交替 |

本质上：**重派的是“生成”，不是“修复”**。没有确定性后处理，LLM 的同一提示词会产出同一种弱动效。

## 4. 建议的最小修复顺序

1. **先修任务终态**：收据齐全后重算 gate，别让 outstanding=0 仍 failed；否则所有降级工作都被状态机抹掉。
2. **文字关键帧**：用确定性后处理补中段关键帧，或把判定改为运动窗口（当前代码已改，需重跑验证）。
3. **MG 位移**：生成后强制主运动 ≥4%，gate 在最终产物上复算；拒绝 sha 相同的假 ok。
4. **删除模板兜底**：`_fallback_generate` 不再用模板，失败就报 `llm_mg_failed_no_template` 并按 clip_id 重派。
5. **报告可诊断**：每个失败 MG 记录 finish_reason/解析错误/缺失字段，不能只写一句“未产出”。
