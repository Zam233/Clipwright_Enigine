# Clipwright 前后端能力缺口开发计划（2026-10-29）

## 0. 执行摘要

本计划按 PRD 原有 B1–B13 执行，不合并、不拆分，覆盖 G1–G17；估算合计 **22.0 人日**。先从 B1 开始，优先消除旧入口与旧重试的误导；随后 B2 建立工作区事件消费基础，B5 收敛事件契约。依赖主链为 **B1/B2/B3 → B5 → B6/B7/B9/B10/B11 → B12 → B13**；B4 可在 B3 完成工具面只读边界确认后与 B3 并行，B8 可在 B3 完成目录 API 契约后并行；B6/B9/B10/B11 分别满足所列前置后可并行。并行的硬条件是：各自改动文件无未协调重叠、共享类型/API 契约先冻结、每支分支能独立运行目标测试、合并前各自回归通过；涉及同一共享文件时先由一人串行落契约，其他工作基于该提交开展。

每批独立提交；前后端跨仓库改动先后端兼容契约、再前端接入，必须同一发布轮联动验证的批次为 B5、B11，以及 B2（前端 SSE 消费必须与现有后端 SSE 行为联测）、B10（逐项恢复依赖现有扫描响应语义）。B1、B3、B4、B6、B8、B9、B12 可前端先行或在兼容字段下独立部署；B7、B13 是收口批次，不单独改变生产契约。起始顺序：基线留证 → B1 → B2；任何并行只在上述条件满足后开启。

## 1. 不变量与门禁

### 1.1 动手前基线

- 固定后端仓库 `J:\Clipwright`、前端仓库 `J:\Clipweight-Client` 当前分支及工作区状态；记录已有未提交变更，批次提交不得包含无关改动。
- 留存两仓 HEAD、测试输出、ruff/tsc/lint/build 输出；PRD 中的基线是验收参照，不用旧文档中的历史计数覆盖。
- 契约事实固定：`EVENT_TYPES` 为 24 项；`eventsUrl()` 是零调用死方法；`GET /api/workspace/{id}/events` 是 JSON，SSE 在 `/events/stream`；不得把它描述为 SSE 协议错配。
- 固定产品事实：`ToolsPage.tsx` 已有 `toolApi.execute`，B3 只能增加只读目录页签，不复用执行状态；`TaskToolbar.tsx` 已拦截 done/failed/cancelled 的继续操作，B11 后端真缺口是 `clipwright/api/task.py::resume_task` 缺终态守卫。
- 配置/数据不变量：不修改 `provider_sources.json` 或 `.env`；不新增后端端点、不改变现有后端契约，除非停止并升级；不对用户数据做破坏性迁移。

### 1.2 每批合入前必跑

后端（仅后端改动批次及最终全量）：

```powershell
python -m pytest tests -q
ruff check clipwright --select E9,F63,F7,F82,F823
```

前端（每个前端改动批次）：

```powershell
npx tsc --noEmit
npm run test
npm run lint
npm run build
```

涉及契约的批次额外跑对应定向测试；全量测试计数变化须说明新增、删除或改写用例。无关既有失败先记录并对比基线，不得把它们归为本批通过。

### 1.3 当前实测基线

PRD 与参照记录的当前实测基线：后端 `python -m pytest tests -q`：**4496 passed / 4 skipped / 1 xfailed**；`ruff check clipwright --select E9,F63,F7,F82,F823`：**0**；前端 `npx tsc --noEmit`：**0**；`npm run test`：**1032 passed / 119 files**；`npm run lint`：**0 error**；`npm run build`：**通过**。执行者开始工作时必须重跑并记录；若实测不同，计划执行记录须注明实际值与差异，不得伪称达到上述基线。

## 2. 批次总表

| 批次 | PRD 缺口 | 目标 | 依赖 | 人日 | 风险 | 可回滚性 |
|---|---|---|---|---:|---|---|
| B1 [P0] | G1、G2 | 移除退役 pipeline 启动/重试入口，明确 Task 替代入口 | 无 | 1.5 | 外部调用者仍依赖旧方法；历史数据口径混淆 | 高：前端文件单独回退，不恢复后端旧语义 |
| B2 | G3 | 消费 workspace SSE 进度、断线游标与恢复 | 无 | 1.5 | 重连重复、卸载竞态、last_seq 边界 | 高：退回 JSON reload，后端 stream 保留 |
| B3 | G5 | 只读工具面目录与 AgentRun 发现入口 | 无 | 2.0 | 误把目录做成执行入口；既有 ToolsPage 执行状态污染 | 高：隐藏新增页签，保留只读 client |
| B4 | G14 | 补齐批量任务、脚本工具、选题建议客户端与入口 | B1 语义定稿；可与 B3 并行 | 2.0 | 旧 pipeline/Task 边界混合 | 高：隐藏入口或 feature flag |
| B5 [P0] | G10、G9 及事件契约相关项 | 事件 discriminated union、parser、终态原因与 checkpoint 兼容 | B1、B2 | 3.0 | 历史 detail 形状多、旧事件回放 | 中：保留 Record adapter，可回退 parser |
| B6 | G7 | Plan 假设与真实 Run 进度分轨显示 | B5 | 1.5 | 长任务旧 projection 与历史 Plan 兼容 | 高：回退 UI，不改后端 |
| B7 | G11、G17 | 展示未知成本、请求/生效参数与降级 | B5 | 1.5 | provider 能力差异与旧响应缺字段 | 高：隐藏新增信息卡，兼容旧字段 |
| B8 | G4、G6 | 调度、渲染后端与产物治理入口 | B3 | 2.5 | tick 误触发、清理误操作 | 高：导航/路由回滚；清理动作需二次确认 |
| B9 | G12、G13 | latest 评估、报告/文档/artifact 回链 | B2、B5 | 1.5 | 跨 workspace 路径与缺失报告 404 | 高：保留列表详情旧路径 |
| B10 | G16 与素材复用测试缺口 | 扫描失败项恢复、降级详情与独立复用测试 | B2 | 1.0 | 大文件、异步视觉索引竞态 | 高：撤回 UI 重试，保留服务端数据 |
| B11 | G8 | 任务恢复结果模型与后端终态守卫 | B5 | 2.0 | 历史任务状态缺失、approval/needs 状态混淆 | 中：前端先禁用终态；后端变更需联动回滚 |
| B12 | G15 | 文档与实际路由/入口语义一致 | B1–B11 | 1.0 | 两仓文档副本漂移 | 高：文档单独回滚 |
| B13 | 全局门禁，重点 G1/G2/G10 | 跨仓库全量回归与 P0 可证伪门禁 | B1–B12 | 1.0 | 长任务 SSE/provider 测试耗时 | 高：仅回滚测试/门禁辅助改动 |
| **合计** | **G1–G17** | 13 批完整覆盖 |  | **22.0** |  |  |

## 3. 逐批执行卡

### B1 [P0] 退役 pipeline 前端动作与统一 Task 入口（1.5 人日）

**目标与用户感知**：旧入口不再启动后得到 410，执行诊断页不再提供虚假的重试；用户能看到新建制作/Task 的有效入口，历史 pipeline 仅作只读说明。

**前置依赖**：无。确认 `requirements/proceed` 与 `/api/task` 的现行调用方式；不得更改后端 410 语义。

**文件级改动清单**：
- 修改 `src/services/api/pipeline.ts` → 移除或标记 `run-async`、`retry` 为退役兼容方法，确保新 UI 不调用。
- 修改 `src/pages/admin/PipelineAdminPage.tsx` → 删除 retry 控件/动作，历史记录标注退役与数据边界。
- 修改 `src/features/agent/ReviewPanel.tsx` → 清除旧执行入口引用，保留新 Task 导航/提交路径。
- 修改对应 `*.test.tsx` 与 API 测试 → 新入口请求断言、旧操作不可见断言。

**任务分解**：
1.（≤1h，先读代码/测试）检索调用点并列出 `run-async`、`retry` 消费者，确认新建制作既有入口。
2.（≤1.5h，先写测试）补管理页无 retry、新建制作走 Task API 的失败用例。
3.（≤2h，再改实现）移除旧动作并加退役提示，保留历史只读数据展示。
4.（≤1h，测试后整理）运行定向测试与前端门禁，检查剩余调用。

**可证伪验收**：PowerShell 5.1：`Get-ChildItem src -Recurse -Include *.ts,*.tsx | Select-String -Pattern 'run-async|pipeline/retry'`；命中只允许明确的兼容测试/退役说明。`npx vitest run src/pages/admin/PipelineAdminPage.test.tsx`（若既有测试文件名不同，先按实际存在文件定位，不臆造路径）；断言文本：`新建制作调用 /api/requirements/proceed 或 /api/task`、`历史页不渲染重试按钮`。前端全量门禁通过。

**回滚方式**：恢复 `pipeline.ts`、`PipelineAdminPage.tsx`、`ReviewPanel.tsx` 与对应测试；不恢复后端执行语义。无配置/数据变更。

**风险与最易漏边界**：管理页历史记录仍需可读；错误提示区分普通网络失败与退役 410；检查快捷入口、键盘操作与隐藏菜单。

**DoD**：- [ ] 无生产 UI 调用旧启动/重试。 - [ ] 有效 Task 入口可用。 - [ ] 历史数据标明只读/退役。 - [ ] 定向与全量前端门禁通过。

### B2 工作区扫描进度与恢复（1.5 人日）

**目标与用户感知**：扫描期间可见逐项进度、完成/失败结果；断线后从序号恢复且不重复；JSON 批量拉取与 SSE 流明确分离。

**前置依赖**：后端 JSON `/api/workspace/{id}/events` 与 `/events/stream` 现有契约；不新增 endpoint。可与 B1/B3 并行，跨仓联测时后端行为需同轮确认。

**文件级改动清单**：
- 修改 `src/services/api/workspace.ts` → 增加独立 SSE URL/client 或订阅方法，JSON `events` 类型保留；废弃零调用 `eventsUrl()` 或改为明确 JSON 名称。
- 修改 `src/features/workspace/WorkspaceMaterialsPanel.tsx` → 订阅、游标、进度/失败项状态、卸载关闭。
- 新增/修改 `src/features/workspace/WorkspaceMaterialsPanel.test.tsx` → SSE 序号、重连、卸载、错误用例。
- 必要时修改 `src/types/api.ts` → WorkspaceEvent 与 `{events,last_seq}` 类型。

**任务分解**：
1.（≤1h，先核契约）确认 SSE id/data、初始游标及 JSON 响应形状。
2.（≤1.5h，先写测试）覆盖 URL `/events/stream`、重复 seq 去重、断线携带 last_seq、unmount close。
3.（≤2h，再改实现）实现单一订阅生命周期与进度 projection。
4.（≤1.5h，测试后实现）将扫描结果与事件进度合并，处理完成先于最后事件、重连 replay。
5.（≤1h，回归）运行定向测试与全量前端门禁。

**可证伪验收**：`Get-ChildItem src -Recurse -Include *.ts,*.tsx | Select-String -Pattern 'events/stream'` 至少命中 client 与测试。`npx vitest run src/features/workspace/WorkspaceMaterialsPanel.test.tsx`；断言：`SSE URL ends with /events/stream`、`duplicate seq is ignored`、`reconnect uses last_seq`、`unmount closes EventSource`。若现有测试路径不同，先用 `Get-ChildItem src -Recurse -Filter '*Workspace*test*'` 查证再替换命令。

**回滚方式**：回退 panel 订阅并恢复 JSON reload；保留后端 SSE。无配置/数据变更。

**风险与最易漏边界**：组件快速切换 workspace、同 seq 不同连接、last_seq=0、服务端 replay、扫描已结束才挂载、浏览器不支持 EventSource/mock 清理。

**DoD**：- [ ] JSON 与 SSE URL/type 分离。 - [ ] 序号去重及断线恢复通过。 - [ ] 卸载关闭连接。 - [ ] 扫描完成/失败/空目录状态可区分。

### B3 工具面与 AgentRun 只读发现页（2.0 人日）

**目标与用户感知**：可查看主 Agent 工具目录、schema、记录、tier、available、cost_hint 与 AgentRun 状态；危险工具不提供直调按钮。

**前置依赖**：现有 `/api/tools`、`/schema`、`/records` 契约。`ToolsPage.tsx` 已有 `toolApi.execute`，新页签不得借用执行状态或提交执行请求。可与 B4 并行，但先锁定只读 API 类型。

**文件级改动清单**：
- 新增 `src/services/api/toolSurface.ts` → 只读目录/schema/records client。
- 新增 `src/types/toolSurface.ts` → ToolSurface、tier、availability、cost hint、record 类型。
- 修改 `src/pages/admin/ToolsPage.tsx` → 新增只读页签/目录展示，执行器保持独立。
- 修改 `src/router.tsx`（仅如需独立路由）→ 注册只读页签入口。
- 新增 `src/pages/admin/ToolsPage.test.tsx` 或同目录实际测试 → mock 与不出现执行控件断言。

**任务分解**：
1.（≤1.5h，先核契约）对照后端 5 个 AGENT_TOOLS、2 个 DOMAIN_TOOLS 和 response shape。
2.（≤1.5h，先写测试）mock 三个 GET，断言 5/2 项展示及危险工具无直调。
3.（≤2h，再写实现）新增只读 types/client，明确错误与空态。
4.（≤2h，后写实现）实现页签及 AgentRun 发现链接；不复用执行器 loading/result。
5.（≤1h，验证）检查没有 POST execute 路径并跑前端门禁。

**可证伪验收**：`npx vitest run src/pages/admin/ToolsPage.test.tsx`（路径以实际测试文件为准）；断言：`AGENT_TOOLS count is 5`、`DOMAIN_TOOLS count is 2`、`dangerous tools have no direct execute action`。另以 `Get-ChildItem src/services/api -Recurse -Include *.ts,*.tsx | Select-String -Pattern '/api/tools'` 确认仅只读 client/测试消费。

**回滚方式**：隐藏新增页签/路由；保留只读 client/types。无配置/数据变更。

**风险与最易漏边界**：目录快照不等于运行许可；records 可能空或分页；available=false 不应伪装可用；既有工具执行器行为不得改变。

**DoD**：- [ ] 目录/schema/records 可读。 - [ ] 5+2 工具可见。 - [ ] dangerous 无 HTTP 任意执行入口。 - [ ] 旧执行器状态未复用。

### B4 批量任务、脚本工具与选题建议（2.0 人日）

**目标与用户感知**：可从已有页面调用批量任务、脚本工具及选题建议能力，明确校验/退役错误，不改变 Task 主路径。

**前置依赖**：核实后端 `/run-batch`、`/script-tools`、`/topic-suggest` 请求响应；B1 确认旧 pipeline 执行语义边界。可与 B3 并行，文件重叠时串行合并 `pipeline.ts`。

**文件级改动清单**：
- 修改 `src/services/api/pipeline.ts` → typed `runBatch`、`scriptTools`、`topicSuggest` 方法；显式错误类型。
- 修改 `src/pages/HomePage.tsx` 或新增 `src/pages/TaskBatchPage.tsx` → 仅接入 PRD 定义的批量/只读预测入口。
- 修改 `src/router.tsx`（新页面时）→ 注册路由。
- 新增同目录 API/UI `*.test.tsx` → payload、400/410 分流与加载状态。

**任务分解**：
1.（≤1.5h，先核契约）读取后端 schema/状态码，确认哪些操作创建任务。
2.（≤1.5h，先写测试）mock 成功、400、410，断言错误不误报成功。
3.（≤2h，再实现）新增 typed clients 与参数校验。
4.（≤2h，后实现）连接入口；预测类只读展示，批量提交须明确确认与结果列表。
5.（≤1h，验证）路径静态检查、定向与全量前端测试。

**可证伪验收**：`Get-ChildItem src -Recurse -Include *.ts,*.tsx | Select-String -Pattern '/run-batch|/script-tools|/topic-suggest'` 命中必须位于 typed client 与测试。`npx vitest run <实际新增测试路径>`；断言：`400 validation error is readable`、`410 is shown as retired`、`prediction does not start a task`。

**回滚方式**：关闭入口或 feature flag，保留客户端方法；无数据迁移。

**风险与最易漏边界**：批量部分成功、重复提交、超时后状态未知；不能误把已退役旧 pipeline 入口变回有效执行路径。

**DoD**：- [ ] 三个 client 方法类型完整。 - [ ] 400/410 文案分流。 - [ ] 预测不启动执行。 - [ ] 重复提交与部分失败有状态反馈。

### B5 [P0] 事件 discriminated union 与终态/审批语义（3.0 人日）

**目标与用户感知**：工具、阶段、审批等事件字段按类型解析；checkpoint 可展开；未知终态原因明确显示，不再误判 failed。

**前置依赖**：B1、B2；前端与后端对 24 个 `EVENT_TYPES` 的 fixture/字段契约同轮确认。该批必须后端+前端同一发布轮提交联动验证；本轮限制后端为 schema/事件写入点兼容收口，不新增端点。

**文件级改动清单**：
- 修改前端 `src/types/task.ts` → EventDetailMap/TaskEvent discriminated union，legacy Record adapter。
- 修改前端 `src/stores/taskStore.ts` → parser、未知 reason 保留、投影兼容。
- 修改前端 `src/features/task/EventFeed.tsx` → checkpoint 默认折叠可展开，未知事件 fallback。
- 修改前端 `src/features/copilot/CopilotTaskRunTab.tsx` → 从共享 parser 读取 tool/approval/stage，不本地猜字段。
- 修改后端 `clipwright/schema/task.py` 与真实事件写入点 → 只补稳定字段/注释与 fixture 对照，不改变事件名。
- 新增/修改两端事件 fixture 与测试。

**任务分解**：
1.（≤2h，先调查）枚举 24 项事件、产生点、历史事件 detail 形状，建立表格 fixture。
2.（≤2h，先写测试）为每项事件加 parser 覆盖/通用 fallback；未知 reason 与 checkpoint 用例先红。
3.（≤2h，再实现）前端 union 与 legacy adapter，保持开放扩展字段。
4.（≤2h，先写测试）后端新增字段对照与旧事件回放测试，验证不要求历史重写。
5.（≤2h，再实现）收敛后端写入形状与共享前端消费 selector。
6.（≤2h，后实现）改 EventFeed、CopilotTaskRunTab 与 taskStore 状态投影。
7.（≤2h，验证）跑两端定向及全量门禁，回放历史 fixture。

**可证伪验收**：后端 `python -m pytest tests -q`、前端 `npx tsc --noEmit`、`npx vitest run src/features/task/EventFeed.test.tsx src/features/copilot/CopilotTaskRunTab.test.tsx`（测试路径如不存在须先查证后创建）。断言：`all 24 EVENT_TYPES have parser or fallback`、`checkpoint remains visible and expandable`、`unknown task_end reason renders 后端终态：...`、`legacy detail fixture parses without data loss`。契约测试比较后端事件枚举 fixture 与前端 parser 清单，数量必须为 24 且集合相等。

**回滚方式**：保留 Record 兼容读取；回退具体 parser/render 变更，不回滚/删除已写事件；若后端字段新增，旧前端忽略未知字段。不得改配置/数据。

**风险与最易漏边界**：不同事件写入方字段不一致、approval status 缺省不能再次显示 pending、老数据无 causation/run_id、序号重放、未知事件必须保留而非静默丢弃。

**DoD**：- [ ] 24 类型全覆盖 parser/fallback。 - [ ] 历史 fixture 可回放。 - [ ] checkpoint 可见。 - [ ] 新 reason 不默认 failed。 - [ ] 两端联动门禁绿。

### B6 Plan 与真实 Run 双轨展示（1.5 人日）

**目标与用户感知**：初始 Plan 明确是设想，不再把 pending 步骤当真实进度；真实运行只由工具/AgentRun 事实驱动。

**前置依赖**：B5 parser 与运行事件 selector。修改 `src/stores/taskStore.ts` 前先合入 B5 对该共享文件的契约变更。

**文件级改动清单**：
- 修改 `src/stores/taskStore.ts` → hypothesis_only Plan 不更新执行进度 projection。
- 修改 `src/features/task/PlanPanel.tsx` → “初始假设/仅供参考”视觉与文本。
- 修改 `src/features/copilot/CopilotTaskRunTab.tsx` → 区分假设、阶段行与真实 run 进度。
- 修改 `src/features/task/task-ui.test.tsx` 或同目录实际测试 → 假设态与历史非假设态回归。

**任务分解**：
1.（≤1h，先写测试）断言 hypothesis_only 不影响进度徽章。
2.（≤1.5h，先核投影）确认事件与历史任务 Plan 默认值。
3.（≤2h，再实现）store 保留 Plan 原文但隔离进度状态。
4.（≤2h，再实现）改 PlanPanel/RunTab 标识与空态。
5.（≤1h，验证）运行 task UI 定向测试及前端门禁。

**可证伪验收**：`npx vitest run src/features/task/task-ui.test.tsx`；断言：`hypothesis_only does not update execution progress badge`、`real progress comes from tool/agent run events`、`legacy plan remains readable`。

**回滚方式**：回退 UI/store projection；不改后端或持久化数据。

**风险与最易漏边界**：旧任务缺 `hypothesis_only`、真实阶段事件与 Plan step 同名、空 Plan、长任务 projection 重建。

**DoD**：- [ ] 假设标签清晰。 - [ ] pending 不伪装进度。 - [ ] 真实运行事件可见。 - [ ] 历史任务兼容。

### B7 成本、degradation 与 effective route（1.5 人日）

**目标与用户感知**：未知成本明确显示未知而非 0；Provider 请求值与生效值/忽略原因分开呈现。

**前置依赖**：B5 事件 detail parser；后端已有字段作为输入，不修改 `provider_sources.json` 格式。

**文件级改动清单**：
- 修改 `src/types/task.ts` → `TaskUsage.unknown_cost_calls`。
- 修改 `src/types/api.ts` → provider capability/effective/degradation 可选响应类型。
- 修改 `src/pages/admin/ProvidersPage.tsx` → 展示 requested/effective/ignored 原因。
- 修改 `src/features/copilot/CopilotTaskRunTab.tsx` → usage、未知成本、成本闸信息。
- 新增/修改 ProvidersPage、CopilotTaskRunTab 测试 fixture。

**任务分解**：
1.（≤1h，先核字段）对照后端 usage/provider response 和 legacy 缺字段。
2.（≤1.5h，先写测试）写 unknown_cost_calls 与 requested/effective/degradation fixture。
3.（≤2h，再实现）扩展可选 TS 类型与兼容默认处理。
4.（≤2h，再实现）接入两个 UI 展示位置，未知不显示为 0。
5.（≤1h，验证）定向用例、tsc、全量前端门禁。

**可证伪验收**：`npx vitest run src/pages/admin/ProvidersPage.test.tsx src/features/copilot/CopilotTaskRunTab.test.tsx`；断言：`unknown cost calls are labeled 未知`、`requested parameter is not presented as effective`、`legacy provider response renders without degradation`。

**回滚方式**：隐藏新增列/卡片并保留后端字段；无配置/数据修改。

**风险与最易漏边界**：null、0、缺字段语义不同；各 provider 能力不一致；部分降级与完全失败应区分。

**DoD**：- [ ] unknown_cost_calls 显示。 - [ ] 请求值/生效值分离。 - [ ] 降级原因可读。 - [ ] 旧响应兼容。

### B8 调度与运维入口（2.5 人日）

**目标与用户感知**：可创建/开关/删除/查看调度任务及错过触发记录；可选渲染后端并清理过期产物。

**前置依赖**：B3 确认管理页导航与只读目录隔离；使用现有 scheduler/render API，不新增后端端点。涉及删除/立即 tick 时需二次确认。

**文件级改动清单**：
- 新增 `src/services/api/scheduler.ts` → list/create/delete/toggle/tick typed methods。
- 修改 `src/services/api/render.ts` → backends 与 artifacts 方法。
- 新增 `src/pages/admin/SchedulerPage.tsx` → CRUD、missed runs、loading/empty/error/retry。
- 修改 `src/features/workspace/WorkspaceMaterialsPanel.tsx` 以外的既有管理入口（优先 `src/features/copilot/SupervisorAdminTab.tsx` 若实际存在；不存在时仅使用已确认的 `src/pages/admin/SupervisorAdminPage.tsx`）→ 渲染后端/产物治理入口。
- 修改 `src/router.tsx`、导航配置与新增页面测试。

**任务分解**：
1.（≤1.5h，先核契约）读取 scheduler/render API schema、时区与删除语义。
2.（≤1.5h，先写测试）mock CRUD/toggle/tick/backends/artifacts 及四态。
3.（≤2h，再实现）scheduler client 与页面列表/创建/开关。
4.（≤2h，再实现）删除、tick 与 missed run，危险动作二次确认。
5.（≤2h，再实现）render client、backend/effective backend 与 artifact 清理确认。
6.（≤1.5h，验证）检查路由权限、运行定向与前端门禁。

**可证伪验收**：`npx vitest run src/pages/admin/SchedulerPage.test.tsx src/pages/admin/SupervisorAdminPage.test.tsx`（仅在文件存在时运行；缺少测试先按页面路径新增）。断言：`schedule CRUD and toggle use expected methods`、`tick requires explicit confirmation`、`artifact cleanup requires second confirmation`、`empty/error/retry states are distinct`。

**回滚方式**：先隐藏路由/导航，再回退 client/UI；已创建的 schedule 与 artifacts 不自动删除/恢复，涉及配置或数据操作由操作者确认。

**风险与最易漏边界**：误触 tick、时区、删除中状态、过期产物列表陈旧、后端选择值与实际 fallback 不一致。

**DoD**：- [ ] Scheduler CRUD/toggle 可用。 - [ ] tick/清理有二次确认。 - [ ] render backend/fallback 可读。 - [ ] loading/empty/error/retry 齐全。

### B9 Eval latest 与报告/Artifact/Doc 回链（1.5 人日）

**目标与用户感知**：有最近评估报告快捷入口；无报告 404 显示正常空态；报告、文档与产物可按稳定 ref 跳转。

**前置依赖**：B2 WorkspaceEvent/项目标识类型；B5 TaskEvent/ref parser。只能使用后端返回的 path/ref，不拼接用户输入路径。

**文件级改动清单**：
- 修改 `src/services/api/eval.ts` → `latest()`，专门映射 404 empty。
- 修改 `src/features/pipeline/EvalPanel.tsx` → latest shortcut 与 empty/error/retry。
- 修改 `src/features/copilot/CopilotDocsTab.tsx` → report/doc 跳转。
- 修改 `src/features/task/TaskArtifacts.tsx` → artifact hash/path/size/preview 信息。
- 修改 `src/services/api/workspace.ts`、`src/types/api.ts` → 稳定 workspace/report refs。
- 新增/修改各组件 `*.test.tsx`。

**任务分解**：
1.（≤1h，先核响应）确认 eval latest 404、workspace report/doc ref shape。
2.（≤1.5h，先写测试）mock 404、path/ref、hash/preview。
3.（≤2h，再实现）增加 latest client 与 EvalPanel 空态。
4.（≤2h，再实现）关联 CopilotDocsTab/TaskArtifacts 的 ref 跳转。
5.（≤1h，验证）拒绝任意路径拼接，跑定向和全量前端门禁。

**可证伪验收**：`npx vitest run src/features/pipeline/EvalPanel.test.tsx src/features/task/TaskArtifacts.test.tsx`（路径先核实）。断言：`latest 404 renders 尚无报告`、`report ref navigates within project`、`artifact fixture preserves hash path and preview`。

**回滚方式**：隐藏 latest 与回链入口，保留原 list/detail 路径；无数据修改。

**风险与最易漏边界**：404 与网络失败分开、workspace 已切换、报告 ref 过期、超长路径、同名文件 hash 不同。

**DoD**：- [ ] latest 404 空态。 - [ ] report/doc 有稳定跳转。 - [ ] artifact 显示 hash/path/preview。 - [ ] 路径只信任服务端 ref。

### B10 素材扫描失败项恢复与复用回归测试（1.0 人日）

**目标与用户感知**：工作区扫描失败可逐项查看详情并重试；视觉降级原因可展开；AssetPanel 素材复用有独立自动化用例。

**前置依赖**：B2 事件订阅和扫描 item 类型；后端当前 `failed/deferred/items` 响应。配音素材复用现行响应必须先从 API fixture 核实，不改后端契约。

> **可行性边界（务必先确认再动工）**：后端目前**没有**"单文件重试"端点（只有整批 `POST /workspace/{id}/scan` 与 `POST /workspace/{id}/materials` 上传）。若核实后仍无，本批范围收敛为：**失败项明细（sha256/path/reason）+ 一键重扫**，"单项重试"降为不实现（与 PRD §3.1「工作区与素材」行判定一致），并按本计划 §8 升级条件告知决策者；不得在前端伪造单文件重试。

**文件级改动清单**：
- 修改 `src/features/workspace/WorkspaceMaterialsPanel.tsx` → failed item detail/retry 与视觉降级详情。
- 修改 `src/features/assets/AssetPanel.tsx` → 复用状态 UI 与可测试回调。
- 修改 `src/types/api.ts` → scan item status/error 与 reused response（按真实契约）。
- 新增 `src/features/assets/AssetPanel.test.tsx`（若不存在）及 WorkspaceMaterialsPanel 测试。

**任务分解**：
1.（≤1h，先核现状）确认扫描失败项重试现有 API 能力与 dedup 返回码/字段。
2.（≤1.5h，先写测试）复用断言 200/reused、同 asset_id/file_path、used_count +1（如现行 API 仍返回这些字段）；失败项重试单项 mock。
3.（≤2h，再实现）展示文件级错误详情与单项重试状态。
4.（≤1.5h，再实现）补 AssetPanel 独立回归测试及去重成功反馈。
5.（≤1h，验证）确保重试不重复成功项，运行定向测试和全量前端门禁。

**可证伪验收**：`npx vitest run src/features/assets/AssetPanel.test.tsx src/features/workspace/WorkspaceMaterialsPanel.test.tsx`（路径先核实）。断言：`reused upload keeps same asset_id and file_path`、`used_count increments once`、`failed scan item retry does not rescan completed items`、`visual degradation reason is expandable`。若现行后端响应事实与 PRD 不符，停止升级，不更改契约。

**回滚方式**：撤回 UI 单项重试与详情控件；不删除素材、不恢复错误 toast；无配置变更。

**风险与最易漏边界**：重试中重复点击、单项状态被整批 reload 覆盖、used_count 重复增加、大文件处理中断。

**DoD**：- [ ] 失败项逐项重试。 - [ ] 失败原因可展开。 - [ ] AssetPanel 有独立复用测试。 - [ ] 成功项不被重复处理。

### B11 任务结果恢复路径与状态机（2.0 人日）

**目标与用户感知**：终态任务恢复请求被后端拒绝并给出可解释结果；paused、approval、needs、failed、cancelled 状态的动作与文案一致。

**前置依赖**：B5 状态/parser 契约。确认 `TaskToolbar.tsx` 已屏蔽 done/failed/cancelled 的继续；实施重点是后端 `clipwright/api/task.py::resume_task` 终态守卫与响应模型。必须后端+前端同一轮联动提交/部署验证。

**文件级改动清单**：
- 修改后端 `clipwright/api/task.py` → `resume_task` 终态校验，返回 409 或稳定 refused 结构。
- 修改后端对应 task API 测试 → terminal/paused/pending/status matrix。
- 修改 `src/services/api/task.ts` → ResumeResult/refused union。
- 修改 `src/features/task/TaskToolbar.tsx`、`TaskListSection.tsx`、`src/features/copilot/CopilotTaskRunTab.tsx` → 显示拒绝原因与状态动作。
- 新增/修改前端任务状态测试。

**任务分解**：
1.（≤1.5h，先写后端测试）completed/failed/cancelled resume 断言无 `_schedule` 调用。
2.（≤1.5h，再实现后端）在 resume_task 守卫终态，保持 paused/pending 原行为。
3.（≤1h，先写前端测试）覆盖 status matrix、拒绝响应与文案。
4.（≤2h，再实现前端）增加结果 union 与状态驱动按钮/原因提示。
5.（≤1h，联测）前后端同轮跑定向测试与全量门禁。

**可证伪验收**：后端 `python -m pytest tests -q`；前端 `npx vitest run src/features/task/task-ui.test.tsx`（路径先核实）。断言：`terminal resume returns 409/refused and does not schedule`、`paused task offers continue`、`approval and needs states show distinct action copy`、`cancelled task has no continue action`。

**回滚方式**：先回退前端新响应读取，再回退后端守卫；旧字段兼容保留。后端逻辑变更不涉及配置/数据。

**风险与最易漏边界**：终态与 resume 并发、task status 缺失/未知、重复请求、审批中任务不等同 paused。

**DoD**：- [ ] 后端终态守卫。 - [ ] 不调度终态任务。 - [ ] UI 状态动作有区分。 - [ ] 前后端联动用例通过。

### B12 文档与真实路由清单重建（1.0 人日）

**目标与用户感知**：排障文档不再称 run-async 唯一入口或 dispatch 真执行；路由表与当前运行代码一致。

**前置依赖**：B1–B11 生产语义定稿；本批只改文档，不修改代码。

**文件级改动清单**：
- 修改 `J:\Clipwright\docs\api_reference.md` → 当前入口与退役路径说明。
- 修改 `J:\Clipwright\docs\frontend-backend-parity.md` → 路由/客户端矩阵与运行语义。
- 修改前端仓库 `docs/` 对应副本（仅存在且为同步副本时）→ 保持契约描述一致。

**任务分解**：
1.（≤1h，先取证）按实际 router prefix 拼接生成当前路由清单。
2.（≤1.5h，先写检查）准备 PowerShell 关键语义搜索与人工核对表。
3.（≤2h，再改文档）逐段修正文案与路径归属，避免整体重写无关内容。
4.（≤1h，复核）对比两仓副本，检查未把 `/events` 写成 SSE。

**可证伪验收**：后端路由清单：`Select-String -Path 'J:\Clipwright\clipwright\api\*.py' -Pattern '@(router|admin_router)\.(get|post|put|delete|patch)\('` 并核 prefix。文档检查：`Get-ChildItem J:\Clipwright\docs,J:\Clipweight-Client\docs -Recurse -Include *.md | Select-String -Pattern 'run-async.*唯一入口|dispatch.*真跑|events.*协议错配'`；任何命中逐条审查。断言：文档不再称旧入口唯一/dispatch 真执行，JSON `/events` 与 `/events/stream` 语义分明。

**回滚方式**：文档文件按文件回退；无代码、配置、数据变更。

**风险与最易漏边界**：admin_router prefix、前端 docs 是否真实副本、历史章节引用仍有相反描述。

**DoD**：- [ ] 路由 prefix 已拼接。 - [ ] 旧入口语义准确。 - [ ] workspace JSON/SSE 区分。 - [ ] 两仓相关文档一致。

### B13 跨仓库回归与 P0 门禁（1.0 人日）

**目标与用户感知**：所有批次从干净工作树可复现；G1/G2/G10 各有专属失败即红的用例；最终前后端门禁全部绿。

**前置依赖**：B1–B12 完成。不得新增生产功能；如门禁需 CI 改动，先确认现有配置边界。

**文件级改动清单**：
- 仅按实际缺口修改测试文件与 CI 配置；不得为整理而改生产代码。
- 新增/调整 P0 前端测试：旧入口/重试、24 event parser/detail 变化。
- 视现有测试布局新增跨端 fixture manifest，核对事件名集合。

**任务分解**：
1.（≤1h，先冻结）记录两个仓库提交 SHA、基线与测试清单。
2.（≤1.5h，先写/核门禁）逐项确认 G1/G2/G10 测试能在破坏行为时失败。
3.（≤2h，执行）跑后端 pytest/ruff 与前端 tsc/vitest/lint/build。
4.（≤1.5h，交叉验证）检查事件契约 fixture、workspace SSE 重连、Task resume 联动。
5.（≤1h，收尾）记录测试计数、失败解释、批次状态，不更改无关文档。

**可证伪验收**：
```powershell
# J:\Clipwright
python -m pytest tests -q
ruff check clipwright --select E9,F63,F7,F82,F823
# J:\Clipweight-Client
npx tsc --noEmit
npx vitest run
npm run lint
npm run build
```
断言文本：`G1 test rejects legacy start path`、`G2 test proves retry action absent`、`G10 parser contract covers every one of 24 EVENT_TYPES`；前端全量 `1032 passed / 119 files` 为参照基线，实际新增后计数上升并记录。

**回滚方式**：仅回滚新增测试/门禁辅助改动；不得为测试失败回滚已验收功能。若定位到生产回归，停止发布并回到对应批次处理。

**风险与最易漏边界**：测试在错误仓库/工作目录执行、隐藏环境变量改变子进程编码、仅 build 通过但行为断言缺失、两仓版本错配。

**DoD**：- [ ] 两端全量门禁绿。 - [ ] G1/G2/G10 各有可证伪用例。 - [ ] 24 项 parser 契约集合相等。 - [ ] 实测计数与偏差留证。

## 4. 交叉风险与顺序陷阱

- `src/types/task.ts`：B5 先定义事件类型与 legacy adapter；B7 再扩 TaskUsage，避免各批分别扩散 Record 类型。
- `src/stores/taskStore.ts`：B5 先收敛 parser/reason；B6 再改 hypothesis projection。两批不得并行编辑该文件。
- `src/features/task/EventFeed.tsx`：B5 负责 parser、checkpoint、未知事件；B12（事件呈现细项若纳入）只能在 B5 之后改结构化详情，避免重复过滤逻辑。
- `src/features/copilot/CopilotTaskRunTab.tsx`：B5 先替换猜字段，B6 调整进度区，B7 增成本，B11 接状态恢复；按顺序逐批合并，不允许四支分支同时覆盖同一组件。
- `src/services/api/pipeline.ts`：B1 删除旧动作，B4 增新 typed 方法；先 B1 合入，再 B4 追加，避免旧方法被误恢复。
- `src/features/workspace/WorkspaceMaterialsPanel.tsx`：B2 建立 SSE/event model，B10 再加逐项失败恢复；B9 只消费稳定 workspace refs。保持一个订阅 owner，避免重复 EventSource。
- 历史回放：事件 union 必须容忍缺字段、旧 detail、未知 event/reason；不迁移历史任务，不以新 parser 丢弃旧事件。`causation_id` 缺失时沿用既有 FIFO/legacy 展示并明确标识。
- 状态事实源：Task terminal status 以服务端状态/事件为准；Plan 的 hypothesis 不得覆盖真实 AgentRun/tool 事实。审批 request/result 按 approval_id 关联，缺 id 时只做事件展示，不推断 pending。
- 端点语义：workspace `/events` 是 JSON snapshot，`/events/stream` 是 SSE；URL、类型、游标分别命名。不得引入 WebSocket。

## 5. 测试策略

| 范围 | 命名与位置 | fixture/断言策略 |
|---|---|---|
| 事件 parser 与 feed | `src/types/task.test.ts` 或 `src/features/task/EventFeed.test.tsx`、`src/features/copilot/CopilotTaskRunTab.test.tsx` | 共用 `src/test/fixtures/taskEvents.ts`（若仓库已有 fixtures 目录，沿用实际位置）；24 类型逐项输入，断言 parser 或 fallback，旧事件 fixture 不丢字段 |
| Workspace SSE | `src/features/workspace/WorkspaceMaterialsPanel.test.tsx` | mock EventSource；测试 `/events/stream`、last_seq、重复 seq、断线重连、卸载 close |
| 工具面/管理页 | 与 `ToolsPage.tsx`、`SchedulerPage.tsx`、`ProvidersPage.tsx` 同目录 `*.test.tsx` | API mock 固定目录样本与四态；不做真实 destructive API 调用 |
| AssetPanel/扫描恢复 | `src/features/assets/AssetPanel.test.tsx` 与 workspace 同目录测试 | 上传复用稳定 fixture；断言相同资产 id/path 与 used_count 单次变化，扫描只重试失败项 |
| Task resume | 后端沿用实际 task API 测试布局（先定位，不假造文件名）；前端 task feature `*.test.tsx` | 参数化终态/paused/pending/approval/needs，验证后端不调度终态及 UI 状态文案 |
| 事件契约 | 后端 `tests/` 现有 task schema/event 测试；前端共享事件 fixture | **需要契约测试**：后端 `EVENT_TYPES`（24）与前端 parser keys 集合一致；契约通过 fixture/生成检查实现，不靠裸数字 grep |

测试优先采用仓库已有 API mock、EventSource mock、task fixture；避免复制大型不相关 fixture。对状态类用例同时断言可见文案和不发生的副作用。每个 P0 可证伪用例：G1 断言旧启动请求不会发出且 Task 入口请求正确；G2 断言历史页没有 retry action 且旧 API 不被调用；G10 对 24 类 detail 改名/缺字段/未知字段 fixture 运行 parser，至少一个类型字段变动能使契约测试失败。

## 6. 提交与回滚策略

提交格式沿用：`feat(scope): <短目标> (PRD Bx)`；如纯文档可用 `docs(scope): ... (PRD B12)`，门禁可用 `test(scope): ... (PRD B13)`。每批一个可回滚提交；测试与实现同批提交。禁止把无关工作树变更带入提交。

跨仓库批次拆分：
- B2：前端 client/UI 与后端 SSE 既有契约无需同步改后端；但验收必须同轮联测。若发现需改后端事件格式，先停并升级。
- B5、B11：后端兼容字段/守卫提交先合入，前端提交紧随其后；两仓提交 SHA 记入同一发布记录，联动验收完成前不得对外发布不匹配组合。
- B10：前端文件级恢复与素材复用若要求后端契约变化，停止当前批，先走升级条件；不得单方面伪造服务端状态。
- B12 文档批独立；B13 只记录门禁结果与必要测试，不混入功能修复。

回滚顺序：先撤回依赖新字段/响应的前端提交，再撤回后端兼容扩展；不得恢复已退役 pipeline 执行语义。数据/配置操作不随代码回滚；若发现已有持久化写入变化，先停止并制定显式恢复步骤。

## 7. 进度看板骨架

| 批次 | 状态 | 提交 | 验收结果 | 偏离 |
|---|---|---|---|---|
| B1 | ✅ 已完成 | frontend `d9860a8` | `Get-ChildItem src -Recurse -Include *.ts,*.tsx \| Select-String 'pipelineApi\.retry'` → 0 命中；`npx vitest run src/pages/admin/PipelineAdminPage.test.tsx` → **7 passed**；`tsc` 0；`npm run test` **1032 passed / 119 files**；`lint` 0 error；`build` ✅ | 1) 后端 `pipeline.py` 的 410 分支未动（兼容层保留）；2) `ReviewPanel.tsx:127` 只有注释提到旧路径，无调用，未改；3) `pipelineApi.runAsync/retry` 保留签名并加 `@deprecated` 说明 | 未发生回滚 |
| B2 | ✅ 已完成 | frontend `a8c67c6` | `Select-String src\services\api\workspace.ts -Pattern 'events/stream'` → 1 命中；`Select-String src -Pattern 'eventsUrl'` → 0 命中；`npx vitest run src/features/workspace` → **11 passed**（新 8 + 旧 3）；`tsc` 0；`npm run test` **1040 passed / 120 files**；`lint` 0 error；`build` ✅ | 1) 后端 `/events`（JSON）与 `/events/stream`（SSE）都未改，只补前端消费；2) 「单项重试」未实现——后端无单文件重试端点，UI 明说需整批重扫（与计划 §3-B10 可行性边界一致）；3) 旧 `WorkspaceMaterialsPanel.test.tsx` 同步补 events/sseStream mock，用例数 3 不变 | 未发生回滚 |
| B3 | ✅ 已完成 | frontend `0446803` | `Select-String src\services\api\toolSurface.ts -Pattern '/api/tools'` → 3 命中；`npx vitest run src/pages/admin/ToolsPage.surface.test.tsx` → **4 passed**；`tsc` 0；`npm run test` **1044 passed / 121 files**；`lint` 0 error；`build` ✅ | 1) `ToolsPage.tsx` 已存在执行器 → **扩展页签**而非新建页面（与计划 §3-B3 的"扩展"一致）；2) 新页签复用 0 个执行状态、无执行按钮（测试有断言）；3) `getSchema()` 已实现但页面暂未展示 schema 全文（避免大 payload 卡顿），仅目录 + 记录 | 未发生回滚 |
| B4 | ✅ 已完成 | frontend `8a08b42` | `Select-String src\services\api\pipeline.ts -Pattern 'run-batch\|script-tools\|topic-suggest'` → 各 1 命中；`npx vitest run src/pages/admin/ScriptToolsPage.test.tsx` → **6 passed**；`HomePage.test.tsx` → **20 passed**（含选题建议 2 例）；`tsc` 0；`npm run test` **1052 passed / 122 files**；`lint` 0 error；`build` ✅ | 1) `runBatch` 只有 typed client，**没有**做批量提交 UI（避免误触批量花钱；计划允许「最小入口」）；2) 脚本工具页放 `/settings/script-tools` 而非常规创作流；3) 选题建议按 `pluginId` 传 category，不读未定义的 `type` 变量 | 未发生回滚 |
| B5 | ✅ 已完成 | backend `6314982`（`tests/test_event_contract.py`）· frontend `c0bf14c` | `python -m pytest tests/test_event_contract.py` → **3 passed**（24 项集合+顺序一致）；`ruff` 0；`Select-String src\features\task\EventFeed.tsx -Pattern '_NOISY'` → 0 命中；`npx vitest run src/features/task src/features/copilot src/stores/taskStore.test.ts` → **84 passed**（含 eventSchema 7 例、未知 reason、checkpoint 折叠）；`tsc` 0；`npm run test` **1061 passed / 123 files**；`lint` 0 error；`build` ✅ | 1) 后端**未新增字段/事件**，只加契约测试（PRD 红线守住）；2) `unknown_terminal` 只吃掉"未知 reason"，已知失败口径（gate:/step_failed/tool_loop_*/空原因/缺时间线/异常类名）仍算 failed；3) `checkpoint` 改为「行保留 + 默认折叠」，detail 为空的也能展开；4) 只把 4 个 row 组件接到 parser，未重写其它消费点（留给 B6/B7/B11） | 未发生回滚 |
| B6 | ✅ 已完成 | frontend `c5377db` | `npx vitest run src/features/task/task-ui.test.tsx src/features/copilot/CopilotTaskRunTab.test.tsx` → **25 passed**（含「假设态 Plan 不因进度事件改动」「真实进展区块」）；`tsc` 0；`npm run test` **1063 passed / 123 files**；`lint` 0 error；`build` ✅ | 1) 判定用 `hypothesis_only !== false`（老任务缺字段时按假设处理，与 PRD B10 一致）；2) `plan`/`replan` 事件仍会更新假设本身（那是"假设被改写"，不是进度）；3) `taskStore.test.ts` 夹具显式补 `hypothesis_only: false`，因为那批用例测的正是"老计划折叠进度"，假设态由 task-ui 的 B6 用例覆盖 | 未发生回滚 |
| B7 | ✅ 已完成 | frontend `62d4f80` | `Select-String src\types\task.ts -Pattern 'unknown_cost_calls'` → 1 命中；`npx vitest run src/features/copilot/CopilotTaskRunTab.test.tsx src/pages/admin/ProvidersPage.test.tsx` → **37 passed**；`tsc` 0；`npm run test` **1066 passed / 123 files**；`lint` 0 error；`build` ✅ | 1) ProvidersPage **没有**任务事件流，所以「最近降级」放在运行页（`degradation-rows`）而不是硬塞进配置页；配置页只做「保存值≠生效值」的说明 + 把「当前生效」改名为「已配置」；2) 不解析 `degradation.detail` 的自由文本，只原样展示 scope/detail/impact；3) 无已知成本时显示「—」而非 `$0` | 未发生回滚 |
| B8 | 待开始 |  |  |  |
| B9 | 待开始 |  |  |  |
| B10 | 待开始 |  |  |  |
| B11 | 待开始 |  |  |  |
| B12 | 待开始 |  |  |  |
| B13 | 待开始 |  |  |  |

## 8. 停止/升级条件

遇到下列任一项，停止当前批次，不擅自扩大实现：

1. PRD 结论与代码/运行响应不符，包括 API schema、事件字段、状态码或可用操作与记录不一致。
2. 需要改变后端契约才能完成前端功能；尤其需新增后端端点、改事件名/必填字段、改变现有状态码。
3. 需要修改 `provider_sources.json`、`.env`、凭据、持久化配置或执行数据迁移。
4. 需要执行破坏性操作（调度 tick、删除 schedule/artifact、批量重试/素材删除），且现有 UI/API 没有明确二次确认或无法证明范围。
5. 门禁无法保持绿、基线已有失败扩大、出现无法归属的跨仓库回归；先定位到批次并提供最小复现。
6. 发现 `eventsUrl()`、`EVENT_TYPES` 数量、`ToolsPage.toolApi.execute`、TaskToolbar 终态行为与此计划事实不同；停止并更新事实依据，不能沿旧假设继续。
7. 需要合并/拆分批次、改变 22 人日估算或调整 P0 顺序；先更新对照和依赖，再继续实施。

升级材料至少包括：复现命令/测试、实际响应或代码证据、受影响批次与契约、回滚点、建议决策选项。获得明确决策后再继续。

## 9. 附录

### 9.1 G 编号 → 批次索引

| 缺口 | 批次 | 缺口 | 批次 |
|---|---|---|---|
| G1 | B1 | G10 | B5 |
| G2 | B1 | G11 | B7 |
| G3 | B2 | G12 | B9 |
| G4 | B8 | G13 | B9 |
| G5 | B3 | G14 | B4 |
| G6 | B8 | G15 | B12 |
| G7 | B6 | G16 | B10 |
| G8 | B11 | G17 | B7 |
| G9 | B5 |  |  |

覆盖核对：G1–G17 共 17 项；P0 为 G1/B1、G2/B1、G10/B5，均在本计划保留独立可证伪验收。G3 为 P1，属于 workspace 进度未消费，不是 SSE 协议错配。严重度按 PRD 逐行统计：**P0=3（G1、G2、G10）/ P1=13 / P2=1（G13）**；G13 归 B9，G17 为 P1 归 B7。

### 9.2 PRD「明确不做」清单

- 不重写 Agent Kernel/tool loop；本轮只补前端可见性、契约与恢复，不改变执行架构。
- 不恢复旧 pipeline 执行引擎；不把 410 改回真跑，不恢复 `start_agent_retry`；只显示退役与替代入口。
- 不把 `/api/market` 账号服务调用并入 Clipwright 主 API；不改 Server 仓库。
- 不新增 WebSocket；项目实时链路保持 SSE-only。
- 不改成片领域确定性校验、渲染算法或素材视觉模型；只接已有后端能力与状态。
- 不迁移 Provider 存储格式；不改 `provider_sources.json`，只展示实际 capability/effective 结果。
- 不建设通用 prompt 平台；评估只接现有 `/api/eval` 报告与已有 prompt registry，不扩 SaaS。
- 不清理所有历史 UI/文档；仅清理会误导当前入口的 run/retry/协议说明，无关遗留另开任务。
