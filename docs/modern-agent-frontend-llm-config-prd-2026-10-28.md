# 前端层级 × LLM 配置 PRD（2026-10-28）

## 0. 一页结论

本轮是一次契约收敛，不重做主循环、AgentRun、SSE 或 provider 抽象。六项改造中，标签层级是前端状态归属调整；说话再行动是事件时序修复；任意 provider/model、逐模型参数和 reasoning_effort 是后端运行时路由与能力协商扩展；`/v1/models` 是已有探测能力的可读化与前端接入收口。

最大回归风险是 `LLMService` 的参数透传覆盖面，以及跨 provider 凭据/模型解析。第二风险是把 `AgentPanel` 内部 tab 提升到 Copilot 全局后，旧需求会话状态和日志事件订阅被重复挂载。最先做 B1（标签层级）和 B2（叙述时序），因为它们独立、可快速验证用户可见行为，且为后续运行参数验收提供稳定观察面。

所有批次均以现有后端 4436 passed / 4 skipped / 1 xfailed、前端 995 passed / 116 files、`ruff`、`tsc`、lint、build 全绿为基线；任何批次合入前必须重新执行受影响测试和全量基线。

## 1. 现状与证据

|编号|用户症状|现状证据（文件:行号）|是否属实|
|---|---|---|---|
|1|新增 Copilot 标签在“Agent 副驾驶”标题和内部 tab 之上，形成两层 tab|`J:\Clipweight-Client\src\features\copilot\CopilotDock.tsx:66-71` 先渲染 `CopilotTabBar`；`J:\Clipweight-Client\src\features\agent\AgentPanel.tsx:114-140` 仍渲染标题和“需求对话/执行日志”二级 tab|是|
|2|主 agent 的工具前说明未稳定进入事件流，工具事件可能先显示|`J:\Clipwright\clipwright\services\agent_loop.py:208-210` 直接 emit `tool_call`；`J:\Clipwright\clipwright\services\llm.py:716-725` 读取 `resp.content` 后无 preamble 回调；`J:\Clipwright\clipwright\services\agent_loop.py:305-308` 只在循环结束 emit `report`|是|
|3|子 agent 指定 provider/model 会被白名单限制|`J:\Clipwright\clipwright\services\stage_subagent.py:335-349` 构造 route 后调用 `is_route_allowed`，失败返回“模型不在白名单”；`J:\Clipwright\clipwright\services\model_router.py:101-106` 非空白名单精确匹配|是|
|4|RunSpec 字段已存在，但运行时校验和实际路由仍未完全放开|`J:\Clipwright\clipwright\schema\run.py:28-50` 有 `provider/model`；`J:\Clipwright\clipwright\services\agent_run.py:395-405` 传给 `StageSpec`；`J:\Clipwright\clipwright\services\stage_subagent.py:354-364` 通过 `use_route`；限制点仍在 335-349|部分属实，链路已有骨架|
|5|模型参数没有逐模型配置形状和统一生效路径|`J:\Clipwright\clipwright\config.py:136-170` 只有 `model_routes/model_prices`；`J:\Clipwright\clipwright\services\llm.py:451-467` chat 显式传 temperature/max_tokens；`llm.py:519-557` structured_output 透传 kwargs；`llm.py:641-716` with_tools 透传 kwargs，但无模型参数解析器|是|
|6|reasoning_effort 没有 Agent 副驾驶入口和 provider 能力契约|`J:\Clipwright\clipwright\config.py:110-124` 仅有 `agent_decide_stream`；`J:\Clipwright\clipwright\providers\base.py:16-24` 能力字段没有 reasoning；`J:\Clipweight-Client\src\features\agent\AgentPanel.tsx:168-170` 仅有 reasoning 展示状态，无设置入口|是|
|7|模型列表探测后端已存在，但失败语义和前端体验需收口|`J:\Clipwright\clipwright\api\providers.py:162-189` 已有 `POST /api/providers/{source_id}/models/fetch`；`J:\Clipwright\clipwright\providers\model_listing.py:17-65` 失败返回空数组；`J:\Clipweight-Client\src\services\api\providers.ts:161-168` 只消费 `models/note`；`ProvidersPage.tsx:154-170` 空列表显示 note|是，属于扩展而非从零建设|

## 2. 目标契约

### 2.1 面板标签层级（改前/改后组件层级图）

改前：

```text
CopilotDock
├── CopilotTabBar（需求对话、任务运行、工作区、文档…）
└── AgentPanel（Agent 副驾驶标题）
    ├── 内部 tab：需求对话
    └── 内部 tab：执行日志
```

改后：

```text
CopilotDock
├── CopilotTabBar（需求对话、执行日志、任务运行、子 agent、阶段、工作区、文档…）
└── ActiveTabBody
    ├── requirements → RequirementsView（从 AgentPanel 提取）
    ├── logs → LogPanel（从 AgentPanel 提取）
    ├── task-run → CopilotTaskRunTab
    ├── subagent/stage/workspace/docs → 原有独立视图
    └── AgentPanel 不再拥有标题行或 tab 状态
```

`CopilotTabKind` 新增 `'logs'`；`REQUIREMENTS_TAB` 保持 id=`requirements`、不可关闭；新增固定 `LOGS_TAB`，id=`logs`、不可关闭。旧 localStorage 只有 requirements 或动态 tab 时，`normalizeTabs()` 在首位 requirements 后插入 logs；旧动态 tab 保持顺序和 payload。`MAX_TABS=8` 继续适用，两个固定 tab 占用两个名额。

### 2.2 说话再行动（事件字段 + 时序）

不新增事件类型，复用后端已有 `narration`、`intent`、`tool_call`；`EVENT_TYPES` 已包含三者（`clipwright/schema/task.py:44-48`）。新增字段形状：

```json
{
  "seq": 12,
  "type": "narration",
  "message": "我先读取工作区，再派发素材阶段。",
  "detail": {
    "text": "我先读取工作区，再派发素材阶段。",
    "kind": "preamble",
    "round": 1,
    "before_tool": "delegate_agent",
    "tool_args_preview": {"stage": "material"}
  },
  "trace_id": "task-id",
  "span": "model"
}
```

`detail.kind` 枚举为 `preamble|intent|final`；旧事件缺字段按 `kind=legacy` 展示。时序必须是：LLM 返回一轮 → 若 `content.strip()` 非空且本轮有 tool calls，先 emit 一条 narration（必要时一条 intent）→ 再 emit `tool_call` → 执行工具 → emit `tool_result` → 回填 provider 消息。一次响应多个 tool call 时，preamble 只发一次，之后每个 call 保持原顺序。

前端 `CopilotTaskRunTab.ModelTurnRows` 和 `EventFeed` 按 `seq` 渲染；`tool_call` 不得在其对应的 preamble 前出现。若 provider 只返回 tool call 无 content，不伪造 narration。SSE 重连按 seq 去重，旧客户端忽略新增 detail 字段。

### 2.3 任意 provider/model 的校验规则

“任意”定义为：provider 必须存在于已配置且可解析凭据的 LLM source；model 必须是该 source 的已启用模型，或 provider 明确允许的自定义模型。`model_whitelist` 不参与 `delegate_agent` 的显式 provider/model 校验；它继续约束风险判定模型和自动 route 选择，避免扩大既有策略面。

拒绝形状：

```json
{"status":"refused","error":"provider 未配置：acme","failure":{"category":"provider_not_configured","retryable":"permanent","blocking_point":"provider=acme","evidence":[]}}
```

未知 provider、无凭据 source、空 model、model 不在 source 清单时均为可读 refused；不回退到主模型，不静默串用其他 provider key。未指定 provider/model 仍使用默认 route，保持旧行为。`RunSpec` 和 `AgentResult` 的 provider/model 字段保留；结果必须回填实际调用的 provider/model。

### 2.4 模型参数（配置形状 + 生效路径 + 优先级）

在 provider source 的 `models[]` 每项增加可选 `generation`：

```json
{"id":"model-a","name":"Model A","enabled":true,
 "generation":{"temperature":0.2,"top_p":0.9,"max_tokens":8192}}
```

Pydantic/存储层允许字段缺省；数值约束为 temperature `0..2`、top_p `0..1`、max_tokens `1..1_000_000`。前端 `ProviderModel` 增加 `generation?: ModelGeneration`。优先级：单次调用显式 kwargs > 当前 route 对应模型 generation > role/source 默认 generation > 既有方法默认值。只发送 provider 支持的字段；不支持的字段记录 degradation，保留调用成功结果。

统一解析点为 `services/llm.py` 的 `_routed_client_and_model`/生成参数组装器；覆盖 `generate`、`chat`、`stream_generate`、`with_tools`、`structured_output`、`structured_output_stream`。工具循环每一轮使用同一解析后的参数，不能首轮生效、后续丢失。

### 2.5 reasoning_effort（支持的 provider / 不支持的降级）

新增 `ReasoningEffort = Literal['low','medium','high']`，任务级/副驾驶会话级可选字段；请求未设置时沿用模型配置，模型配置未设置时不传。OpenAI-compatible provider 仅在 capability 声明 `reasoning_effort=true` 时发送 `reasoning_effort`；Anthropic 使用其协议对应的 thinking/budget 映射由 adapter 决定；Ollama 或 capability=false 不发送。

降级事件：

```json
{"type":"degradation","message":"模型 model-a 不支持 reasoning_effort，已忽略 high。",
 "detail":{"kind":"unsupported_parameter","parameter":"reasoning_effort","requested":"high","provider":"ollama","model":"model-a","effective":null}}
```

前端设置入口显示支持状态和“已忽略”结果，不把请求值显示成已生效值。reasoning 内容仍走既有 `thinking` 事件；`on_reasoning` 回调异常只记录日志，不阻断生成。

### 2.6 /v1/models 探测（接口签名 + 失败语义）

保留并扩展 `POST /api/providers/{source_id}/models/fetch`，响应：

```json
{"status":"ok","models":[{"id":"m1","name":"m1","enabled":true}],"source_id":"src1","provider":"openai","note":""}
```

失败仍 HTTP 200（source 不存在仍 404），但返回 `status`=`unsupported|empty|error`、`models:[]`、可读 `note`，并可选 `error_code`=`provider_unsupported|http_error|invalid_response|credential_error|timeout`。旧前端只读 models/note，兼容保留。内部 `list_models_for_source(provider,resolved)` 改为返回 `ModelListingResult` 的实现可在 API 层适配；解析兼容 OpenAI `data[].id`、Ollama `models[].name`、Anthropic `data[].id`。

前端 `providersApi.fetchModels()` 返回 `{status, models, note, errorCode?}`；`ProvidersPage` 在 loading、成功、空结果、失败四态显示，失败保留手动新增模型入口，不清空已有模型，不白屏，不提示“已获取”。

## 3. 批次计划

|批次|目标|人日|依赖|验收类型|
|---|---|---:|---|---|
|B1|统一 Copilot 单层标签|1.0|无|前端单测 + 手工回放|
|B2|主 agent preamble 事件时序|1.0|无，可与 B1 并行|后端单测 + 前端事件渲染|
|B3|子 agent 显式 provider/model 放开|1.5|无，可与 B1/B2 并行|后端契约/路由单测|
|B4|逐模型 generation 参数|2.0|B3|后端 provider mock + 设置页单测|
|B5|reasoning_effort 入口与降级|1.5|B4|能力协商单测 + 前端单测|
|B6|模型列表探测契约收口|1.0|无，可与 B1-B3 并行|API/前端状态单测|
|B7|跨仓库回归与文档同步|1.0|B1-B6|全量基线 + E2E 复现|

### B1 统一 Copilot 单层标签

**目标**：全局只存在一排标签；需求对话、执行日志、任务运行同级；保留需求会话和日志行为。

**改动面**：`J:\Clipweight-Client\src\stores\copilotStore.ts`、`CopilotTabBar.tsx`、`CopilotDock.tsx`、`AgentPanel.tsx`；新增可复用 `RequirementsView`/`LogPanel` 导出或等价拆分。不要在 Dock 中重复挂载两个 SSE 订阅。

**实施步骤**：1. 将 AgentPanel 内部 JSX 拆为无 tab 的 `RequirementsView` 与 `LogPanel`，保留原 hooks、store 写入和 `BottomBar` 所需宿主；2. `CopilotTabKind` 加 logs，固定 tabs 加 LOGS_TAB；3. `normalizeTabs` 对旧持久化数据插入 logs 并限长；4. Dock switch 增加 logs，requirements 直接渲染需求视图；5. 删除 AgentPanel 标题行和内部 tab 状态，仅保留兼容导出壳（若测试/外部引用需要）；6. 更新 aria role/testid。

**可证伪验收**：`npm run test -- src/features/copilot/CopilotTabBar.test.tsx src/features/copilot/CopilotTaskRunTab.test.tsx src/features/agent/AgentPanel.test.tsx`；新增断言 `screen.getAllByRole('tab').length` 等于固定+动态数量，页面不存在两个“执行日志”，切换 logs 不卸载 requirements store；`npm run build`。

**回滚**：恢复上述四个文件；localStorage schema 只增字段，旧版本会忽略 logs，回滚不需迁移。

**风险**：AgentPanel 约 1600 行，拆分时禁止改需求业务逻辑；若无法稳定拆分，先保留内部组件函数但由 Dock 传 `view`，同一提交仍必须只有一排可见 tab。

### B2 主 agent 说话再行动

**目标**：工具前的真实 preamble 先入事件流和前端，严格先 narration/intent 后 tool_call。

**改动面**：`J:\Clipwright\clipwright\services\llm.py:641-784`、`services/agent_loop.py:189-272`、`tests/test_agent_loop.py`、`tests/clipwright/test_llm_provider_protocol.py`；前端主要校验 `CopilotTaskRunTab.tsx` 与 `src/types/task.ts`。

**实施步骤**：1. 给 `with_tools` 增加可选 `on_preamble(content, round, tool_calls)` 回调，默认 None 保持旧调用兼容；2. 在确认 `resp.success` 且存在 tool_calls 后同步回调一次；3. agent_loop 回调 emit narration，detail 写 kind/round/before_tool；4. 任何回调失败只记录 warning，不阻断工具；5. 多 tool call 仅一条 preamble；6. 新增序列断言并覆盖无 content 场景。

**可证伪验收**：`python -m pytest tests/test_agent_loop.py tests/clipwright/test_llm_provider_protocol.py -q`；新增测试断言事件类型序列为 `narration,tool_call,tool_result`，无 preamble 时首事件为 tool_call；`npm run test -- src/features/copilot/CopilotTaskRunTab.test.tsx src/features/task/task-ui.test.tsx`。

**回滚**：移除回调参数和 emit 分支；事件 schema 无新增类型，旧事件可继续回放。

**风险**：Anthropic/OpenAI 消息回填必须保留 content，不能只发事件后丢掉 assistant content；测试需分别覆盖两种协议。

### B3 子 agent 显式 provider/model 放开

**目标**：delegate_agent 的显式 route 只校验“已配置 provider + 可用 model”，不受 `model_whitelist` 阻断；未知/未配置 provider 可读拒绝。

**改动面**：`schema/run.py`（必要时加 route validation 字段）、`services/model_router.py`、`services/stage_subagent.py`、`services/agent_run.py`、`supervisor/spawn.py`、`tests/test_stage_subagent.py`、`tests/test_model_router.py`、`tests/test_subagent_spawn.py`。

**实施步骤**：1. 添加 `resolve_explicit_route(provider, model)`，解析 source、凭据和模型清单；2. 将 stage_subagent 335-349 的 `is_route_allowed` 替换为显式 route 解析；3. 保留 `choose_route/risk_judge_route` 的 whitelist 行为；4. 让 research spawn 使用同一解析函数；5. AgentResult 回填实际 provider/model；6. 为未知 provider、未配置 source、已配置模型、未指定 route 各加测试。

**可证伪验收**：`python -m pytest tests/test_model_router.py tests/test_stage_subagent.py tests/test_subagent_spawn.py -q`；断言显式 `p/model` 在 whitelist 非空时仍能调用，`unknown/m` 返回 category=`provider_not_configured`，不会调用默认 client；全量 pytest。

**回滚**：恢复 stage_subagent whitelist 分支；新增函数保留但不调用，旧 route 行为完全复原。

**风险**：source 模型清单为空的兼容策略必须明确：仅当 provider adapter 明确声明允许自定义模型时放行，否则返回 model_not_configured，不能因列表接口失败扩大权限。

### B4 逐模型 generation 参数

**目标**：设置页逐模型编辑 temperature/top_p/max_tokens，真实调用的六条 LLM 路径都使用同一优先级。

**改动面**：后端 `services/provider_sources.py`、`services/model_router.py`、`services/llm.py`、`providers/base.py`；前端 `src/services/api/providers.ts`、`src/pages/admin/ProvidersPage.tsx`；配置兼容涉及 `config_schema.py` 但不新增破坏性顶层字段。

**实施步骤**：1. 为模型项增加 `generation` 解析/校验，旧模型缺省；2. `ModelRoute` 增加可选 generation 或 model config 引用；3. 实现 `_effective_generation_kwargs`，显式 kwargs 优先；4. 在 generate/stream_generate/with_tools/structured 两种路径统一调用；5. provider capability 不支持的参数进入 degradation；6. 前端模型行增加数字输入、保存 payload、非法值 inline 错误；7. 测试首轮/后续工具轮参数一致。

**可证伪验收**：`python -m pytest tests/clipwright/test_provider_sources.py tests/clipwright/test_llm_provider_protocol.py tests/test_model_router.py -q`；mock client 断言每条调用收到指定值，显式 temperature 覆盖模型值；`npm run test -- src/pages/admin/ProvidersPage.test.tsx`；`npx tsc --noEmit`。

**回滚**：旧模型 JSON 可直接读取；移除 generation 解析后默认值恢复，前端隐藏编辑控件但不删除已存字段。

**风险**：IsoBase 客户端可能拒绝未知 kwargs；必须根据 capability 过滤并发 degradation，而不是 catch 后假装参数生效。

### B5 reasoning_effort 入口与降级

**目标**：Agent 副驾驶能选择 low/medium/high；支持的 provider 真实透传，不支持时显示 degradation。

**改动面**：`providers/base.py`、各 LLM provider capability adapter、`services/llm.py`、`services/degradation.py`、`schema/task.py`（仅若需要任务请求字段）、`AgentPanel`/设置页、前端 `types/task.ts` 与相关 API client。

**实施步骤**：1. capability 增加 `reasoning_effort: bool`；2. 增加请求字段和模型配置可选字段；3. 在统一参数组装器按 provider 过滤/映射；4. 不支持时 append degradation，事件 detail 写 requested/effective；5. AgentPanel 入口显示当前值和降级状态；6. 回放器保留 degradation；7. 覆盖 stream/non-stream/tool loop。

**可证伪验收**：后端新增能力矩阵测试，支持 provider mock 断言收到字段，不支持 provider 断言未收到字段且有 degradation；前端 `AgentPanel.test.tsx` 断言选择值、服务端降级消息和真实值分离；`python -m pytest -q` 与 `npm run test -- --run`。

**回滚**：默认不传 reasoning_effort；移除入口后旧任务 payload 仍可解析，degradation 事件对旧前端透明。

**风险**：不同 provider 的“高推理预算”语义不可强行等价；adapter 必须声明映射，未声明即 ignored。

### B6 模型列表探测契约收口

**目标**：添加/编辑 provider 时通过 `/v1/models` 拉取模型，四态可见，失败不清空旧模型且保留手动新增。

**改动面**：`providers/model_listing.py`、`api/providers.py`、`tests/clipwright/test_provider_available.py`、新增 provider API 测试；前端 `services/api/providers.ts`、`ProvidersPage.tsx`、`ProvidersPage.test.tsx`。

**实施步骤**：1. 保持 `list_models_for_source` 兼容 list 调用方，内部补充 status/error_code 结果适配；2. API 返回 status/source_id/provider/note/error_code；3. 区分 unsupported、empty、error、ok；4. 前端 fetch 状态机 loading/ok/empty/error；5. 成功才覆盖模型列表，空/失败保持已有列表；6. 自定义模型按钮始终可用。

**可证伪验收**：后端 mock HTTP 200 data、401、超时、未知 provider 四场景断言响应；前端测试断言失败 note 可见、已有 model 仍在、不会出现成功提示；`npx tsc --noEmit && npm run build`。

**回滚**：API 保留旧 `models/note` 字段，前端删 status 分支即可回到当前行为；不删除已存模型。

**风险**：不同 OpenAI-compatible Base URL 可能已包含 `/v1`；测试必须断言最终 URL 不出现 `/v1/v1`，失败提示包含可操作原因。

### B7 跨仓库回归与文档同步

**目标**：所有批次可从干净工作区复现，文档与前后端契约一致。

**改动面**：后端 `J:\Clipwright\docs\api_reference.md`、`frontend-backend-parity.md`、`fix-and-feature-plan.md §10`；前端 `J:\Clipweight-Client\docs\` 对应副本；必要时新增契约测试。

**实施步骤**：1. 逐批执行受影响测试；2. 后端全量 pytest 与 ruff；3. 前端全量 vitest、tsc、lint、build；4. 启动两端，创建 provider、拉取模型、配置 reasoning、启动任务，观察 SSE 顺序；5. 记录执行批次和结果，不修改既有无关脏改动。

**可证伪验收**：见第 6 节完整 DoD；失败必须定位到批次和测试，不以截图代替命令结果。

**回滚**：按批次 revert 对应提交；不跨批次回滚共享 schema，先恢复兼容字段和旧前端读取路径。

**风险**：跨仓库提交顺序导致短暂版本不匹配；先后端保持旧字段，再前端启用新字段，最后清理过时代码。

## 4. 数据与接口变更清单

|范围|变更|兼容策略|
|---|---|---|
|后端 `schema/run.py`|RunSpec provider/model 语义改为显式 route；AgentResult 回填实际值|字段名不变，旧空字符串继续默认 route|
|后端 `schema/task.py`|不新增事件类型；narration/intent detail 增加 kind/round/before_tool|旧事件缺字段按 legacy；旧前端忽略 detail|
|后端 `providers/base.py`|ProviderCapabilities 增加 reasoning_effort 与 generation 参数支持位|dataclass 默认 false，旧 adapter 无需立即改造|
|后端 provider source|models item 增加 generation 对象|旧 models 读取不受影响，缺省使用方法默认|
|后端 `/api/providers/{id}/models/fetch`|增加 status/source_id/provider/error_code|保留 models/note，旧客户端继续可用|
|前端 `types/task.ts`|事件 detail 继续 Record，必要时增加 ReasoningEffort union|未知事件字段保留，不丢弃|
|前端 `services/api/providers.ts`|ProviderModel.generation、fetchModels 返回 status/errorCode|可选字段，不影响旧 source|
|前端 `copilotStore.ts`|新增 logs 固定 tab，持久化 normalize migration|旧 localStorage 自动插入 logs|
|设置 UI|逐模型 generation 与 reasoning 入口|未配置显示默认，不回填伪造值|

## 5. 测试计划

|动作|文件/测试|断言点|
|---|---|---|
|新增|`tests/test_agent_loop.py`|preamble 事件先于 tool_call；无文本不新增事件；多 tool call 只一条|
|修改|`tests/clipwright/test_llm_provider_protocol.py`|OpenAI/Anthropic 均保留 assistant content，回调顺序正确|
|新增|`tests/test_model_router.py`|显式 route 绕过 whitelist 但必须命中 configured source；未知 provider 可读 refused|
|修改|`tests/test_stage_subagent.py`|provider/model 真正进入 use_route 和 agent client；结果回填实际 route|
|修改|`tests/test_subagent_spawn.py`|research route 与 stage 使用同一显式解析规则|
|新增|`tests/clipwright/test_llm_generation_params.py`|generate/chat/stream/structured/with_tools 全路径参数和优先级|
|修改|`tests/clipwright/test_provider_sources.py`|旧模型数据可读、新 generation 校验范围、非法值拒绝|
|新增|`tests/clipwright/test_provider_model_fetch_api.py`|ok/empty/unsupported/error 四种响应和 URL 组装|
|修改|`src/features/copilot/CopilotTabBar.test.tsx`|requirements/logs 同级，固定 tab 不可关闭，迁移旧持久化|
|修改|`src/features/agent/AgentPanel.test.tsx`|AgentPanel 兼容壳/RequirementsView 行为不变；reasoning 入口状态|
|修改|`src/features/copilot/CopilotTaskRunTab.test.tsx`|narration/intent/thinking 按 seq 展示，tool_call 不抢跑|
|修改|`src/pages/admin/ProvidersPage.test.tsx`|模型四态、失败保留旧列表、generation 编辑和手动模型入口|
|删除|无|本轮禁止删除既有基线测试；若拆组件导致 import 变更，只迁移等价断言到新文件并保留测试语义|

## 6. 完成定义（DoD）

- [ ] 后端原基线 `python -m pytest tests -q` 达到 4436 passed / 4 skipped / 1 xfailed，且无新增失败。
- [ ] 后端 `ruff check --select E9,F63,F7,F82,F823` 返回 0 错。
- [ ] 前端 `npm run test` 达到 995 passed / 116 files 基线以上，新增测试全绿。
- [ ] 前端 `npx tsc --noEmit`、`npm run lint`（0 error）、`npm run build` 全绿。
- [ ] grep/静态断言确认 `CopilotDock` 只渲染一处 `CopilotTabBar`，`AgentPanel` 不再渲染可见二级 tab。
- [ ] 任务 SSE 可复现 `narration/intent → tool_call → tool_result`，seq 单调且重连去重。
- [ ] provider/model 显式派发：已配置模型成功；未知 provider、无凭据、未知模型分别返回可读 refused，不回退默认模型。
- [ ] 逐模型 temperature/top_p/max_tokens 在普通生成、流式结构化、结构化、工具循环首轮和后续轮均可在 mock 请求中观察到。
- [ ] reasoning_effort 支持 provider 收到真实参数；不支持 provider 产生 degradation，UI 显示 ignored/effective=null。
- [ ] `/api/providers/{id}/models/fetch` 成功、空、失败、未知 provider 均不白屏；失败不覆盖已有模型，手动模型入口可用。
- [ ] 手工路径：设置-供应商新建源 → 拉取 `/v1/models` → 选择模型参数 → 打开副驾驶 reasoning 选项 → 创建任务 → 任务运行标签看到 narration 后再看到工具调用。
- [ ] 后端 `docs/api_reference.md`、`frontend-backend-parity.md`、`fix-and-feature-plan.md §10` 与前端 docs 副本已同步。

## 7. 明确不做

- 不重做原生 tool-use loop、AgentRun 或 SSE 存储；它们已有稳定契约，本轮只补事件时序和参数入口。
- 不让 `model_whitelist` 全局失效；它继续约束自动路由和风险判定，显式 delegate route 才走 configured-source 校验。
- 不把 provider 的不同 reasoning 语义强行统一；没有 capability 声明就产生 degradation 并忽略参数。
- 不把 `/v1/models` 失败当成“默认模型列表”；失败保留空结果和可读 note，用户可手动添加。
- 不迁移旧模型参数到新的顶层全局字段；逐模型配置放在 source models item，避免 role/provider 串配置。
- 不改变需求对话业务流程、阶段顺序、预算闸和子 agent 星型拓扑；这些是本轮验收的前置不变量。
- 不删除旧前端字段、旧事件 detail 或旧 localStorage；至少保留一个发布周期的向后兼容读取。

---

## 8. 与既有缺口清单的合并（本节为合并视图，实际以本节为准）

> 背景：这份 PRD 只规划了本次六项改造。上一轮前端缺口调研（见
> `docs/modern-agent-closeout-plan-2026-10-27.md` §12）报出的 **3 条已报缺口 + 9 条新发现（N1–N9）**
> 当时**没有**并入本文件。本节把它们逐条映射到批次，避免两份清单各自为政。

### 8.1 覆盖核对（逐条比对，不用"应当已包含"敷衍）

| 来源 | 缺口 | 本 PRD 原文是否已覆盖 | 结论 |
|---|---|---|---|
| 已报 1 | 「正在思考」无流式内容 | **否**。原文只在 §2.5 写「reasoning 内容仍走既有 `thinking` 事件」——它**假设 thinking 已经能用**，而缺口正是「`agent_loop` 根本不发 thinking，`_emit_thinking` 是死代码」 | 并入 **B5**（改名扩范围） |
| 已报 2 | 文案无法完整传入 agent | **否**。全文未出现 `brief` / `script_text` / artifact 引用 | 新增 **B8** |
| 已报 3 | 上传配音提示 `deduplicated` | **否**。原文两处"去重"都是「SSE 按 seq 去重」，与素材 SHA-256 去重无关 | 新增 **B9** |
| N1 | `task_end.reason` 映射旧口径 → 成功显示失败 | 否（不在本 PRD 范围） | **已修复**（`cdfcf96`，`terminalStatusOf` + 5 条用例），不排期 |
| N2 | `PlanPanel` 仍把 Plan 当执行进度 | 否 | 新增 **B10** |
| N3 | 子 agent 详情查 tracker 快照，不消费 `agent_run_*` | 否 | 并入 **B11** |
| N4 | 审批只有日志，没有提交 `approval_id` 的入口 | 否 | 并入 **B11** |
| N5 | `context_loaded.trimmed` 后端传 bool，loader 返回 list | 否 | 并入 **B12** |
| N6 | 工具 call↔result 用工具名配对，同名并发会串 | 否 | 并入 **B12** |
| N7 | 事件在 store(400) 与 EventFeed(120) 二次截断 | 否 | 并入 **B12** |
| N8 | `doc_write`/`report`/`context_loaded` 无结构化详情与跳转 | 否 | 并入 **B12** |
| N9 | 子 agent 筹码把 `replayed` 当状态，未消费 `failure/needs` | 否 | 并入 **B11** |

### 8.2 批次调整（B1–B7 + 新增 B8–B12）

| 批次 | 目标 | 人日 | 依赖 | 覆盖的缺口 |
|---|---|---|---|---|
| B1 | 统一 Copilot 单层标签 | 1.0 | — | 本次 1 |
| B2 | 主 agent 说话再行动（preamble 先行） | 1.0 | — | 本次 2 |
| B3 | 子 agent 显式 provider/model 放开 | 1.0 | B2 | 本次 3 |
| B4 | 逐模型 generation 参数 | 1.5 | — | 本次 4 |
| **B5** | **reasoning 链路：流式 thinking + reasoning_effort** | 2.0 | B2 | 本次 5 + **已报 1** |
| B6 | `/v1/models` 探测前端接入与失败语义 | 1.0 | — | 本次 6 |
| B7 | 跨仓库回归与文档同步 | 0.5 | B1–B6、B8–B12 | — |
| **B8** | 文案 artifact 化 + delegate 强制 `input_refs` | 1.5 | — | **已报 2** |
| **B9** | 素材去重改 200 复用（`deduplicated: true` / `status: "reused"`） | 0.5 | — | **已报 3** |
| **B10** | Plan 降级为「初始假设」的 UI 收口 | 1.0 | B1 | **N2** |
| **B11** | 子 agent 与审批的 run 化 UI（消费 `agent_run_*` + 真实 approve） | 1.5 | B2、B3 | **N3/N4/N9** |
| **B12** | 事件呈现收口（trimmed 传 list / 按 seq 配对 / 详情可展开 / 独立 thinking 缓冲） | 1.5 | B2、B5 | **N5/N6/N7/N8** |

合计约 **14 人日**（原 7 批 ≈ 8 人日 + 新增 5 批 ≈ 6 人日）。

### 8.3 B5 扩范围后的规格（替代 §2.5 的"假设 thinking 已可用"）

- **后端生产端**：`LLMService.with_tools` 增加 `on_reasoning` 参数并逐块转发 provider 的
  reasoning delta；`agent_loop.run_loop` 每收到一块 emit `thinking`（`detail={delta, done:false, round}`），
  结束时 emit 一条 `done:true`。缓冲按 `task_id/run_id`，**不再复用全局单槽 `_LAST_REASONING`**。
- **顺序约束**：同一轮内 `thinking` → `narration(preamble)` → `tool_call`，seq 必须单调；
  模型只给 tool_call 不给 content 时不得伪造 narration/thinking。
- **降级**：provider 不返回 reasoning 时，不发 `thinking`（前端显示"未提供"，保持现状语义）；
  `reasoning_effort` 不支持时按 §2.5 记 degradation，但**不能**因此吞掉 thinking。

### 8.4 B8/B9 的最小规格（原文未覆盖，这里补上）

- **B8 文案 artifact 化**：
  - `proceed` 落 `docs/script.md`（全文，不截断）；`TaskRequest.extra` 增加 `script_path` 与 `script_sha256`；
  - `context_loader` 增加 `script` 段（默认给前 2000 字 + artifact 路径 + 全长/hash），
    `LIMITS` 增加 `script` 键，`trimmed` 保留被裁键名；
  - `delegate_agent` schema 增加 `input_refs: string[]`（artifact 路径），`agent_run` 解析并校验存在性与 hash；
  - 验收：10k 字脚本 proceed → `context_loaded.sources` 含 script artifact（长度/hash 一致）→
    `delegate_agent.args.input_refs` 有值 → 阶段拿到的 `creative_brief/script_text` 与原文一致。
- **B9 素材去重**：
  - `asset_manager` 不再用 `error="deduplicated"` 表达复用，改为 `status="reused"` + `deduplicated=True`；
  - `api/asset.py` 对复用返回 **200**（不再 400），真正的格式/权限/写盘错误继续 4xx/5xx；
  - 前端 `HomePage.pickAudio` / `AssetPanel.handleUpload` 显示「文件已存在，已复用」并继续使用返回的
    `file_path`/`duration_sec`；
  - 验收：同一 mp3 二次上传 → 200、`asset_id/file_path` 相同、`used_count` +1、UI 不报错。

---

## 9. 实施记录（按批次，含证据与偏离说明）

> 基线：后端 `4436 passed / 4 skipped / 1 xfailed` + ruff 门禁；前端 `995 passed / 116 files`
> （计入本批新增用例后为 **1004+ passed / 117 files**）+ `tsc` + `lint`（0 error）+ `build`。

### B9 素材去重改 200 复用 ✅（已报缺口 3）

| 改动 | 位置 | 证据 |
|---|---|---|
| 复用不再写 `error`，改 `status="reused"` + `deduplicated=True` | `clipwright/services/asset_manager.py`（`AssetInfo` 新增字段 + dedup 分支） | `tests/clipwright/test_asset_governance.py::test_upload_same_audio_twice_returns_200_and_reused`：二次上传 → 200、`asset_id/file_path` 相同、`used_count` +1 |
| 复用时不覆盖库内文件名 | `clipwright/api/asset.py::upload_asset` | 同上断言 `filename` 两次一致 |
| 治理摘要按 `deduplicated` 统计 | `clipwright/api/asset.py::governance_summary` | 兼容旧 `error=="deduplicated"` |
| 前端两种入口都提示「已存在，已复用」 | `src/pages/HomePage.tsx`（配音）、`src/features/assets/AssetPanel.tsx`（批量素材） | `HomePage.test.tsx::B9` 断言提示文案而不是红字错误 |

**偏离**：`AssetPanel` 只做 toast 级提示（组件 837 行、依赖多，未新增独立测试文件），由 `tsc + build` 保证类型与构建正确性。

### B1 统一 Copilot 单层标签 ✅

- `copilotStore`：新增 `'logs'` kind、`LOGS_TAB`、`FIXED_TABS`；`normalizeTabs` 把固定标签前置并补 `logs`、按 `MAX_TABS` 限长；`closeTab` 对固定标签 no-op。
- `AgentPanel.tsx`：原「标题行 + 需求/日志二选一」拆除，改出 `RequirementsPane`（离线横幅 + 需求视图 + BottomBar + 时间轴差异浮层）与导出 `LogPanel`；`AgentPanel` 保留为兼容壳。
- `CopilotDock.tsx`：`requirements → RequirementsPane`、`logs → LogPanel`，全局只有一排标签。
- 证据：新增 `src/features/copilot/CopilotDock.test.tsx`（标签总数 = 固定+动态、`执行日志` 只出现一次、切 logs 不清空需求会话 store）、`CopilotTabBar.test.tsx` +3、`copilotStore.test.ts` +2；`EditorLayout.test.tsx` 的 AgentPanel mock 同步补齐导出（此前 8 项红）。

### B2 主 agent 说话再行动 ✅

- `LLMService.with_tools(on_preamble=...)`：确认 `success` 且存在 `tool_calls` 后**同步**回调一次（同轮多工具只一条；无 content 不回调；回调异常只记 warning）。
- `agent_loop`：回调 emit `narration`，`detail={kind:"preamble", round, before_tool, tool_count, shadow}`；系统提示词新增「动手前先说话」规则。
- 前端：`CopilotTaskRunTab` 的模型轮次把 `detail.kind==="preamble"` 标成「动手前」，排在工具调用卡片之前。
- 证据：`tests/test_agent_loop.py` +4（序列 `narration→tool_call→tool_result`、无 content 不发、多工具单条、回调异常不阻断）；`CopilotTaskRunTab.test.tsx` +1。

### B3 子 agent 任意 provider/model ✅

- 新增 `model_router.resolve_explicit_route(provider, model)` → `ExplicitRoute{ok, route, reason, message}`；
  失败分类 `provider_missing / provider_not_configured / model_missing / model_not_configured`。
- `stage_subagent` / `supervisor.spawn` 的 `is_route_allowed` 换成显式解析；`_policy_failure` 支持传 category。
- 自动选路（`choose_route` / `risk_judge_route`）**继续**受 `model_whitelist` 约束。
- `ProviderCapabilities` 新增 `arbitrary_models`（openai/ollama = True，anthropic = False）：来源无模型清单时只有声明的 provider 放行。
- 证据：`tests/test_model_router.py` +6、`tests/test_stage_subagent.py` +1（白名单非空仍放行 + 回填 provider/model）、`tests/test_subagent_spawn.py` 改写 2 项。
- **顺带修的真实缺陷**：这三处测试此前会把 source 写进用户真实 `provider_sources.json`（并覆盖 `main` 角色绑定）。已加 autouse 隔离 fixture，并手工清理被写入的 10 个测试源、恢复角色绑定。

### B4 逐模型 generation 参数 ✅

- `provider_sources`：`GENERATION_LIMITS` / `normalize_generation`（越界截断、非法丢弃、未知键丢弃）/ `generation_for(provider, model)`；模型项无配置时不写空字段。
- `ModelRoute.generation`；`LLMService._effective_generation_kwargs` 统一合并（模型配置 < route < 显式 kwargs）并按 `ProviderCapabilities.generation_params` 过滤，不支持的参数不下发且登记 `unsupported_parameter`。
- 生效点：`generate`、`stream_generate`（`with_tools` 每轮都经 `generate` → 首轮/后续轮一致）；`chat` 的 `temperature/max_tokens` 改可选以免硬默认压掉配置。
- 前端：`ProviderModel.generation` + `validateGeneration`（与后端同区间），模型行「生成参数」入口 + 三个数字输入 + 非法值 inline 错误 + 当前生效值展示。
- 证据：`tests/clipwright/test_llm_generation_params.py`（9 项，含「每轮参数一致」「显式覆盖」「不支持则不发」）；`ProvidersPage.test.tsx` +2。

### B5 reasoning 链路 ✅（已报缺口 1）

- `LLMService.generate/with_tools` 新增 `on_reasoning`（with_tools 带 round 转发）。
- `agent_loop`：reasoning → `thinking` 事件（`detail={delta, done:false, round}`），收尾一条 `done:true`；顺序 `thinking → narration(preamble) → tool_call`。
- 缓冲改 `agent_loop._THINKING`（按 task_id，`take_thinking` 取走即清），`agent_kernel._emit_thinking` 不再读全局单槽 `_LAST_REASONING`（那是跨任务串台源）。
- `reasoning_effort`：模型项可配（`REASONING_EFFORTS` 校验）、请求级 `extra.reasoning_effort` 可传；capability 不支持 → 不下发 + `degradation(scope="reasoning_effort")`，**thinking 照常转发**。
- 前端：需求视图「推理强度」下拉（默认跟随模型配置），随 `proceed.extra_params.reasoning_effort` 下发，并明确提示不支持的模型会忽略。
- 证据：`tests/test_agent_loop.py` +5、`tests/clipwright/test_llm_generation_params.py` +6、`AgentPanel.test.tsx` +1。
- **偏离（必须记）**：`with_tools` 每轮走的是**非流式** `generate`（isobase 工具轮不支持 stream+tools 组合），因此 thinking 是「每轮一块」而非 token 级 delta；token 级思考仍由需求对话的 `structured_output_stream` 承担。前端两种形态都按同一 `thinking` 事件渲染。

### 尚未完成（下一轮继续）

B6（`/v1/models` 前端四态）、B8（文案 artifact 化 + `input_refs`）、B10（Plan 降级 UI）、
B11（子 agent 与审批 run 化）、B12（事件呈现收口）、B7（跨仓库全量回归与文档同步）。

