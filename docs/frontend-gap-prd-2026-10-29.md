# Clipwright 后端能力 × Clipweight-Client 前端实现缺口 PRD

- 版本：v1.0（2026-10-29）
- 后端：`J:\Clipwright`，Python 3.14 + FastAPI，`feat/master-agent`
- 前端：`J:\Clipweight-Client`，React 19 + TypeScript + Vite + TanStack Router + zustand，`feat/master-agent`
- 本文只记录本次证据核查结果与后续 PRD，不修改任何代码。
- 证据原则：代码与运行路由优先于旧文档；每条结论均给出文件:行号或明确的可复现“无匹配”命令。

---

## 0. 一页结论

### 0.1 最该先做的 3 件事

| 优先级 | 工作 | 原因 | 直接证据 |
|---|---|---|---|
| 1 | **B1：清理已退役 pipeline 调用并统一 Task 入口** | 前端仍有 `run-async`、旧 retry 调用；后端默认关闭旧管线时前者明确返回 410，后者返回退役语义，用户会在“执行诊断”里得到假重试入口。 | 后端 `clipwright/api/pipeline.py:303-321,633-651`；前端 `src/services/api/pipeline.ts:26-31,73-85`、`src/pages/admin/PipelineAdminPage.tsx:120-131` |
| 2 | **B2：补齐任务/事件事实源的前端契约** | 后端事件共 24 类，但前端仍把 `detail` 当无结构字典猜字段；`checkpoint` 被静默过滤；工作区进度**完全没有前端消费**（`eventsUrl()` 是全仓零调用的死方法，SSE `/events/stream` 从未订阅）。 | 后端 `clipwright/schema/task.py:28-55`、`clipwright/api/workspace.py:310-347`；前端 `src/features/task/EventFeed.tsx:28,116`、`src/services/api/workspace.ts:161-163`（grep `eventsUrl` 仅 1 处定义、0 处调用） |
| 3 | **B3：把已有治理能力变成可发现、可操作页面** | `/api/tools`、`/api/scheduler`、渲染后端/产物清理等能力已存在，但前端无客户端或入口；运维人员只能知道结果，不能配置、预览或恢复。 | 后端 `clipwright/api/tool_surface.py:15-53`、`clipwright/api/scheduler.py:14-66`、`clipwright/api/render.py:78-104,1091-1091`；前端全仓无匹配，见 §1 命令 |

### 0.2 最大回归风险

**最大风险是“旧 pipeline 兼容层、Task 原生链路、管理页统计”三套语义同时存在。** 后端 `run-async` 在 `agent_mode=on` 时转 Task、否则 410；管理页仍主动读取历史 pipeline runs 并保留 retry；前端 Task 页面又按 `PlanStep` 折叠事件。一次只改一端会产生“按钮可点但返回 410”“状态显示 running 但实际 paused”“统计混入退役历史”的交叉回归。

### 0.3 与 2026-10-28 PRD 的关系

| 2026-10-28 §9 已修项目 | 本轮不再作为全新缺口 | 仍不完整的残留 |
|---|---|---|
| B1 单层 Copilot 标签 | `CopilotDock`/`AgentPanel` 双层标签已修，证据见旧 PRD §9.2 | 本轮只查同级功能的可发现性，不重列标签缺口。 |
| B2 narration preamble | 已有 `narration → tool_call` 顺序与测试 | 前端仍以通用 `detail` 猜字段；没有完整的 `model_request/model_response` 消费契约。 |
| B3 arbitrary provider/model | 显式 route 已绕过自动路由 whitelist | `arbitrary_models` 的允许策略只在后端 capability 里，前端没有展示“自定义模型是否被该 provider 允许”的可解释状态。 |
| B4 generation 参数 | 配置、保存、工具轮参数已接通 | 前端 `GENERATION_LIMITS.max_tokens=200000` 与旧 PRD 设计一致，但没有展示 provider 实际生效/降级结果。 |
| B5 thinking + reasoning_effort | thinking 事件与降级已接线 | **仍是每轮一块而非 token 级**：旧 PRD §9.5 明确 `with_tools` 走非流式 `generate`，前端却用同一展示语义，用户看不出“未提供”和“每轮聚合”。 |
| B6 `/v1/models` | 四态和保留旧清单已接通 | `fetchModels` 只记录 `status/note`，没有将 source/provider 的实际能力与模型级可用参数做成可解释回显。 |
| B8 script artifact + input_refs | 文案全文落 `docs/script.md`，阶段可读全文 | 前端没有“文案 artifact 已被哪个子 agent 使用”的回链入口。 |
| B9 素材去重 200 复用 | HomePage 已有提示 | **仍不完整**：旧 PRD §9.1 记录 `AssetPanel` 只有 toast、没有独立测试；本轮将其作为可恢复性/测试缺口，不重复列“deduplicated 400”。 |
| B10 Plan 假设态 | `hypothesis_only` 与文案已修 | `taskStore` 仍折叠 `PlanStep` 状态，长任务中会继续制造“步骤进度”暗示，见 G7。 |
| B11 run/审批 UI | `agent_run_*`、审批按钮、failure/needs 已有消费 | 审批入口只覆盖 supervisor exec 路径，工具面目录没有普通用户可见的 pending/approval 统一入口，见 G5/G6。 |
| B12 事件呈现 | `trimmed`、causation、详情展开、400 上限均已修 | `checkpoint` 仍静默丢弃；工作区事件（JSON/SSE）前端从未消费；事件 detail 仍无前后端共享 schema。 |

---

## 1. 核查范围与方法

### 1.1 实际读取材料

| 类别 | 材料 |
|---|---|
| 约束与文档规则 | `J:\Clipwright\AGENTS.md`、`J:\Clipwright\docs\README.md` |
| 主 Agent 设计与执行记录 | `docs/modern-agent-prd-2026-10-27.md` |
| 上一份前端/LLM PRD | `docs/modern-agent-frontend-llm-config-prd-2026-10-28.md`，重点核对 §8 与 §9 |
| 前端缺口收口记录 | `docs/modern-agent-closeout-plan-2026-10-27.md`，重点核对 §9–§11 |
| 对账与 API 文档 | `docs/frontend-backend-parity.md`、`docs/api_reference.md` |
| 后端 | `clipwright/api/*.py`；`schema/task.py`；`tool/surface.py`；指定 services；`api/providers.py` |
| 前端 | `src/services/api/**`、`src/pages/**`、`src/features/**`、`src/stores/**`、`src/types/task.ts` |

### 1.2 可复现命令

以下命令在 PowerShell 5.1 下执行，命令输出是本 PRD 的盘点依据：

```powershell
# 后端真实路由装饰器；再读取每个文件的 APIRouter(prefix=...) 拼接路径
Select-String -Path 'J:\Clipwright\clipwright\api\*.py' `
  -Pattern '@(router|admin_router)\.(get|post|put|delete|patch)\('

Select-String -Path 'J:\Clipwright\clipwright\api\*.py' `
  -Pattern 'APIRouter\(prefix='

# 事件类型逐条查前端消费位置
$events = @('task_start','plan','replan','step_start','tool_call','tool_result','approval_request','approval_result','degradation','artifact','step_end','gate_hit','checkpoint','task_end','inject','narration','intent','thinking','report','doc_write','context_loaded','agent_run_start','agent_run_update','agent_run_end')
foreach ($e in $events) {
  $hit = Select-String -Path 'J:\Clipweight-Client\src\**\*.ts','J:\Clipweight-Client\src\**\*.tsx' -Pattern "['\"]$e['\"]|type.*$e|event.type.*$e"
  "{0}`t{1}" -f $e, @($hit).Count
}

# 工具面与前端发现入口
Select-String -Path 'J:\Clipwright\clipwright\tool\surface.py' -Pattern 'AGENT_TOOLS|DOMAIN_TOOLS|def build_surface|def schemas'
Select-String -Path 'J:\Clipweight-Client\src\**\*.ts','J:\Clipweight-Client\src\**\*.tsx' -Pattern '/api/tools|tool_surface|delegate_agent|research_agent|replan_closure|validate_stage_plan'

# 前端 HTTP 调用面
Get-ChildItem 'J:\Clipweight-Client\src\services\api' -Recurse -File |
  Select-String -Pattern '/api/'

# 指定治理/运维面是否有页面和调用
Select-String -Path 'J:\Clipweight-Client\src\**\*.ts','J:\Clipweight-Client\src\**\*.tsx' `
  -Pattern '/api/scheduler|/api/tools|/api/eval/latest|/api/render/backends|/api/render/artifacts|/api/pipeline/run-batch|/api/pipeline/script-tools|/api/pipeline/topic-suggest'

# 旧 PRD 关键已修项与残留
Select-String -Path 'J:\Clipwright\docs\modern-agent-frontend-llm-config-prd-2026-10-28.md' `
  -Pattern '^### B[0-9]+|偏离|仍未完成|已完成|arbitrary_models|token 级|AssetPanel'
```

### 1.3 覆盖量

| 对象 | 本次盘点结果 |
|---|---:|
| `clipwright/api/*.py` 路由装饰器 | **328 条**（命令实测；含 `router` 与 `admin_router`，路径必须再拼 prefix） |
| 路由文件 | **40 个**含目标装饰器的文件 |
| `EVENT_TYPES` | **24 个**（`schema/task.py:28-55`，逐条查前端） |
| 工具面 | `AGENT_TOOLS` 5 个、`DOMAIN_TOOLS` 2 个，另有注册工具与 Skill 面；`surface.py:35-41,127-127` |
| 前端 API 文件 | `src/services/api/**` 全量，含 `task/providers/workspace/eval/supervisor` |
| 前端页面/feature/store/type | 指定目录全量 glob/grep；重点核查任务运行、Copilot、工作区、Admin、Provider |

> 旧 `frontend-backend-parity.md` 的 225/236 条数字来自旧快照；当前命令实测 328 个装饰器。该文档还把 `/api/pipeline/run-async` 描述为唯一执行入口，而当前代码在默认配置下可返回 410，故本文以当前代码为准，判定旧文档过期。

---

## 2. 现状证据表

| 编号 | 用户可感知症状 | 后端事实（文件:行号） | 前端事实（文件:行号） | 判定 | 严重度 |
|---|---|---|---|---|---|
| G1 | 从旧入口启动制作，页面显示启动失败/410，而新入口实际是 Task。 | `clipwright/api/pipeline.py:303-321`：`agent_mode` 非 `on` 时 `run-async` 返回 410，并指向 `/api/requirements/proceed` 或 `/api/task`。 | `src/services/api/pipeline.ts:26-31` 仍暴露 `run-async`；`src/features/agent/ReviewPanel.tsx:127-127` 注释显示曾存在直连旧入口。 | 属实 | P0 |
| G2 | 在执行诊断页点“重试”，界面给出失败或退役提示，用户以为能恢复任务。 | `clipwright/api/pipeline.py:633-651` 的旧 retry 路径已返回 410；监督派工返回 `execution_status=retired`，`clipwright/api/supervisor.py:223-233,279-289`。 | `src/pages/admin/PipelineAdminPage.tsx:120-131` 仍调用 `pipelineApi.retry`；`src/services/api/pipeline.ts:73-85` 仍有 retry 方法。 | 属实 | P0 |
| G3 | 工作区扫描/索引过程看不到持续进度：只看到按钮转圈与最终计数，中途失败也只有一个字符串。 | `/events` 是即时返回的 JSON（`clipwright/api/workspace.py:310-323`，注释说明拆两个端点就是为了避免长连接挂死），SSE 是 `/events/stream`（`326-347`）。 | `src/services/api/workspace.ts:161-163` 的 `eventsUrl()` 生成的是 JSON 端点 URL，且**全仓 0 处调用**（`grep eventsUrl` 只命中定义）；`WorkspaceMaterialsPanel.tsx` 只调 `scan` 后 reload，既不轮询也没订阅 SSE。 | 属实（真实缺口=进度未消费；原判"协议错配"是误读，JSON 端点本身正确） | P1 |
| G4 | 用户看不到调度任务的创建、开关、删除与错过触发记录。 | `/api/scheduler` 路由为 `GET/POST /schedules`、`DELETE`、`toggle`、`tick`，`clipwright/api/scheduler.py:14-66`。 | 指定前端目录 grep `/api/scheduler` 无匹配；`src/pages/**` 无调度页面（以 §1 命令复现）。 | 属实 | P1 |
| G5 | 用户/管理员无法从 UI 查看主 Agent 实际可见的工具面、权限档、可用性和覆盖率。 | `/api/tools` 目录、schema、records 存在，`clipwright/api/tool_surface.py:15-53`；工具面含 AGENT_TOOLS/DOMAIN_TOOLS，`clipwright/tool/surface.py:35-41,125-127,289-317`。 | 前端 grep `/api/tools|tool_surface|delegate_agent|replan_closure` 无匹配；`src/services/api/tool.ts:28-43` 只对应旧 `/api/tool/list|execute|batch`。 | 属实 | P1 |
| G6 | 渲染后端选择、有效回退后端、过期成片清理没有操作入口。 | `/api/render/backends`，`clipwright/api/render.py:1091`；`DELETE /api/render/artifacts`，`78-104`。 | `src/services/api/render.ts:40-107` 没有 `backends` 或 `artifacts` 方法；指定目录 grep 无匹配。 | 属实 | P1 |
| G7 | 长任务仍可能把“初始假设”看成真实执行进度，步骤停在 pending 时用户误判卡住。 | 后端 Plan 明确是 `hypothesis_only`，`clipwright/schema/task.py:81-94`。 | `src/stores/taskStore.ts:64-110` 仍按 `plan.steps` 接收 `step_start/tool_result/step_end` 并更新状态；`src/features/copilot/CopilotTaskRunTab.tsx:327-329` 同时展示 `PlanPanel` 和阶段行。 | 属实（旧 B10 修复后仍不完整） | P1 |
| G8 | 任务被暂停后可继续；但已结束任务也可能被"继续"重新调度，UI 无从得知被拒绝的原因。 | `clipwright/api/task.py:179-186` 的 `resume_task` 没有终态检查，直接 `_schedule` 并返回 `status=running`；`agent_kernel.pause/cancel` 有 `task.terminal` 守卫（`agent_kernel.py:443,453`），`kernel.run`（`518-528`）没有。 | `src/features/task/TaskToolbar.tsx:26-39` 对 `paused/pending` 显示开始/继续；`src/services/api/task.ts:53-61` 只声明 `{status:string}`，没有"被拒/未发生"的结果模型。 | 属实 | P1 |
| G9 | 事件列表看不到 checkpoint，无法解释"这个任务从哪个检查点恢复/续跑过"。 | `EVENT_TYPES` 含 `checkpoint`（`clipwright/schema/task.py:41`），kernel 在暂停/收尾时写检查点。 | `src/features/task/EventFeed.tsx:28` 把 `checkpoint` 放进 `_NOISY`，`116` 整行过滤。**注**：过滤初衷是降低噪声，但它同时藏掉了"从哪恢复"的事实；应改为默认折叠而非丢弃。 | 属实（设计取舍引发的可见性缺口） | P1 |
| G10 | 前端对工具、阶段、审批等事件字段的解释随后端 detail 改名而失真。 | 后端事件信封只保证 `detail: dict[str, Any]`，`clipwright/schema/task.py:97-109`；不同服务写入字段分散。 | `src/features/copilot/CopilotTaskRunTab.tsx:20-31,117-149,236-240` 直接猜 `stage/status/tool/approval_id`；`src/features/copilot/RunningSubagents.tsx:34-69` 同样按字符串读取。 | 属实 | P0 |
| G11 | 成本未知、成本闸、调用次数在任务页不完整显示，用户可能把未知成本当 0。 | 后端 `TaskUsage` 有 `unknown_cost_calls`，`clipwright/schema/task.py:150-157`；工具面成本未知用 `None`，`clipwright/tool/surface.py:470-485`。 | `src/types/task.ts:86-92` 的 `TaskUsage` 没有 `unknown_cost_calls`；`CopilotTaskRunTab.tsx:310-338` 未展示 usage/unknown cost。 | 属实 | P1 |
| G12 | 子 agent 报告、artifact、上下文装载能力存在，但没有从工具面目录发现并跳转的入口。 | `GET /api/tools` 只返回目录，工具说明明确含 `report_path`/`AgentResult`，`clipwright/tool/surface.py:43-69,89-113`；工作区报告/文档端点在 `clipwright/api/workspace.py:168-237`。 | 前端虽有 `workspaceApi.reports/report/docs`，`src/services/api/workspace.ts:166-195`，但工具面目录无客户端；Copilot 只能依赖事件中的路径，`EventFeed.tsx:33-45,77-86`。 | 部分 | P1 |
| G13 | 评估报告列表能看，最近报告快捷入口/404 空态没有统一处理。 | `/api/eval/latest` 无报告时返回 404，`clipwright/api/eval.py:79-87`。 | `src/services/api/eval.ts:48-59` 只有 list/get，没有 latest；`src/features/pipeline/EvalPanel.tsx` 只走列表/详情（grep 可复现）。 | 属实 | P2 |
| G14 | 批量创建、脚本工具、选题建议等后端能力在前端没有调用方法，用户只能手工绕 API。 | `clipwright/api/pipeline.py:330-355` 的 `/run-batch`；`811-873` 的 `/script-tools`、`/predict-script`、`/predict-material`、`/topic-suggest`。 | `src/services/api/pipeline.ts:1-130` 无 `runBatch/scriptTools/topicSuggest`；全仓对应路径 grep 仅命中后端/文档。 | 属实 | P1 |
| G15 | 文档与代码对入口、重试语义的说明冲突，排障人员按文档操作会走错误链路。 | 当前 `/run-async` 410 与监督派工退役见 `clipwright/api/pipeline.py:303-321`、`clipwright/api/supervisor.py:215-233`。 | 旧文档 `docs/api_reference.md:118-124,140-148` 仍称 `run-async` 唯一入口、dispatch 会真跑；对账文档 `frontend-backend-parity.md:68-72` 也保留旧真跑叙述。 | 属实 | P1 |
| G16 | 素材扫描失败后只有一条 note，无法按文件重试/查看错误详情；视觉降级原因也只在素材表内部。 | 工作区扫描返回 `failed/deferred/items`，`clipwright/api/workspace.py:152-165`；素材状态结构在 `clipwright/services/workspace.py:411-440`。 | `WorkspaceMaterialsPanel.tsx:50-75,115-125` 只汇总计数/字符串 note，没有事件流消费、失败项操作或详情展开。 | 属实 | P1 |
| G17 | 模型列表能力已配置，但“自定义模型是否允许、参数是否实际生效”在 UI 中没有结果态。 | `provider_sources` 对 generation/reasoning 归一化，`clipwright/services/provider_sources.py:247-305,333-344`；Provider fetch 状态在 `clipwright/api/providers.py:162-201`。 | `ProvidersPage.tsx:159-185,235-268` 只展示保存/获取 note；`providers.ts:231-251` 返回 status/errorCode，但没有 effective capability/degradation 字段。 | 部分 | P1 |

> 本表共 17 条，其中 **P0=3**（G1、G2、G10）、**P1=13**（G3–G9、G11、G12、G14–G17）、**P2=1**（G13）。
> 每条均有代码行号；「未核实」项不纳入缺口计数。
> **审稿修订（2026-10-29 复核）**：G3 原判 P0 + “SSE 协议错配”经复核为误读——`/events` 是设计好的 JSON 端点，
> 且 `eventsUrl()` 全仓零调用；真实缺口是“工作区进度完全未消费”，故降为 P1 并改写证据。
> 另：`EVENT_TYPES` 实际 **24** 项（`schema/task.py:28-55`），初稿里 27/29 的说法已更正；
> 严重度计数初稿写「P1=12 / P2=2」，按本表逐行统计实为 **P1=13 / P2=1**，已更正。

---

## 3. 接口缺口矩阵

### 3.1 后端有、前端无客户端或无入口

| 分组 | 后端真实路径（文件:行号） | 当前前端状态 | 归属批次 |
|---|---|---|---|
| 任务与事件 | `POST /api/pipeline/run-batch`、`POST /api/pipeline/script-tools`、`POST /api/pipeline/topic-suggest`：`clipwright/api/pipeline.py:330-355,811-873` | 无 `pipelineApi` 方法，无页面入口 | B4 |
| 任务与事件 | `GET /api/eval/latest`：`clipwright/api/eval.py:79-87` | 无 `evalApi.latest`，只能列表后自行挑 | B5 |
| 子 agent 与审批 | `GET /api/tools`、`GET /api/tools/schema`、`GET /api/tools/records`：`clipwright/api/tool_surface.py:15-53` | 旧 `/api/tool/*` 有客户端，Kernel 工具面没有客户端/页面 | B3 |
| 子 agent 与审批 | 普通工具面中的 `replan_closure`/`validate_stage_plan`：`clipwright/tool/surface.py:125-127,256-286` | 无独立可发现入口；只可能由模型调用 | B3 |
| 工作区与素材 | `GET /api/workspace/{id}/events`（JSON）/ `/events/stream`（SSE）：`clipwright/api/workspace.py:310-347` | 两个端点都**没有任何前端消费**；`eventsUrl()` 生成 JSON URL 但从未被调用 | B2 |
| 工作区与素材 | `GET /api/render/backends`、`DELETE /api/render/artifacts`：`clipwright/api/render.py:78-104,1091` | 无 render client 方法、无设置/运维入口 | B6 |
| 模型提供商与配置 | `ProviderCapabilities.arbitrary_models/reasoning_effort/generation_params` 的实际结果 | provider 页面仅保存请求值，不展示 effective/degradation | B7 |
| 评测与提示词 | `/api/eval/latest`（同上）及 prompt candidate 的后端服务能力 | `evalApi` 只有报告列表/详情；没有 candidate registry 操作入口 | B5/B7 |
| 运维 | `/api/scheduler/*`：`clipwright/api/scheduler.py:14-66` | 无 `/api/scheduler` 调用，无页面 | B8 |
| 运维 | `/api/stats/usage`、插件 health/contributions（具体路由见路由清单） | 部分被 `project.ts` 复用或仅显示插件列表；未形成统一运维诊断入口 | B8 |

### 3.2 前端调用但后端不存在/语义已退役

| 前端调用 | 证据 | 后端事实 | 判定 |
|---|---|---|---|
| `POST /api/pipeline/run-async` | `src/services/api/pipeline.ts:26-31` | 路由存在但默认/非 agent_mode 返回 410，`clipwright/api/pipeline.py:303-321` | 语义幽灵：路径存在，执行能力已退役 |
| `POST /api/pipeline/retry/{id}/{agent}` | `src/services/api/pipeline.ts:73-85`；管理页 `PipelineAdminPage.tsx:120-131` | 后端 retry 路径返回 410；监督派工只登记，`supervisor.py:223-233,279-289` | 语义幽灵：路径存在，UI 仍宣称可恢复 |
| `/api/workspace/{id}/events` 作为 SSE URL | `src/services/api/workspace.ts:161-163`（`eventsUrl()`，全仓 0 处调用） | 该路径是设计好的 JSON 端点（`workspace.py:310-323`），SSE 在 `/events/stream`（`326-347`） | 不是协议错误：是**死方法 + 功能未消费**，按 B2 补 SSE 消费并清理或改造 `eventsUrl()` |
| `/api/market/*/rating`、`POST /api/market/*` | `src/services/api/market.ts:66-83` | 这些调用通过 `_srv` 账号/市场服务，不属于 Clipwright 主 API；Clipwright `api/market.py:19-73` 没有 rating/upload | 不属主后端缺口，需在 PRD 中明确服务归属 |

### 3.3 字段级不一致

| 字段 | 后端事实 | 前端事实 | 影响 |
|---|---|---|---|
| `TaskUsage.unknown_cost_calls` | `schema/task.py:150-157` | `types/task.ts:86-92` 缺失 | 未知成本被隐藏，预算判断不可解释 |
| `context_loaded.detail.trimmed` | 已由旧 B12 改为被裁段名 list，旧 PRD §9.8 记录 | 前端事件 detail 仍是 `Record<string, unknown>`，没有 `string[]` 类型/渲染约束 | 新字段可进入 UI，但编译器无法阻止 bool/list 漂移 |
| `approval_request.detail.approval_id/risk/status` | 事件产生方分散于工具/监督路径；`task.py:35-37` 只定义事件名 | `CopilotTaskRunTab.tsx:236-240` 直接猜字段，`status` 空即 pending | 事件字段变更会把已结束审批再次显示为待处理 |
| `agent_run_* detail.run_id/status/failure/needs` | 工具说明返回 AgentResult，`surface.py:43-49,89-113` | `RunningSubagents.tsx:34-69`、`CopilotSubagentTab.tsx:55-68` 各自解析 | 同一 run 的状态/失败语义可能分裂 |
| `Workspace events` | JSON `/events` 返回 `{events,last_seq}`，SSE `/events/stream` 返回 `id/data` | `eventsUrl` 未表达 stream 协议，也没有 `last_seq` client 类型 | 重连游标与批量拉取语义混用 |
| `ProviderModel.generation` | 后端 normalize 会丢弃非法键/值，`provider_sources.py:283-305` | 前端允许 `generation`，但无服务端 effective/degradation 回填 | 用户看到保存值，不等于实际下发值 |

---

## 4. 事件缺口矩阵

> `EVENT_TYPES` 当前实际为 `clipwright/schema/task.py:28-55` 的 **24** 项。前端 union 已覆盖名称，但“有 union”不等于“有消费”：以下按实际渲染、投影或显式处理判定。

| 事件类型 | 前端消费位置 | 当前判定 | 证据/问题 |
|---|---|---|---|
| `task_start` | TaskStore 通用入 store，EventFeed 通用行 | 有消费但弱 | 无专用 detail 类型，用户只看到原始 message。 |
| `plan` | `taskStore.ts:69-70`、EventFeed 可展开 | 已消费 | 仍与 hypothesis/执行状态混在同一 projection，见 G7。 |
| `replan` | `taskStore.ts:72-77`、EventFeed 可展开 | 已消费 | 没有“为什么重规划/新增约束”结构化展示。 |
| `step_start` | `taskStore.ts:86-91`、Copilot StageRows | 已消费 | 仍把阶段当执行主视图。 |
| `tool_call` | `CopilotTaskRunTab.tsx:114-123`、EventFeed | 已消费 | `detail.tool` 为猜测字段。 |
| `tool_result` | `taskStore.ts:92-100`、`pairToolCalls` | 已消费 | B12 已按 causation 优先；旧事件仍按名 FIFO。 |
| `approval_request` | `CopilotTaskRunTab.tsx:198-280` | 已消费但局部 | 只覆盖 supervisor exec 提交 API，不是统一 approval service。 |
| `approval_result` | 同上 | 已消费 | 结果与 request 由 seq 行展示，未按 approval_id 折叠。 |
| `degradation` | `EventFeed.tsx:30-32,68-70` | 已消费但弱 | 没有 provider/model/effective 字段类型；容易只显示红色错误。 |
| `artifact` | EventFeed 通用行、TaskArtifacts | 部分消费 | 事件行只按路径 key 猜，artifact schema/hash/大小没有统一详情。 |
| `step_end` | StageRows、taskStore | 已消费 | 与 `agent_run_end` 并存，UI 没有明确事实源优先级。 |
| `gate_hit` | EventFeed 通用行；TaskStore保留 task | 部分消费 | 没有使用量/未完成项专用面板。 |
| `checkpoint` | **无可见消费** | 发了但没人看 | `EventFeed.tsx:28,115-116` 把它放 `_NOISY` 并过滤。 |
| `task_end` | `taskStore.ts:157-169`、EventFeed | 已消费 | reason 映射集中在前端，新增后端 reason 仍默认 failed。 |
| `inject` | EventFeed 可展开、TaskStore 事件留存 | 已消费 | 没有显示注入文本是否已被下一轮模型读取。 |
| `narration` | ModelTurnRows | 已消费 | `detail.kind` 只在前端猜 `preamble`。 |
| `intent` | ModelTurnRows | 已消费 | 没有结构化 intent schema。 |
| `thinking` | ThinkingDisclosure、ModelTurnRows | 已消费 | B5 残留：工具轮每轮一块而非 token 级；无 `done`/round 类型。 |
| `report` | EventFeed 可展开、Workspace reports | 部分消费 | 没有 report ref 与任务/子 run 的稳定跳转类型。 |
| `doc_write` | EventFeed 可展开 | 部分消费 | 路径可复制，但没有文档内容刷新与来源 run 关联。 |
| `context_loaded` | EventFeed 可展开 | 部分消费 | 前端不区分 `sources/trimmed/bytes/budget`，只展示 JSON。 |
| `agent_run_start` | RunningSubagents/CopilotSubagentTab | 已消费 | 两个组件各自 fold，缺统一 selector。 |
| `agent_run_update` | 同上 | 已消费但状态枚举开放 | `status` 为字符串，`needs_review/needs` 等没有共享 union。 |
| `agent_run_end` | 同上 | 已消费 | report/failure/needs 字段仍按 Record 猜。 |

### 4.1 明确的事件契约缺口

1. 后端 `TaskEvent.detail` 是开放字典，前端 `TaskEvent` 也是 `Record<string, unknown>`（`schema/task.py:97-109`、`types/task.ts:124-135`），没有按事件类型映射的 discriminated union。
2. `task_end.reason` 仍由前端自建白名单（`taskStore.ts:53-61`），后端新增终态原因会静默落入 failed。
3. workspace 事件不是 `TaskEvent`，但前端没有独立 `WorkspaceEvent` 类型和 SSE 消费器，造成 `eventsUrl` 协议错配。

---

## 5. UX 缺口清单

| 类型 | 缺口 | 证据 | 用户影响 |
|---|---|---|---|
| 空态 | `/api/eval/latest` 没有前端快捷读取，报告为空时只能进入列表后得到空数组。 | 后端 `eval.py:79-87`；前端 `eval.ts:48-59` | 新用户不知道“还没有跑过评估”是正常空态还是加载失败。 |
| 加载态 | 工作区扫描仅显示按钮 loading，不展示扫描事件流/每个文件进度。 | `WorkspaceMaterialsPanel.tsx:50-63,94-110`；后端已有 SSE `workspace.py:326-347` | 大目录扫描时页面像卡住，失败和仍在运行不可区分。 |
| 错误语义 | 旧 pipeline retry 的 410 与普通网络错误共用前端 retry 错误文案。 | `PipelineAdminPage.tsx:120-131`；后端 `pipeline.py:633-651` | 用户重复点击一个已退役动作，无法理解替代入口。 |
| 状态语义 | paused、awaiting approval、needs input、failed 没有统一状态机。 | 后端 TaskStatus `schema/task.py:20-25`；前端 `TaskToolbar.tsx:26-39`、`CopilotTaskRunTab.tsx:239` | “暂停”“待授权”“缺输入”可能都被理解为失败或可直接继续。 |
| 文案 | EventFeed 对未专门映射的事件显示内部类型/原始 message；StageRows 直接显示 stage 名。 | `EventFeed.tsx:60-74`；`CopilotTaskRunTab.tsx:40-47` | 英文裸字段、内部阶段名外泄，用户无法判断下一步。 |
| 可发现性 | 工具面目录、调度器、渲染后端/清理能力没有入口。 | G4–G6 | 后端“有能力”不等于产品“可用”。 |
| 可恢复性 | 工作区失败项没有逐项重试；工作区事件（JSON/SSE）都未被消费，断线也没有 `last_seq` 游标可用。 | `WorkspaceMaterialsPanel.tsx:124-125`；`workspace.ts:161-163`（`eventsUrl()` 零调用） | 扫描部分失败只能整批再扫，耗时和费用不可控。 |
| 可恢复性 | 任务结果没有 timeline 时，`to-project` 409 只有后端 detail，无前端专用修复动作。 | `clipwright/api/task.py:265-273`；`task.ts:96-104` | 用户无法从结果页回到“补输入/重试/继续任务”。 |
| 一致性 | 管理页同时称“主 Agent 任务与历史管线”，又保留老 runs 的成功率和 retry。 | `PipelineAdminPage.tsx:38-43,138-149` | 指标口径混合，用户误以为历史 pipeline 仍是生产执行源。 |
| 信息密度 | 任务页同时显示 PlanPanel、阶段、模型轮次、工具、授权、EventFeed、子 agent、产物。 | `CopilotTaskRunTab.tsx:326-338` | 长任务中关键结论/阻塞原因被大量事件淹没。 |
| 测试可见性 | B9 AssetPanel 复用只 toast，没有独立用例。 | 旧 PRD §9.1（line 380）明确记录偏离；`AssetPanel.tsx:113-131` | 去重回归只能靠构建/手工观察，容易重新把 200 复用当错误。 |

---

## 6. 分批计划

> 不重复 2026-10-28 PRD 的 B1–B12；本轮批次从 B1 重新编号，目标只覆盖本文件新发现或旧修复后的残留。

| 批次 | 目标 | 改动面（具体文件） | 可证伪验收（命令 + 断言） | 回滚 | 风险 | 人日 |
|---|---|---|---|---|---|---:|
| B1 | 退役 pipeline 前端动作，统一 Task 入口 | `src/services/api/pipeline.ts`、`PipelineAdminPage.tsx`、`ReviewPanel.tsx`、相关 router/测试；必要时 `docs/api_reference.md` | `Select-String src -Pattern 'run-async|pipeline/retry'` 只允许兼容测试；前端测试断言新建制作调用 `/api/requirements/proceed`/`/api/task`，历史页无 retry 按钮；后端 `python -m pytest tests -q` | 恢复旧 UI 文件，不恢复已退役后端执行语义 | 旧外部调用者依赖兼容方法；保留只读提示与 410 文案 | 1.5 |
| B2 | 修正 workspace SSE、补扫描进度与恢复 | `src/services/api/workspace.ts`、`WorkspaceMaterialsPanel.tsx`、workspace feature tests；必要时 `src/types` | `Select-String src -Pattern 'events/stream'` 命中 client；测试断言 URL 含 `/events/stream`、seq 去重、断线从 last seq 恢复；后端 `pytest` 工作区测试 | 回退为 JSON reload；保留后端 stream | SSE 生命周期、重复连接、组件卸载竞态 | 1.5 |
| B3 | 工具面/AgentRun/审批统一目录与发现入口 | 新增 `src/services/api/toolSurface.ts`；`src/pages/admin/ToolsPage.tsx` 或 `SupervisorAdminPage.tsx`；`src/types/toolSurface.ts`；共享 run selector | GET `/api/tools`、`/schema`、`/records` mock 测试；断言 5 个 AGENT_TOOLS、2 个 DOMAIN_TOOLS 可见，dangerous 不显示为直调；`npx tsc --noEmit`、`npm run test` | 页面可隐藏，客户端只读方法保留 | 目录是快照，不得误做 HTTP 执行入口 | 2.0 |
| B4 | 补齐批量任务/脚本/建议客户端与入口 | `src/services/api/pipeline.ts`、`src/pages/HomePage.tsx` 或新 `TaskBatchPage.tsx`、测试 | grep 断言 `/run-batch`、`/script-tools`、`/topic-suggest` 只有 typed client；mock 断言 400/410 可读分流；不改变旧 Task 主路径 | 新入口 feature flag/路由隐藏 | 能力属于旧 pipeline 与 Task 的混合边界，先做只读预测/批量提交 | 2.0 |
| B5 | 事件 discriminated union 与终态/审批语义收口 | `src/types/task.ts`、`src/stores/taskStore.ts`、`EventFeed.tsx`、`CopilotTaskRunTab.tsx`、后端 `schema/task.py`/事件写入点、测试 | 生成/维护事件 fixture；断言每个 EVENT_TYPES 有 parser；checkpoint 可展开；未知 reason 显示“后端终态：…”而非 failed；`python -m pytest tests -q` + `npx tsc --noEmit` | 保留 Record 兼容 adapter，回退 parser 不回退事件 | 后端旧事件 detail 形状多，需 legacy parser | 3.0 |
| B6 | Plan 与真实 Run 双轨视觉分离 | `taskStore.ts`、`PlanPanel.tsx`、`CopilotTaskRunTab.tsx`、task tests | 断言 `hypothesis_only=true` 不更新“进度”徽章；真实进度只来自 tool/agent/run 事件；`npm run test -- task-ui` | 恢复旧 PlanPanel 展示但不改变后端 | 用户习惯旧步骤视图；需保留历史任务兼容 | 1.5 |
| B7 | 成本、degradation、effective route 可解释 | `src/types/task.ts`、`src/types/api.ts`、`ProvidersPage.tsx`、`CopilotTaskRunTab.tsx`、providers tests | fixture 含 `unknown_cost_calls`、`requested/effective/degradation`；UI 明确显示未知成本与忽略参数；`npm run test`、`npx tsc --noEmit` | 隐藏新增列/卡片，保留后端字段 | provider 返回旧字段、不同 provider capability 差异 | 1.5 |
| B8 | 调度与运维入口 | 新增 `src/services/api/scheduler.ts`；`src/pages/admin/SchedulerPage.tsx`；扩展 render client 与 `SupervisorAdminPage.tsx`；路由/测试 | API mock 覆盖 schedule CRUD/toggle/tick、render backends/artifacts；页面有 loading/empty/error/retry；`npm run lint && npm run build` | 路由和导航独立回滚 | 破坏性清理必须二次确认；调度 tick 不能误触发 | 2.5 |
| B9 | 评估报告 latest、报告/Artifact/Doc 回链 | `src/services/api/eval.ts`、`EvalPanel.tsx`、`CopilotDocsTab.tsx`、`TaskArtifacts.tsx`、workspace types/tests | 404 latest 显示“尚无报告”；report path 能按 project/run 跳转；fixture 断言 hash/path/preview；`npm run test` | 保留列表/详情旧路径 | 报告路径跨 workspace，需只允许后端返回 ref | 1.5 |
| B10 | 素材扫描失败项恢复与 B9 独立测试 | `AssetPanel.tsx`、`WorkspaceMaterialsPanel.tsx`、对应 tests、`src/types/api.ts` | 重复上传断言 200、`status=reused`、同 asset_id/file_path、used_count +1；扫描失败项可单项重试；`npm run test -- AssetPanel` | 仅回滚 UI 重试控件，不恢复错误 toast | 大文件/视觉索引异步状态竞态 | 1.0 |
| B11 | 任务结果恢复路径与状态机 | `task.ts` client、`TaskToolbar.tsx`、`TaskListSection.tsx`、`CopilotTaskRunTab.tsx`、后端 `api/task.py` 测试 | 终态 resume 返回 409 或结构化 refused；paused/approval/needs/failed 各有按钮与文案；API/前端状态机测试 | 先只在前端禁用终态按钮，后端保持兼容字段 | 历史任务状态缺失，需 legacy 映射 | 2.0 |
| B12 | 文档与真实路由清单重建 | 只改 `docs/api_reference.md`、`docs/frontend-backend-parity.md`、前端 docs 副本（实现阶段才改） | 用 §1 路由命令生成清单；断言文档不再称 run-async 唯一入口、不再称 dispatch 真跑；全文 `Select-String` 只保留当前语义 | 文档单独回滚，不影响代码 | 文档快照/分支差异造成再次漂移 | 1.0 |
| B13 | 跨仓库回归与 P0 门禁 | 不新增生产代码；测试文件与 CI 配置按需 | 执行 §8 全部命令；每个 G1/G3/G10/G11 P0 有对应测试；前端 build/lint 全绿 | 只回滚测试/门禁变更 | 长任务 SSE 与 provider 测试耗时 | 1.0 |

### 6.1 依赖顺序

`B1 → B5 → B6/B7/B11`；`B2 → B5/B9/B10`；`B3 → B7/B8`；`B4` 可与 B3 并行；`B12` 在 B1–B11 后执行；`B13` 最后执行。P0 顺序为 B1、B2、B5、B11。

---

## 7. 明确不做

| 不做项 | 本轮边界 |
|---|---|
| 不重写 Agent Kernel/tool loop | 本轮只核查前端可见性、协议与恢复，不改变 `agent_kernel` 的执行架构。 |
| 不恢复旧 pipeline 执行引擎 | 不把 410 改回真跑，不恢复 `start_agent_retry`；前端应显示退役及替代入口。 |
| 不把 `/api/market` 账号服务调用并入 Clipwright 主 API | `src/services/api/market.ts` 的 `_srv` 调用另属 Server 仓库，本轮只记录归属，不改 Server。 |
| 不新增 websocket | 项目约定 SSE-only；workspace/task/render 均按 SSE 处理。 |
| 不改成片领域确定性校验、渲染算法、素材视觉模型 | 只补其已有后端能力的前端入口/状态。 |
| 不迁移 Provider 存储格式 | 不改变 `provider_sources.json`，只展示实际 capability/effective 结果。 |
| 不建设通用 prompt 平台 | 评估只接现有 `/api/eval` 报告与已有 prompt registry，不扩展为 SaaS。 |
| 不清理所有历史 UI/文档 | 仅清理会造成当前入口误导的 run/retry/协议说明；无关遗留文案另开任务。 |

---

## 8. DoD

### 8.1 自动化门禁

- [ ] 后端 `python -m pytest tests -q` 全绿；任何计数变化附测试增删解释。
- [ ] 后端 `ruff check clipwright --select E9,F63,F7,F82,F823` 返回 0。
- [ ] 前端 `npx tsc --noEmit` 返回 0。
- [ ] 前端 `npm run test` 全绿。
- [ ] 前端 `npm run lint` 无 error。
- [ ] 前端 `npm run build` 全绿。
- [ ] 每个 P0 缺口都有对应用例：G1、G2、G10 各有后端/前端可证伪断言；G3（工作区进度）按 P1 要求补 SSE 用例。

### 8.2 契约与 UX 门禁

- [ ] 路由清单由 §1 命令重新生成；真实 prefix 已拼接，未把 admin router 混成普通 router。
- [ ] 前端不再把 `run-async`/旧 retry 作为新任务或恢复任务按钮；历史只读页明确标注退役。
- [ ] workspace JSON 拉取与 workspace SSE URL 分离，SSE 能按 seq 恢复、去重、卸载关闭。
- [ ] `EVENT_TYPES` 每项有 parser/通用 fallback；`checkpoint` 不再静默丢弃；未知事件可折叠显示。
- [ ] `task_end` 新 reason 不再默认伪装成 failed；paused、approval、needs、failed、cancelled 有独立文案和操作。
- [ ] `TaskUsage.unknown_cost_calls`、provider degradation/effective route 在 UI 可见。
- [ ] 工具面目录展示 AGENT_TOOLS、DOMAIN_TOOLS、tier、available、cost_hint；不开放 HTTP 任意执行。
- [ ] 调度器、渲染后端/清理、评估 latest、报告/文档回链均有 loading/empty/error/retry。
- [ ] B9 素材复用有独立测试，不只依赖 toast、tsc、build。

---

## 9. 附录

### 9.1 核查命令清单

```powershell
# 路由与 prefix
Select-String -Path 'J:\Clipwright\clipwright\api\*.py' -Pattern '@(router|admin_router)\.(get|post|put|delete|patch)\('
Select-String -Path 'J:\Clipwright\clipwright\api\*.py' -Pattern 'APIRouter\(prefix='

# EVENT_TYPES
Select-String -Path 'J:\Clipwright\clipwright\schema\task.py' -Pattern '"[a-z_]+"'
Select-String -Path 'J:\Clipweight-Client\src\**\*.ts','J:\Clipweight-Client\src\**\*.tsx' -Pattern 'event\.type|TaskEventType|checkpoint|agent_run_'

# 工具面
Select-String -Path 'J:\Clipwright\clipwright\tool\surface.py' -Pattern 'AGENT_TOOLS|DOMAIN_TOOLS|build_surface|schemas'
Select-String -Path 'J:\Clipwright\clipwright\api\tool_surface.py' -Pattern '@router\.'
Select-String -Path 'J:\Clipweight-Client\src\**\*.ts','J:\Clipweight-Client\src\**\*.tsx' -Pattern '/api/tools|delegate_agent|research_agent|replan_closure|validate_stage_plan'

# 前端调用与幽灵路径
Get-ChildItem 'J:\Clipweight-Client\src\services\api' -Recurse -File | Select-String -Pattern '/api/'
Select-String -Path 'J:\Clipweight-Client\src\**\*.ts','J:\Clipweight-Client\src\**\*.tsx' -Pattern 'run-async|pipeline/retry|events/stream|/api/scheduler|/api/render/backends|/api/render/artifacts'

# 旧 PRD 实施记录核对
Select-String -Path 'J:\Clipwright\docs\modern-agent-frontend-llm-config-prd-2026-10-28.md' -Pattern '^### B|偏离|仍未完成|arbitrary_models|token 级|AssetPanel'

# 回归门禁
python -m pytest tests -q
ruff check clipwright --select E9,F63,F7,F82,F823
npx tsc --noEmit
npm run test
npm run lint
npm run build
```

### 9.2 发现但未纳入本轮缺口清单的次要问题

| 问题 | 证据 | 不纳入原因 |
|---|---|---|
| `models` 管理页仍用裸 `getApiClient()` 调 `/api/test/*` | 旧对账文档 `frontend-backend-parity.md:178-185` | 有 UI、有真实后端消费；属于客户端模块抽取优化，不是功能断裂。 |
| `SettingsPage` 残留 WebSocket 地址文案 | 旧对账文档 `frontend-backend-parity.md:278-289` | SSE-only 约定已明确，当前功能不受影响。 |
| `api_reference.md` 的整体历史路由数量与当前不一致 | 旧文档 `frontend-backend-parity.md:12,22` 与本次命令实测 | 已由 G15 纳入文档收口，不另拆缺口。 |
| `AssetPanel` 组件较大 | 旧 PRD §9.1 line 380 | 只在 B10 补针对性测试，不做组件重构。 |
| `CopilotTaskRunTab` 注释仍提 supervisor 快照 | `src/features/copilot/CopilotTaskRunTab.tsx:4-6` | 注释漂移，行为缺口已在 B11/B12 历史修复中处理；不单列 P0。 |
| `/api/plugin/{id}/bundle`、`/contributions`、`/health` 的深度运维展示不足 | `clipwright/api/plugin.py:257,311,413` | 本轮优先覆盖工具面、调度、渲染、事件；插件运维另开治理 PRD。 |

---

## 10. 执行记录（留给后续填）

| 批次 | 日期 | 实施提交/变更范围 | 验收命令与结果 | 未完成项/偏离 | 回滚结果 | 负责人 |
|---|---|---|---|---|---|---|
| B1 |  |  |  |  |  |  |
| B2 |  |  |  |  |  |  |
| B3 |  |  |  |  |  |  |
| B4 |  |  |  |  |  |  |
| B5 |  |  |  |  |  |  |
| B6 |  |  |  |  |  |  |
| B7 |  |  |  |  |  |  |
| B8 |  |  |  |  |  |  |
| B9 |  |  |  |  |  |  |
| B10 |  |  |  |  |  |  |
| B11 |  |  |  |  |  |  |
| B12 |  |  |  |  |  |  |
| B13 |  |  |  |  |  |  |
