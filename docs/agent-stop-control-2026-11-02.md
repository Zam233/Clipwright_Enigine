# 任务执行期的「停止」控制（2026-11-02）

## 一、要解决的问题

任务跑起来以后，前端把输入区换成一条「主 agent 正在执行…」的提示条，用户除了等没有别的动作：

- 跑歪了、跑贵了、发现方案说错了，都只能等它跑完再改；
- 想中止只能刷新页面——而**刷新只是我这边的界面断了，后端任务还在继续烧钱**；
- 更糟的是续跑：任务被强行掐断以后，下一个任务根本看不出"是用户主动喊停"，
  只会当成一次异常终止，于是从头再来。

所以「停止」不是前端按钮的事，它必须**由后端真中止**，并在会话里**留下痕迹**。

## 二、后端：`POST /api/requirements/{session_id}/stop`

实现在 `RequirementsService.stop_task()`（`clipwright/services/requirements_service.py`），
路由在 `clipwright/api/requirements.py`。没有 body，响应：

```json
{ "stopped": true, "task_id": "task_xxx", "status": "plan_ready", "note": "已按你的要求停止任务…" }
```

三件事一次做完：

1. **真中止**：`kernel.cancel(task_id)` —— 置 `_CONTROL[task_id]="cancel"`、`handle.cancel()`
   打断正在跑的协程（只改状态的话，执行中的阶段仍要跑满墙钟）、作废该任务待答的问题、
   发一条 `task_end{reason:"cancelled"}`、落检查点。任务状态变 `cancelled`（终态）。
2. **会话放开**：状态写回 `plan_ready`。前端就是靠这个字段把输入框 / 附件按钮 / 发送按钮
   重新显示出来的（`AgentPanel` 只在 `pipeline_running` / `planning` 时隐藏 composer）。
3. **留痕（关键）**：往 `session.messages` 追加一条 `role=assistant` +
   `metadata.user_stopped=true` 的说明，同时往会话时间线落一条同内容条目。
   `context_loader` 的会话片段会把它带进**下一个任务**的上下文——主 agent 由此知道
   "是用户喊停的，接着做，不要从头再来"。

### 没有活任务时

`stopped=false`，`note` 为空，**不伪造中止说明**（否则会话里会出现"你明明没点停止"的记录）。
会话状态若仍悬在 `pipeline_running`/`planning`，顺手改正为 `plan_ready`。

### 与「兜底放手」的分工

| 场景 | 由谁处理 | 结果 |
|------|---------|------|
| 用户点停止 | `stop_task()` | 任务 `cancelled`，会话 `plan_ready`，留 `user_stopped` |
| 任务自己跑完/失败/超时 | `watch_task_session()` | `done` → `pipeline_done`，其它终态 → `plan_ready` |
| 任务已不在内存（重启/淘汰） | `watch_task_session()` 连续 5 次取不到 → 释放会话；`release_stale_pipeline_sessions()` 周期扫存量 | `plan_ready` |

两条路互补：`watch_task_session` 管"任务自己结束"，`stop_task` 管"人要它结束"，
前者不写 `user_stopped`（它不知道是谁干的），后者必须写。

## 三、前端：输入区原位恢复

`J:\Clipweight-Client\src\features\agent\AgentPanel.tsx`：

- 运行条（`data-testid="requirements-running-bar"`）在 `status ∈ {pipeline_running, planning}`
  时显示，右侧是停止按钮 `data-testid="requirements-stop"`；点下去进入 `stopping` 态
  （转圈 + 禁用，防连点）。
- `stopRunningTask()` 调 `requirementsApi.stop()` → 本地 `setStatus('plan_ready')` +
  清 `requirementsBusy` + 掐掉在途的对话流 → 用响应里的 `note` 立刻在消息流里补一条，
  用户不用等下一次轮询就能看到"已停止"。
- composer 的渲染条件是 `!['pipeline_running','planning'].includes(status)`，
  所以状态一回 `plan_ready`，编辑框、附件按钮、发送按钮**原样回来**，不需要刷新。
- planning 阶段的停止也走同一条路：`/plan/dispatch` 是幂等的、立刻返回 `task_id`，
  所以 `_live_task_id()` 能取到那个 `plan_only` 任务并取消它；`confirmBrief` 里的轮询
  发现状态不再是 `planning` 就收尾。

## 四、验证（人工）

1. 起一个任务，运行条出现 → 点「停止任务」；
2. 断言：按钮转圈后消失、输入区/附件/发送回来、消息流出现"已按你的要求停止任务…"；
3. 断言：后端任务 `cancelled`、会话 `plan_ready`、时间线末条带 `user_stopped`；
4. 再说一句「接着做」→ 新任务带着上一轮上下文启动，且**不会**从头再来。

代码侧已验证（单进程 TestClient，`stop` 前后 5 条断言全过）：任务 `pending → cancelled`、
会话 `pipeline_running → plan_ready`、末条消息 `user_stopped=True`、时间线条目出现、
二次点击 `stopped=false` 且不伪造说明。
