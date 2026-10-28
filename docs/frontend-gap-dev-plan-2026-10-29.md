# 前端缺口开发计划（2026-10-29）

- 依据 PRD：`docs/frontend-gap-prd-2026-10-29.md`（G1–G17 / P0=3、P1=12、P2=2 / B1–B13）
- 后端：`J:\Clipwright`（`feat/master-agent`）；前端：`J:\Clipweight-Client`（`feat/master-agent`）
- 本文只做计划，不改代码。每条改动都落到文件与函数/组件名；每条验收都能用命令跑出来。
- **修订说明（相对 PRD §6 的事实校正）**：
  1. `src/pages/admin/ToolsPage.tsx` **已存在**（旧 `/api/tool/*` 的执行页），B3 改为**扩展它**（新增「Agent 工具面」页签），不新建页面。
  2. `eventsUrl()` 是死方法（全仓 0 调用），`/api/workspace/{id}/events` 是**设计好的 JSON 端点**；B2 的目标是"让前端真正消费工作区进度（SSE + JSON 回填）"，不是改后端协议。
  3. 工作区**没有**"单项重试"后端端点（只有整批 `POST /workspace/{id}/scan` 与 `POST /workspace/{id}/materials` 上传）；B10 把"单项重试"降级为"失败项明细 + 一键重扫"，单项重试列入 §8 停止条件。

---

## 0. 执行摘要

| 项 | 内容 |
|---|---|
| 批次 | 13 批（B1–B13），与 PRD 一一对应，无新增无遗漏 |
| 总人日 | **22**（= PRD §6 各批人日之和：1.5+1.5+2.0+2.0+3.0+1.5+1.5+2.5+1.5+1.0+2.0+1.0+1.0） |
| 关键路径 | B5（事件契约）→ B6（Plan 双轨）→ B11（任务状态机）→ B13（回归门禁） |
| P0 批次 | **B1、B5、B11**（对应 G1/G2、G10、G8+G2 的连带）；PRD 里 G3 已降级为 P1，故 B2 不在 P0 |
| 建议从哪开始 | **B1**：改动只在前端 2 个文件 + 1 个测试，能立刻消除"点了报 410"的假入口，且不依赖任何后端改动 |
| 提交节奏 | 一批一提交（后端+前端的同批拆两次提交，见 §6）；每批合入前必须重跑该批验收命令 + 该仓库全量门禁 |
| 可并行 | B3 ∥ B4（互不碰文件）；B7 ∥ B8（前者只动画配置页与运行页，后者新增页面）；其余均串行 |
| 双仓库批次 | **B5、B11** 必须后端+前端同一轮改动（契约/状态机两端语义要一起落），其余为纯前端（B12 纯文档） |

### 0.1 完成后用户能感知到什么

1. 管理页不再有"点了报 410"的假入口（B1）。
2. 工作区扫描能看到逐文件进度与失败明细，而不是一个转圈（B2）。
3. 能看见主 Agent 真实可用的工具面 / 调度任务 / 渲染后端（B3、B8）。
4. 长任务里"初始假设"和"真实进展"分开，事件详情不再靠猜字段（B5、B6）。
5. 未知成本、被忽略的模型参数、终态任务能否"继续"都有明确说法（B7、B11）。

---

## 1. 不变量与门禁

### 1.1 开工基线（当前实测，2026-10-29）

| 仓库 | 命令 | 当前基线 |
|---|---|---|
| 后端 | `python -m pytest tests -q` | **4496 passed / 4 skipped / 1 xfailed** |
| 后端 | `python -m ruff check clipwright --select E9,F63,F7,F82,F823` | 0（All checks passed） |
| 前端 | `npx tsc --noEmit` | 0 |
| 前端 | `npm run test`（`npx vitest run`） | **1032 passed / 119 files** |
| 前端 | `npm run lint` | 0 error（169 warnings） |
| 前端 | `npm run build` | ✅ |

> 全量后端回归**不要**设 `PYTHONIOENCODING=utf-8`：会让 `tests/test_eval_loop.py::test_replay_missing_file_is_loud` 假红（子进程 stdout 用 cp936 解码）。

### 1.2 每批合入前必跑（不得省略）

```powershell
# 前端批次
cd J:\Clipweight-Client; npx tsc --noEmit; npm run test; npm run lint; npm run build
# 后端批次（B5/B11/B12）
cd J:\Clipwright; python -m pytest tests -q; python -m ruff check clipwright --select E9,F63,F7,F82,F823
```

### 1.3 不允许被本计划破坏的不变量

- 创作只有一条路：`requirements/proceed` → Task（tool loop）；**不恢复**旧 pipeline 执行引擎。
- SSE-only：不新增 WebSocket。
- 不迁移 `provider_sources.json` 结构与内容。
- `EVENT_TYPES` 新增类型必须同时更新前端 union 与回放器（本计划不改后端事件类型）。
- 每个 P0（G1/G2、G8、G10）必须有可证伪用例，不允许只靠手工观察。

---

## 2. 批次总表

| 批次 | PRD 缺口 | 目标 | 依赖 | 人日 | 风险 | 可回滚性 |
|---|---|---|---|---|---:|---|---|
| **B1** `[P0]` | G1、G2 | 清掉退役 pipeline 前端动作，历史页只读并指向 Task 入口 | — | 1.5 | 低 | 文件级回滚（2 文件+1 测试） |
| B2 | G3 | 工作区进度真正被消费（SSE + JSON 回填 + 失败明细） | — | 1.5 | 中（SSE 生命周期） | 文件级，后端零改动 |
| B3 | G5、G12 | 「Agent 工具面」页签：工具目录/schema/records 可发现 | — | 2.0 | 中（易被误当执行入口） | 页签级回滚 |
| B4 | G14 | 批量任务/脚本工具/选题建议客户端 + 最小入口 | — | 2.0 | 中（旧 pipeline 边界） | 入口可隐藏 |
| **B5** `[P0]` | G10、G9 | 事件 discriminated union + parser + checkpoint 折叠 + 终态 reason 不再伪装 failed | B1 | 3.0 | 高（跨仓契约） | 保留 `legacy` adapter 可回退 |
| B6 | G7 | Plan（假设）与真实 Run 双轨视觉分离 | B5 | 1.5 | 中 | 组件级回滚 |
| B7 | G11、G17 | 未知成本、降级、生效值可解释 | B5 | 1.5 | 中 | 卡片级回滚 |
| B8 | G4、G6 | 调度页 + 渲染后端/产物清理入口 | — | 2.5 | 中高（破坏性操作） | 路由级回滚 |
| B9 | G13 | 评估 latest 首屏、报告/文档/产物回链 | B5 | 1.5 | 低 | 面板级回滚 |
| B10 | G16、B9 残留 | 素材复用独立用例 + 扫描失败明细 | B2 | 1.0 | 低 | 用例级 |
| **B11** `[P0]` | G8 | 任务状态机：终态不可 resume、各状态有独立文案与动作 | B5 | 2.0 | 中高（后端守卫） | 后端加守卫可单独回滚 |
| B12 | G15 | 文档与真实路由清单重建 | B1–B11 | 1.0 | 低 | 纯文档 |
| B13 | — | 跨仓库回归与 P0 门禁固化 | 全部 | 1.0 | 低 | 只回滚测试/CI |
| **合计** | | | | **22** | | |

---

## 3. 逐批执行卡

### B1 `[P0]` 退役 pipeline 前端动作收口（1.5 人日｜前端单仓）

**目标 / 用户可感知**：管理页不再出现"重试"这种点了必然失败的按钮；历史 pipeline 页顶部明确写「历史只读 + 替代入口」。

**前置依赖**：无。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/pages/admin/PipelineAdminPage.tsx` | 删除「重试」按钮与其 handler（现第 125 行 `await pipelineApi.retry(...)`）；页头加「历史只读」徽章；新增一块「新任务请走」指引（`/api/requirements/proceed` → Task，链接到「新建制作」）；`retryError` 状态与相关 JSX 一并删除 |
| `src/services/api/pipeline.ts` | `runAsync`（第 26 行）与 `retry`（第 77 行）改为 `/** @deprecated 已退役：后端返回 410 */`，方法体保留但**只允许 legacy 只读页/测试引用**；新增注释指向 `/api/task` 与 `/api/requirements/proceed` |
| `src/pages/admin/PipelineAdminPage.test.tsx` | 新增 2 例：① 页面不再渲染「重试」；② 渲染「历史只读」与替代入口文案 |
| `src/services/api/pipeline.test.ts`（新建） | 断言 `runAsync`/`retry` 的 URL 仍指向旧路径（**只用于证明它们是 legacy，不被新流程调用**）——若仓库无此文件则新建 |

**任务分解**

1. 先写测试：`PipelineAdminPage.test.tsx` 增加两例（红）。
2. 改 `PipelineAdminPage.tsx`：删按钮/handler/错误态（绿）。
3. 给 `pipeline.ts` 两个方法加 `@deprecated` 与说明注释。
4. 搜索全仓确认无其他调用点：`Select-String -Path src -Pattern 'pipelineApi\.retry|pipelineApi\.runAsync'`。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
Select-String -Path src\**\*.ts,src\**\*.tsx -Pattern 'pipelineApi\.retry'          # 期望 0 命中
Select-String -Path src -Pattern "run-async"                                        # 期望仅 src/services/api/pipeline.ts 的 legacy 定义/注释
npx vitest run src/pages/admin/PipelineAdminPage.test.tsx                           # 期望含用例名「不再渲染重试按钮」「显示历史只读与替代入口」
npx tsc --noEmit; npm run build
```

**回滚**：`git checkout -- src/pages/admin/PipelineAdminPage.tsx src/services/api/pipeline.ts src/pages/admin/PipelineAdminPage.test.tsx`。

**风险与边界**：别顺手删后端 410 分支（那是兼容层，PRD 明确不恢复执行引擎）；历史 runs 的统计展示保持原样，只删动作。

**DoD**

- [ ] 管理页无「重试」按钮，且无 `pipelineApi.retry` 调用点
- [ ] 页头有「历史只读」与替代入口文案
- [ ] 新增 2 用例通过；`tsc`/`build` 绿
- [ ] `pipeline.ts` 两个方法有 `@deprecated` 说明

---

### B2 工作区进度可消费（1.5 人日｜前端单仓）

**目标 / 用户可感知**：扫描时能看到"正在索引哪个文件 / 已索引 N / 失败项及原因"，断线重连能接着来。

**前置依赖**：无（后端已有 `/events`（JSON）与 `/events/stream`（SSE），`clipwright/api/workspace.py:310-347`）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/services/api/workspace.ts` | 把死方法 `eventsUrl`（161–163）替换为：`eventsStreamUrl(projectId, since=0)`（用 `apiBase()` 拼 `/api/workspace/{id}/events/stream?since=`）与 `events(projectId, since=0)`（JSON 回填，直接复用现有 `/events`）；导出 `WorkspaceEvent` 类型（`{seq,type,ts,message,detail}`） |
| `src/features/workspace/WorkspaceMaterialsPanel.tsx` | `scan()` 改为：先把 `since` 记下 → 调 `scan` → 用 `connectSseStream` 订阅 `eventsStreamUrl(projectId, since)`，把事件追加到本地 `progress` 列表（≤200 条）；卸载/切项目时 `close()`；保留原 `note` 汇总但改为"进度 + 汇总"两段；失败项列表来自事件的 `detail.failed[]`（`{sha256,path,reason}`），没有则显示 note 里的计数 |
| `src/types/workspace.ts`（新建） | `WorkspaceEvent`、`ScanFailure`（`sha256/path/reason`）类型 |
| `src/features/workspace/WorkspaceEvents.test.tsx`（新建） | 3 例：URL 含 `/events/stream`；按 `seq` 去重（重复帧不重复渲染）；卸载时调用 `close()` |

**任务分解**

1. 先写 `WorkspaceEvents.test.tsx`（mock `connectSseStream`），红。
2. `workspace.ts` 加 `eventsStreamUrl`/`events` + 类型（保持 `scan/materials/upload/remove` 不变）。
3. 面板接 SSE：`useEffect` 依赖 `projectId`，返回清理函数；帧解析失败静默丢弃（不崩 UI）。
4. 失败项渲染 `showing N 项失败：<path> — <reason>`（无则显示"无失败项"）。
5. 手工验证一次真实扫描（可选，不阻塞）。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
Select-String -Path src\services\api\workspace.ts -Pattern 'events/stream'   # 期望 1 命中
Select-String -Path src -Pattern 'eventsUrl'                                 # 期望 0 命中（死方法已替换）
npx vitest run src/features/workspace/WorkspaceEvents.test.tsx               # 3 例通过
npx tsc --noEmit; npm run build
```

**回滚**：回退这 3 个文件；`scan()` 恢复为"调用 + reload"。

**风险与边界**：SSE 重复连接（`useEffect` 依赖要稳定，`connectSseStream` 句柄必须 `close`）；切换项目时用 `alive` 标志防 setState after unmount；**不做**单项重试（后端无端点，见 §8）。

**DoD**

- [ ] `workspaceEventsStream` 真实被订阅（不是死代码）
- [ ] 卸载/切项目不泄漏连接
- [ ] 失败项可读（path + reason），无失败项时明说
- [ ] 3 例测试通过；`tsc`/`build` 绿

---

### B3 「Agent 工具面」页签（2.0 人日｜前端单仓）

**目标 / 用户可感知**：管理员能看见主 Agent 真实可用工具（5 个 agent 工具 + 2 个领域工具 + 域工具完整列表）、tier、可用性、最近调用记录。

**前置依赖**：无（后端 `GET /api/tools`、`/api/tools/schema`、`/api/tools/records` 已存在：`clipwright/api/tool_surface.py:15-53`）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/services/api/toolSurface.ts`（新建） | `listCatalog()` → `GET /api/tools`；`getSchema()` → `GET /api/tools/schema`；`listRecords(limit)` → `GET /api/tools/records` |
| `src/types/toolSurface.ts`（新建） | `ToolSurfaceEntry {name, kind, tier, available, description, danger?, cost_hint?}`、`ToolSurfaceRecord` |
| `src/pages/admin/ToolsPage.tsx` | 顶部页签从 `'tool' | 'skill'` 扩成 `'tool' | 'skill' | 'surface'`；新页签渲染：分组（agent/domain/其余）、`data-tier` 字段、`available=false` 灰显并给原因、`danger` 行**不提供执行按钮**（只读）；底部可折叠「最近调用记录」 |
| `src/pages/admin/ToolsPage.surface.test.tsx`（新建） | 4 例：① 渲染 5 个 agent 工具名（`delegate_agent`/`research_agent`/`get_agent_run`/`list_agent_runs`/`cancel_agent_run`）；② 渲染 `replan_closure`/`validate_stage_plan`；③ `danger` 行无「执行」按钮；④ 接口失败显示可读错误 |

**任务分解**

1. 先写 4 例测试（mock 三个端点），红。
2. 建 `toolSurface.ts` + 类型。
3. `ToolsPage.tsx` 加页签与只读列表（**不接** `surface.execute`，避免把目录做成任意执行入口）。
4. 记录列表：时间、工具、状态、耗时；空态「还没有调用记录」。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
Select-String -Path src\services\api\toolSurface.ts -Pattern '/api/tools'    # 期望 3 命中（tools、schema、records）
Select-String -Path src\pages\admin\ToolsPage.tsx -Pattern "surface"          # 期望页签存在
npx vitest run src/pages/admin/ToolsPage.surface.test.tsx                     # 4 例通过
npx tsc --noEmit; npm run lint; npm run build
```

**回滚**：删除新页签分支与两个新文件（`ToolsPage` 回到 `tool|skill` 两页签）。

**风险与边界**：**绝不**把 `/api/tools` 目录做成"点一下就执行"的入口（那是 `surface.execute` 的能力，需要 Kernel 上下文）；`available=false` 要显示原因而不是静默隐藏。

**DoD**

- [ ] 三类端点都有 typed client
- [ ] agent/domain 工具全部可见且 tier/available 可见
- [ ] danger 行只读
- [ ] 4 例通过；lint/build 绿

---

### B4 批量 / 脚本工具 / 选题建议（2.0 人日｜前端单仓）

**目标 / 用户可感知**：创作表单能一键生成选题建议；批量提交与脚本工具在 UI 有入口（而不是只有 API）。

**前置依赖**：B1（同一文件 `pipeline.ts`，先收口退役方法再加新方法，避免冲突）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/services/api/pipeline.ts` | 新增 `runBatch(body)` → `POST /api/pipeline/run-batch`；`scriptTools(body)` → `POST /api/pipeline/script-tools`；`topicSuggest(body)` → `POST /api/pipeline/topic-suggest`；沿用既有 `predictScript`/`predictMaterial` |
| `src/pages/HomePage.tsx` | 选题输入框旁加「生成选题建议」按钮 → 调 `topicSuggest`，结果以可选列表插入 topic（不覆盖用户已写内容）；失败给可读文案 |
| `src/pages/admin/ScriptToolsPage.tsx`（新建） | 脚本工具最小页：输入文案 → 选择操作（扩写/改写/续写）→ 展示结果与成本提示；**只读预览，不写入时间线** |
| `src/services/api/pipeline.batch.test.ts`（新建） | 2 例：`runBatch`/`topicSuggest` 的 URL 与 payload 正确 |
| `src/pages/HomePage.test.tsx` | +1 例：点「生成选题建议」→ 调 `topicSuggest` 并渲染候选 |

**任务分解**

1. 先写两个测试文件（红）。
2. `pipeline.ts` 加三个方法（`@deprecated` 的两方法保持不动）。
3. `HomePage.tsx` 接入建议按钮（沿用现有 `predictScript` 的 loading/错误模式）。
4. 新建 `ScriptToolsPage.tsx` + `src/router.tsx` 路由（`/admin/script-tools`）。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
Select-String -Path src\services\api\pipeline.ts -Pattern 'run-batch|script-tools|topic-suggest'  # 期望各 1 命中
npx vitest run src/services/api/pipeline.batch.test.ts src/pages/HomePage.test.tsx
npx tsc --noEmit; npm run build
```

**回滚**：删新方法/新页面/新路由；`HomePage` 去掉按钮。

**风险与边界**：`run-batch` 属于旧 pipeline 边界（后端保留但非主路径），入口文案要写清"批量/兼容用途"；`script-tools` 结果**不得**自动写入时间线（避免绕过需求确认）。

**DoD**

- [ ] 三个端点有 typed client 与至少一个 UI 入口
- [ ] 结果只读预览，不写时间线
- [ ] 测试与构建绿

---

### B5 `[P0]` 事件契约 union 化与终态语义（3.0 人日｜后端+前端同轮）

**目标 / 用户可感知**：事件详情不再靠猜字段；`checkpoint` 能折叠看到；后端新增终态原因时 UI 不再一律显示"失败"。

**前置依赖**：B1（同一 `EventFeed`/运行页文件，先清退役入口）。

**文件级改动清单**

| 端 | 文件 | 改什么 |
|---|---|---|
| 前端 | `src/features/task/eventSchema.ts`（新建） | `EVENT_PARSERS: Record<TaskEventType, (detail)=>TypedDetail>`；导出 `parseTaskEvent(ev): ParsedTaskEvent`、`EVENT_TYPE_KEYS`（顺序与后端 `EVENT_TYPES` 一致） |
| 前端 | `src/types/task.ts` | 增 `TaskEventDetailMap`（24 键 discriminated union：`tool_call:{tool,args}`、`tool_result:{tool,status,duration_ms,error?}`、`agent_run_end:{run_id,status,summary?…}`、`context_loaded:{sources,trimmed:string[],bytes,budget}`、`thinking:{delta,done,round}`、`doc_write:{path,chars?}`…）；`TaskUsage` 增 `unknown_cost_calls`（B7 复用） |
| 前端 | `src/stores/taskStore.ts` | `terminalStatusOf`（53–61）改为：命中白名单 → 对应状态；未命中 → `'unknown_terminal'` 并把 `raw reason` 存进 `finish.reason`；`applyEvent` 不再把 `checkpoint` 丢弃（保留在 `events` 里，由 UI 折叠） |
| 前端 | `src/features/task/EventFeed.tsx` | 删掉 `_NOISY`（28）与过滤（116）；`checkpoint` 改为默认折叠的一组行（`data-event-type="checkpoint"` + 展开按钮），其余保持现有详情展开 |
| 前端 | `src/features/copilot/CopilotTaskRunTab.tsx` | `StageRows`/`ModelTurnRows`/`ToolCallRows`/`ApprovalRows` 改用 `ParsedTaskEvent` 取字段，去掉 `as Record<string, unknown>` 手写猜键 |
| 后端 | `clipwright/schema/task.py` | 只加注释：`EVENT_TYPES` 是契约源，新增类型必须同步前端 `eventSchema.ts`（不改字段） |
| 后端 | `tests/test_event_contract.py`（新建） | 读 `clipwright/schema/task.py` 的 `EVENT_TYPES` 与前端 `src/features/task/eventSchema.ts` 的 `EVENT_TYPE_KEYS`，断言两集合相等（跨仓契约测试，用绝对路径读前端文件；找不到文件时 `pytest.skip` 并说明） |
| 前端 | `src/features/task/eventSchema.test.ts`（新建） | 24 类型逐个 parse（fixture 覆盖）；未知 type 返回 `{kind:'unknown'}` 不抛；`detail` 缺键时给默认值不崩 |
| 前端 | `src/features/copilot/EventPresentation.test.tsx` | 扩展 1 例：`checkpoint` 可见且默认折叠 |
| 前端 | `src/stores/taskStore.test.ts` | +1 例：未知 `task_end.reason` → `status='unknown_terminal'` 且保留 raw reason |

**任务分解**

1. 后端先写 `tests/test_event_contract.py`（此时前端文件未建 → skip；建好后转绿）。
2. 前端建 `eventSchema.ts` + 类型（纯类型 + 纯函数，先写 `eventSchema.test.ts`）。
3. 改 `taskStore.ts`（终态映射 + 不再丢 checkpoint），跑 `taskStore.test.ts`。
4. 改 `EventFeed.tsx`（折叠而非丢弃），跑 `EventPresentation.test.tsx`。
5. 改 `CopilotTaskRunTab.tsx` 四个 row 组件使用 typed detail，跑 `CopilotTaskRunTab.test.tsx`。
6. 双仓全量门禁。

**可证伪验收**

```powershell
cd J:\Clipwright
python -m pytest tests/test_event_contract.py -q          # 断言「EVENT_TYPES 与前端 parser 键集合一致」
Select-String -Path clipwright\schema\task.py -Pattern '"checkpoint"'
cd J:\Clipweight-Client
Select-String -Path src\features\task\EventFeed.tsx -Pattern '_NOISY'      # 期望 0 命中
npx vitest run src/features/task/eventSchema.test.ts src/features/copilot/EventPresentation.test.tsx src/stores/taskStore.test.ts
npx tsc --noEmit; npm run test; npm run build
```

**回滚**：保留旧 `Record<string, unknown>` 读取路径作为 `legacyParse`（一开关切换）；`checkpoint` 折叠改动可单独 revert。

**风险与边界**：历史事件 detail 形状不齐（早期事件没有 `causation_id`、`agent_run_*` 字段少）→ parser 必须"缺键给默认值"；`task_end.reason` 白名单要与 kernel 现有 reason 常量对齐（`tool_loop_completed`/`dry_run_preview`/`cancelled`/`gate:*`/`tool_loop_error:*`），拿不准就查 `agent_kernel.py` 的 `emit(task.id, "task_end"` 各分支。

**DoD**

- [ ] 24 个事件类型都有 parser，且契约测试绿
- [ ] `checkpoint` 可见（折叠）
- [ ] 未知终态 reason 不再显示"失败"
- [ ] 四个 row 组件无手写 `as Record<string, unknown>` 猜键
- [ ] 双仓门禁全绿

---

### B6 Plan（假设）与真实 Run 双轨（1.5 人日｜前端单仓）

**目标 / 用户可感知**：长任务里 Plan 不再像进度条；真实进展只来自事件。

**前置依赖**：B5（`ParsedTaskEvent` 可用来判断"真实进展"）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/stores/taskStore.ts` | `_foldPlan`（64–110）加守卫：`plan.hypothesis_only !== false` 时**不**根据 `step_start/step_end/tool_result` 改 `PlanStep.status`（假设保持"设想"） |
| `src/features/task/PlanPanel.tsx` | 假设态不再渲染步骤状态徽章（改为「设想」+ 顺序号）；新增 `data-hypothesis="true"` 断言锚点（已存在，保持） |
| `src/features/copilot/CopilotTaskRunTab.tsx` | 在 `StageRows` 之上加标题「真实进展（来自事件）」；`PlanPanel` 区块标题改「初始假设（不追踪进度）」 |
| `src/features/task/task-ui.test.tsx` | +2 例：① 假设态下喂 `step_end` 事件，PlanStep 徽章不变；② 运行页出现「真实进展（来自事件）」标题 |

**任务分解**：先写 2 例（红）→ 改 store 守卫 → 改两个组件文案与结构 → 跑 `task-ui.test.tsx` + `CopilotTaskRunTab.test.tsx`。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
npx vitest run src/features/task/task-ui.test.tsx src/features/copilot/CopilotTaskRunTab.test.tsx
Select-String -Path src\features\copilot\CopilotTaskRunTab.tsx -Pattern '真实进展'
npx tsc --noEmit; npm run build
```

**回滚**：恢复 `_foldPlan` 无条件折叠（组件文案单独 revert）。

**风险与边界**：历史任务（`hypothesis_only` 缺失）默认按假设处理会把老任务的步骤状态也冻住 → 用 `hypothesis_only !== false` 判定，并在 `taskStore` 顶部注释说明。

**DoD**

- [ ] 假设态不因事件改 step 状态
- [ ] 运行页明确区分「假设」与「真实进展」
- [ ] 2 例通过；构建绿

---

### B7 成本 / 降级 / 生效值可解释（1.5 人日｜前端单仓）

**目标 / 用户可感知**：未知成本不再被当成 0；模型参数"请求了但被忽略"能看见。

**前置依赖**：B5（`TaskUsage` 类型与 typed detail）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/types/task.ts` | `TaskUsage` 增 `unknown_cost_calls?: number`（与后端 `schema/task.py:157` 对齐；B5 已加则复用） |
| `src/features/copilot/CopilotTaskRunTab.tsx` | 新增「用量」区块：turns / tool_calls / tokens / cost / **未知成本 N 次**（`unknown_cost_calls>0` 时红字说明"这些调用的成本未计入金额"） |
| `src/pages/admin/ProvidersPage.tsx` | 模型行的生成参数区加一行「最近降级」：从 `degradation` 事件（`detail.detail` 含 `reasoning_effort/top_p` 等）聚合显示 `已忽略：top_p（provider 不支持）`；并在参数区顶部注明「保存值 ≠ 一定生效，实际下发受 provider 能力限制」 |
| `src/features/copilot/CopilotTaskRunTab.test.tsx` | +1 例：fixture 含 `usage.unknown_cost_calls=2` → 渲染「未知成本 2 次」 |
| `src/pages/admin/ProvidersPage.test.tsx` | +1 例：渲染「保存值 ≠ 一定生效」说明 + 降级提示 |

**任务分解**：先补类型 → 跑 `tsc` 看断点 → 写 2 例 → 改两个组件 → 全量跑。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
Select-String -Path src\types\task.ts -Pattern 'unknown_cost_calls'        # 期望 1 命中
npx vitest run src/features/copilot/CopilotTaskRunTab.test.tsx src/pages/admin/ProvidersPage.test.tsx
npx tsc --noEmit; npm run build
```

**回滚**：删用量区块与新说明行。

**风险与边界**：`degradation` 事件的 `detail.detail` 是字符串（后端 `note_degradation(detail=…)`），前端不要去解析自由文本做结构化判断，只做"原样展示 + 归类为忽略项"。

**DoD**

- [ ] `unknown_cost_calls` 在类型与 UI 都有
- [ ] provider 页明确"保存≠生效"并展示最近降级
- [ ] 2 例通过

---

### B8 调度与渲染运维入口（2.5 人日｜前端单仓）

**目标 / 用户可感知**：能创建/开关/删除定时任务；能看到渲染后端与清理过期成片。

**前置依赖**：无（后端 `/api/scheduler/*`、`/api/render/backends`、`DELETE /api/render/artifacts` 已存在）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/services/api/scheduler.ts`（新建） | `list()`/`create(body)`/`toggle(id)`/`remove(id)`/`tick()`，对应 `GET|POST /api/scheduler/schedules`、`POST /schedules/{id}/toggle`、`DELETE /schedules/{id}`、`POST /tick` |
| `src/pages/admin/SchedulerPage.tsx`（新建） | 列表（cron 5 段 + 时区 + 下次触发 + 状态）、新建表单（含 cron 校验：字段数与取值范围）、开关、删除（二次确认）、「立即触发一次」（二次确认，提示会消耗额度）；空态「还没有定时任务」 |
| `src/services/api/render.ts` | 新增 `getBackends()` → `GET /api/render/backends`；`cleanupArtifacts()` → `DELETE /api/render/artifacts` |
| `src/pages/admin/BackendConfigPage.tsx` | 加「渲染后端」区块：列出可用后端、当前默认、`probe` 结果；加「清理过期成片」（二次确认 + 结果条数回显） |
| `src/router.tsx` | 注册 `/admin/scheduler` 路由（`lazyPage` 与现有 admin 页同构） |
| `src/pages/admin/SchedulerPage.test.tsx`（新建） | 4 例：列表渲染、创建调用 payload、删除需确认、tick 需确认 |

**任务分解**

1. 先写 `SchedulerPage.test.tsx`（红）。
2. 建 client + 页面 + 路由（复用现有 `ConsoleShell`/`Button` 模式）。
3. `render.ts` 加两个方法并在 `BackendConfigPage` 展示。
4. 全量跑 + `lint`/`build`。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
Select-String -Path src\services\api\scheduler.ts -Pattern '/api/scheduler'      # 期望 ≥4 命中
Select-String -Path src\router.tsx -Pattern 'SchedulerPage'                       # 期望 ≥2 命中
npx vitest run src/pages/admin/SchedulerPage.test.tsx
npx tsc --noEmit; npm run lint; npm run build
```

**回滚**：删路由与两个新文件；`render.ts` 两方法可保留（无 UI 引用即无效）。

**风险与边界**：`tick` 会真的触发调度（可能花钱）→ 必须二次确认；`DELETE /api/render/artifacts` 是破坏性操作 → 二次确认 + 回显删除数量；不要在前端做 cron 语义推断（只做格式校验，触发时间以后端返回为准）。

**DoD**

- [ ] 调度 CRUD/toggle/tick 全部有 UI 与确认
- [ ] 渲染后端可见、产物清理有确认与结果回显
- [ ] 4 例通过；lint/build 绿

---

### B9 评估 latest 与回链（1.5 人日｜前端单仓）

**目标 / 用户可感知**：评估面板首屏直接给最近一份报告；没跑过时明说"尚无报告"，不是空白。

**前置依赖**：B5（typed `doc_write`/`report` detail 用于回链）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/services/api/eval.ts` | 新增 `latest(): Promise<EvalReport | null>`（`GET /api/eval/latest`，404 → `null`，其他错误抛出） |
| `src/features/pipeline/EvalPanel.tsx` | 首屏改调 `latest()`；`null` 时渲染「还没有跑过评估」+ 「去跑一次」说明；列表保留为次要 tab |
| `src/features/copilot/CopilotDocsTab.tsx` | 报告/文档项加「打开」：`workspaceApi.report(projectId, seq)` / `docs(projectId)` 结果用 `<pre>` 侧栏内展开（不跳出编辑器） |
| `src/features/task/TaskArtifacts.tsx` | artifact 行若有 `path`，加复制按钮与「在工作区查看」跳转（用 `projectStore.projectId`） |
| `src/features/pipeline/EvalPanel.latest.test.tsx`（新建） | 3 例：404 → 空态文案；有报告 → 渲染摘要；网络错误 → 可读错误 + 重试 |
| `src/features/task/TaskArtifacts.test.tsx` | +1 例：带 path 的 artifact 渲染复制按钮 |

**任务分解**：先写 `EvalPanel.latest.test.tsx` → 加 `latest()` → 改面板 → 回链两处 → 全量。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
Select-String -Path src\services\api\eval.ts -Pattern 'eval/latest'         # 期望 1 命中
npx vitest run src/features/pipeline/EvalPanel.latest.test.tsx src/features/task/TaskArtifacts.test.tsx
npx tsc --noEmit; npm run build
```

**回滚**：面板改回列表优先；`latest()` 可保留。

**风险与边界**：`/api/eval/latest` 404 是正常空态（不是错误）；报告路径必须来自后端返回值，前端不得自行拼路径。

**DoD**

- [ ] latest 有 404 → 空态语义
- [ ] 报告/文档/产物都能在编辑器内打开或复制路径
- [ ] 4 例通过

---

### B10 素材复用用例 + 扫描失败明细（1.0 人日｜前端单仓）

**目标 / 用户可感知**：重复上传会明确说"已存在，已复用"；扫描失败时能看到失败文件与原因（而不是只有一个计数）。

**前置依赖**：B2（失败项来自工作区事件）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `src/features/assets/AssetPanel.tsx` | 行为不变（已有 `deduplicated` toast），补 `data-testid="asset-upload-note"` 与 `data-dedup="true"` 便于断言 |
| `src/features/assets/AssetPanel.upload.test.tsx`（新建） | 3 例：① `assetApi.upload` 返回 `{deduplicated:true,status:'reused'}` → 显示「1 个文件已存在，已复用」且不出现 error toast；② 返回 400 → 落到 fallback 分支并提示；③ 成功 → 列表刷新被调用 |
| `src/features/workspace/WorkspaceMaterialsPanel.tsx` | 失败项明细列表（`sha256` 前 8 位 + path + reason），无失败项时「无失败项」；顶部「一键重扫」（复用 B2 的 `scan()`） |

**任务分解**：先写 `AssetPanel.upload.test.tsx`（红）→ 补 testid → 失败项 UI → 全量。

**可证伪验收**

```powershell
cd J:\Clipweight-Client
npx vitest run src/features/assets/AssetPanel.upload.test.tsx     # 3 例通过（含 200 复用断言）
Select-String -Path src\features\assets\AssetPanel.tsx -Pattern 'data-dedup'
npx tsc --noEmit; npm run build
```

**回滚**：删测试文件与 testid 不影响行为。

**风险与边界**：单项重试需要后端新端点（`POST /workspace/{id}/materials/{sha256}/retry` 之类），**本轮不做**，见 §8。

**DoD**

- [ ] 复用 200 有独立用例（不再只靠 build/tsc）
- [ ] 扫描失败项可读
- [ ] 3 例通过

---

### B11 `[P0]` 任务状态机与恢复（2.0 人日｜后端+前端同轮）

**目标 / 用户可感知**：任务结束后点「继续」会被明确拒绝并说明原因；paused/待授权/缺输入/失败/取消各有文案与动作。

**前置依赖**：B5（终态 reason 语义）。

**文件级改动清单**

| 端 | 文件 | 改什么 |
|---|---|---|
| 后端 | `clipwright/api/task.py` | `resume_task`（179–186）加终态守卫：`task.terminal` → `HTTPException(409, detail="任务已结束，无法继续")`，返回体含 `status:"refused"`、`reason:"task_terminal"`；`pause_task` 已有 `KernelError` 路径保持 |
| 后端 | `tests/test_task_api.py` | +2 例：① done 任务 resume → 409 且 `reason=task_terminal`；② paused 任务 resume → 200 且 `resumed=true` |
| 前端 | `src/services/api/task.ts` | `resume` 返回类型改 `{status:string; resumed?:boolean; refused?:boolean; reason?:string}` |
| 前端 | `src/features/task/TaskToolbar.tsx` | 终态（done/failed/cancelled/unknown_terminal）不渲染「继续」；`paused` 显示「继续」，`pending` 显示「开始」，`running/planning` 显示暂停/取消；失败态加「查看结果」 |
| 前端 | `src/stores/taskStore.ts` | `resume` 捕获 409 → `error` 文案「任务已结束，无法继续（已结束任务不能续跑）」并在 `task` 上保留终态 |
| 前端 | `src/features/task/task-ui.test.tsx` | +2 例：终态不显示继续；resume 409 显示拒绝文案 |
| 前端 | `src/features/copilot/CopilotTaskRunTab.tsx` | 顶部状态行按 5 种状态给不同徽章文案（`paused/needs_review/needs/failed/cancelled`） |

**任务分解**

1. 后端先写 2 个 API 用例（红）→ 加守卫（绿）→ 跑 `tests/test_task_api.py`。
2. 前端改 `task.ts` 类型 + `taskStore.resume` 错误处理。
3. `TaskToolbar` 状态分支重写（按 5 态矩阵）。
4. 运行页状态徽章。
5. 双仓全量门禁。

**可证伪验收**

```powershell
cd J:\Clipwright
python -m pytest tests/test_task_api.py -q            # 含「终态 resume 返回 409」
Select-String -Path clipwright\api\task.py -Pattern 'task_terminal'
cd J:\Clipweight-Client
npx vitest run src/features/task/task-ui.test.tsx src/stores/taskStore.test.ts
Select-String -Path src\features\task\TaskToolbar.tsx -Pattern 'done.*failed.*cancelled|terminal'
npx tsc --noEmit; npm run build
```

**回滚**：后端守卫单独 revert 即恢复旧行为；前端按钮禁用与文案可独立回滚（不依赖后端）。

**风险与边界**：409 会让老的"点继续就叫成功"的调用方看到错误 → 错误文案必须可读；不要顺手改 `pause/cancel` 语义；`unknown_terminal` 也要按终态处理（不显示继续）。

**DoD**

- [ ] 终态 resume 后端拒绝且前端可读
- [ ] 5 种状态各有徽章文案与动作
- [ ] 双仓门禁全绿

---

### B12 文档与真实路由清单重建（1.0 人日｜后端单仓）

**目标 / 用户可感知**：排障不再被文档误导（"run-async 是唯一入口"这类话消失）。

**前置依赖**：B1–B11（文档要写最终状态）。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `docs/api_reference.md` | `POST /api/pipeline/run-async` 条目（第 118 行）改为「已退役：`agent_mode=on` 转 Task，其余 410；新入口 `POST /api/requirements/proceed` / `POST /api/task`」；`/retry` 同步标注退役 |
| `docs/frontend-backend-parity.md` | 删掉 225/236 的静态快照口径，改为「由 `scripts/route_inventory.py` 生成，当前 328 个装饰器 / 335 个 method+path」；客户端矩阵补本计划新增的 client（toolSurface/scheduler/workspace events） |
| `scripts/route_inventory.py`（新建） | 从 `app.routes` 导出 `METHOD PATH` 清单（含 `_cache/routes.txt` 生成逻辑），`--out docs/route_inventory.txt` |
| `AGENTS.md` | 「验证清单」补 `npm run lint`、`npm run build`、契约测试路径（若缺失） |
| `docs/route_inventory.txt`（生成物） | 由脚本产出，纳入仓库便于 diff |

**任务分解**：写脚本 → 生成清单 → 改两份文档 → `Select-String` 自查残留"唯一入口"表述。

**可证伪验收**

```powershell
cd J:\Clipwright
python scripts/route_inventory.py --out docs/route_inventory.txt
Select-String -Path docs\api_reference.md -Pattern '唯一入口'          # 期望仅出现在「已退役」语境（人工确认 1 处并标注）
Select-String -Path docs\frontend-backend-parity.md -Pattern '225|236' # 期望 0 命中（改为脚本生成口径）
python -m pytest tests -q | Select-Object -Last 3
```

**回滚**：文档单独 revert；脚本保留无副作用。

**风险与边界**：文档改动不要顺带改产品语义描述（如"SSE-only"不变）。

**DoD**

- [ ] 退役入口在文档里有可读说明
- [ ] 路由清单可一键再生
- [ ] 残留误导表述清零

---

### B13 跨仓库回归与 P0 门禁固化（1.0 人日｜测试与 CI）

**目标**：每个 P0 缺口都有可证伪用例，且门禁在 CI 里跑得到。

**前置依赖**：B1–B12。

**文件级改动清单**

| 文件 | 改什么 |
|---|---|
| `tests/test_event_contract.py` | B5 产物：确认为 CI 必跑（不需要新用例，只需保证路径在 `tests/` 下） |
| `.github/workflows/ci.yml`（后端/前端各一） | 若缺 `npm run lint`/`npm run build`（前端）与 ruff 门禁（后端），补齐；不加长超时 |
| `docs/frontend-gap-prd-2026-10-29.md` §10 | 逐批填执行记录（提交号、验收结果、偏离） |

**任务分解**：核对 CI 步骤 → 补缺失步骤 → 全量跑 6 条门禁 → 填执行记录。

**可证伪验收**

```powershell
cd J:\Clipwright; python -m pytest tests -q; python -m ruff check clipwright --select E9,F63,F7,F82,F823
cd J:\Clipweight-Client; npx tsc --noEmit; npm run test; npm run lint; npm run build
Select-String -Path J:\Clipwright\docs\frontend-gap-prd-2026-10-29.md -Pattern '^\| B[0-9]+ \|'   # 执行记录 13 行都有内容
```

**回滚**：只回滚 CI 增补行。

**风险与边界**：CI 里跑全量后端会慢（实测 ~8 分钟）；不要为了快而删减门禁。

**DoD**

- [ ] 6 条门禁本地全绿
- [ ] CI 覆盖 lint/build/ruff
- [ ] PRD 执行记录 13 行填满

---

## 4. 交叉风险与顺序陷阱

| 共用文件 | 涉及批次 | 顺序要求 / 冲突点 |
|---|---|---|
| `src/types/task.ts` | B5、B7、B11 | 必须先 B5 建 union，再 B7 加 `unknown_cost_calls`，最后 B11 改 `resume` 返回类型；三次都改同一文件，建议连续做 |
| `src/stores/taskStore.ts` | B5、B6、B11 | B5 改终态映射；B6 改 `_foldPlan` 守卫；B11 加 resume 错误处理。**按 B5→B6→B11 顺序**，每步都跑 `taskStore.test.ts` |
| `src/features/copilot/CopilotTaskRunTab.tsx` | B5、B6、B7、B11 | 四批都动：B5 typed detail → B6 分区标题 → B7 用量区块 → B11 状态徽章。建议同一周内做完，避免 rebase 冲突 |
| `src/features/task/EventFeed.tsx` | B5（唯一） | 只有 B5 动；`_NOISY` 删除与折叠要在同一次提交 |
| `src/pages/admin/ProvidersPage.tsx` | B7（唯一） | 与 B3/B8 不冲突，可并行 |
| `src/services/api/pipeline.ts` | B1、B4 | B1 先给旧方法打 `@deprecated`，B4 再加新方法；反过来会出现"新方法挨着旧方法但没标注"的歧义 |
| `src/features/workspace/WorkspaceMaterialsPanel.tsx` | B2、B10 | B2 建 SSE 与失败项数据源，B10 只加明细 UI 与重扫按钮 |

**影响历史任务/回放的改动**：B5（parser 必须容忍旧 detail）、B6（`hypothesis_only` 缺失按假设处理）、B11（历史终态任务不再显示"继续"）—— 三处都要在 PR 描述里写清兼容策略。

---

## 5. 测试策略

| 类型 | 位置与命名 | 说明 |
|---|---|---|
| 前端组件/单元 | 与实现同目录 `*.test.tsx` | 现有范式（`task-ui.test.tsx`、`CopilotRunDetails.test.tsx`） |
| 前端契约 | `src/features/task/eventSchema.test.ts` | 24 类型逐个 parse + 未知类型兜底 |
| 后端 API | `tests/test_task_api.py`（扩）、`tests/test_event_contract.py`（新） | 终态 409、EVENT_TYPES ↔ 前端键集合一致 |
| fixture 复用 | `src/features/copilot/CopilotTaskRunTab.test.tsx` 里的 `events` 数组抽到 `src/test/fixtures/taskEvents.ts`（新建）供 B5/B6/B7/B11 共用 | 避免四批各写一份事件 fixture |
| provider mock | 沿用 `src/pages/admin/ProvidersPage.test.tsx` 的 `vi.hoisted` + `vi.mock('@/services/api')` 范式 | B7 复用 |
| P0 可证伪用例 | G1/G2 → B1；G8 → B11；G10 → B5 | 每个 P0 至少 1 条断言；B13 复核 |

**契约测试的实现方式**（B5/B13 关键）：`tests/test_event_contract.py` 用 `pathlib` 读 `clipwright/schema/task.py` 的 `EVENT_TYPES` 与 `J:\Clipweight-Client\src\features\task\eventSchema.ts` 的 `EVENT_TYPE_KEYS`，两集合必须相等；前端文件不存在时 `pytest.skip("前端仓库不在本机")`，避免 CI 单仓拉取失败。

---

## 6. 提交与回滚策略

- 提交信息沿用仓库风格：`fix(admin): 移除已退役 pipeline 重试入口 (PRD B1)`；跨仓批次两次提交，顺序：**后端先**（`feat(task): 终态 resume 返回 409 (PRD B11)`）→ 前端（`feat(task): 状态机与恢复文案 (PRD B11)`）。
- 每批一个提交（B5/B11 各两个），不合批提交；提交前必须本地跑该批验收 + 该仓库全量门禁。
- 回滚点：每批提交信息里写 `Rollback: git revert <sha>`；破坏性 UI（B8 的清理/触发）在代码里保留 `confirm` 二次确认，不做 feature flag。
- 跨仓联动验证：B5/B11 的验收必须在**两个仓库都跑通**后才算完成（契约测试在前端仓库缺失时会 skip，所以本地必须两仓都在）。

---

## 7. 进度看板

| 批次 | 状态 | 提交 | 验收结果 | 偏离 |
|---|---|---|---|---|
| B1 | 待开始 | | | |
| B2 | 待开始 | | | |
| B3 | 待开始 | | | |
| B4 | 待开始 | | | |
| B5 | 待开始 | | | |
| B6 | 待开始 | | | |
| B7 | 待开始 | | | |
| B8 | 待开始 | | | |
| B9 | 待开始 | | | |
| B10 | 待开始 | | | |
| B11 | 待开始 | | | |
| B12 | 待开始 | | | |
| B13 | 待开始 | | | |

---

## 8. 停止条件 / 升级条件（必须先问人）

1. **PRD 结论与代码不符**：例如某端点其实已不存在，或前端已有实现（B3 的 `ToolsPage` 就属于此类，已在本计划修正）。
2. **需要改后端契约**：新增事件类型、改 `detail` 字段名、改 `EVENT_TYPES`（B5 明确不新增）。
3. **需要动用户配置**：`provider_sources.json`、`.env`、`settings.json`（本计划不碰）。
4. **需要新增后端端点**：工作区"单项重试"就属于此类 —— 前端只能做到"失败项明细 + 一键重扫"；要单项重试必须先加端点并评估并发/幂等。
5. **破坏性操作缺少确认手段**：`DELETE /api/render/artifacts`、`POST /api/scheduler/tick` 若无法二次确认，先停下。
6. **门禁无法保持全绿**：任一必跑门禁失败且 30 分钟内定位不到原因，停下来而不是改门禁阈值。

---

## 9. 附录

### 9.1 G 编号 → 批次索引

| G | 批次 | G | 批次 |
|---|---|---|---|
| G1 | B1 | G10 | B5 |
| G2 | B1 | G11 | B7 |
| G3 | B2 | G12 | B3、B9 |
| G4 | B8 | G13 | B9 |
| G5 | B3 | G14 | B4 |
| G6 | B8 | G15 | B12 |
| G7 | B6 | G16 | B10 |
| G8 | B11 | G17 | B7 |
| G9 | B5 | — | — |

### 9.2 PRD「明确不做」复述（防范围蔓延）

- 不重写 Agent Kernel / tool loop。
- 不恢复旧 pipeline 执行引擎（不把 410 改回真跑）。
- 不把 `/api/market` 账号服务调用并入 Clipwright 主 API（属 Server 仓库）。
- 不新增 WebSocket（SSE-only）。
- 不改渲染算法、成片领域校验、素材视觉模型。
- 不迁移 provider 存储格式。
- 不建通用 prompt 平台。
- 不清理所有历史 UI/文档（只清理会造成当前入口误导的部分）。

### 9.3 计划本身的证据来源

- 缺口条目：`docs/frontend-gap-prd-2026-10-29.md`（含复核修订）。
- 人日：同上 §6 表（总 22）。
- 文件路径：均已在本机 `Test-Path`/`grep` 确认存在；`src/pages/admin/ToolsPage.tsx`、`src/pages/admin/BackendConfigPage.tsx`、`src/router.tsx`、`src/features/task/TaskArtifacts.tsx`、`src/features/pipeline/EvalPanel.tsx` 等为既有文件（扩展而非新建）。
- 门禁基线：后端 4496 passed / 4 skipped / 1 xfailed；前端 1032 passed / 119 files（2026-10-29 实测）。

---

## 10. 执行记录（实施时逐批追加）

| 批次 | 日期 | 提交 | 验收命令与结果 | 未完成项/偏离 | 回滚结果 |
|---|---|---|---|---|---|
| B1 | | | | | |
| B2 | | | | | |
| B3 | | | | | |
| B4 | | | | | |
| B5 | | | | | |
| B6 | | | | | |
| B7 | | | | | |
| B8 | | | | | |
| B9 | | | | | |
| B10 | | | | | |
| B11 | | | | | |
| B12 | | | | | |
| B13 | | | | | |
