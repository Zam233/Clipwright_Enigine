# 前端主 Agent 化适配清单（验收用）

> 用途：路线 C（`agent-native-prd-2026-10-17.md`）落地后，**逐项验证前端"先前使用 pipeline 的部分"是否已经适配**。
> 现在这份清单是**盘点 + 要求**，验收时逐行打勾并记录证据（截图/测试名）。
> 盘点依据：`src/services/api/pipeline.ts`、`src/stores/agentStore.ts`、`src/features/agent/*`、`src/pages/{HomePage,ExportPage,admin/PipelineAdminPage}.tsx`、`src/features/supervisor/*`。

## 0. 结论先行（盘点结果）

- 前端对"管线"的依赖集中在 **4 处**：`pipelineApi`（12 个方法）、`agentStore`（阶段/日志/建议状态机）、`AgentPanel`（SSE + 交互）、`Supervisor*`（消费 `/api/pipeline/{pid}/*`）。
- **没有任何一处直接读后端内部结构体**：都经 API/类型层，这是好消息——适配可以只改"数据形状映射层"，不用重写 UI。
- 真正的迁移成本在**语义**：`phase`（阶段）→ `plan step`；`logs`（trace 事件流）→ `events`（typed 事件流）；`suggestions`（自愈建议）→ `replan`。

## 1. 逐项适配清单

图例：**必须** = 主 agent 化上线前必须完成；**兼容** = 双轨期两套都能跑；**已就绪** = 现在就不用改。

| # | 模块 | 现在依赖什么 | C 之后必须变成什么 | 阶段 | 验收方式 |
|---|---|---|---|---|---|
| 1 | `services/api/pipeline.ts` | 12 个方法：`runAsync/run`、`status`、`result`、`trace`、`traceStreamUrl`、`retry`、`cancel`、`step`、`templates*`、`predict*` | 新增 `taskApi`（`create/status/events/eventsStream/pause/resume/cancel/inject/plan`）；`pipelineApi` 保留但标注"经典模式（将下线）" | C3 | 单测：新 API 客户端覆盖 8 个端点；`tsc` 无 `pipelineApi` 新引用 |
| 2 | `types/pipeline.ts` | `LogEntry`（`type: LogEventType` 枚举，含 `agent_start/agent_end/mg_*`）、`PipelinePhase`、`AgentStat`、`PipelineSummary` | 新增 `types/task.ts`：`Task`、`Plan`、`PlanStep`、`TaskEvent`（typed union）、`ToolCall`；保留旧类型一个版本 | C3 | `tsc`；事件类型断言测试（每种事件都能被前端渲染分支命中） |
| 3 | `stores/agentStore.ts` | `phase`（对应 6 阶段）、`logs[]`、`suggestions[]`、`agentStats`、`pipelineStartedAt` | 新增 `taskStore`：`task/plan/steps/events/cost`；`phase` 变为**派生值**（由 step 状态聚合），不再由后端阶段事件直给 | C3+C4 | store 单测：给定一串 `TaskEvent`，能算出 plan/step 状态与成本；不依赖 `phase` 字段 |
| 4 | `features/agent/AgentPanel.tsx` | `GET /trace/stream/{pid}`（EventSource）+ 日志过滤 + 重试按钮 + 建议面板 | 改用 `events/stream`（同源 SSE 契约，事件类型换成 TaskEvent）；重试按钮 → 「暂停/继续/注入」；建议面板 → 计划视图入口 | C4 | 组件测：mock SSE 推送 5 类事件后断言 UI；e2e：一条 task 从头跑完 |
| 5 | `features/agent/ReviewPanel.tsx` / `TimelineDiffView.tsx` | 读 `agentStore.suggestions`（自愈建议）与时间线快照 diff | `suggestions` 语义变为 `replan` 建议（来自 Kernel 的 reflect）；diff 组件保留（产物仍是 timeline） | C4 | 组件测：replan 建议渲染 + 采纳后计划变更可见 |
| 6 | `features/supervisor/useSupervisorEvents.ts` | 复用 `/trace/stream/{pid}`，按 `ledger_entry/attribution/diag_report/dispatch/subagent_*/exec` 分流 | 增加 `task_event` 分支（plan/step/tool_call/approval/artifact）；保留现有分支一个版本 | C3 | 单测：`toSupervisorEvent` 对新旧事件都能映射；未知类型丢弃 |
| 7 | `features/supervisor/*`（Drawer/DecisionTimeline/DiagnosisCard/ExecLogList/SubagentTree/…） | 消费 `/api/pipeline/{pid}/{ledger,decisions,subagents,exec,…}` | 「决策记录」段改为「计划变更」；诊断书改为「Step 归因」；执行日志/子代理/授权保留（v1.5 资产原样复用） | C4 | 组件测 + 人工核对：抽屉里能看到 plan/replan/step 归因 |
| 8 | `pages/HomePage.tsx` | `pipelineApi.runAsync` 起任务 | 走 `taskApi.create`（`plan_only` 默认开 → 先出计划页） | C3+C4 | e2e：首页输入一句话 → 出现计划 → 确认后开跑 |
| 9 | `pages/ExportPage.tsx` | 渲染队列 SSE（`/api/render/queue/stream`） | **不变**（渲染仍是独立队列，不属于 agent 编排） | — | 已就绪 |
| 10 | `pages/admin/PipelineAdminPage.tsx` | `GET /api/pipeline/runs` + trace JSON 检视 | 增加 `GET /api/task`（任务列表）；保留 runs 作为 legacy 视图 | C6 | 手动验证两套列表都能开 |
| 11 | `layouts/EditorLayout.tsx` | `<SupervisorLauncher />`（浮动入口） | 增加「计划」入口；浮动入口保留（授权/待审徽标仍需常驻） | C4 | 组件测：两个入口都能开面板 |
| 12 | 全局提示 | 无 | **配置建议条**：agent 想改 Persona/路由时只出现「建议」，点击才落盘（PRD Q2） | C4 | 组件测：建议条出现 → 点确认才调 settings API |
| 13 | 错误与降级文案 | 按阶段报错（`AgentPanel` 显示 `agent_end`/`error`） | 按 Step 报错 + 收尾三件套（结论/已完成/未完成原因） | C4 | 构造超限任务，前端展示三件套 |

## 2. 双轨期（C3）必须同时成立的三件事

1. `AGENT_MODE=off` 时前端走老路径，**行为与今天一致**（PRD A13：timeline 逐字节比对，由后端保证，前端只需保证不并发订阅两套流）。
2. 两个入口互斥展示：经典模式不显示「计划」入口，agent 模式不显示「重试 Agent 按钮」。
3. SSE 只订阅一条：`/trace/stream` 与 `/events/stream` 不得同时开（否则事件重复、成本翻倍）。

## 3. 基线审计（2026-10-17，C0 结束时的事实）

> 这一节是**当前事实**，不是目标。结论：**13 项里只有 1 项已就绪，其余 12 项尚未适配**——
> 因为主 agent 化目前只到 C0（后端三件套），前端还没动。这份审计给 C3/C4 一个可对照的起点。

| # | 模块 | 是否已适配 | 证据 |
|---|---|---|---|
| 1 | `services/api/pipeline.ts` | ❌ 未适配 | 仍是 12 个 pipeline 方法；无 `taskApi`（`src/services/api/pipeline.ts:1-124`） |
| 2 | `types/pipeline.ts` | ❌ 未适配 | 事件类型仍是 `mag_start/agent_start/mg_*` 等管线语义；无 `TaskEvent`（`src/types/pipeline.ts:88-115`） |
| 3 | `stores/agentStore.ts` | ❌ 未适配 | `phase`/`logs`/`suggestions` 直接来自管线事件（`src/stores/agentStore.ts:42-60`） |
| 4 | `features/agent/AgentPanel.tsx` | ❌ 未适配 | 直接开 `EventSource(pipelineApi.getTraceStreamUrl(pid))`、消费 `clearSuggestions`（`AgentPanel.tsx:948,963`） |
| 5 | `ReviewPanel.tsx` / `TimelineDiffView.tsx` | ❌ 未适配 | 读 `agentStore.suggestions`（管线自愈建议语义） |
| 6 | `features/supervisor/useSupervisorEvents.ts` | ⚠️ 部分 | 已按 type 分流（v1.5 资产），但只认 `ledger_entry/attribution/...`，无 `task_event`（`useSupervisorEvents.ts:26-40`） |
| 7 | `features/supervisor/*` 抽屉 | ⚠️ 部分 | 决策/诊断/执行/子代理面板可用（v1.5 资产可复用），但「计划变更」「Step 归因」没有 |
| 8 | `pages/HomePage.tsx` | ❌ 未适配 | 只调 `predictScript`；任务创建仍走管线入口（`HomePage.tsx:190`） |
| 9 | `pages/ExportPage.tsx` | ✅ **已就绪** | 渲染队列 SSE 独立于 agent 编排，**不需要改** |
| 10 | `pages/admin/PipelineAdminPage.tsx` | ❌ 未适配 | 只读 `GET /api/pipeline/runs`，无任务列表视图 |
| 11 | `layouts/EditorLayout.tsx` | ⚠️ 部分 | 只挂了 `<SupervisorLauncher />`，无「计划」入口（`EditorLayout.tsx:319`） |
| 12 | 配置建议条（Q2） | ❌ 缺失 | 无任何"agent 建议改配置"的 UI |
| 13 | 错误与降级文案 | ❌ 未适配 | 按 `agent_end`/`error` 事件报错，没有收尾三件套展示 |

**结论**：现在去跑"前端验收"只会得到 1/13 通过。正确的顺序是先做完 C3（后端双轨 + Task API）
与 C4（前端计划视图），再按本表逐项打勾。本文件 §3 到时替换为「验收记录」。

> 验收口径：**每一项必须有证据**（测试文件:用例名 / e2e 录屏 / 截图）。没有证据视为未通过——这是上一轮"接线没接上却被当成做完"的教训。

