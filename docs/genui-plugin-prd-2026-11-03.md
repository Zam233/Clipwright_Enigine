# GenUI 插件与三宿主支持 PRD

日期：2026-11-03  
状态：调研与规划稿；未改代码、未运行测试  
目标读者：后续负责实施的 agent

## 1. 结论先行

**结论：两者都要，但扩展重点在前端宿主；不需要新增后端 ContributionKind，也暂不需要新增 slot。** 后端现有 `UI_SLOT`、`PROMPT`、Tool/能力网关及 prompt override 已能供插件声明布局、能力与提示词；然而前端 `Slot` 仅把贡献的 `layout` 交给静态 `PluginLayoutRenderer`，后者支持14类控件而不支持模型会话级 GenUI spec，三个目标宿主也没有统一的 spec 校验、渲染及交互回传入口。因此须新增前端 GenUI renderer/宿主接线，并对后端 requirements 流程加最小的插件 prompt/输出校验接线；这不是“只放入一个插件目录”即可完成的功能。

判据：贡献类型冻结为17类且含 `UI_SLOT`/`PROMPT`（`J:\Clipwright\clipwright\plugins\contribution.py:22-45`）；声明白名单及内置冲突为默认拒绝（同文件 `:121-130,175-193`）；前端 Slot 冻结15项（`J:\Clipweight-Client\src\features\plugins\slots.ts:13-47`），Slot 仅处理 layout 或对应专用 renderer（`J:\Clipweight-Client\src\features\plugins\Slot.tsx:49-67`）；布局渲染器当前联合类型止于14种（`J:\Clipweight-Client\src\features\plugins\types.ts:13-27`）。

## 2. GenUI 参考实现调研

已取得 [dsh-genui README](https://raw.githubusercontent.com/omdsh-dev/dsh-genui/main/README.md)（本次未抓取仓库源码与 SKILL.md，以下对参考实现的细节以 README 声明为限，不推断源码实现）。它将 `dsh-ui` fenced block 中的 JSON 作为呈现 spec，根对象可含 `title`、`items`，节点以 `type` 区分；示例包含 `row`、`stat`、`progress`、`echart` 等。白名单规模宣称30+，涵盖排版、表格/图表、流程、表单/测验、媒体、Mermaid/3D 等。动作是明确声明 `action` 的交互控件触发结构化事件回宿主/模型；排序、过滤、滑杆、测验评分等纯 UI 状态由前端本地完成。非法 spec 提供校验诊断、节点级容错，禁用后 fence 回落普通代码块；禁止脚本注入/eval，重型图表/3D 引擎按需加载。

本地相同点：布局以 JSON 声明、控件白名单、宿主侧执行动作、能力由插件供应；未知控件可见占位、`file` 控件值回宿主而上传执行仍在宿主。依据 `types.ts:8-27,149-181`、`PluginLayoutRenderer.tsx:64-140,378-439`、`AGENTS.md:118-128`。

差异与复用：本地 `UILayout` 是插件静态配置面板，按钮 `UIAction` 由 renderer 直接请求 endpoint；GenUI 是 LLM 在单次 brief/plan/agent 交互中生成的组合展示数据，交互可能本地处理，也可能回当前会话。两者生命周期、风险和动作归属不同，不应把 LLM spec 伪装成 `/api/plugin/contributions` 的静态 `layout`。复用本地主题、React 原生控件、可访问性实践、ErrorBoundary 与 JSON-only 思路；新建独立 GenUI spec validator/renderer，并让宿主持有 action callback。不可照搬依赖 CDN/任意外链、HTML、全量 Mermaid/echart/3D 能力。

## 3. 差距分析

| 差距 | 证据与影响契约 | 归属/最小处理 |
|---|---|---|
| 三宿主需要接收模型产生的动态 spec，当前 Slot 数据源只有 `/api/plugin/contributions` 静态贡献 | `usePluginSlots.ts:47-75,87-90`；`Slot.tsx:29-35,49-67` | **需要前端宿主扩展**：单独的 `GenUIBlock`/renderer 接口接收 spec，不新增 slot。 |
| 静态 UILayout 不足以表达 GenUI | `types.ts:8-27` 共14类；`PluginLayoutRenderer.tsx:142-446` switch；`UIAction` 是 endpoint/method/body，`types.ts:166-181` | **需要前端宿主扩展**：独立 GenUI AST/组件白名单与 action callback。不要扩充 `UIWidget` 来承载图表/测验/模型动作。 |
| 后端 Requirements 对话输出 brief 数据，但没有约定 `genui_spec` | `requirements_agent.py:77-158` 生成并规范 brief；`requirements_service.py:2958-3068` 流式给 delta/reasoning/question/result，`3070-3112` 缓冲事件/心跳 | **需要 engine 扩展**：requirements response/session message 增加可选且经 schema 过滤的 `genui_spec` 字段；SSE result/event 投影需保持回放一致。具体数据模型/序列化落点待实施时核对 service store schema。 |
| 规划书产出是结构化 plan + markdown，非 GenUI | `prompts.py:42-43`；`requirements_agent.py:175-215`；`requirements_service.py:2656`（翻译函数入口，需再读实现）；前端 `PlanReviewPanel.tsx:20-31,61-99` | **engine + 前端宿主扩展**：由已有计划翻译环节生成受限 spec；原始 plan 保持权威，spec 只作展示，不得替代确认用 plan。 |
| 需求副驾无会话绑定的 UI action 协议 | `CopilotDock.tsx:34-82` 仅按 tab 渲染既有面板；`AgentPanel.tsx:624-672` 消费 `status/delta/reasoning/stage/question`，`676-707` 处理最终 brief/plan；task 事件事件集合冻结于 `schema/task.py:34-72`，前端 `eventSchema.ts:18-56` 精确对账31类 | **需要前端宿主扩展**：GenUI action 必须路由到当前 requirements session 或 task，不要把任意 action 发送到全局 chat。仅当选择 task SSE 承载 spec 时，才新增事件并同步后端/前端事件 union、回放及契约测试；首版建议通过 requirements result/投影数据而非新增 task event。 |
| 插件供给 prompt 与工具能力可复用，但插件本身不能注入前端 bundle | prompt 单一来源 `prompts.py:7-14`，覆盖机制 `prompt_overrides.py:7-21`；工具网关 `api/capability.py:1-19,74-104`；Contribution dispatch 支持集合不含新 UI 事件 `contribution_dispatch.py:33-50` | **新建插件可解决**：提供 `PROMPT` 贡献与可选 TOOL/能力。其余 spec 协议由宿主实现。 |
| manifest/入口必须按 api v2 声明 | 示例 `plugins/example_full/plugin.yaml:1-17`；类贡献写法 `plugins/example_full/main.py:45-71`；entry_point 模块路径校验 `schema/plugin.py:62-95`；插件加载前登记声明 `plugins/base.py:86-95` | **插件可解决**：新增 plugin.yaml + main.py，`entry_point` 必须是 `<插件目录>.main` 形式（如 `genui_plugin.main`，实际 import package 名须与目录匹配）。manifest 每个 kind:id 明确声明。 |
| 模型输出需严格消毒，现有 `asText` 只覆盖普通文本 | `AGENTS.md:118-128` 规定值回宿主、未知控件占位；渲染边界在项目地图旧版 #13，主仓 `AGENTS.md` 当前实际行号 `118-128` 对应规则 #25；用户给定规则 #13 需实施时核对主/前端 AGENTS 一致性 | **需要前端宿主扩展**：所有文本 leaf 用 `asText`/`textList`；schema 运行时校验不得依赖 TS 类型。 |

### 现状契约补充

- ContributionKind 明列17种，见 `J:\Clipwright\clipwright\plugins\contribution.py:22-45`；`ConflictPolicy.REJECT` 默认，内置 owner 冲突只有显式 OVERRIDE 才过，见 `:47-50,175-193`。`declared_scope` 是运行时唯一授权登记点，未声明拒绝，见 `:121-130,152-158`。`override_builtin` 映射到 OVERRIDE，见 `plugins/base.py:86-95`；GenUI 插件不应覆盖内置贡献，字段保持 false。
- 插件 api_version、contributions、override_builtin 与 entry_point 校验：`schema/plugin.py:27-44,62-95`；示例 `plugin.yaml` 使用 api_version 2 和逐项声明。注意仓库内示例的 entry_point `_example_full.main` 与目录名 `example_full` 有差异，属于测试/加载命名安排；实施者须按 loader package alias 核验，不可照抄。`loader.py:921-923` 读取 entry_point。
- Slot 15项冻结，见 `slots.ts:13-47`。`Slot.tsx:13-27,29-70` 暴露 context/filter/optionSources/onValuesChange；`usePluginSlots.ts:47-59,71-85` 后端拉取、未知 slot 丢弃、失败为空。UILayout 共14控件见 `types.ts:13-27`，UIAction 只能 GET/POST JSON endpoint/body，见 `:166-181`；虽然 SlotLayout 与 UILayout 同构，动态 LLM spec 不是静态 plugin contribution。

## 4. 设计

### 4.1 Spec 数据契约

采用独立版本化 JSON，初版仅支持确定性基础组件：

```json
{
  "version": 1,
  "title": "简报摘要",
  "items": [
    {"type": "text", "content": "核心目标"},
    {"type": "list", "items": ["受众", "风格"]},
    {"type": "callout", "tone": "info", "content": "待确认事项"},
    {"type": "table", "columns": ["段落", "时长"], "rows": [["开场", "8秒"]]},
    {"type": "button", "label": "继续追问", "action": {"type": "ask", "payload": {"prompt": "..."}}}
  ]
}
```

必填：顶层 `version=1`、`items` 数组；每节点必填白名单 `type` 和该类型要求字段；文本必须为字符串/字符串数组，不允许 React 对象直出。初版白名单建议 `text/list/callout/badge/stat/progress/table/steps/divider/row/col/card/accordion`。节点数≤100、嵌套≤6、文本单字段≤4000字符、总序列化 spec≤64KB；数组/表格各≤100项，表格列≤12。按钮 action 类型首版限 `ask`（映射宿主现有对话发送）、`select`（本地选择）、`confirm`（宿主已有确认回调）；没有宿主 callback 的 action 显示 disabled。绝不允许模型指定 endpoint、HTTP method、URL、任意 capability 名、脚本或任意 props。

未知组件：仅该节点降级为可读“暂不支持的组件”占位并记录 telemetry，不导致兄弟节点消失。非法顶层/超过资源阈值：整个块降级为普通文本/原 brief-plan 卡片，同时保留权威结构化内容；不得空白。重复 id、异常深度、超限、危险键（`__proto__`/`constructor`/`prototype`）拒绝或剔除。

### 4.2 产出 spec

- 创意简报：`RequirementsAgent._generate_brief` 的 structured output 可新增可选 `genui_spec`；brief 字段继续以 `CreativeBrief` 为权威。提示词应通过既有 `CREATIVE_BRIEF_SYSTEM`/`PluginPromptRegistry` 插件扩展机制定义，不新增 prompt 符号；除非实测发现无法追加输出规范，再提案新增 `GENUI_SPEC_SYSTEM` 并接入 `prompts.py` 与 override 符号审查。
- 规划书：使用现有 `PLAN_TRANSLATE_SYSTEM`（`prompts.py:42-43`）及 `_translate_plan`/`RequirementsAgent.translate_scenes` 所在当前真实路径附加相同 schema 约束。规划书只消费 plan JSON 与 brief；禁止根据 markdown 再解析出业务字段。
- 右侧副驾驶：首版不让主 agent task 任意生成并下发新事件。先由 requirements chat 返回的 spec 展示在副驾驶的现有 message 卡片；针对 task run 的 action 只允许把用户选择转换为现有注入/提问接口，后续版本再评估 task SSE event。
- 插件提供规范文本、组件使用示例和可选的 `genui_spec_validate` TOOL/能力；首版 validator 应为 engine 共用纯函数而不是要求模型自己调用工具。插件负责提示模型“可用什么”，不拥有最终校验权。

### 4.3 校验

后端在 Pydantic v2 schema/requirements response boundary 校验：版本、键集合、类型、最大字节、节点数、深度、字段长度、表格尺寸、action kind 与 payload allowlist；不合法 spec 置空并记录可诊断的降级原因，不阻断 brief/plan 正常返回。前端再以运行时 validator 防代理、旧缓存、历史数据或不可信插件返回绕过。校验绝不可用 eval/HTML parser 执行表达式。

前端容错：逐节点 render try/catch 或 ErrorBoundary；所有模型文本走 `asText`/`textList`；URL 默认不支持，若未来支持 image，仅允许同源受控资产 URL 或宿主签发的短时 URL，协议仅 https/http(same-origin)，拒绝 data/blob/file、用户名密码、外域重定向。

### 4.4 渲染与挂载

新增 `GenUISpecRenderer`，不复用 `PluginLayoutRenderer`。理由：后者管理表单状态、任意受控 JSON action endpoint 和插件静态布局；GenUI 要组合展示组件、会话 action callback、递归边界及另一套 schema。共享基础 UI primitive，不共享 schema。

- 创意简报：`AgentPanel.tsx` 的 `BriefCard`（`:1930` 起）与 `RequirementsPane` message 渲染处（`AgentPanel.tsx:170-190`）；首页 `CreativePromptField` 是用户输入表单（`HomePage.tsx:1360-1367`），不应拿 GenUI 覆盖该编辑输入。首页现有 `<Slot name="create.dub">` 在 `HomePage.tsx:1194`，与简报无关。
- 规划书：`PlanCard`（`AgentPanel.tsx:2011` 起）和 `PlanReviewPanel`（`src/features/plan/PlanReviewPanel.tsx:61-99,135-180`）。GenUI 作为只读辅助视图插入方案卡/审阅页，原 PlanView 编辑、legacy 警示、25% 时长校验和确认逻辑不变。
- 右侧副驾驶：`CopilotDock` 仅负责 tab 容器，`:34-82`；requirements tab 实际为 `RequirementsPane`。将 renderer 放进 message component，传入当前 session/task callback；避免新增 `editor.right-panel` 贡献，因为它是编辑器插件面板，与副驾驶交互不是同一语义。

**Slot 决策：首版不新增 Slot。** 三处是已有业务宿主的消息卡片/审阅区，不是插件任意挂载点；把 LLM 的 transient spec 当 Slot contribution 会混淆静态插件声明与每轮数据。若后续证明第三方插件需在任意宿主扩展挂载点，再提交 SlotRegistry 评审：唯一所有者、生命周期/状态持久性、权限与 action 回调、无贡献退化、移动端布局、可见性上下文最小化、宿主侧缓存失效、冲突排序与 disabled 情况均须量化；新增 slot 必须同步 TS union/registry/API 文档/契约测试，不得静默扩大名单。

### 4.5 安全边界

- 主文档永不接收模型 HTML/JS/CSS，不使用 `dangerouslySetInnerHTML`、eval、动态 import 模型给出的模块路径。
- 外链图片/视频/字体默认禁用；任何资源均由宿主 allowlist 与代理策略控制。
- `scene3d`、`echart`、`mermaid`、plot、地图等首版不开放。若后续需要，须单独安全评审依赖许可、包体/懒加载、协议/资源拦截、性能预算及无依赖回退。spec 中出现这些类型按未知节点占位，不升级运行时。
- 所有按钮动作绑定宿主闭包，不接收 URL/endpoint/HTTP method；`File` 上传继续属于宿主，遵循 `AGENTS.md:118-128` 的值回传和宿主执行原则。

## 5. 任务拆分

估算以一位熟悉代码库的全栈工程师计，人日为工程日，不含等待产品裁决。

| 批次 | 可执行动作 | 验收标准 | 人日 | 关键路径 |
|---|---|---|---:|---|
| Batch 0 核实 | 复核 `_translate_plan` 完整实现、requirements 存储/响应 Pydantic 模型、message timeline 序列化、Copilot callback 能否承接 ask；核对两个仓库 AGENTS 编号/文本 | PRD 中待核实点全变成具体文件/字段/调用链；确认无需 slot/kind | 0.5 | 是 |
| Batch 1 契约 | 定义 `GenUISpecV1` 后端 Pydantic 与前端 TS runtime validator；确定 13种首版组件、限制和 action unions | 有合法/非法 fixture；版本与上限前后端一致；不改 CONTRIBUTION/slot union | 1.5 | 是 |
| Batch 2 后端插件与产出 | 新建 `plugins/genui`，manifest api v2 声明 `PROMPT`（及需要的 TOOL），main 提供 schema 约束；requirements brief/plan 输出挂可选 spec、后端校验、session/message 持久化与回放 | 插件禁用时 brief/plan 原样工作；非法 spec 不丢业务字段；prompt override 检查通过 | 2.0 | 是 |
| Batch 3 前端 renderer | 新增独立 renderer、runtime sanitization、逐节点容错、无 action disabled、本地组件态 | 组件快照、坏节点负例、asText 边界、100节点和6层上限验证 | 2.0 | 是 |
| Batch 4 三宿主接入 | BriefCard、PlanCard/PlanReviewPanel、RequirementsPane message 渲染；明确当前会话 action callbacks | brief/plan/task 切换时不串会话；确认/编辑旧逻辑保留；禁用插件退回原卡片 | 2.0 | 是 |
| Batch 5 文档/验收 | 更新 API parity、插件开发文档、添加契约测试与 `verify_provider_pluginization.py` 检查思路；人工视觉/无插件验收 | 后端/前端契约集合一致、插件 enable/disable smoke、自测清单通过 | 1.0 | 否 |

关键路径：Batch 0 → 1 → 2 与 3 可并行 → 4 → 5。总计约9人日（另预留1人日处理历史 session schema/backfill 发现的问题）。

## 6. 验收清单

本 PRD 阶段不运行以下命令；这是实施阶段的验收计划。

**可自动自证**

- Python：`py -3.14 -m pytest tests/test_event_contract.py tests/test_plugin_contributions.py -q`（实际文件名实施前核对）；新增 `tests/test_genui_spec.py` 覆盖 schema fixture、超限、未知组件、安全键、非法 action。
- 前端：`npx tsc --noEmit`；新增 renderer/host tests 覆盖节点容错、所有文本过 `asText`、无 action 禁用、ask callback 绑定 session id、PlanReview 业务行为不变。
- 插件门禁：扩展 `py -3.14 scripts/verify_provider_pluginization.py`：断言 genui manifest 逐项声明与 contributions 对齐、无新增 ContributionKind/API route、prompt symbol 存在且 wired、插件禁用后没有 spec 产出依赖；脚本当前不等价于全量验收，不能单独作为通过依据。
- `git diff --check`；前后端协议 fixtures 对照；插件开启/关闭均通过；requirements SSE 最终 result 与 session 回放保持同一个 spec/版本。

**必须人工验收**

- 简报、规划书、主 agent 副驾驶三条流程分别展示；移动/窄窗不溢出；深色/浅色主题可读。
- 断网/禁插件/旧 session/非法或缺 spec 下原 brief、plan、消息和确认入口仍可用。
- 点击按钮只影响所属 session/task；规划书仍确认用户看到的结构化 plan，而不是 spec 的替代结果。
- 浏览器 DOM 不存在模型注入 HTML/script；Network 面板无模型控制外链请求；任何图片不自动远程加载。

## 7. 风险与回滚

- 插件禁用：后端停止 GenUI prompt 扩展与 spec 生成；前端识别缺失/无效 spec 时显示现有 BriefCard/PlanCard/Markdown，不让业务宿主依赖插件在线才能读取历史。若 spec 已持久化，仍只用内建 renderer 的安全 schema；未知版本显示原始结构化 brief/plan，不尝试动态加载插件代码。
- 精确回滚：实施拆分为“前端 renderer/接入”和“后端 schema/产出/插件”可独立提交；按依赖顺序 revert 宿主接入，再 revert后端产出，最后 revert插件。不得用运行时 feature flag；若回滚需兼容已存数据，保留宽容读、停止写入，直到数据迁移/生命周期确认。
- 风险：需求聊天流与存储投影有多条路径；遗漏 result/event/session 任一路径会造成刷新后卡片消失。PlanView 与 markdown 并存，误将展示 spec 当执行权威会造成用户确认偏差。动作错绑会让副驾驶跨任务操作。schema 双端漂移会产生半渲染。通过 fixture 单一版本、严格会话 id 和普通卡片 fallback 控制。

## 8. 明确不做

- 不重写 `CreativePromptField`，不把静态创作提示输入替换为模型生成表单。
- 不新增 ContributionKind、HTTP route、slot 名、task event（除后续证明确需 task SSE 展示并走评审）。
- 不让 GenUI spec 取代 CreativeBrief、ProductionPlan/PlanView 或 agent task/event 权威数据。
- 不支持任意 HTML、脚本、CSS、远程组件、任意 endpoint、外链图片及文件上传动作。
- 首版不支持 Mermaid、ECharts、3D scene、plot、地图、媒体播放、工作区持久 dock、表格导出、跨会话交互状态。
- 本次只产出调研与 PRD，不改业务代码、不跑测试、不 commit。

## 调研证据索引与未核实项

已直接读取并引用：`J:\Clipwright\clipwright\plugins\contribution.py`；`schema/plugin.py`；`plugins/base.py`；`plugins/example_full/plugin.yaml` 与 `main.py`；`clipwright/prompts.py`；`clipwright/plugins/prompt_overrides.py`；`clipwright/api/capability.py`；`clipwright/schema/task.py`；`clipwright/agents/requirements_agent.py`；`clipwright/services/requirements_service.py` stream_chat；前端 `slots.ts`、`Slot.tsx`、`usePluginSlots.ts`、`PluginLayoutRenderer.tsx`、`types.ts`、`eventSchema.ts`、`HomePage.tsx` 对应片段、`AgentPanel.tsx`、`PlanReviewPanel.tsx`、`CopilotDock.tsx`、`CreativePromptField.tsx` 与 `AGENTS.md`。

尚未直接读取 `requirements_service.py:_translate_plan` 函数体、后端 requirements 数据 schema/存储完整序列化链、`HomePage.tsx` 中需求聊天组件挂载关系及三个宿主的所有 message/card 组件、仓库完整源码与 dsh-genui SKILL.md。实施者在 Batch 0 按 `requirements_service.py:2656`、`:2814-2833`、`:2958-3112`，`AgentPanel.tsx:170-201`、`:2011+`，`HomePage.tsx:1194` 及主仓 `schema/requirements.py` 进一步确认；在此之前 PRD 将“持久化具体字段、stream 事件投影策略”标为待核实，不应直接照字段名编码。

## 9. 实施状态（2026-11-03，同日落地）

按这份 PRD 已落地**最小可用纵切**，结论与 §1 一致：**一个插件 + engine 与前端宿主的接线**，未新增
ContributionKind / slot / task event。

| 批次 | 实际落地 | 位置 |
|---|---|---|
| 契约 | 13 类组件白名单、上限（100 节点 / 6 层 / 4000 字 / 12 列 / 100 行 / 64KB）、动作白名单（ask/select/confirm）、危险键剔除、未知组件降级、版本校验 | `clipwright/schema/genui.py`；前端镜像 `src/features/genui/validate.ts` |
| 产出 | 模型可在简报里给 `genui`（字段已进 `CreativeBrief`，因此自动出现在字段表与 JSON 骨架里）；模型不给/给坏时用 `build_brief_genui()` / `build_plan_genui()` **确定性**补一份；legacy 规划书路径同样补 | `schema/requirements.py`、`agents/requirements_agent.py`、`services/requirements_service.py` |
| 插件 | `plugins/genui/`（manifest api v2 + 一条 `PROMPT` 贡献，`agent: requirements`），教模型"能写什么"；不渲染、不校验、不覆盖内置 | `plugins/genui/main.py` |
| 渲染 | 独立 `GenUISpecRenderer`（不复用 `PluginLayoutRenderer`）：渲染前再洗一遍、逐节点 `NodeBoundary` 容错、手风琴走原生 `details`、文本全走 `asText`、无 HTML | `src/features/genui/GenUISpecRenderer.tsx` |
| 宿主 | 创意简报卡与规划书卡各加一个 GenUI 块（`data-testid` 分别是 `brief-genui` / `plan-genui`），两者都在右侧 agent 副驾驶（`RequirementsPane`）的消息流里，因此一处接线覆盖三个目标面 | `src/features/agent/AgentPanel.tsx` |
| 动作 | `ask` → `sendChat(当前需求会话, prompt)`；`select`/`confirm` 无宿主语义 → 按 `supportedActions={['ask']}` 渲染为禁用态 | 同上 `onGenUIAction` |

**与 PRD 的三处偏差（有意，已记录）**：

1. **规划书接在 `PlanCard` 而不是 `PlanReviewPanel`**：全仓 `PlanReviewPanel` 只被 `import type` 使用，
   没有任何挂载点（组件是"设计稿级"的存在）。规划书审阅的现役宿主是 `PlanCard`，所以接在那里。
2. **未接 task SSE event**：按 §4.2/§8，首版不新增事件；副驾驶的 GenUI 来自 requirements 消息卡片。
3. **模型只被鼓励产出简报 spec**；规划书 spec 一律由场景确定性生成（不信任"翻译步骤顺手编的展示结构"）。

**验证（本机实跑）**

- `py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q` → **29 passed**（清洗器 12 项、构造器 4 项、
  schema 接线 5 项、agent 兜底 4 项、插件一致性 4 项）；
- `py -3.14 -m pytest <全部 test_requirements_*.py> tests/clipwright/test_plugin_contribution.py` →
  **108 passed / 3 failed**，3 个失败是**既存**的 `test_requirements_stream_chat`（与本改动无关）；
- 前端 `npx tsc --noEmit` → 退出码 0；`npx vitest run src/features/genui` → **14 passed**；
  `npx vitest run src/features/agent src/features/sessionTimeline` → **81 passed / 5 skipped**（既有用例未回归）。

## 10. 勘误（2026-11-07，依据 coding plan §0.5 与实测复核）

coding plan 复核时发现 §9 有几处判断**与代码不符**，逐条更正（§9 原文保留以便对照）：

1. **§9「加字段会自动出现在提示词字段表与 JSON 骨架里」→ 撤回**。`prompts.py` 的
   `CREATIVE_BRIEF_SYSTEM` 是已展开常量（1801 字符），**不含** `{{BRIEF_TEMPLATE}}`，因此
   `requirements_agent.py` 的两处 replace 都是空操作——**加 `genui` 字段不会自动改提示词**。
2. **§9「一处接线覆盖三个目标面」→ 收窄**为「**渲染**接线覆盖三个面（简报卡/规划书卡都在
   `RequirementsPane` 里），**数据产出未覆盖**」：生产路径（`requirements_service.py`）不写 `genui`。
3. **§9「规划书只走确定性构造器」→ 意图保留**，但要说明：已落地的 `requirements_agent` 里仍有一处
   "模型给了就洗一遍"的分支（死代码），接线时应删除，避免与声明矛盾。
4. **§5 任务拆分与估算失效**：真实接线点在 `services/requirements_service.py`（`_handle_gathering` /
   `_translate_plan`），不在 `agents/requirements_agent.py`；以 coding plan §3 取代。
5. **§4.2「`RequirementsAgent._generate_brief` 可新增可选 genui」→ 该函数生产不可达**
   （`stage_subagent` 的 6 个阶段里没有 `requirements`），真实落点是 `_handle_gathering`。

**另加一条本期新增的不变量**（已写进两仓 `AGENTS.md` #26 的配套约束 C10）：**展示层不得影响业务判定**
—— `genui` 必须从 `_brief_hash`（简报指纹，用于幂等键与"规划书是否作废"）里排除。
详见 [coding plan](genui-plugin-coding-plan-2026-11-07.md) §0.3-⑧ §3 T1.1。
