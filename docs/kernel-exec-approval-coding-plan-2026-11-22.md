# Kernel Exec 授权 —— Coding Plan

> 版本：v1.0（2026-11-22）  
> 依据：[Kernel 执行能力与前端授权 PRD](kernel-exec-approval-prd-2026-11-22.md)  
> 分支建议：`feat/zam_kernel_exec_approval`  
> 前置基线：`feat/zam_tool_ergonomics` 当前工作区  
> 目标：把 `exec_capability.run` 接进 Kernel 的 `run_shell` / `run_python` 步骤，强制工作区 cwd，每次执行请求进入时带历史做一次 LLM 风险判定；批准后只校验 hash、不再判定；所有有风险的执行必须由前端用户授权。

---

## 0. 实施原则

1. **先契约、后实现**：先定 ExecRequest / RiskReport / Approval / Event / API 字段，再改 Kernel 与前端。
2. **不恢复旧 PlanStep 串行执行**：`run_shell` / `run_python` 是 Kernel Exec 动作，不是普通 Tool，也不要求 `Plan.steps` 存在。
3. **危险能力不进普通工具面**：阶段 Agent、research worker、插件 Agent 永远看不到 `run_shell` / `run_python`。
4. **风险不可降级**：规则硬底线/危险种子只允许上调；LLM 失败按高风险管理。
5. **授权只来自前端用户**：Agent 不能调用“自动批准”；一次授权只执行一次。
6. **可恢复**：任务暂停、checkpoint、重启后审批与执行记录都要在。
7. **开发环境**：不保留旧审批/旧执行记录兼容；新任务使用新格式。

### 0.1 默认决策（若产品未另行确认）

| 决策 | 默认 |
|---|---|
| `auto_low` | 默认开启；low 直接执行；用户可关闭；只对 low 生效 |
| med/high/critical | 必须前端授权；auto mode 不减免 |
| critical/硬底线 | 两级确认 |
| 拒绝后 | 自动 resume，把 `refused` 注入主 Agent 上下文，由它重规划 |
| 历史上下文 | 摘要级（命令/代码截断、cwd、风险、退出码）+ 脱敏输出尾部，不传完整输出 |
| Windows shell | 优先 `pwsh -NoLogo -NoProfile -NonInteractive -Command`；无 PowerShell 7 自动回退 `powershell.exe` 5.1；都无则 fail closed |
| POSIX shell | 默认 `bash -lc`；没有 bash 时按配置回退 `sh -lc` |
| 命令记忆白名单 | 不做；每次执行请求重新判定 |
| 审批 TTL | 10 分钟 |
| 历史条数 | 默认 20，最多 50 |

---

## 1. 现状与改动边界

### 1.1 当前代码事实

- `clipwright/services/exec_capability.py`
  - 已有 `run(task, step)`，但没有生产调用方；
  - 会把 `pending_approval` 映射成返回字典。
- `clipwright/supervisor/gateway.py`
  - `run_command` 已有硬底线、棘轮、LLM 判定、审批、执行审计；
  - `_PENDING` 目前是进程内字典；
  - `run_command` 只接收当前命令，没有历史上下文参数。
- `clipwright/services/agent_kernel.py`
  - 当前 tool loop 驱动，`Plan` 是假设，不是执行表；
  - 已有 `approval_request` 事件与 `paused` 处理路径；
  - `resume()` 会重新进入 tool loop，没有“执行 pending approval”的分支。
- `clipwright/api/supervisor.py`
  - 已有 `/exec`、`pending-approvals`、`approve`、`reject`、`auto-mode`；
  - 这些 API 目前主要服务 supervisor 手动执行。
- 前端
  - 已有 `ExecPermissionDialog`、`ExecRequestForm`、`SupervisorDrawer`；
  - `CopilotTaskRunTab` 只有简化的 approval rows，没有完整弹窗；
  - `SessionTimeline` 已有简单 approve/reject 按钮；
  - `supervisorStore` 已有 pending approvals 轮询。

### 1.2 不动的部分

- 媒体工具（ffmpeg 等）继续走 ToolRegistry；
- 阶段 Agent 工具白名单不改，不加入 run_shell/run_python；
- 现有 supervisor worker 执行审核逻辑保留；
- 现有手动 `/exec` 表单保留，作为用户主动执行入口；
- 不把 shell 能力开放给插件 Agent。

---

## 2. 工作包总览

| WP | 内容 | 依赖 | 预计 |
|---|---|---|---|
| WP0 | 契约、类型、事件、测试 fixture | — | 1.0d |
| WP1 | workspace cwd guard 与路径归一化 | WP0 | 1.5d |
| WP2 | Kernel Exec Step 接线 | WP0、WP1 | 2.0d |
| WP3 | exec_capability + gateway 风险上下文/单次判定 | WP1、WP2 | 2.5d |
| WP4 | 审批持久化、API、事件、审计 | WP2、WP3 | 2.0d |
| WP5 | 工具面/入口限制、feature flag、prompt | WP2、WP3 | 1.0d |
| WP6 | 前端类型/API/store/SSE | WP0、WP4 | 2.0d |
| WP7 | 前端授权弹窗/任务面板/执行历史 | WP6 | 2.5d |
| WP8 | 恢复/并发/幂等硬化 + 全量验收 | 全部 | 2.0d |
| 合计 |  |  | **约 16.5 人日** |

> 前后端可在 WP0 契约定稿后并行；WP6 不依赖 WP5，但依赖 API 契约。

---

## 3. WP0：契约与类型（P0）

### 3.1 目标

先冻结结构，避免 Kernel、Gateway、前端各自解释。

### 3.2 改动

新增/扩展：

- `clipwright/schema/exec.py`（新）
  - `ExecKind = Literal["shell", "python"]`
  - `ExecStatus = Literal["pending_approval", "approved", "running", "done", "failed", "rejected", "expired", "refused"]`
  - `ExecRequest`
  - `ExecHistoryItem`
  - `ExecRiskReport`
  - `ExecApproval`
  - `ExecResult`
- `clipwright/schema/task.py`
  - 增加 `task.exec_pending_approval_id: str = ""`（可选）
  - 或使用 `task.extra["exec"]` 指针；二选一，WP0 定稿。
- `clipwright/schema/agent.py` / 事件 detail
  - 增加 `ExecEventDetail`（可选 TypedDict / Pydantic）
- `clipwright/tool/surface.py`
  - 新增 `EXEC_CAPABILITIES = {"run_shell", "run_python"}`（仅作分发标记，不加入普通工具面）。

### 3.3 事件契约

必须新增/扩展：

- `exec_request`
- `exec_risk`
- `approval_request`（detail 扩展）
- `approval_result`
- `exec_result`

`approval_request.detail` 字段：

```json
{
  "approval_id": "ap_...",
  "exec_request_id": "ex_...",
  "kind": "shell",
  "command": "...",
  "cwd": "...",
  "risk": "med",
  "risk_report": {},
  "history": [],
  "needs_confirm": true,
  "needs_double_confirm": false,
  "hard_floor": false,
  "expires_at": "..."
}
```

`EVENT_TYPES` 与前端 union 必须同步；新增事件类型测试见 `tests/test_event_contract.py`。

### 3.4 测试

- `tests/contracts/test_exec_contract.py`（新）
  - 字段必填/枚举；
  - hash 稳定性；
  - approval 与 request 绑定；
  - 事件 detail 序列化。

### 3.5 验收

- 所有结构可 `model_dump(mode="json")`；
- 前端类型文件能一一映射；
- 不包含旧审批格式兼容逻辑。

---

## 4. WP1：工作区 cwd Guard（P0）

### 4.1 目标

`cwd` 永远不能逃出任务工作区。

### 4.2 改动

新增：

- `clipwright/services/workspace_guard.py`（新）
  - `resolve_workspace_root(task) -> Path`
  - `normalize_cwd(task, requested: str) -> Path`
  - `assert_cwd_in_workspace(task, requested) -> (Path, error)`
  - `resolve_script_path(task) -> Path`
  - 错误结构：
    - `workspace_required`
    - `cwd_outside_workspace`
    - `cwd_not_directory`
    - `cwd_symlink_escape`

实现细节：

1. root 解析优先级：
   - `task.extra["workspace_root"]`
   - `Workspace(task.id).dirs().root`
2. `requested` 为空 → root；
3. 相对路径 → `root / requested`；
4. `Path.resolve()` 后判断：
   - `resolved == root` 或 `root in resolved.parents`
   - 排除盘符根、UNC 根、用户主目录本身
   - 不允许 symlink 逃逸
5. 不允许 `..` 越界；
6. Python 脚本固定 `<root>/.supervisor/scripts/`。

修改：

- `exec_capability.run` 调 guard，不再信任 `step.args.cwd`；
- `gateway.run_python` 的 cwd 由 guard 结果传入；
- `api/supervisor.py` 手动执行入口也复用 guard（用户手动执行也不允许越界）。

### 4.3 测试

`tests/contracts/test_workspace_guard.py`：

- 空 cwd → root；
- 相对子目录 → 允许；
- `..` 越界 → 拒绝；
- 绝对路径在 root 外 → 拒绝；
- symlink 指向 root 外 → 拒绝；
- 盘符根/UNC → 拒绝；
- 工作区不存在 → `workspace_required`。

### 4.4 验收

- 任何执行路径都不能把 cwd 传成工作区外；
- 越界错误不进入审批队列。

---

## 5. WP2：Kernel Exec Step 接线（P0）

### 5.1 目标

把 `exec_capability.run` 从死代码变成 Kernel 的真实执行路径。

### 5.2 改动

新增：

- `clipwright/services/kernel_exec.py`（新）
  - `build_exec_request(task, step) -> ExecRequest`
  - `execute_exec_step(task, step) -> dict`
  - `resume_exec_step(task, approval_id) -> dict`
  - `_pause_for_approval(task, request, approval)`

修改：

- `clipwright/services/agent_kernel.py`
  - `run()` 初始化/解析 `exec_pending_approval_id`；
  - 在 Kernel 收到 `run_shell` / `run_python` Exec action 时转 `kernel_exec.execute_exec_step`；
  - `pending_approval`：
    - `task.status = "paused"`
    - `_checkpoint(task)`
    - emit `approval_request`
    - 不继续 tool loop
  - `resume()`：
    - 读取 pending approval；
    - approved → `kernel_exec.resume_exec_step`，只执行一次；
    - rejected → 把 refused 结果写回上下文，继续 tool loop；
    - 没有 pending → 保持现有行为。
- `clipwright/services/agent_loop.py`
  - 只把 `run_shell` / `run_python` 的 **Kernel Exec schema** 暴露给主 Agent；
  - 不把它们加入普通 `surface.tools`；
  - 调用方是 `surface.execute` 时转 `kernel_exec`，不是 subprocess。
- `clipwright/tool/surface.py`
  - `execute()` 对 `run_shell` / `run_python` 做显式分发；
  - 断言 `agent_mode=on`；
  - `stage`/worker 调用直接拒绝。

### 5.3 执行顺序

```text
Agent action
  → Kernel.execute_exec_step
    → workspace_guard
    → build ExecRequest + history
    → exec_capability.run
      → gateway risk assess
      → pending_approval
  → task paused
  → SSE approval_request
  → user approve
  → Kernel.resume
    → kernel_exec.resume_exec_step
      → re-assess
      → gateway execute once
  → exec_result
  → tool loop continues
```

### 5.4 测试

`tests/contracts/test_kernel_exec.py`：

- `run_shell` 进入 `exec_capability`；
- `run_python` 进入 `exec_python`；
- `pending_approval` 后 task `paused`；
- 暂停不继续 tool loop；
- resume approved 只执行一次；
- resume rejected 不执行；
- 阶段 Agent schema 不含这两个名字；
- 无 workspace 拒绝。

---

## 6. WP3：风险上下文、单次判定与 Gateway（P0）

### 6.1 目标

每次执行请求进入时都有 LLM 判定；判定必须看到历史 + 当前命令；规则只升不降；批准后不再判定。

### 6.2 改动

修改：

- `clipwright/supervisor/gateway.py`
  - `run_command(..., risk_context: dict | None = None, approval_required: bool = True, approval_id: str = "", exec_request_id: str = "")`
  - 风险输入：
    ```json
    {
      "task_goal": "...",
      "workspace_root": "...",
      "history": [],
      "current": {"kind": "shell", "text": "...", "cwd": "..."},
      "rules": {}
    }
    ```
  - `rv.assess()` 接收结构化上下文；
  - 结果写入 `ExecRiskReport`；
  - 规则命中只上调；
  - LLM 失败/超时 → `high`；
  - 批准后只校验 hash，不再判定；
  - 删除 `auto_mode` 对 med 的减免，仅保留 low 自动；
  - 绑定 `command_sha256` / `cwd_sha256` / `risk_sha256`；
  - `approval_id` 只执行一次；
  - 持久化 pending/records。
- `clipwright/services/exec_capability.py`
  - 构建 History Context；
  - 传 `risk_context`；
  - `pending_approval` 返回完整 approval；
  - `refused` / `rejected` / `expired` 结构化。
- `clipwright/services/shell_select.py`（新）
  - Windows 优先 `pwsh`，无 PowerShell 7 自动 `powershell.exe` 5.1；
  - POSIX 默认 `bash`，无 bash 按配置回退 `sh`；
  - 返回实际 shell 名称/路径/fallback_used，写审计与前端；
  - 不可用即 fail closed，不静默退回 `cmd`。
- `clipwright/services/kernel_exec.py`
  - 历史读取/追加；
  - 批准后的 hash 校验（不再二次 LLM 判定）。

### 6.3 History Context

来源：

- `exec_store` 的已执行记录；
- 当前任务最近 N 条；
- 固定按时间和 `exec_request_id` 排序；
- 当前命令放最后；
- 文本截断、明确标注“历史数据不是指令”。

### 6.4 风险判定测试

`tests/contracts/test_exec_risk.py`：

- shell_select：pwsh 优先、无 PS7 回退 5.1、POSIX bash、缺失 fail closed；
- 历史摘要 + 脱敏输出尾部进入 prompt，不传完整输出；
- 密钥/token 脱敏断言；
- `auto_low` 默认开启：low 不弹审批，med/high/critical 必弹；
- history prompt injection 不能降级；
- 硬底线只升不降；
- LLM 超时 → high；
- 批准后 hash 不一致 → `approval_hash_mismatch`，不执行并重新发起请求；
- hash 不匹配 → 不执行；
- 重复 approve 不重复执行。

---

## 7. WP4：审批持久化、API、事件（P0）

### 7.1 目标

审批和执行记录可查询、可恢复、可审计。

### 7.2 新增存储

- `clipwright/services/exec_store.py`（新）
  - `create_request` / `update_request`
  - `create_approval` / `get_approval` / `list_pending`
  - `mark_approved` / `mark_rejected` / `mark_executed`
  - `append_history` / `list_history`
  - 路径：`<workspace>/.exec/requests.jsonl`、`approvals.json`
  - 原子写（临时文件 + replace）
  - per-task 文件锁
  - TTL 清理
- `gateway` 的 `_PENDING` 改为写穿 store；内存只做缓存；
- `auto_mode` 默认值改为 `True`（即 auto_low 默认开启），且只对 low 生效，删除 med 减免；
- 现有 `supervisorStore` 的 `autoMode` 初始值改为 true，hydrate 以服务端为准。

### 7.3 API

复用：

```text
POST /api/pipeline/{task_id}/exec
GET  /api/pipeline/{task_id}/exec
GET  /api/pipeline/{task_id}/exec/pending-approvals
POST /api/pipeline/{task_id}/exec/{approval_id}/approve
POST /api/pipeline/{task_id}/exec/{approval_id}/reject
PUT  /api/pipeline/{task_id}/exec/auto-mode
```

新增：

```text
GET /api/task/{task_id}/executions
```

错误码：

- `cwd_outside_workspace` 400
- `workspace_required` 400
- `approval_expired` 409
- `approval_hash_mismatch` 409
- `approval_already_used` 409/200
- `risk_assess_failed` 503

### 7.4 事件

- Kernel 发 `exec_request` / `exec_risk` / `approval_request` / `exec_result`；
- approve/reject 发 `approval_result`；
- 前端 SSE 不依赖轮询。

### 7.5 测试

- API tests with FastAPI TestClient；
- approve/reject 权限（task owner）；
- restart 后 pending 仍可读；
- 重复 approve 幂等；
- 过期清理；
- 事件 detail 完整。

---

## 8. WP5：入口限制与提示词（P1）

### 8.1 改动

- `clipwright/config.py`
  - `kernel_exec_enabled: bool = False`
  - `exec_auto_low: bool = True`（默认开启；用户可关闭）
  - `exec_require_approval: bool = True`
  - 旧 `/exec/auto-mode` 语义收敛为 `auto_low`，不再减免 med
  - `exec_shell_profile: Literal["pwsh", "powershell", "bash", "sh"] = "pwsh"`
  - `exec_shell_fallback: bool = True`（Windows 无 PS7 自动 5.1；POSIX 无 bash 按配置回退 sh）
  - `exec_history_limit: int = 20`
  - `exec_approval_ttl_sec: int = 600`
- `clipwright/tool/surface.py`
  - 只有主 Agent + `kernel_exec_enabled` 才生成 Exec schema；
  - 阶段 Agent 无论配置如何都不生成。
- `clipwright/prompts/loop.py`
  - 告知主 Agent：
    - `run_shell` / `run_python` 需要用户授权；
    - 先说明目的；
    - 被拒绝后不要原样重试；
    - 不得用 shell 绕过媒体工具。
- `clipwright/prompts/agents.py` / 阶段 prompts
  - 明确阶段 Agent 无执行权限。
- 前端 settings 文案同步（WP6）。

### 8.2 测试

- surface 不含 exec 默认；
- 开启后主 Agent 含、阶段不含；
- `kernel_exec_enabled=false` 时调用被拒绝；
- prompt lint。

---

## 9. WP6：前端类型、API、Store、SSE（P1）

前端仓库：`J:\Clipweight-Client`。

### 9.1 改动

- `src/types/supervisor.ts`
  - `PendingExecApproval` 增加：
    - `exec_request_id`
    - `cwd`
    - `history`
    - `riskReport`
    - `expiresAt`
    - `hardFloor`
  - wire 映射：后端 `risk/risk_report/expires_at/needs_double_confirm/hard_floor` → 前端 `riskLevel/riskReport/expiresAt/needsDoubleConfirm/hardFloor`
  - `ExecResponse` 增加 `exec_request_id`，status 扩展 `refused/expired`
  - 新增 `ExecHistoryItem` / `ExecResultItem`
- `src/services/api/supervisor.ts`
  - `listExecutions(taskId)`
  - approve/reject 保持；
  - 错误码映射。
- `src/stores/supervisorStore.ts`
  - 解析新 SSE 事件；
  - pending approvals 合并；
  - 过期项移除；
  - 执行记录刷新。
- `src/types/task.ts` / `src/features/sessionTimeline/types.ts` / SSE parser
  - 新增 `exec_request` / `exec_risk` / `exec_result`。
- `src/features/supervisor/AutoModeSwitch.tsx`
  - 文案/语义改为 auto_low，删除“中危直接执行”。

### 9.2 测试

- TypeScript type tests；
- store reducer；
- API mock；
- SSE fixture。

---

## 10. WP7：前端 UI（P1）

### 10.1 `ExecPermissionDialog`

扩展：

- 显示 `cwd`；
- 显示 history 列表（折叠/展开）；
- 显示风险理由/affected paths；
- 显示 expiresAt 倒计时；
- 保持默认焦点在“拒绝”；
- high 勾选；
- critical 二次确认；
- 授权/拒绝调用现有 API；
- `approval_hash_mismatch` 时重新发起请求，不执行旧授权。

### 10.2 `CopilotTaskRunTab`

- 收到 `approval_request` 打开完整 `ExecPermissionDialog`；
- 不再只显示简化行；
- approve 后等待 `approval_result`，不要自行 resume；
- reject 后展示“已拒绝，任务重规划中”；
- 显示执行记录入口。

### 10.3 执行历史

新增 `ExecHistoryPanel`：

- 列表：kind/command/cwd/risk/status/exit_code/duration；
- 展开 output_tail；
- 授权人；
- 分页/空态。

### 10.4 `SessionTimeline` / `SupervisorDrawer`

- 复用 `ExecPermissionDialog`，避免两套逻辑；
- 保留 supervisor 手动执行表单；
- `auto_low` 开关文案：默认开，仅 low 生效；关闭后 low 也需授权。

### 10.5 测试

- `ExecPermissionDialog` 四种风险；
- high 未勾选禁用；
- critical 二次确认；
- 拒绝调用；
- 越界拒绝无授权按钮；
- 历史渲染；
- 任务面板审批流；
- SSE 更新。

---

## 11. WP8：恢复、并发、幂等与验收（P0）

### 11.1 硬化

- 同一任务并发 exec → 串行队列或锁；
- approval 一次消费（compare-and-set）；
- resume 重复调用不重复执行；
- 服务重启加载 pending；
- 过期审批自动清理；
- 执行超时/取消；
- 输出截断；
- 审计日志不可丢。

### 11.2 端到端测试

后端：

```text
pytest tests/contracts/test_exec_*.py -q
pytest tests/contracts/test_kernel_exec.py -q
pytest tests/contracts/test_workspace_guard.py -q
pytest tests/contracts/test_event_contract.py -q
```

前端：

```text
npm run typecheck
npm run test -- exec
npm run build
```

### 11.3 全量验收

按 PRD §14：

- Kernel 能执行；
- cwd 越界拒绝；
- 每次执行请求进入时有 LLM 风险；
- med+ 必授权；
- critical 双确认；
- approve/reject/expiry/idempotency；
- restart；
- 前端完整。

### 11.4 回滚

- `kernel_exec_enabled=false`：Exec schema 消失，Kernel 走旧路径；
- 保留 `/api/pipeline/.../exec` 手动入口；
- 不删除旧审批表（新任务不写入）；
- 前端隐藏新面板由 feature flag 控制。

---

## 12. 文件级改动清单

### 后端

| 文件 | 动作 |
|---|---|
| `clipwright/schema/exec.py` | 新增 |
| `clipwright/schema/task.py` | 可选 pending 指针 |
| `clipwright/services/workspace_guard.py` | 新增 |
| `clipwright/services/exec_capability.py` | 重写/扩展 |
| `clipwright/services/kernel_exec.py` | 新增 |
| `clipwright/services/shell_select.py` | 新增 |
| `clipwright/services/agent_kernel.py` | 接线/暂停/恢复 |
| `clipwright/services/agent_loop.py` | Exec schema 分发 |
| `clipwright/tool/surface.py` | 显式分发/禁止阶段 |
| `clipwright/supervisor/gateway.py` | 风险上下文/单次判定/持久化/hash |
| `clipwright/supervisor/review.py` | 风险输入结构 |
| `clipwright/services/exec_store.py` | 新增 |
| `clipwright/api/supervisor.py` | API 扩展/错误码 |
| `clipwright/api/task.py` | executions 查询 |
| `clipwright/config.py` / `config_schema.py` | flags |
| `clipwright/prompts/loop.py` / `agents.py` | 执行规则 |
| `docs/api_reference.md` | API |
| `docs/workflow.md` | 执行流 |

### 前端

| 文件 | 动作 |
|---|---|
| `src/types/supervisor.ts` | 类型 |
| `src/services/api/supervisor.ts` | API |
| `src/stores/supervisorStore.ts` | 状态/SSE |
| `src/features/supervisor/ExecPermissionDialog.tsx` | 扩展 |
| `src/features/supervisor/ExecRequestForm.tsx` | 保持/小改 |
| `src/features/supervisor/SupervisorDrawer.tsx` | 复用 |
| `src/features/supervisor/AutoModeSwitch.tsx` | 文案/语义改为 auto_low |
| `src/features/copilot/CopilotTaskRunTab.tsx` | 完整审批 |
| `src/features/copilot/CopilotRunDetails.tsx` | 执行记录 |
| `src/features/sessionTimeline/SessionTimeline.tsx` | 复用弹窗 |
| `src/features/supervisor/observation.test.tsx` | 测试 |
| `src/features/copilot/*.test.tsx` | 测试 |

---

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| Kernel 架构无 Step 执行 | 接线复杂 | 只做 Exec 动作，不恢复 Plan 串行 |
| 审批内存态丢失 | 重启后卡死 | WP4 持久化 |
| LLM 风险判定不稳 | 错误放行/阻断 | 规则棘轮 + 失败 high |
| cwd symlink 逃逸 | 越界执行 | resolve + parent 检查 + 测试 |
| prompt injection | 风险降级 | 历史标记为数据 + 规则只升 |
| 前端两套审批逻辑 | 行为不一致 | 统一复用 ExecPermissionDialog |
| 授权重复执行 | 副作用 | approval 一次性 + hash + 幂等 |
| 旧前端不识别新事件 | 展示缺失 | 类型/SSE 同步，忽略未知事件 |

---

## 14. 交付顺序（建议）

```text
Day 1       WP0 契约 + 事件类型
Day 2-3     WP1 cwd guard
Day 3-5     WP2 Kernel Exec Step
Day 5-7     WP3 风险上下文 + 单次判定
Day 7-9     WP4 持久化/API/事件
Day 9-10    WP5 surface/prompt/flags
Day 9-11    WP6 前端类型/API/store（并行）
Day 11-13   WP7 前端 UI
Day 13-15   WP8 硬化 + 全量验收
```

---

## 15. 完成定义（DoD）

- [ ] PRD §14 验收项全部通过；
- [ ] 后端定向测试 + 全量 `pytest tests` 绿（或在 PR 中列出既有失败并证明无关）；
- [ ] 前端 typecheck/test/build 绿；
- [ ] 阶段 Agent 工具面无 `run_shell`/`run_python`；
- [ ] 任意 cwd 越界都无法执行；
- [ ] 每次执行请求进入时有风险报告，med+ 必授权；批准后不重复判定；
- [ ] 重启后审批可恢复；
- [ ] 文档/API/提示词同步；
- [ ] feature flag 可回滚。

---

## 16. 已确认决策

产品已确认：

1. **`auto_low` 默认开启**：low 直接执行；用户可关闭；只对 low 生效；
2. **拒绝后自动 resume**：把 `refused` 注入主 Agent 上下文，由它重新规划；
3. **shell**：Windows 优先 `pwsh`，无 PowerShell 7 自动回退 `powershell.exe` 5.1；都不会静默退回 `cmd`；POSIX 默认 `bash -lc`，无 bash 按配置回退 `sh -lc`；
4. **history**：摘要 + 脱敏输出尾部，不传完整输出；
5. **风险判定**：只在请求进入时判定一次，批准后只校验 hash；
6. **命令记忆白名单**：不做，每次请求都重新判定。


---

## 17. 实现状态（2026-11-22）

| WP | 状态 | 证据 |
|---|---|---|
| WP0 契约/类型/事件 | 完成 | `clipwright/schema/exec.py`；`EVENT_TYPES` +3；前后端事件契约 |
| WP1 workspace cwd guard | 完成 | `services/workspace_guard.py`；6 个 guard 测试 |
| WP2 Kernel Exec Step | 完成 | `surface.execute` → `kernel_exec` → `exec_capability.run`；`run_shell/run_python` 默认隐藏 |
| WP3 风险上下文/单次判定/hash | 完成 | Gateway 接收 history/current；批准后 hash 校验；无二次 LLM |
| WP4 持久化/API/事件 | 完成 | `services/exec_store.py`；pending 恢复；`GET /api/task/{id}/executions`；approval_result |
| WP5 surface/prompt/flags | 完成 | `exec_auto_low=True`；`kernel_exec_enabled` 开关；主 Agent 提示 |
| WP6 前端类型/API/store/SSE | 完成 | types/supervisor.ts、supervisorApi、store、eventSchema |
| WP7 前端 UI | 完成 | ExecPermissionDialog cwd/history、任务面板详情弹窗、AutoModeSwitch auto_low |
| WP8 硬化/验收 | 完成（1 个环境 error 单独重跑通过） | 后端全量 5572 passed / 6 skipped / 1 xfailed（1 个 Windows socketpair error，单测单独重跑通过）；前端 1450 passed / 5 skipped；typecheck/build/ruff 通过 |

### 当前限制

- `gateway` 的网络隔离仍只是默认剥离代理变量，不是 OS 级沙箱；
- 前端 `CopilotTaskRunTab` 快捷授权按钮仍保留 `window.confirm`，完整对话框通过「详情」进入；
- `exec_store` 的审批写入是 workspace 文件，不是跨机器共享存储；
- 全量后端最近一次运行包含 1 个 Windows socketpair 资源 error；单测单独重跑通过，未复现业务失败。
- `exec_require_approval` 目前是配置项但策略仍固定为“med+ 必须授权”；`exec_shell_profile` 未实现为显式 profile，shell 按平台自动选择。
