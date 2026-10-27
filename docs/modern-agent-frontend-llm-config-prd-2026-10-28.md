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
