# 现代 Agent 如何结束任务：Claude Code / DeepSeek Harness / opencode 三实现对照

> 调研口径：全部结论都落在可核对的一手材料上——Claude Code 官方文档（hooks / goal）、
> opencode v1.18.4 真实源码（`packages/opencode/src/session/prompt.ts`、`processor.ts`）、
> 以及本机安装的 DeepSeek Harness 包 README（`dsh-agent` / `dsh-agent-loop` /
> `dsh-goal-round-driver` / `dsh-tool-goal`）。
>
> 取证方式：本机直连 GitHub 被墙（`curl` 20s 超时、0 字节），经 Clash 代理
> `http://127.0.0.1:7897` 才拉到源码（1.0s，HTTP 200）。源码副本落在
> `J:\Clipwright\logs\oc-src\`（prompt.ts / processor.ts / max-steps.ts / cc-hooks.md / cc-goal.md）。

---

## 0. 一句话结论

三个实现都**没有**"一个大超时"这种粗暴终止。它们把「结束」拆成三层不同职责：

```
① 模型的自然停     本轮不再发起工具调用（引擎只负责识别，不做判断）
② 策略层的续跑判定  目标是否达成？没达成 → 再开一轮，并把理由喂回去
③ 引擎层的硬刹车    预算/错误/人 → 强制收尾，但必须留下可读的终态与原因
```

真正的差异不在"停不停"，而在**第②层由谁做判断、第③层留什么证据**。

---

## 1. 三层判定模型（对照表）

| 层 | 谁决定 | 判据 | Claude Code | DeepSeek Harness | opencode |
|---|---|---|---|---|---|
| ① 自然停 | 模型 | 本轮没有工具调用 | 无 `tool_use` → 触发 `Stop` 事件 | 无工具调用 → turn 关闭，触发 `agent/turn-stopping` | `finish` 有值且不是 `tool-calls`、且无未处理的 tool part |
| ② 续跑 | 策略 / 评估器 | 「完成条件」是否成立 | `/goal`：小模型每轮裁决 `not yet met / met / impossible`；或 Stop 钩子 `decision:"block"` | goal rounds driver：`maxGoalRounds` 内自动再开一轮；`complete` / `blocked` | `agent.steps` 内继续下一 step；`MAX_STEPS_PROMPT` 在最后一步收口 |
| ③ 硬刹车 | 引擎 | 预算 / 错误 / 人工 | `stop_hook_active` + **连续 8 次续跑上限**；`StopFailure` 错误分类 | 轮次上限（blocker `round-limit`）、max tokens、持久化失败、`cancel()`、插件卸载 | doom loop（同工具同参 ×3）、abort、`content-filter` |

三家都把①交给模型、把②做成**可插拔的判定**、把③收窄成"只防死循环和防失控"。

---

## 2. Claude Code：把「停」做成可拦截的事件

### 2.1 `Stop` 钩子 = 终止事件 + 可覆盖的裁决

- `Stop` 在主 agent「回复完毕」时触发；**用户中断不会触发**，API 错误改走 `StopFailure`。
- 钩子返回 `{"decision":"block","reason":"…"}` 即可**阻止 Claude 停下**并继续这一轮
  （`reason` 在 block 时必填；exit code 2 同义，stderr 作为理由）。
  `hookSpecificOutput.additionalContext` 是"非错误版"的同一机制：对话继续，但转录里标成
  `Stop hook feedback` 而不是 hook error。
- 另一个终止拦截点：`PostToolBatch` 的 exit 2 = **在下一轮模型调用之前停住 agentic loop**。

### 2.2 防死循环的三道保险（这是它最值得抄的地方）

1. `stop_hook_active`：hook 输入里的布尔值，`true` 表示"本次继续已经是 stop hook 造成的"。
   官方原话是让实现检查它，避免在永不成立的条件上反复 block。
2. **8 次连续续跑上限**：连续被停止钩子续跑 8 次后，Claude Code 直接忽略下一次 block 并结束本轮；
   **每次 Claude 调用工具都会把这个计数清零**（所以正常干活不会触发）。
   可用 `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` 调整。
3. 错误分类：`StopFailure` 的 `error` 字段区分 `rate_limit / overloaded / authentication_failed /
   billing_error / max_output_tokens / model_not_found / server_error / unknown`；
   这一事件**没有决策能力**（只能记录/告警），即"错误导致的终止"和"判定导致的终止"在事件层面就分开了。

### 2.3 `/goal`：把续跑判定外包给一个"新鲜"的模型

- `/goal` 本质是**会话级的 prompt-based Stop 钩子**：每轮结束，把"条件 + 到目前为止的对话"发给一个小快模型，
  返回三种裁决之一：
  - `Not yet met` → 继续，并把裁决理由当作下一轮的指导；
  - `Met` → 清掉目标，转录里记一条 achieved；
  - `Impossible` → 清掉目标，记一条 failed（**不用你手动清**）。
- **无进展就停**："如果 Claude 一直回应评估器却没进展（连续几轮没有任何工具使用），Claude Code 停掉循环、
  打一条警告、把控制权交回你，目标保留；你下一条消息后评估恢复。"
- **上限只能写在条件里**：想有界就自己写类自然语言条款，例如 `... or stop after 20 turns`。
- 错误分两档：**"你必须先修的错误"**（如自动压缩也救不回来的上下文溢出）→ 清掉目标并警告；
  其余错误 → `retry`（服务器过载/断连）或 `pause`（限流/额度用尽），可用
  `CLAUDE_CODE_GOAL_CHECKIN_MINUTES` 控制自动重试。

---

## 3. DeepSeek Harness：引擎不做预算，把预算交给"目标"

（以下是本机 `@deepseek-ai/dsh@` 各包 README 的自述，属一手材料。）

### 3.1 循环层的终止语义

- **明确不内置 turn 预算**。`dsh-agent-loop` 的"已知限制"原文：
  > No built-in turn budget — tool calls or steering continue the current turn; a policy that bounds
  > runaway turns must cancel from an existing lifecycle extension point such as `agent/turn-stopping`.
- 与之配套的拦截点：`agent/turn-stopping` 在"本来要结束的一轮关闭之前"运行，监听器可以 **steer 让这一轮继续**
  （与 Claude Code 的 `decision:"block"` 是同一形状，只是换成了进程内事件）。
- 取消是显式的：`cancel(cause)` 默认连待处理输入一起清（`keepInbox: true` 只砍当前轮）；
  已送达的流式文本会作为 `interrupted: true` 的锚点留在历史里——**"被打断"也是一个可读的消息**。

### 3.2 目标（goal）层的终止规则

| 规则 | 取值 / 行为 |
|---|---|
| 自动续跑的轮次上限 | 写在**目标定义**里（`maxGoalRounds`），不在 driver 里 |
| 轮次耗尽 | 记为 blocker，稳定错误码 **`round-limit`** |
| 允许宣布 blocked 的门槛 | **同一条件连续 3 轮**（`blockedAfterConsecutiveRounds: 3`），且必须写 `blocked_reason`；"难、不确定、还有活要干"都不算 blocked |
| 判定后立即收口 | 自动 `complete`/`blocked` 成功后**物理结束本轮 turn**，并发给模型一条收尾指令（`<goal_complete>` / `<goal_blocked>`）让它对用户说最后一句 |
| 其它终止源 | 一轮以 max tokens 结束、持久化写失败、agent 被取消、插件卸载 |
| 取消后 | **永不自动重启**；中断的目标在下一次空闲点被 pause |
| resume / fork 之后 | 活动目标自动 disarm，必须有人的显式 resume 才继续——"driver 绝不自己把活复活" |

设计取舍很清楚：**引擎只保证"可中断、可恢复、可审计"，"该不该继续"属于目标与策略。**

---

## 4. opencode：一个 `while (true)` 里的全部终止逻辑

（源码：`J:\Clipwright\logs\oc-src\prompt.ts`（v1.18.4，1631 行）、`processor.ts`。）

### 4.1 循环出口：靠 `finish` + 是否还有活着的工具调用

```ts
while (true) {                                   // prompt.ts:1088
  ...
  const hasToolCalls = lastAssistantMsg?.parts.some(
    (part) => part.type === "tool" && !part.metadata?.providerExecuted && !isOrphanedInterruptedTool(part),
  ) ?? false                                    // 1106-1109

  if (
    lastAssistant?.finish &&
    !["tool-calls"].includes(lastAssistant.finish) &&
    !hasToolCalls &&
    lastUser.id < lastAssistant.id
  ) { ...; break }                              // 1111-1130
```

两个细节值得抄：

- **不信 provider 的 `finish`**：注释写明"有些 provider 即使消息里有工具调用也回 `stop`"，所以额外用
  `hasToolCalls` 兜住，避免漏发工具结果。
- **`unknown` 被显式排除在"完成"之外**（见 4.4 的 `finished` 判定），这是踩过两次坑之后加的。

### 4.2 步数上限：软着陆，而不是硬砍

```ts
const maxSteps = agent.steps ?? Infinity          // 1178 每个 agent 可配，缺省不限
const isLastStep = step >= maxSteps               // 1179
...
...(isLastStep ? [{ role: "assistant", content: MAX_STEPS_PROMPT }] : [])   // 1281
```

`MAX_STEPS_PROMPT`（`packages/core/src/session/runner/max-steps.ts`）的内容是：

> CRITICAL — MAXIMUM STEPS REACHED … Tools are disabled until next user input. Respond with text only.
> … MUST provide a text response summarizing work done so far … List of any remaining tasks …
> Any attempt to use tools is a critical violation. Respond with text ONLY.

即：**到顶前先夺走工具面**，命令模型写"做了什么 / 还剩什么 / 建议怎么办"，最后一条消息本身就是可用产物。
对比"直接 cancel 掉"，这样收尾既省钱又留下交接信息。

### 4.3 压缩、结构化输出、内容过滤：三类"不是结束"的分支

- **上下文溢出 → 压缩后继续**（1161-1168）：`compaction.create(...)` 之后 `continue`。
  压缩是循环内部动作，不是终止条件。
- **结构化输出**：`StructuredOutput` 工具成功后直接 `break`（1288-1293）——"拿到你要的结构"就是完成。
- **内容过滤 / 拒答**：`finish === "content-filter"` 时抛 `ContentFilterError` 并 `break`（1301-1308）。
  代码注释点明了动机：这类轮次**可能一个字都没输出**，以前会静默 idle，现在必须显式报错。

### 4.4 终止判定的两个真实事故（同一个 if 两个方向都能错）

- [#43622](https://github.com/anomalyco/opencode/issues/43622)：`finish` 为 `"unknown"` 时**不在退出排除集**里 →
  会话静默退出（exit 0、无任何报错）。
- [#49414](https://github.com/anomalyco/opencode/issues/49414)：同一个 `"unknown"`、无工具调用的情形下，
  另一种解释导致**永不终止的请求风暴**。

现在的写法把 `unknown` 放进 `!["tool-calls","unknown"].includes(finish)`（1295），
即"未知原因不算完成"，宁可继续也不假装成功——但这也说明：**终止判定必须只有一处、且边界要能被测出来**。

### 4.5 doom loop：重复动作交给人裁决，而不是静默重试

```ts
const DOOM_LOOP_THRESHOLD = 3                     // processor.ts:29
const recentParts = parts.slice(-DOOM_LOOP_THRESHOLD)
if (recentParts.length === 3 && recentParts.every(p =>
      p.type === "tool" && p.tool === value.name &&
      p.state.status !== "pending" && JSON.stringify(p.state.input) === JSON.stringify(input)))
  yield* permission.ask({ permission: "doom_loop", patterns: [value.name], ... })   // 356-379
```

阈值 3、判据是"最近 3 个工具 part 同名且入参完全一致"，动作是**升级成一次权限询问**
（交给人或策略），而不是自动熔断。已知局限（issue #25254）是只比较当前消息内、漏掉跨消息的重复。

### 4.6 中断：把 AbortError 落成一条完整消息

```ts
const finalizeInterruptedAssistant = Effect.gen(function* () {
  if (msg.time.completed) return
  msg.error ??= MessageV2.fromError(new DOMException("Aborted","AbortError"), { aborted: true, ... })
  msg.time.completed = Date.now()
  yield* sessions.updateMessage(msg)
})                                                // 1203-1211
```

中止不是"留个悬空 assistant 消息"，而是：标 `aborted`、补 `time.completed`、写回存储。
所以在 UI/恢复侧，"被打断"和"正常结束"一样是**可判定状态**。

---

## 5. 收敛：5 类终止条件 + 4 条工程约束

### 5.1 五类终止条件（三家都在用，只是默认值不同）

| 类别 | Claude Code | DSH | opencode |
|---|---|---|---|
| 完成标准达成 | `/goal` 判 `Met` | `update_goal(complete)` | `finish` 非 tool-calls 且无活工具；结构化输出成功 |
| 无进展 | 连续几轮无工具使用即停循环 | 轮次上限 → `round-limit` blocker | 无（靠 doom loop 兜） |
| 重复动作 | `stop_hook_active` + 8 次上限 | 依赖策略层 | `doom_loop` 阈值 3 |
| 预算上限 | 只有 8 次续跑上限，其余靠条件里写 | `maxGoalRounds`（在目标里，不在引擎里） | `agent.steps`（默认 `Infinity`） |
| 外部干预 | 用户中断（不触发 Stop）、`StopFailure` 错误、`/goal clear` | `cancel()`、max tokens、持久化失败、卸载 | abort、`content-filter`、压缩失败 |

### 5.2 四条工程约束（比"选哪个条件"更重要）

1. **终态必须落成一条消息 + 一个事件**：opencode 的 `finalizeInterruptedAssistant`、Claude Code 的
   `last_assistant_message`、DSH 的收尾指令——三家的共同点是"结束要说一句话"，不允许静默 idle。
2. **原因必须机器可读**：Claude Code 的 `error` 枚举、DSH 的 blocker code（`round-limit` / `model-reported`）、
   opencode 的 `finish` 值。日志里有、结构里没有 = 不可用。
3. **判定只许一处**：opencode 同一个 `finish` 判定出过方向相反的两个 bug；Claude Code 用
   `stop_hook_active` + 硬上限兜底，就是承认"策略会写错，所以引擎要留最后一根保险丝"。
4. **终止 ≠ 丢弃上下文**：DSH 轮次耗尽是写 blocker 而非销毁目标；Claude Code 的 `/goal` 保留目标直到你下一条消息；
   opencode 被打断的消息仍带着已生成的文本。**能接着说，比能停下来更难做，也更值钱。**

---

## 6. 对照 Clipwright 内核：已对齐什么、还缺什么

**已对齐**

- 无进展检测（`_gate_hit` 的 `_PROGRESS`，N=6）≈ Claude Code"连续几轮无工具使用就停"；
- 跨阶段重复失败熔断（`_REPEAT_FAILS`，阈值 5）≈ opencode `doom_loop`（但我们是跨阶段聚合，覆盖面更大）；
- 预算全 0 = 不限（`TaskLimits`）≈ DSH"引擎不做预算"+ opencode `agent.steps ?? Infinity`；
- 用户中止（`POST /api/requirements/{id}/stop`）≈ DSH `cancel(cause)`：真打断协程 + 留 `user_stopped` 记录；
- 会话状态只由任务终态驱动（不变量 #14）≈ 三家"终态必须显式落地"。

**建议补齐（按性价比排序）**

1. **软着陆（opencode 式 `MAX_STEPS_PROMPT`）**：接近上限时先**收掉工具面**、命令模型写
   "已完成 / 未完成 / 建议"，再结束。比直接 cancel 省钱，且留下的产物能直接进下一轮上下文。
2. **终止原因编码化**：现在无进展/熔断/降级未处置的原因散在事件与日志里；建议在任务上落一个
   `terminal_reason` 枚举（`done / no_progress / repeat_fail / degradation_unresolved / user_cancelled / budget`），
   前端和续跑都能直接读。
3. **目标语义**：我们只有"任务"，没有 `maxGoalRounds` + blocker code 这一层；
   DSH"同一条件连续 3 轮才允许 blocked、且必须写理由"值得抄——否则 agent 一遇难就宣布做不到。
4. **判定单点 + 边界可测**：opencode 的教训是同一个 if 两个方向都出过事故。`_gate_hit` 应保持
   "唯一判定点"，并对 `0 = 不限` 这类边界留可执行的边界用例（不是全量测试，只钉边界）。
5. **续跑要带上"上一轮为什么结束"**：Claude Code 会把裁决理由喂进下一轮、DSH 会写 blocker；
   我们目前只对"用户中止"写了说明，其它终态也应当写。

---

## 7. 参考

- Claude Code · Hooks reference（`Stop` / `StopFailure` / `PostToolBatch`、`stop_hook_active`、8 次续跑上限）
  https://code.claude.com/docs/en/hooks
- Claude Code · Keep Claude working toward a goal（`/goal` 三裁决、无进展停、错误两档、条件里写上限）
  https://code.claude.com/docs/en/goal
- opencode v1.18.4 源码：`packages/opencode/src/session/prompt.ts`（1088 / 1106-1130 / 1161-1168 / 1178-1281 /
  1295-1308 / 1203-1211）、`packages/opencode/src/session/processor.ts`（29 / 356-379）、
  `packages/core/src/session/runner/max-steps.ts`
- opencode issue #43622（`finish:"unknown"` → 静默退出）、#49414（同一处 → 请求风暴）
- DeepSeek Harness 本机包 README：`@deepseek-ai/dsh-agent`、`dsh-agent-loop`、
  `dsh-goal-round-driver`、`dsh-tool-goal`
