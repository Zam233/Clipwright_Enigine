> ⚠️ **本文是历史记录**：GenUI 能力已于 2026-11-08 迁移进插件（display_spec 扩展点 + plugins/genui/contract.json）。当前态见 [docs/genui.md](genui.md)，迁移裁定见 [docs/genui-plugin-migration-prd-2026-11-07.md](genui-plugin-migration-prd-2026-11-07.md)。本文中的 schema/genui.py、genui 字段、确定性构造器等均已**不存在**，仅供追溯。

# GenUI 插件 · 编码计划（Coding Plan）

日期：2026-11-07  
状态：**只做规划**——本次不改业务代码、不跑测试、不 commit  
目标读者：下一个直接照着做的实施 agent  
前置文档：[`docs/genui-plugin-prd-2026-11-03.md`](genui-plugin-prd-2026-11-03.md)（PRD，含 §9 实施状态）  
已落地提交：后端 `d79abfd`（feat(genui)），前端 `a96966a`（feat(genui)），两仓均在 `feat/master-agent`，工作区干净

---

## §0 现状核对（必读，不要跳过）

### 0.1 一句话结论

**契约、清洗器、渲染器、宿主挂载点都已经落地且质量不错；但"spec 从哪里来"这条链在生产路径上是断的。**
默认配置下 `brief-genui` 与 `plan-genui` **一次都不会渲染**，因为真正在跑的简报/规划书产出函数（`requirements_service.py` 里的 `_handle_gathering` / `_translate_plan`）根本不写 `genui` 字段；而写了 `genui` 的函数（`RequirementsAgent._generate_brief` / `translate_scenes`）**在生产上没有任何调用点**。

所以这份计划的主体不是"打磨"，而是 **§3 Batch 1：把 spec 接到真实产出路径上**。其余批次才是 PRD 原计划的读取补全、动作语义、组件扩充与验收。

### 0.2 已落地 · 复核通过（含提交号与 file:line）

| 能力 | 证据 | 复核结论 |
|---|---|---|
| 版本常量 | `clipwright/schema/genui.py:26` `GENUI_VERSION = 1` | ✅ |
| 13 类组件白名单 | `clipwright/schema/genui.py:29-32` | ✅ 与前端 `src/features/genui/types.ts:17-20` 逐项一致 |
| 容器类型集合 | `clipwright/schema/genui.py:35`（`row/col/card/accordion`） | ✅（后端常量目前只作文档，`_clean_node` 按 kind 分支判断） |
| 动作白名单 | `clipwright/schema/genui.py:38` `{ask, select, confirm}` | ✅ |
| 资源上限 | `clipwright/schema/genui.py:46-52`（100 节点 / 6 层 / 4000 字 / 64KB / 100 项 / 12 列 / 2000 载荷） | ✅ |
| 危险键剔除 | `genui.py:55` + 递归剔除 `:201-204` | ✅ |
| 清洗器 | `genui.py:313-365` `sanitize_spec()` | ✅ 未知组件降级（`:206-208`）、未知动作丢弃（`:94-112`）、超限截断、版本不符整块丢弃（`:333-334`） |
| 确定性构造器 | `genui.py:371-405` `build_brief_genui()`；`:408-448` `build_plan_genui()` | ✅ 纯函数、无 I/O、无模型调用 |
| Schema 接线 | `clipwright/schema/requirements.py:75-83`（brief）、`:101-107`（plan），均为 `field_validator(mode="before")` + `sanitize_spec` | ✅ 只动 `genui` 一个字段，权威字段不受影响 |
| 插件清单 | `plugins/genui/plugin.yaml:1-13`（`api_version: "2"`、`override_builtin: false`、`permissions: []`） | ✅ |
| 插件贡献 | `plugins/genui/main.py:64-75` 一条 `ContributionKind.PROMPT`，`meta={"agent": "requirements", "priority": 6}` | ✅ 形状正确 |
| PROMPT 转调接线 | `clipwright/plugins/contribution_dispatch.py:174-180` → `PluginPromptRegistry.register` | ✅ 机制存在（**但没有消费方，见 0.3-②**） |
| 前端类型契约 | `src/features/genui/types.ts:14-30`（版本/白名单/动作）、`:117-129`（节点 union） | ✅ |
| 前端运行时校验 | `src/features/genui/validate.ts:320-350` `validateGenUISpec()`、`:353` `hasGenUI()` | ✅ 逐条镜像后端 |
| 渲染器 | `src/features/genui/GenUISpecRenderer.tsx:390-419`（入口）、`:75-92`（`NodeBoundary` 逐节点容错）、`:94-128`（动作按钮禁用态） | ✅ 无 `dangerouslySetInnerHTML`；所有文本走 `asText`（`:142`、`:243`、`:285`、`:302`、`:339`、`:364`）；表格横向滚动 `:234`；手风琴原生 `<details>` `:361` |
| 简报卡挂载 | `src/features/agent/AgentPanel.tsx:1968-1970`（`data-testid="brief-genui"`） | ✅ 挂载点存在（**数据不到，见 0.3**） |
| 规划书卡挂载 | `AgentPanel.tsx:2057-2059`（`data-testid="plan-genui"`） | ✅ 同上 |
| 动作回落 | `AgentPanel.tsx:742-751` `onGenUIAction`：只认 `ask` → `sendChat(当前需求会话, prompt)`；`select/confirm` 由 `supportedActions={['ask']}` 渲染为禁用 | ✅ 没有 endpoint/URL 泄漏 |
| 前端类型声明 | `src/types/persona.ts:313`（`CreativeBrief.genui?: unknown`）、`:326`（`ProductionPlan.genui?: unknown`） | ✅ `unknown` 是刻意的，强制走校验器 |
| 不变量登记 | 后端 `AGENTS.md:130`（#26）、前端 `AGENTS.md:206`（#26） | ✅ |
| 测试 | `tests/clipwright/test_genui_spec.py`（29 用例：清洗 12 / 构造器 4 / schema 5 / agent 兜底 4 / 插件 4）；`src/features/genui/genui.test.tsx`（14 用例） | ✅ 数量与 PRD §9 的 29/14 吻合 |

### 0.3 已落地但必须复核的疑点（**这批才是本计划的起点**）

#### ① 简报 spec 的"模型产出路径"是断的：字段表机制已死

- `clipwright/prompts.py:40` 的 `CREATIVE_BRIEF_SYSTEM` 是**已展开**的常量（1801 字符），**不含** `{{BRIEF_TEMPLATE}}` 占位，**也不含** `genui` 行。
  - 只读内省证据（**未运行测试**，仅读常量）：`py -3.14 -c "from clipwright.prompts import CREATIVE_BRIEF_SYSTEM as S; print('{{BRIEF_TEMPLATE}}' in S, 'genui' in S, len(S))"` → `False False 1801`。
- 因此 `clipwright/agents/requirements_agent.py:45-47` 的模块级 `replace("{{BRIEF_TEMPLATE}}", brief_prompt_template())` 与 `:124-125` 的运行时 replace **都是空操作**——占位符在迁移到 `prompts.py` 时就被展开并冻结了。
- 反证：`tests/clipwright/test_genui_spec.py:200-204` 断言 `"genui" in brief_prompt_template()`——它测的是 `brief_prompt_template()` 的**返回值**，而那个返回值在今天没有任何调用点把它放进提示词。**绿测试盖着死代码。**
- **PRD 需修正**：§9「字段已进 `CreativeBrief`，因此自动出现在字段表与 JSON 骨架里」**不成立**。加字段**不会**自动改提示词。

#### ② 插件的 PROMPT 没有注入到真正跑的简报路径

- `PluginPromptRegistry.get_for_agent("requirements")` 全仓只有两个调用点：`agents/requirements_agent.py:127`（brief）与 `:209`（plan translate）——两者都在 0.3-③ 的死代码里。
- 生产上跑的是 `clipwright/services/requirements_service.py:2254`：`"system_prompt": CREATIVE_BRIEF_SYSTEM + context`，其中 `CREATIVE_BRIEF_SYSTEM` 是**该文件自己的第三份**定义（`:396`），**不查插件注册表**。
- 结论：插件能加载、贡献能注册，**但没有任何消费方**。所以"插件禁用/启用"今天**不产生任何可观察差异**（PRD §9 的插件退化验收实际上什么都没验到）。
- 附注：这份 `:396` 的提示词与 `prompts.py:40` 的**字段集不同**（它列了 `production_plan`/`reference_style`/`bgm_requirement`/`era_background`/`material_requirements`/`animation_style`/`asset_ratio` 等**不属于** `CreativeBrief` 的键），属于既存的提示词单一来源（不变量 #23）违规，但不在本计划范围内（见 §6）。

#### ③ `RequirementsAgent` 在生产上不可达

- `clipwright/services/stage_subagent.py:33-40` 的 `_STAGE_DEPENDENCIES` 只有 `structure/material/edit/animation/audio/quality`，**没有 `requirements`**；`:90` 明确抛 `ValueError("阶段 {stage} 没有对应的 Agent（requirements 由需求服务处理）")`。
- 全仓 grep `RequirementsAgent(` 只命中 `tests/**` 与 `scripts/**`（如 `tests/clipwright/test_web_capability_wiring.py:42`）。
- 结论：`agents/requirements_agent.py` 里的 `execute`（`:61`）、`_generate_brief`（`:77`）、`_ensure_brief_genui`（`:143`）、`translate_scenes`（`:191`）、`_plan_genui`（`:245`）**全部是生产死代码**。

#### ④ 规划书 spec：确定性构造器写好了，但没接在实际路径上

- `requirements_agent.py:191 translate_scenes()` 里 `:228`（LLM 分支）与 `:241`（降级分支）都调 `_plan_genui` → **零调用点**（grep `translate_scenes` 只命中定义本身）。
- 实际路径是 `requirements_service.py:2660 _translate_plan()`：返回体（`:2765-2769`）只有 `summary/markdown_content/total_duration_sec/scene_count/raw_scenes`，**没有 `genui`**。
- 两条主路径都经过它：内联 `_generate_plan`（`:2453` → `:2641` 调用 `_translate_plan`）与派发回填 `_finalize_dispatched_plan`（`:3712` → `:3775` 调用 `_translate_plan`）。

#### ⑤ legacy 路径的 genui 几乎不可达

- `requirements_service.py:2444-2446` 是**唯一**在运行期调用 `build_plan_genui` 的地方，位于 `_legacy_plan_from_scenes`（`:2411`）。
- 它的唯一调用点是 `:1232`，位于 `if PLAN_DISPATCH_ENABLED:` 的 `else` 分支内（`:91`，`CLIPWRIGHT_PLAN_DISPATCH` 默认 `1`）。
- 结论：默认配置下 `build_plan_genui` **运行期零调用**；只有显式 `CLIPWRIGHT_PLAN_DISPATCH=0` 且内联生成失败时才可能走到。

#### ⑥ 右侧副驾驶：产品面**已覆盖**，缺的是数据

- 链路：`src/features/copilot/CopilotDock.tsx:42` → `<RequirementsPane />`；unified 流里 brief/plan 由 `AgentPanel.tsx:1149-1177` 的 `renderReport` / `renderPlan` 渲染，数据来自时间线条目。
- 时间线条目由 `requirements_service.py:1399 _close_turn()` 写入：`:1425 live.append("report", brief, ...)`、`:1435 live.append("plan", plan, ...)`，`brief`/`plan` 就是 `_brief_event`/`_plan_event`。
- 所以：**不需要独立面板、不需要持久 dock、不需要新 slot**（与 PRD §4.4 结论一致）。但正因为数据来自同一批 dict，brief/plan 没有 genui → 副驾驶上也看不到。
- 另需注意：非 unified 视图的消息级卡片来自 `stream_chat` 返回的 `creative_brief`/`production_plan`（`AgentPanel.tsx:687-718`），即 `get_session` 的结果。

#### ⑦ 既有产品行为：出了规划书后，消息级简报卡会被丢掉

- `src/features/agent/requirementsAttachments.ts:22`：`creative_brief: brief && !plan ? brief : null`。
- 影响：同一轮既返回 brief 又返回 plan 时（确认简报后的一轮），消息级 `BriefCard` 不渲染；时间线的 `report` 条目仍会渲染简报卡。验收时必须按"时间线路径"判断简报 GenUI 是否可见，不能只看消息气泡。

#### ⑧ `_brief_hash` 把展示层也算进指纹 —— 补 genui 前必须先修

- `requirements_service.py:101-110`：`_brief_hash` 对**整个 brief dict**（`json.dumps(..., sort_keys=True)`）取 sha256 前 16 位。
- 消费点：幂等键 `session_id:brief_hash`（`:3621-3622`、`:82`）、时间线 meta（`:1430`、`:1440`）、以及 `:3758` 的"**简报在生成期间被改过 → 这份规划书作废**"判定。
- 风险：一旦我们在读/写路径补 `genui`，**同一份简报在"补过"与"没补过"两侧会算出不同指纹** → 派发拿 A 指纹、回填拿 B 指纹 → 误报 `brief_changed` 并把刚生成的规划书作废。
- 结论：**`genui` 必须从指纹里排除**（展示层不得影响业务判定，这是 #26 的直接推论）。这条排在 Batch 1 最前面。

### 0.4 完全没做

| 缺项 | 现状证据 | 影响 |
|---|---|---|
| 老会话读时补 spec | `requirements_service.py:759-771 get_session()` 原样返回 Mongo dict | 2026-11-03 之前落库的会话永远没有 spec（回落原卡片，不崩） |
| `select` / `confirm` 语义 | `AgentPanel.tsx:742-743` 非 `ask` 直接 `return`；渲染器按 `supportedActions={['ask']}` 禁用 | 模型写了这两个动作也点不动（禁用态，无欺骗） |
| 跨仓契约自动对账 | 后端 `genui.py:46-52` 与前端 `validate.ts:24-29` 是**手工对抄**，无任何自动比对 | 改一边忘另一边 → 半渲染 / 前后端判断不一致 |
| 前端 64KB 总量上限 | `validate.ts:24-29` 只有 5 个上限，**没有 `MAX_SPEC_BYTES`**（后端 `genui.py:49` 有） | 后端会丢的 spec，前端可能照渲（受节点/文本上限约束，风险有限但属漂移） |
| 插件启停的真实退化验收 | 见 0.3-②，启停无可观察差异 | 无法证明"禁用插件后业务照常" |
| 文档 | `Get-ChildItem docs\*.md` 中只有 PRD 提到 genui（33 处）；`docs/api_reference.md`、`docs/frontend-backend-parity.md` **零**记录 | 下一个人只能靠读代码 |
| 表格本地排序/筛选 | `GenUISpecRenderer.tsx:232-262` 纯静态表；`:435-438 useGenUILocalState` 是**未被使用**的占位导出（`renderer` 头部注释 `:10` 却宣称"表格排序这类纯 UI 状态不回调宿主"——**注释与实现不符**） | 长表格（100 行）只能看不能筛 |
| 性能 / 窄屏 / 长 spec 实测 | 无任何记录；`compact` 只影响间距（`:402`） | 100 节点 × 4000 字的最坏情况未验证 |
| `PlanReviewPanel` 处置 | 全仓只有 `src/layouts/EditorLayout.tsx:17` 的 `import type { PlanView }`；组件本体（`:61`，241 行）**零挂载** | dead code，且它会构成第二个"确认"入口 |

### 0.5 PRD 需修正清单（直接写进 PRD 的 diff，不改代码）

1. **§9「字段已进 `CreativeBrief`，因此自动出现在字段表与 JSON 骨架里」→ 撤回。** 证据：0.3-①。
2. **§9「两者都在 RequirementsPane 的消息流里，因此一处接线覆盖三个目标面」→ 收窄为"渲染接线覆盖三个面，数据产出未覆盖"**。证据：0.3-④⑥。
3. **§9「规划书 spec 只走确定性构造器（不信任翻译步骤）」→ 意图保留，但要补一句"当前实现里的模型分支（`requirements_agent.py:253` 的 `sanitize_spec(plan.get("genui"))`）与这条声明自相矛盾，接线时应删除该分支"**。
4. **§5 的任务拆分与估算已失效**：核心接线点在 `requirements_service.py`，不在 `agents/requirements_agent.py`；Batch 2 的"requirements brief/plan 输出挂可选 spec"实际落点是服务层。本计划 §3 取代 PRD §5。
5. **§4.2「创意简报：`RequirementsAgent._generate_brief` 的 structured output 可新增可选 `genui_spec`」→ 该函数生产不可达**（0.3-③），真实落点是 `_handle_gathering`。

---

## §1 目标与不变量

### 1.1 本计划要达成的目标

1. **G1**：默认配置下，创意简报卡与规划书卡在右侧副驾驶（unified 时间线与消息气泡两条渲染路径）**都能稳定出现** GenUI 块。
2. **G2**：这份 spec 是**可缺省、可损坏、可禁用**的展示层——任何一环坏掉，brief/plan 的权威字段、确认流程、规划书作废判定都**不改变行为**。
3. **G3**：前后端规则**同源可验证**（不是"两份手抄，靠人记得一起改"）。
4. **G4**：老会话、坏 spec、插件被禁用、窄屏、深色主题这五种情况下都有明确且可验收的行为。

### 1.2 必须守住的契约（任何任务都不得违反）

| # | 契约 | 出处 |
|---|---|---|
| C1 | spec 只有 `version: 1` + 13 类组件白名单；扩组件必须走 §2.4 的验收条件 | `genui.py:26,29-32`；`types.ts:14-20` |
| C2 | 上限：100 节点 / 6 层嵌套 / 单文本 4000 字 / 表格 12 列 100 行 / 动作载荷 2000 字 / 整块 64KB | `genui.py:46-52` |
| C3 | 动作只认 `ask/select/confirm`；**spec 绝不携带 endpoint / URL / method / capability 名** | `genui.py:38`；`AGENTS.md:130`（#26） |
| C4 | 双道校验：后端写入洗一遍（`sanitize_spec`），前端渲染前再洗一遍（`validateGenUISpec`）；**改规则必须两边一起改** | `genui.py:313`；`validate.ts:320`；`AGENTS.md:206`（#26） |
| C5 | 不碰 HTML：无 `dangerouslySetInnerHTML`、无 eval、无外链图片/iframe；所有文本走 `asText` | `AGENTS.md:206`；`GenUISpecRenderer.tsx:142` 等 |
| C6 | 逐节点容错：单节点失败只占一行占位，不整卡白屏；无可用 spec 时整块不渲染 | `GenUISpecRenderer.tsx:75-92,398` |
| C7 | 权威数据不在 spec 里：`CreativeBrief` 字段、`markdown_content`、`raw_scenes` 才是确认与执行对象 | `genui.py:10-11`；`AGENTS.md:130` |
| C8 | 版本策略：**加字段不升版本**（前端容忍未知字段）；**换结构才升版本**，且旧版本整块丢弃、回落原卡片 | `genui.py:25,333-334` |
| C9 | 插件只教"能写什么"，不渲染、不校验、不覆盖内置、不新增 ContributionKind/slot/task event | `plugins/genui/main.py:7-16`；PRD §1 |
| C10 | **展示层不得影响业务判定**：`genui` 不得进入 `_brief_hash`、不得影响状态机、不得触发确认 | 本计划 0.3-⑧ 推导自 #26 |

### 1.3 "前后端规则必须同源"的具体含义（Batch 3 的任务依据）

现在两份规则是**人工对齐**的（`genui.py:46-52` ↔ `validate.ts:24-29`），没有任何自动检查。本计划要求：

- **单一真相文件**：把白名单、上限、动作、色调枚举抽成一份 `genui_contract.json`（后端目录为权威副本），后端/前端测试各自断言自己的常量与该 JSON 一致；
- **跨仓一致性检查脚本**：比对两仓的 JSON 副本是否逐字节相同，作为 §4 的可执行验收项；
- 任何新增组件/改上限的 PR，**必须同时改 JSON 与两侧实现**，否则脚本红。

---

## §2 契约冻结表

### 2.1 组件白名单（13 类）

`text / list / callout / badge / stat / progress / table / steps / divider / row / col / card / accordion`
（`genui.py:29-32`；`types.ts:17-20`）

### 2.2 每类必填字段与可选字段（以 `sanitize_spec` 的实际行为为准，不是以文档为准）

| 组件 | 必填 | 可选 | 清洗行为 | 证据 |
|---|---|---|---|---|
| `text` | `content` | `size` ∈ {display,h1,h2,h3,body,muted,caption}；`tone` ∈ {info,success,warning,error} | 非字符串叶子转 `""`；超 4000 截断 | `genui.py:215-222` |
| `list` | `items`: `string \| {title,desc}` 数组 | — | 非数组给空；条目非法丢弃 | `:126-144,223-224` |
| `callout` | `content`；`tone` 缺省 `info` | `title`（≤120） | tone 不在集合回落 `info` | `:225-234` |
| `badge` | `label`（≤60） | `tone` ∈ {success,warn,danger,accent} | 非法 tone 丢弃 | `:235-239` |
| `stat` | `label`（≤80）、`value`（≤80） | `delta`（≤40） | 全走 `_as_text` | `:240-248` |
| `progress` | `value`（0–100，钳制） | `label`、`valueLabel` | 非数字给 0.0 | `:249-258` |
| `table` | `columns`（≤12）、`rows`（≤100 行） | — | 单元格截 500 字；单元格数按列数裁剪 | `:147-165,259-260` |
| `steps` | `steps`（≤100，`string \| {title,desc}`） | `current`（int ≥0，钳到 `len-1`） | 无 title 的条目丢弃 | `:168-190,261-262` |
| `divider` | — | `label`（≤80） | — | `:263-267` |
| `row` / `col` | `items`（子节点数组） | — | 递归，深度 +1 | `:268-275` |
| `card` | `items` | `title`（≤120）、`tone` | 递归，深度 +1 | `:276-289` |
| `accordion` | `items`: `{title, items[]}`（≤100 组） | — | 非对象子项丢弃 | `:290-305` |

**所有节点**都可带 `action`（`:307-309`），形状 `{type, label?, payload?}`，载荷只留标量与 ≤2 层容器（`:77-112`）。

### 2.3 上限数值（冻结，改动需走 §1.3 的同源流程）

| 常量 | 值 | 后端 | 前端 |
|---|---|---|---|
| `MAX_NODES` | 100 | `genui.py:46` | `validate.ts:24` |
| `MAX_DEPTH` | 6 | `:47` | `:25` |
| `MAX_TEXT_CHARS` | 4000 | `:48` | `:26` |
| `MAX_SPEC_BYTES` | 65536 | `:49` | **缺失 → T3.2** |
| `MAX_ARRAY_ITEMS` | 100 | `:50` | `:27` |
| `MAX_TABLE_COLS` | 12 | `:51` | `:28` |
| `MAX_ACTION_PAYLOAD_CHARS` | 2000 | `:52` | `:29` |

### 2.4 动作语义（当前 + 拟议）

| 动作 | 当前语义 | 宿主支持 | 拟议（见 §7-Q2） |
|---|---|---|---|
| `ask` | 把 `payload.prompt`（回退 `message`/`question`）发回**当前需求会话** | ✅ `AgentPanel.tsx:742-751` | 保持 |
| `select` | 无。渲染为禁用态 | ❌ | **默认不做**：渲染器没有"选项组"视觉形态，开放等于给模型一个无法被理解的控件 |
| `confirm` | 无。渲染为禁用态 | ❌ | **默认不做**：`confirm` 会变成"模型写的按钮能推进状态机"，直接违反 C10 |

### 2.5 版本策略

- 现行：`version` 必须是 `1`（接受 `"1"`/`1.0`）；缺失视为 1；其它值 → **整块丢弃**并回落原卡片（`genui.py:325-334`；`validate.ts:326-330`）。
- 加字段（如未来给 `stat` 加 `unit`）**不升版本**：后端清洗器保留未知字段会被丢弃，前端只渲染已知字段，两边都安全。
- 换结构（如把 `accordion.items` 改成 `sections`）**必须升到 2**，且前端同时支持 1 与 2 的读取至少一个版本周期。

### 2.6 组件扩充：为什么现在不扩（以及扩的验收条件）

**候选与结论：**

| 候选 | 结论 | 理由 |
|---|---|---|
| `keyvalue` | **不扩** | 与 `table`（2 列）+ `list{title,desc}` 重复；`build_brief_genui` 已用 2 列表格表达键值（`genui.py:391`） |
| `image` | **不扩** | 需要域名白名单/短时签名 URL/代理策略；PRD §4.5 已明令首版禁用外链资源 |
| `tabs` | **不扩** | 纯静态只读卡片里，tabs 藏内容 = 用户看不见；`accordion` 已覆盖"分层折叠" |
| `chart` / `echart` | **不扩** | 需要图表库（体积 + 懒加载 + 许可证）；`stat`+`progress`+`table` 已能表达简报/规划书的全部数字 |
| `mermaid` / `svg` / `scene3d` / `plot` | **不扩** | PRD §4.5/§8 明确排除；`AGENTS.md:130` 已写死 |

**若未来要扩，必须同时满足（缺一不可）：**

1. 零新依赖，或依赖可树摇且首屏 gzip 增量 ≤ 30KB（需给出 `npm run build` 前后体积对比）；
2. 不加载任何模型可控的远端资源（无 URL 字段，或 URL 必须过宿主白名单+同源/签名）；
3. 有独立的安全评审结论（XSS / SSRF / 原型污染 / 资源耗尽各一条）；
4. 后端清洗器 + 前端校验器 + `genui_contract.json` 三处同步，且 `tests/clipwright/test_genui_spec.py` 与 `src/features/genui/genui.test.tsx` 各补正例/负例；
5. 插件 `plugins/genui/main.py:27-30 COMPONENTS` 同步（`test_plugin_components_match_schema` 会盯住）；
6. `AGENTS.md` #26（两仓）同步更新 13 → N。

---

## §3 任务拆分

**约定**：人日以"熟悉本仓的全栈工程师"为单位，不含等待产品裁决。  
**关键路径**：Batch 1 → Batch 2 → Batch 3 → Batch 5/6 可并行。  
**本项目测试约定**：`pytest` / `vitest` **默认由人执行**；agent 只用 `py -m py_compile`、`npx tsc --noEmit` 这类不产生副作用的自证。§4 区分了两类。

---

### Batch 1 · P0：让 spec 在真实路径上产出（关键路径，3.5 人日）

> 没有这一批，后面所有批次验收的都是死代码。

#### T1.1 — `_brief_hash` 排除 `genui`（先做，否则 T1.2 会踩雷）

- **归属**：后端
- **文件**：`clipwright/services/requirements_service.py`
- **动作**：
  - 改 `_brief_hash`（`:101-110`）：序列化前剔除 `genui` 键，例如 `payload = {k: v for k, v in brief.items() if k != "genui"}`；空 dict 仍返回 `""`；
  - 在注释里写清原因：`genui` 是展示层派生字段，不得影响幂等键与"简报是否变更"的判定（C10）。
- **验收**：
  - `py -3.14 -m py_compile clipwright/services/requirements_service.py` 退出码 0；
  - 新增用例 `tests/clipwright/test_genui_spec.py::TestBriefHash`：
    - `_brief_hash({"title": "T"}) == _brief_hash({"title": "T", "genui": {"version": 1, "items": []}})`；
    - `_brief_hash({"title": "T"}) != _brief_hash({"title": "T2"})`；
    - `_brief_hash({}) == ""`。
- **人日**：0.5
- **依赖**：无（Batch 1 第一步）
- **关键路径**：是

#### T1.2 — 简报产出接线：`_handle_gathering` 的两个调用点补 genui

- **归属**：后端
- **文件**：
  - `clipwright/schema/genui.py`（新增公共函数）
  - `clipwright/services/requirements_service.py`（`:1142` 主路径、`:1264` 修订路径）
- **动作**：
  1. 在 `clipwright/schema/genui.py` 末尾新增纯函数：
     ```python
     def ensure_brief_spec(brief: dict[str, Any]) -> dict[str, Any]:
         """模型给了就洗，没给/给坏就确定性补一份；绝不改其它字段。"""
     ```
     实现直接搬 `requirements_agent.py:143-156` 的逻辑（`sanitize_spec(brief.get("genui"))` → `spec or build_brief_genui(brief) or {}` → 写回 `brief["genui"]`），**并保留 `problems` 日志**。
  2. `requirements_service.py:1166-1170`（读取 `result["brief_draft"]` 处）：`brief_data = ensure_brief_spec(_new_brief)`，且 `_brief_event = brief_data`；
  3. `requirements_service.py:1264-1270`（修订路径 `revised.get("brief_draft")` 处）：同样过一遍；
  4. `requirements_agent.py:143-156` 的 `_ensure_brief_genui` 改为调用 `ensure_brief_spec`（保留方法名，避免动测试），或直接删方法并改 `:139` 调用公共函数。
- **验收**：
  - 新增用例 `tests/clipwright/test_genui_spec.py::TestServiceBriefWiring`：构造 `brief={"title":"T","overview":"O"}`，调 `ensure_brief_spec` 后 `brief["genui"]["items"]` 非空且 `title` 未变；
  - 既有 4 个 `TestAgentFallback` 用例仍绿（若删 `_ensure_brief_genui` 则改测公共函数）；
  - `py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q` → 全绿（预计 29 + 4~6 例）。
- **人日**：1.0
- **依赖**：T1.1
- **关键路径**：是

#### T1.3 — 规划书产出接线：`_translate_plan` 末尾补 genui，并删掉模型分支

- **归属**：后端
- **文件**：`clipwright/services/requirements_service.py`；`clipwright/schema/genui.py`；`clipwright/agents/requirements_agent.py`
- **动作**：
  1. `clipwright/schema/genui.py` 新增纯函数：
     ```python
     def ensure_plan_spec(plan: dict[str, Any], scenes: list[dict[str, Any]]) -> dict[str, Any]:
         """规划书只用确定性构造器：不看模型给的 genui（见 PRD §9 与计划 0.5-③）。"""
     ```
     实现 = `build_plan_genui(plan, scenes) or {}`，**不读 `plan.get("genui")`**。
  2. `requirements_service.py:2768`（`_translate_plan` 里 `result["raw_scenes"] = scenes` 紧接 `return result` 之前）：`result["genui"] = ensure_plan_spec(result, scenes)`。
     这样 `_generate_plan`（`:2641` 调用）与 `_finalize_dispatched_plan`（`:3775` 调用）**两条路径一次覆盖**。
  3. `requirements_service.py:2444-2446`（legacy 路径）改用同一函数，保持行为不变。
  4. 删掉 `requirements_agent.py:253` 的 `sanitize_spec(plan.get("genui"))` 分支（消除与"只走确定性"的自相矛盾），`_plan_genui` 整体改为调用 `ensure_plan_spec`；若该模块判定为死代码（T1.5），则只留一行转发。
- **验收**：
  - `py -3.14 -m py_compile clipwright/services/requirements_service.py clipwright/schema/genui.py` 退出码 0；
  - 新增用例：给 `_translate_plan` 一个不含 `genui` 的 LLM 桩返回值 + 2 个场景，断言返回 dict 的 `genui.items` 非空、`markdown_content` 未被改动；
  - 新增负例：LLM 桩返回一个**含非法 `genui`** 的 dict，断言最终 `genui` 仍是确定性生成的那份（证明不看模型）；
  - `py -3.14 -m pytest tests/clipwright/test_genui_spec.py tests/test_requirements_plan_translate_retry.py tests/test_requirements_plan_revision_call.py -q` 全绿。
- **人日**：0.5
- **依赖**：T1.1
- **关键路径**：是

#### T1.4 — 让提示词真的含 `genui`：修 `{{BRIEF_TEMPLATE}}` + 注入插件 PROMPT

- **归属**：后端
- **文件**：`clipwright/prompts.py`；`clipwright/services/requirements_service.py:2197-2260`
- **动作**（两条都要做，缺一模型就不知道能写 spec）：
  1. **把占位符放回** `clipwright/prompts.py:40` 的 `CREATIVE_BRIEF_SYSTEM`：把已展开的字段表 + JSON 骨架替换成字面量 `{{BRIEF_TEMPLATE}}`，保留其余文案**逐字不变**（这是不变量 #23 的"搬运必须逐字不变"要求，改完要做值快照对比：`brief_prompt_template()` 的输出必须与改动前的字段表段落**完全相同**）。
  2. 修 `requirements_agent.py:45-47`：占位符回来之后，模块级别再急着 replace；改为**运行时**替换（`:124-125` 已有该逻辑，保留即可），模块级那三行删掉或改成幂等函数。
  3. **把插件提示词接进真实简报路径**：`requirements_service.py:2254` 改成：
     ```python
     from clipwright.plugins.prompt_registry import PluginPromptRegistry
     plugin_prompts = PluginPromptRegistry.get_for_agent("requirements")
     system_prompt = CREATIVE_BRIEF_SYSTEM + context
     if plugin_prompts:
         system_prompt += _hdr("PROMPT_PLUGIN_EXT_HEADER") + "\n\n".join(plugin_prompts)
     ```
     （`_hdr` 已在该文件定义，`:3807`；`PROMPT_PLUGIN_EXT_HEADER` 与 agent 侧用法一致，见 `requirements_agent.py:129`。）
- **验收**：
  - `py -3.14 -c "from clipwright.prompts import CREATIVE_BRIEF_SYSTEM as S; assert '{{BRIEF_TEMPLATE}}' in S"` 退出码 0；
  - `py -3.14 -c "from clipwright.schema.requirements import brief_prompt_template as t; assert 'genui' in t()"` 退出码 0；
  - 新增用例 `tests/clipwright/test_genui_spec.py::TestPromptContainsGenui`：断言 `brief_prompt_template()` 含 `genui` 且含 `"genui":` 骨架键（等价于现有 `:200-204`，但要加一条**真正拼出来的 system prompt** 断言——见下）；
  - 新增用例：用 monkeypatch 注册一条假插件 prompt，调用一个抽出来的 `_brief_system_prompt()` 帮助函数，断言返回值**同时**含字段表与假插件文本（证明注入生效）。建议把 `:2197-2260` 的 prompt 拼装抽成 `_brief_system_prompt(context: str) -> str` 以便测试。
- **人日**：1.0
- **依赖**：无（可与 T1.2/T1.3 并行）
- **关键路径**：是

#### T1.5 — 处置 `RequirementsAgent` 死路径（决定并落地其一）

- **归属**：后端
- **文件**：`clipwright/agents/requirements_agent.py`；`clipwright/agents/__init__.py`；`clipwright/services/stage_subagent.py`（只读参照）
- **动作**（二选一，**必须选一个并写下理由**；推荐 A）：
  - **A（推荐，0.5 人日）**：保留类但显式降级为"预留/仅测试可达"——在模块 docstring 顶部写明「生产路径由 `services/requirements_service.py` 承担，本 Agent 当前无调用点（`stage_subagent.py:33-40` 无 `requirements`），勿在其上新增生产逻辑」，并把 genui 相关方法改为调用 `schema/genui.py` 的公共函数（T1.2/T1.3 已做），防止两套实现漂移。
  - **B（0.5 人日）**：删除 `translate_scenes`（`:191-242`）与 `execute`/`_generate_brief`，把 `agents/__init__.py:7` 的导出摘掉，同步删改 6 个只测它的用例文件（`test_plan_first.py`、`test_requirements_material_overview.py`、`test_requirements_plan_web_search.py`、`test_scene_grounding_wiring.py`、`test_web_capability_wiring.py`、`test_content_query.py`）。**风险高、收益低，不推荐**。
- **验收**：模块顶部有明确说明；`py -3.14 -m py_compile clipwright/agents/requirements_agent.py` 退出码 0；`grep -rn "translate_scenes" clipwright/` 只剩定义或零命中（与所选方案一致）。
- **人日**：0.5
- **依赖**：T1.2、T1.3
- **关键路径**：是（决定 T1.2/T1.3 是否要保留转发层）

---

### Batch 2 · P1：读取路径与老会话（1.0 人日）

#### T2.1 — 老会话读时补 spec

- **归属**：后端
- **文件**：`clipwright/services/requirements_service.py:759-771`（`get_session`）
- **动作**：
  - 在 `get_session` 返回前统一补：`creative_brief` 非空则过 `ensure_brief_spec`，`production_plan` 非空则过 `ensure_plan_spec(plan, plan.get("raw_scenes") or plan.get("scenes") or [])`；
  - **返回浅拷贝**（`dict(session)` + 新 dict 的 brief/plan），不要原地改 `_memory_sessions` 里的对象（`:2807 _persist()` 会在 `:2832` 用这个对象写回内存会话）；
  - 只补不存：**不回写 Mongo**。理由：读路径补是幂等的纯函数，回写会引入"读操作产生写"的副作用，且 T1.2/T1.3 已经保证新数据自带 spec。
- **验收**：
  - 新增用例 `tests/clipwright/test_requirements_session_cache.py` 或 `tests/clipwright/test_genui_spec.py::TestOldSessionBackfill`：mock `RequirementsSessionModel.find_by_id` 返回一个**不含 `genui`** 的老会话 dict，断言 `get_session()` 结果里 brief/plan 都有可用 spec，且 Mongo 模型对象**未被改动**；
  - 负例：`creative_brief=None` 时不报错、不凭空造 spec。
- **人日**：0.5
- **依赖**：T1.2、T1.3
- **关键路径**：是

#### T2.2 — 前端：老会话 / 无 spec 时确认"整块不渲染、原卡片完好"

- **归属**：前端
- **文件**：`src/features/genui/genui.test.tsx`；`src/features/agent/AgentPanel.test.tsx`
- **动作**：
  - 在 `genui.test.tsx` 补 3 条：`undefined` / `{}` / `{version:1,items:[]}` 三种输入 `hasGenUI()` 均为 `false`、`GenUISpecRenderer` 返回 `null`；
  - 在 `AgentPanel.test.tsx` 补 1 条：`BriefCard` 传 `genui={undefined}` 时 **不存在** `[data-testid="brief-genui"]`，且标题/受众等字段仍然渲染（证明回落不空屏）。
- **验收**：`npx vitest run src/features/genui src/features/agent` → 全绿（预计 14+3 与既有 81 用例）。
- **人日**：0.5
- **依赖**：无
- **关键路径**：否

---

### Batch 3 · P1：契约同源 + 插件退化（2.0 人日）

#### T3.1 — 抽出 `genui_contract.json` 单一真相，双端断言

- **归属**：两仓
- **文件**：
  - 后端新增 `clipwright/schema/genui_contract.json`（**权威副本**）
  - 后端 `clipwright/schema/genui.py`（改为从 JSON 读？**不改**——保留 Python 常量，只加"断言一致"的测试，避免引入 I/O 依赖）
  - 前端新增 `src/features/genui/contract.json`（副本）
  - 后端 `tests/clipwright/test_genui_spec.py`
  - 前端 `src/features/genui/genui.test.tsx`
- **动作**：
  1. JSON 结构（**只读数据，不含逻辑**）：
     ```json
     {"version": 1,
      "components": ["text","list",...],
      "containers": ["row","col","card","accordion"],
      "actions": ["ask","select","confirm"],
      "tones": ["info","success","warning","error"],
      "limits": {"max_nodes":100,"max_depth":6,"max_text_chars":4000,
                 "max_spec_bytes":65536,"max_array_items":100,
                 "max_table_cols":12,"max_action_payload_chars":2000}}
     ```
  2. 后端测试：断言 `set(GENUI_COMPONENT_TYPES) == set(json["components"])`、`GENUI_CONTAINER_TYPES`、`GENUI_ACTION_KINDS`、`GENUI_TONES`、7 个 `MAX_*` 全部与 JSON 相等（**逐项**，不是抽样）。
  3. 前端测试：同样断言 `GENUI_COMPONENT_TYPES`、`GENUI_ACTION_KINDS`、`MAX_NODES/MAX_DEPTH/MAX_TEXT_CHARS/MAX_ARRAY_ITEMS/MAX_TABLE_COLS/MAX_ACTION_PAYLOAD_CHARS` 与 JSON 相等。
  4. 插件侧：`plugins/genui/main.py:27-30 COMPONENTS` 已有测试盯住（`test_genui_spec.py:263-268`），补一条"插件 PROMPT 文本里的上限数字与 JSON 一致"（可选，避免文案与契约漂移）。
- **验收**：
  - `py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q` 全绿；
  - `npx vitest run src/features/genui` 全绿；
  - 故意把前端 `validate.ts` 的 `MAX_TABLE_COLS` 改成 13 后跑 vitest → **必须红**（改回去）。
- **人日**：1.0
- **依赖**：无
- **关键路径**：是

#### T3.2 — 补齐前端缺失的 `MAX_SPEC_BYTES`

- **归属**：前端
- **文件**：`src/features/genui/validate.ts`
- **动作**：
  - 在 `:24-29` 增 `export const MAX_SPEC_BYTES = 64 * 1024;`
  - 在 `validateGenUISpec` 组装完 `spec` 之后（`:344-349`）加：`JSON.stringify(spec).length > MAX_SPEC_BYTES → { spec: null, problems: [..., 'spec 超过 65536 字节...'] }`，与后端 `genui.py:359-364` 的**清洗后再量**语义一致（后端量的是 `json.dumps(spec)` 长度，前端量 `JSON.stringify(spec).length`，都是字符数近似字节，注释里写清这是近似）。
- **验收**：
  - `npx tsc --noEmit` 退出码 0；
  - `genui.test.tsx` 新增用例：40 个 4000 字节点 → `spec === null`；
  - 既有 14 例不回归。
- **人日**：0.5
- **依赖**：T3.1（先入 JSON 再实现，避免又手抄一次）
- **关键路径**：是

#### T3.3 — 插件启用/禁用退化验收 + `verify_provider_pluginization.py` 扩展

- **归属**：后端（脚本）+ 人工（观察）
- **文件**：`scripts/verify_provider_pluginization.py`；`clipwright/plugins/loader.py:798-802`（参照）
- **动作**：
  1. 新增 `@check("genui_plugin_contract")`（放在 `prompt_*` 系列之后，约 `:257` 前）：
     - 断言 `plugins/genui/plugin.yaml` 的 `contributions` 与 `main.py::GenUIPlugin.contributions()` 逐项对齐（kind/id 集合相等）；
     - 断言 `GenUIPlugin().contributions()[0].meta["agent"] == "requirements"` 且 `meta["prompt"]` 非空；
     - 断言 `main.COMPONENTS` == `GENUI_COMPONENT_TYPES`；
     - 断言 `prompt_overrides` 未登记 `genui` 相关符号（插件不得覆盖内置提示词）。
  2. 新增 `@check("genui_off_degradation")`：monkeypatch `PluginPromptRegistry._prompts` 为空（模拟插件被禁用），断言：
     - `_brief_system_prompt()`（T1.4 抽出来的帮助函数）**仍可用**且不含 GenUI 段落；
     - `ensure_brief_spec({"title":"T"})` 仍产出 spec（**禁用插件不影响确定性兜底**——这是"禁用后业务照常"的关键证据）。
- **验收**：
  - `py -3.14 scripts/verify_provider_pluginization.py -k genui` 退出码 0；
  - `py -3.14 scripts/verify_provider_pluginization.py` 全量退出码 0（**注意**：该脚本要求后端可导入 + FastAPI app 可构建，不含 pytest）。
- **人日**：0.5
- **依赖**：T1.4、T3.1
- **关键路径**：是

---

### Batch 4 · P2：动作语义（0.5 人日必做决策 + 1.5 人日可选实现）

#### T4.1 — 动作语义拍板并落成文档 + 代码注释

- **归属**：两仓
- **文件**：`src/features/genui/GenUISpecRenderer.tsx:68-72`（`ACTION_LABEL`）；`AgentPanel.tsx:735-751`；`plugins/genui/main.py:45`（"不需要 action 就不要写"）；`AGENTS.md`（两仓 #26）
- **动作**（默认按 §7-Q2 的建议执行）：
  - 保留 `select/confirm` 在**白名单**内（向后兼容：老 spec 写它们不会被判非法），但**宿主继续不支持** → 渲染为禁用态；
  - 在 `GenUISpecRenderer.tsx:68-72` 给三个动作各加一行注释，说明"白名单 ≠ 宿主支持"；
  - 在 `AGENTS.md` #26 补一句："动作白名单是**词法**契约，是否可点由宿主的 `supportedActions` 决定；当前只实现 `ask`。"
- **验收**：`npx tsc --noEmit` 0；`npx vitest run src/features/genui` 全绿（既有"宿主只支持 ask 时 confirm 禁用"用例 `genui.test.tsx:111-119` 覆盖了这条语义）。
- **人日**：0.5
- **依赖**：无
- **关键路径**：否

#### T4.2 —（**可选，需 §7-Q2 拍板后才做**）实现 `select`/`confirm`

- **归属**：两仓
- **前置**：用户明确要求实现；否则**跳过**。
- **若要实现 `confirm`**：
  - 语义 = 调用当前卡片的确认回调（`onConfirmBrief` / `onConfirmPlan`），**不是**发送聊天消息；
  - 落点：`AgentPanel.tsx:1968-1970` 给 `GenUISpecRenderer` 传 `supportedActions={['ask','confirm']}`，并在 `onGenUIAction` 里按 `event.context`（需扩展 `GenUIActionEvent` 增加 `scope: 'brief' | 'plan'`）分派到 `confirmBrief` / `confirmPlan`；
  - **必须加**：二次确认（`window.confirm` 或既有确认 UI），因为这是"模型写的按钮推进状态机"——即便实现，也不能单击直达（C10 的底线）。
- **若要实现 `select`**：
  - 需要新的渲染形态：节点 `action.payload.options: string[]` → 渲染成一组可选按钮，选中后把 `{type:'select', payload:{value}}` 回宿主；
  - 语义 = 同 `ask`（发回当前会话），但 prompt 由宿主模板拼装（`"选择：{value}"`），**不让模型定义发送内容**；
  - 需要清洗器支持 `payload.options`（当前 `_clean_payload` 已能保留，但 `MAX_ACTION_PAYLOAD_CHARS=2000` 需验证够用）。
- **验收**（若做）：`npx tsc --noEmit` 0；新增 vitest 用例覆盖"confirm 需二次确认""select 回传 value 且不含模型自定义 prompt"；`py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q` 绿（清洗器未改）。
- **人日**：1.5
- **依赖**：T4.1、§7-Q2
- **关键路径**：否

---

### Batch 5 · P2：表格交互与组件扩充评估（1.5 人日）

#### T5.1 — 表格本地排序/筛选（纯前端状态，不发请求）

- **归属**：前端
- **文件**：`src/features/genui/GenUISpecRenderer.tsx:232-262`（table 分支）；`:435-438`（`useGenUILocalState` 现为未使用占位）
- **动作**：
  - 把 table 分支抽成独立的 `TableNode` 子组件（函数组件 + `useState` 保存 `sortCol/sortDir/filter`）；
  - **只在行数 > 8 时**显示排序/筛选控件（简报/规划书常见 2–6 行，不要给它们加噪声）；
  - 表头点击 → 按列升/降序（字符串按 `localeCompare('zh-CN')`，纯数字串按数值）；
  - 一个输入框按全表任意列包含匹配过滤；
  - **禁止**回调宿主、禁止改 spec、禁止发请求（`AGENTS.md:206` 的"本地优先"）；`useGenUILocalState` 要么用掉、要么删掉（现在注释与实现不符）。
- **验收**：
  - `npx tsc --noEmit` 0；
  - `genui.test.tsx` 新增 3 条：9 行表出现排序控件、点表头后首行变化、过滤后行数减少；6 行表**不出现**控件；
  - 既有"渲染文本与表格"用例（`:93-102`）不回归。
- **人日**：1.0
- **依赖**：无
- **关键路径**：否

#### T5.2 — 组件扩充评估（**产出文档，不写代码**）

- **归属**：两仓（文档）
- **文件**：本节 → 追加到 `docs/genui-plugin-prd-2026-11-03.md` 的新 §10，或本文件 §2.6 扩写
- **动作**：按 §2.6 的 6 条验收条件，对 `chart`（最可能被提的候选）做一次纸面评估：
  - 体积：`echarts` ~330KB gzip（需实测 `npm run build` 前后对比，**不许凭印象**）；
  - 依赖许可：Apache-2.0（需核实具体包）；
  - 安全影响：无 URL 字段则风险低，但 `option` 直通（DSH 的 `echart` 是 option 直通）会把任意配置交给模型 → 必须定义受控子集 schema；
  - 结论建议：**首版不做**，给出"若做则需要的 6 条"。
- **验收**：文档里每个候选都有一句结论 + 理由 + （若做）验收条件；不产生任何代码 diff。
- **人日**：0.5
- **依赖**：无
- **关键路径**：否

---

### Batch 6 · P3：实测回归 / 文档 / 面板处置 / 性能（3.0 人日）

#### T6.1 — `PlanReviewPanel` 处置：不接入、不删除、消歧义

- **归属**：前端
- **文件**：`src/features/plan/PlanReviewPanel.tsx`；`src/layouts/EditorLayout.tsx:17`
- **动作**：
  - **不接入**。理由：它自带 `onConfirm(plan)`（`PlanReviewPanel.tsx:44-47,61`）与"自定义维度缺 reason 禁止确认"（`:53` 注释）等**独立确认语义**，接入即产生第二个确认入口，违反"确认唯一权威"（规划书确认现役入口是 `PlanCard` → `confirmPlan` → `requirementsService`）。
  - 在 `PlanReviewPanel.tsx:1-3` 顶部加注释：「当前**无挂载点**（全仓只有 `EditorLayout.tsx:17` import 了它的 `PlanView` 类型）。规划书确认现役入口是 `AgentPanel` 的 `PlanCard`。若要复活本组件，必须先回答：确认入口是否唯一、GenUI 是否也要挂进来。」
  - 可选（低风险）：把 `PlanView`/`BriefView`/`PlanSectionView`/`PlanCustomSectionView` 四个纯类型迁到 `src/features/plan/types.ts`，`EditorLayout.tsx:17` 改 import 目标，消除"为了一个类型拖着 241 行死组件"的错觉。**不要删组件本体**（属另一项技术债，见 §6）。
- **验收**：`npx tsc --noEmit` 0；`npx vitest run src/features/plan`（若存在）与 `src/features/agent` 全绿。
- **人日**：0.5
- **依赖**：无
- **关键路径**：否

#### T6.2 — 简报 spec 实测回归（3 类选题 + 兜底，**必须人看**）

- **归属**：后端（观察）+ 人工（判定）
- **文件**：无代码改动；产出记录追加到本文件 §4.3 或 `docs/genui-plugin-prd-2026-11-03.md` §9
- **动作**：在真实模型下跑 3 条选题，各记录：brief 走过的是**模型产出**还是**确定性兜底**（看日志 `创意简报的 genui_spec 已降级` / `ensure_brief_spec` 的 problems）、spec 节点数、是否有未知组件降级。
  - 选题 A（知识区长片）：`科技史：从蒸汽机到大模型`；
  - 选题 B（Vlog）：`一个人去北海道看雪的七天`；
  - 选题 C（数码评测）：`三款降噪耳机横评，谁最能隔绝地铁噪音`。
- **验收**（人工可观察）：每类选题的简报卡都出现结构化块（至少 1 个 `table` 或 `list` + 1 个 `text`）；不出现"（暂不支持的组件）"占位；brief 的权威字段与 spec 内容**不矛盾**（如 spec 写的时长与 `duration_estimate` 一致）。
- **人日**：1.0
- **依赖**：Batch 1 全部
- **关键路径**：是（这是"功能真的成立"的唯一直接证据）

#### T6.3 — 文档同步

- **归属**：两仓
- **文件**：
  - `docs/api_reference.md`：加一节「GenUI 字段」——`CreativeBrief.genui` / `ProductionPlan.genui` 的形状、上限、与 `GET /api/requirements/session/{id}` 的关系（只读展示，权威字段在上层）；
  - `docs/frontend-backend-parity.md`：加一行对照「`genui` spec：后端 `sanitize_spec` ↔ 前端 `validateGenUISpec`；契约文件 `genui_contract.json`」；
  - `docs/development.md`（或新建 `docs/genui.md`，**推荐后者**）：组件清单、上限、动作语义、如何加组件（6 条验收条件）、两道校验的分工、禁用插件后的行为；
  - 前端 `docs/` 同步副本（仓库约定：执行计划期间同步 `J:\Clipweight-Client\docs\`）。
- **验收**：四个文档里 `grep genui` 均能命中要点；不出现与 §2 表格矛盾的数值（人工核对一遍）。
- **人日**：1.0
- **依赖**：Batch 1–3（文档写的是最终行为）
- **关键路径**：否

#### T6.4 — 性能与体积（首屏 / 窄屏 / 长 spec）

- **归属**：前端
- **文件**：`src/features/genui/GenUISpecRenderer.tsx`；记录写到 `docs/genui.md`（T6.3 产物）
- **动作**：
  1. 最坏用例：100 节点 + 每个 4000 字（但总量 ≤64KB，所以实际会先被 `MAX_SPEC_BYTES` 丢）→ 构造一个**贴着 64KB** 的合法 spec（如 15 个 4000 字 text），记录首屏渲染耗时（`performance.now()` 包一层，或 React DevTools Profiler）；
  2. 100 行 × 12 列表格：记录渲染耗时与是否卡顿；若 > 100ms，加"只渲染前 50 行 + 底部「还有 N 行」"（不要上虚拟滚动——收益不抵复杂度）；
  3. 窄屏 375px：`brief-genui` 里 12 列表格必须横向滚动不撑破卡片（`:234 overflow-x-auto` 已处理，需实测确认 `th` 的 `whitespace-nowrap`（`:241`）不会把卡片顶宽）；
  4. 深色主题：`badge` 的 `accent` tone 与 `callout` 的 `warning` 在深色下对比度 ≥ 4.5:1（`.md` 主题变量核查或肉眼）。
- **验收**（人工）：三项耗时与两项视觉结论写进文档；若加了"前 50 行"截断，需在 UI 上明示（不能静默丢数据）。
- **人日**：0.5
- **依赖**：T5.1
- **关键路径**：否

---

### 任务汇总

| 批次 | 任务数 | 人日 | 关键路径 |
|---|---:|---:|---|
| Batch 1（P0 产出接线） | 5 | 3.5 | 是 |
| Batch 2（P1 读取/老会话） | 2 | 1.0 | 是 |
| Batch 3（P1 契约同源/插件退化） | 3 | 2.0 | 是 |
| Batch 4（P2 动作） | 1 必做 + 1 可选 | 0.5（+1.5） | 否 |
| Batch 5（P2 表格/扩充评估） | 2 | 1.5 | 否 |
| Batch 6（P3 实测/文档/面板/性能） | 4 | 3.0 | 部分是 |
| **合计** | **17 必做（+1 可选）** | **11.5（+1.5 = 13.0）** | — |

---

## §4 测试与验收矩阵

**本项目约定**：`pytest` 与 `vitest` **默认由人执行**（本计划不运行它们）。下面的"agent 可自证"只列不产生副作用、不写数据、不联网的命令。

### 4.1 agent 可自证（实施 agent 每完成一个任务应自己跑）

| 目的 | 命令（工作目录） | 期望输出 |
|---|---|---|
| 语法/导入 | `py -3.14 -m py_compile clipwright/schema/genui.py clipwright/schema/requirements.py clipwright/services/requirements_service.py`（`J:\Clipwright`） | 退出码 0，无输出 |
| 常量内省 | `py -3.14 -c "from clipwright.prompts import CREATIVE_BRIEF_SYSTEM as S; print('{{BRIEF_TEMPLATE}}' in S, 'genui' in S)"` | `True False`（T1.4 前）→ `True True` 或含占位（T1.4 后，`genui` 由模板函数生成） |
| 契约字段表 | `py -3.14 -c "from clipwright.schema.requirements import brief_prompt_template as t; print('genui' in t())"` | `True` |
| 白名单同源 | `py -3.14 -c "import json; from clipwright.schema.genui import GENUI_COMPONENT_TYPES as C; d=json.load(open('clipwright/schema/genui_contract.json',encoding='utf-8')); print(set(C)==set(d['components']))"` | `True`（T3.1 后） |
| 跨仓契约一致 | `scripts/verify_genui_contract.py`（T3.1 新增，比对两仓 JSON 逐字节） | 退出码 0，打印 `genui_contract: OK (sha=…)` |
| 前端类型 | `npx tsc --noEmit`（`J:\Clipweight-Client`） | 退出码 0 |
| 插件化门禁 | `py -3.14 scripts/verify_provider_pluginization.py -k genui`（`J:\Clipwright`） | 退出码 0，两项 check 全绿 |
| diff 卫生 | `git diff --check`（两仓） | 退出码 0 |

### 4.2 需要人执行（授权后）

| 目的 | 命令 | 期望 |
|---|---|---|
| 后端 GenUI 契约 | `py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q` | 全绿；预计 29 → 40+ 例（T1.1–T1.3、T2.1、T3.1 新增） |
| 后端需求流回归 | `py -3.14 -m pytest tests/clipwright/test_requirements_stream_chat.py tests/test_requirements_plan_translate_retry.py tests/test_requirements_plan_revision_call.py tests/test_requirements_brief_ready_state.py tests/clipwright/test_requirements_session_cache.py tests/clipwright/test_requirements_messages_pagination.py -q` | 全绿。**注意**：`test_requirements_stream_chat.py` 在 2026-11-04 有 **3 个既存失败**（PRD §9 记录，与本改动无关）；实施者要先确认这 3 个是否已被其它提交修掉，未修则只接受"失败数不增加" |
| 插件贡献回归 | `py -3.14 -m pytest tests/clipwright/test_plugin_contribution.py tests/clipwright/test_genui_spec.py -q` | 全绿 |
| 前端 GenUI | `npx vitest run src/features/genui` | 全绿；14 → 25+ 例 |
| 前端宿主 | `npx vitest run src/features/agent src/features/sessionTimeline` | 全绿（2026-11-04 基线 81 passed / 5 skipped，不得回归） |
| 前端全量 | `npm run test` | 基线 1125 passed / 5 skipped（2026-11-01），不得下降 |
| 构建 | `npm run build` | 成功；首屏 gzip 增量 ≤ 5KB（T6.4 记录） |

### 4.3 必须人看（agent 无法自证）

| 项 | 怎么判 | 对应任务 |
|---|---|---|
| 简报卡出现结构化块 | 真实模型跑 3 类选题（§3-T6.2），截图或记录节点类型 | T6.2 |
| 规划书卡出现结构化块 | 确认简报后等规划书，`PlanCard` 顶部出现 `table` + `stat` 行 | T1.3 |
| 模型产出 vs 兜底 | 看后端日志有无 `genui_spec 已降级` 与 `problems`；记录"这次是模型写的还是构造器写的" | T6.2 |
| 老会话 | 打开一个 2026-11-03 之前的会话，简报/规划书卡仍有结构化块（读时补） | T2.1 |
| 插件禁用退化 | 把 `PluginData/plugins/genui/.enabled` 写成 `disabled` 重启，再跑一次简报：**仍有**结构化块（确定性兜底），只是模型不再被告知规范 | T3.3 |
| 坏 spec | 手改 Mongo 里某会话的 `creative_brief.genui` 为 `{"version":99}`，刷新：整块消失、原字段完好、不白屏 | C6/§5 |
| 窄屏 375px | 表格横向滚动，卡片不溢出；`badge` 不被截断成半个字 | T6.4 |
| 深色主题 | `callout`/`badge` 各 tone 可读 | T6.4 |
| 动作不串会话 | 点 `ask` 按钮，消息只进**当前**需求会话（看 URL 里的 session id 不变） | T4.1 |
| DOM 里没有模型 HTML | DevTools Elements 搜 `script`/`iframe`/`onerror` 零命中；Network 无模型控制的外链请求 | C5 |

---

## §5 风险与回滚

### 5.1 风险清单

| # | 风险 | 触发条件 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | **`_brief_hash` 漂移导致规划书被误作废** | 先补 genui 再改 hash（或顺序颠倒） | 用户每次确认都得到"简报变化过，规划书作废" | **T1.1 必须排在 T1.2/T1.3 之前**；`_brief_hash` 用排除 `genui` 的实现，并加断言用例 |
| R2 | `get_session` 读时补 spec **原地改了内存会话** | T2.1 直接 `session["creative_brief"]["genui"]=...` | 下一轮 `_persist` 把 spec 写进 Mongo，"只读补"变成"隐式写" | T2.1 明确要求返回浅拷贝；用例断言 mock 模型对象未被改 |
| R3 | 恢复 `{{BRIEF_TEMPLATE}}` 时文案漂移 | T1.4 手工删字段表 | 简报字段表/骨架与模型不一致（正是 A-01 修过的坑） | 改动前后对 `brief_prompt_template()` 做**值快照对比**，必须逐字相同；`git diff` 只允许 `prompts.py:40` 一处变化 |
| R4 | 插件 PROMPT 注入到**规划书翻译**提示词 | `_translate_plan` 若也加 `get_for_agent("requirements")` | 模型在规划书步骤被要求写 `genui`，但结果被丢弃 → 浪费 token + 可能干扰 `markdown_content` 生成 | T1.3 明确**只在 `_handle_gathering`（简报）注入**；`_translate_plan` 不注入（其输出由 `ensure_plan_spec` 确定性生成） |
| R5 | 双端规则漂移 | 只改一侧上限/白名单 | 半渲染：后端保留的节点前端降级成占位，或反之 | T3.1 的 JSON 单一真相 + 双端断言 + 跨仓脚本；改规则必须三处同步 |
| R6 | 旧 spec 版本 | 将来升 `version: 2` | 老数据整块丢弃 | 现行策略已是"不认的版本整块丢弃 + 回落原卡片"（`genui.py:333-334`）；升版时必须前后端同批发布，不允许后端先升 |
| R7 | 脏 spec（手改/代理篡改） | 用户或中间层改响应 | 渲染异常 | 双道校验 + 逐节点 `NodeBoundary`（`GenUISpecRenderer.tsx:75-92`）；`get_session` 读时也补一遍（T2.1）等于第三道 |
| R8 | 动作错绑会话 | `onGenUIAction` 用了全局 chat 而非当前会话 | 追问落到别的会话 | 保持 `AgentPanel.tsx:744` 现写法（`useAgentStore.getState().requirementsSessionId`）；`select` 若实现，prompt 必须由宿主模板拼、不许模型给 |
| R9 | 100 行表 / 64KB spec 卡顿 | 长规划书 | 首屏掉帧 | T6.4 量化；超标就截断显示并明示 |
| R10 | 插件目录 CWD 依赖 | 从非 `J:\Clipwright` 启动服务 | `PluginLoader(plugin_dir=Path("plugins"))`（`main.py:242-246`）找不到插件 → 规范不注入 | 属既有问题（不是本计划引入）；**验收时必须在 `J:\Clipwright` 下启动**，并在 T6.3 文档里写明；若发现实际踩到，另立任务改成 settings 驱动 |
| R11 | `resolveMessageAttachments` 丢掉简报 | 同轮返回 plan | 消息级简报卡不渲染（时间线仍渲染） | 不改行为（既有产品裁定），只写进 §4.3 的验收口径，避免误判为 GenUI bug |

### 5.2 回滚（按提交粒度，不用运行时 feature flag）

严格按"新增能力的依赖顺序"回滚：

1. **先回滚前端宿主接线**（`AgentPanel.tsx` 的 `brief-genui` / `plan-genui` 两块 + `onGenUIAction`）→ 卡片回到纯字段视图，后端继续下发 spec 也不渲染；
2. **再回滚后端产出**（`ensure_brief_spec` / `ensure_plan_spec` 的调用点）→ 不写 `genui`，前端 `hasGenUI` 为 false，静默不渲染；
3. **最后回滚契约/清洗器**（`schema/genui.py`、`schema/requirements.py` 的字段）→ 此时必须确认 Mongo 里已有的 `genui` 字段**不会被 Pydantic 拒绝**（`CreativeBrief` 移字段后多余键会被忽略，`extra` 不 forbid，安全）；
4. 插件目录（`plugins/genui/`）可**任何时刻**独立删除：它是纯提示词贡献，删除后简报/规划书照常（`plugin.yaml:1-13`、`main.py:64-75`）。

**禁止**：
- 用环境变量做 GenUI 开关（PRD §7 已裁定）；
- 回滚时顺手改 `MAX_*` 或白名单（会让新旧数据两套语义）。

**已存数据兼容**：`genui` 是可选字段且清洗器"洗坏返回 `None` → 字段置 `{}`"，所以**保留宽容读、停止写入**是最安全的中间态；不需要数据迁移脚本。

---

## §6 明确不做（scope out）

1. **不新增** `ContributionKind` / slot / task event / HTTP 路由（PRD §1 结论不变；`schema/task.py:35-72` 的 31 类事件不动）。
2. **不动** `clipwright/services/requirements_service.py:396` 那份第三提示词与 `CreativeBrief` 字段集的既存漂移（它列了 `production_plan`/`asset_ratio` 等非模型字段）。这是不变量 #23 的既有欠账，牵动面大，**另立 PRD**；本计划只要求"genui 能进提示词"。
3. **不重写** `CreativePromptField`（首页创作输入），不把静态表单换成模型生成 UI。
4. **不接入** `PlanReviewPanel`（见 T6.1），也**不删除**它（删除属技术债清理，需单独评估谁在用 `PlanView`）。
5. **不打开** LLM 生成规划书 spec 的路径（规划书只用 `build_plan_genui`，见 §7-Q1）。
6. **不支持** 任意 HTML/CSS/脚本、远端组件、外链图片/iframe、`file` 上传动作、spec 自带 endpoint。
7. **不引入** `mermaid` / `echart` / `plot` / `scene3d` / `svg` / `chart` / `image` / `tabs` / `keyvalue`（§2.6）。
8. **不做** 表格导出、跨会话交互状态、spec 持久化到工作区文件、GenUI 深链。
9. **不改** `PluginLayoutRenderer` 与 `UILayout` 那套静态插件表单（两套 schema 不能混，`AGENTS.md:206`）。
10. **本次不改任何代码、不跑测试、不 commit。**

---

## §7 需要产品/用户拍板的问题

> 每条都给了**默认建议**。不回复即按默认执行。

**Q1｜规划书要不要开放 LLM 生成 spec？**
- 现状：`translate_scenes`（死代码）里模型给了会洗一遍再用，与 PRD §9「只走确定性」矛盾。
- 默认建议：**不开放**。规划书是用户确认的对象，展示层若能被模型编造，用户可能"看着漂亮的卡片确认了一份 markdown 里没有的方案"。只保留 `build_plan_genui` 从 `raw_scenes` 确定性生成。同时删掉模型分支（T1.3-4）。

**Q2｜`select` / `confirm` 现在实现吗？**
- 默认建议：**都不实现**（T4.1 只做文档化）。`confirm` 会让"模型写的按钮推进状态机"，越过 C10；`select` 没有选项组视觉形态，现在实现等于给模型一个用户看不懂的控件。若将来要做 `confirm`，**必须带二次确认**，且只用于"回到用户已经看过的那份 brief/plan"。

**Q3｜老会话（2026-11-03 之前）读时补 spec，还是显示旧卡片？**
- 默认建议：**读时补**（T2.1）。理由：字段足够推导、纯函数零成本、避免"同一功能新老会话表现不同"。补出来的 spec 与当时模型可能产出的不同，但它只是展示层的另一种排版。

**Q4｜简报 spec 该不该在"出了规划书之后"继续显示？**
- 现状：`requirementsAttachments.ts:22` 在同轮有 plan 时把 `creative_brief` 置 null（消息级）；时间线的 `report` 条目仍显示简报卡。
- 默认建议：**保持现状**（不改产品行为），只在验收口径里写清"看时间线，不要看消息气泡"。

**Q5｜右侧副驾驶是否需要独立的 GenUI 面板 / 持久 dock？**
- 默认建议：**不需要**。`CopilotDock.tsx:42` 的 `RequirementsPane` 已覆盖三个目标面（简报卡、规划书卡都在它的消息/时间线里）；再加 dock 等于同一份 spec 渲染两遍。PRD §4.4 的"首版不新增 slot"继续有效。

**Q6｜`PlanReviewPanel` 接入还是删掉？**
- 默认建议：**不接入、不删除**（T6.1：加注释 + 可选类型迁出）。接入会产生第二个确认入口；删除牵动 `EditorLayout.tsx:17` 的类型引用与未知的其它消费者，收益低。

**Q7｜组件扩充的优先级**（若确实要扩，先扩哪个）
- 默认建议：**一个都不扩**。若必须扩，`chart` 是唯一有实际诉求的（规划书的时长/段数分布），但它要过 §2.6 的 6 条；体积实测前不要立项。

**Q8｜插件被禁用时的期望行为**
- 默认建议：**只失去"模型知道怎么写 spec"，不失去"卡片有 spec"**。确定性构造器不依赖插件（`ensure_brief_spec` / `ensure_plan_spec` 都在 `schema/genui.py`，不是插件代码）。T3.3 的 `genui_off_degradation` 就是这个断言的证据。若产品希望"禁用即完全无 GenUI"，那要改成前端也受控——**不推荐**（会让展示能力依赖插件在线）。

**Q9｜要不要把 `genui` 从 `CreativeBrief` 挪到独立的 `display` 容器？**
- 现状：`genui` 与权威字段同级（`requirements.py:75-77`）。
- 默认建议：**不挪**。挪会破坏老数据、且前端 `brief.genui` 读取点要全改。保留同级 + `_brief_hash` 排除（T1.1）已足够表达"它是附属展示"。

---

## 附录 A：本计划引用的文件清单（按仓）

**后端 `J:\Clipwright`**
`clipwright/schema/genui.py`（461 行）· `clipwright/schema/requirements.py`（162 行）· `clipwright/agents/requirements_agent.py`（297 行）· `clipwright/services/requirements_service.py`（3811 行）· `clipwright/services/stage_subagent.py` · `clipwright/prompts.py` · `clipwright/plugins/prompt_registry.py` · `clipwright/plugins/prompt_overrides.py`（`resolve_constant` `:432`）· `clipwright/plugins/contribution_dispatch.py`（prompt 分支 `:174-180`）· `clipwright/plugins/loader.py`（`:171`、`:798-802`）· `clipwright/api/requirements.py`（`:440-449`）· `clipwright/api/capability.py` · `clipwright/schema/task.py`（`:35-72`，31 类事件）· `clipwright/main.py`（`:242-246`）· `plugins/genui/{plugin.yaml,main.py,__init__.py}` · `tests/clipwright/test_genui_spec.py`（292 行，29 例）· `scripts/verify_provider_pluginization.py`（13 项 check）· `AGENTS.md:130`（#26）

**前端 `J:\Clipweight-Client`**
`src/features/genui/types.ts`（145 行）· `src/features/genui/validate.ts`（357 行）· `src/features/genui/GenUISpecRenderer.tsx`（438 行）· `src/features/genui/genui.test.tsx`（154 行，14 例）· `src/features/agent/AgentPanel.tsx`（2145 行；`:36-38`、`:155-210`、`:601-751`、`:1119-1191`、`:1959-1975`、`:2047-2062`）· `src/features/agent/requirementsAttachments.ts`（`:15-25`）· `src/types/persona.ts`（`:313`、`:326`）· `src/pages/EditorPage.tsx`（`:262-320`）· `src/features/copilot/CopilotDock.tsx`（`:42`）· `src/features/plan/PlanReviewPanel.tsx`（241 行）· `src/layouts/EditorLayout.tsx`（`:17`）· `AGENTS.md:206`（#26）

## 附录 B：本计划"未核实"的项与核实办法

| 未核实项 | 为什么没核实 | 怎么核实 |
|---|---|---|
| `plugins/genui` 在运行期是否真的被加载、PROMPT 是否登记 | 需要跑 `PluginLoader`，会写 `PluginData/`（本任务禁止副作用） | 启动服务后 `GET /api/plugin/plugins` 看是否有 `genui`；或看启动日志 `Loaded N third-party plugins` 是否含 `genui` |
| `test_requirements_stream_chat.py` 的 3 个既存失败是否还在 | 未运行 pytest（本项目约定由人跑） | `py -3.14 -m pytest tests/clipwright/test_requirements_stream_chat.py -q`，看失败数与 PRD §9 的 3 个是否一致 |
| 深色主题下各 tone 的实际对比度 | 需要浏览器渲染 | §4.3 的人工验收 |
| `echarts` 等候选依赖的真实 gzip 体积 | 未安装/未构建 | T5.2 要求实测后再写进文档，不许凭印象 |
| 真实模型是否会用插件 PROMPT 产出合法 spec、质量如何 | 需要真实 LLM 调用 | T6.2 的 3 类选题实测 |

## 附录 C：评审补记（2026-11-07，实施 agent 复核 coding plan 时验证）

写这份计划的规划专家把几项标成了"未核实"（附录 B）。评审时我已就地核实了三项，结论如下——**不改变计划的任务与估算**，只是把"未核实"变"已核实"：

1. **`plugins/genui` 确实被加载、PROMPT 确实登记**（附录 B 第 1 行）：启动 `TestClient(app)` 后
   `PluginPromptRegistry.get_entries_for_agent("requirements")` 返回 `[("genui", "requirements")]`；
   启动日志的 `Loaded 43 third-party plugins` 列表里含 `genui`。**但** 0.3-② 的结论不受影响：
   **登记了没有消费方**——生产简报路径（`requirements_service.py:2254`）不查这个注册表。
2. **`_STAGE_DEPENDENCIES` 确认只有 6 个阶段**（0.3-③ 的依据）：运行时打印为
   `['animation','audio','edit','material','quality','structure']`，**无 `requirements`** →
   `RequirementsAgent` 在生产不可达，`_generate_brief` / `_ensure_brief_genui` / `translate_scenes` /
   `_plan_genui` 均为死代码。
3. **`_brief_hash` 确认对整份 brief 取 sha256**（0.3-⑧）：`requirements_service.py:101-110` 是
   `json.dumps(brief, sort_keys=True, default=str)` 后取前 16 位，**没有任何字段排除**。
   → T1.1「先把 `genui` 从指纹里排除」必须是 Batch 1 的第一件事，否则接线一上线就会出现
   "定位不到规划书 / 简报变更误作废"。

另补充一条计划未列的既有事实（不影响任务拆分，但影响 T6.2 的实测口径）：**2026-11-03 的纵切落地时，
`tests/clipwright/test_genui_spec.py` 29 例与前端 `src/features/genui/genui.test.tsx` 14 例全绿**——
它们测的是契约与渲染器本身，**测不到"生产上有没有 spec"**（正是 0.3-① 说的"绿测试盖着死代码"）。
所以 T6.2 的实测必须走真实 LLM + 真实简报路径，不能用这两组单测代替。

---

## 实施进度（滚动更新）

> 只记"哪条任务真的落地了 + 证据"，细节仍看 §3。每条都有 code 证据与自检命令。

| 任务 | 状态 | 落地内容 | 证据（自检） |
|---|---|---|---|
| **T1.1** `_brief_hash` 排除 `genui` | ✅ 已完成 | `requirements_service.py:_brief_hash` 序列化前 `{k: v for k, v in brief.items() if k != "genui"}`；只剩展示层时返回 `""` | `tests/clipwright/test_genui_spec.py::TestBriefHash`（4 例） |
| **T1.2** 简报产出接线 | ✅ 已完成 | 新增 `schema/genui.py::ensure_brief_spec`（服务层与 Agent 共用）；接到 `_handle_gathering` 主路径（`brief_draft` 落库前）与修订路径；Agent 的 `_ensure_brief_genui` 改为转发 | `TestEnsureSpecWiring`（4 例）+ 生产冒烟（见下） |
| **T1.3** 规划书产出接线 | ✅ 已完成 | 新增 `ensure_plan_spec`（**只**用确定性构造器，不看模型 genui）；`_translate_plan` 在 `return` 前统一补 → 内联与派发回填两条路径一次覆盖；legacy 路径改用同一函数；删掉 Agent 里"模型给了就用"的分支 | `TestPlanTranslateWiring`（3 例）+ `TestEnsureSpecWiring::test_ensure_plan_spec_ignores_model_spec` |
| **T1.4** 提示词真的含 `genui` | ✅ 已完成 | `prompts.py::CREATIVE_BRIEF_SYSTEM` 恢复 `{{BRIEF_TEMPLATE}}` 占位（改动前后逐字比对：只有字段表那一段换成模板，其余零差异）；`requirements_agent` 改为运行时展开（`_brief_system_prompt()`）；**生产简报路径**新增 `requirements_service._brief_system_prompt()`，追加 `PluginPromptRegistry.get_for_agent("requirements")` | `TestPromptPlaceholderAndInjection`（4 例）+ 生产冒烟：`_brief_system_prompt('')` 长度 2452、含 `GenUI 展示规范` |
| **T1.5** 死路径处置（选项 A） | ✅ 已完成 | `requirements_agent.py` 模块 docstring 顶部写明「本 Agent 当前无生产调用点（`stage_subagent` 无 `requirements`），勿在其上新增生产逻辑」；genui 两个方法改为转发公共函数 | `grep translate_scenes` 仅剩定义；`py_compile` 0 |

**Batch 1 收尾自检（本机实跑）**

```powershell
py -3.14 -m py_compile clipwright/prompts.py clipwright/schema/genui.py clipwright/schema/requirements.py `
    clipwright/agents/requirements_agent.py clipwright/services/requirements_service.py   # 0
py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q        # 45 passed
py -3.14 -m pytest <tests/test_requirements_plan_*.py + tests/clipwright/test_requirements_*.py> -q
# 100 passed / 3 failed —— 3 个失败是既存 test_requirements_stream_chat（与本批无关）
py -3.14 -c "import clipwright.main; print('ok')"                # ok
```

**仍未解决（留给 Batch 2+）**：老会话读时补 spec（T2.1）；前后端契约同源与插件启停退化验收（Batch 3）；
`select`/`confirm` 语义（Batch 4）；表格本地排序/筛选（Batch 5）；实测回归与文档（Batch 6）；
以及本批刻意**没动**的既有违规——`requirements_service.py:396` 的第三份 `CREATIVE_BRIEF_SYSTEM`
（字段集比 `CreativeBrief` 宽，前端依赖，合并属独立清理）。

### Batch 2 进度

| 任务 | 状态 | 落地内容 | 证据 |
|---|---|---|---|
| **T2.1** 老会话读时补 spec | ✅ 已完成 | 新增 `requirements_service._with_genui_specs(session)`：**浅拷贝 + 只补不存**（绝不原地改 `_memory_sessions` / 不回写 Mongo）；`get_session` 两条返回路径（内存 / Mongo）都过它；简报走 `ensure_brief_spec`，规划书走 `ensure_plan_spec`（只看 `raw_scenes`/`scenes`） | `TestOldSessionBackfill`（4 例）：入参 deepcopy 相等、身份不同、模型 genui 被忽略、空字段安全 |
| **T2.2** 前端"无 spec 整块不渲染" | 🟡 已写未跑 | `genui.test.tsx` 增 `it.each` 三形态（`undefined` / `{}` / `{version:1,items:[]}`）：`hasGenUI=false` 且渲染器返回 `null` | `npx tsc --noEmit` 0；**vitest 未跑**（本 goal 约定：未经授权不跑 vitest） |

### Batch 3 进度

| 任务 | 状态 | 落地内容 | 证据 |
|---|---|---|---|
| **T3.1** 契约单一真相 | ✅ 已完成 | 新增后端 `clipwright/schema/genui_contract.json`（权威）+ 前端 `src/features/genui/contract.json`（**逐字节副本**）；`schema/genui.py::load_contract()` 供测试/脚本读取（运行期不依赖 I/O）；后端 `TestContractSingleSource` 逐项断言常量 == JSON；前端 `ContractSingleSource` 同样断言（含 7 个上限） | `pytest tests/clipwright/test_genui_spec.py` → 53 passed；`tsc --noEmit` 0 |
| **T3.2** 前端补 `MAX_SPEC_BYTES` | ✅ 已完成 | `validate.ts` 增 `MAX_SPEC_BYTES = 64*1024`，并在**清洗后**量 `JSON.stringify(spec).length`（与后端 `len(json.dumps(spec))` 同口径，注释写清是字符数近似）；超限 → `spec=null` | 前端 `MAX_SPEC_BYTES` 2 例（40×4000 字 → null；未超限通过） |
| **T3.3** 插件启停退化 + 自检扩展 | ✅ 已完成 | `verify_provider_pluginization.py` 新增 3 项：`genui_contract_sync`（两仓 JSON 逐字节相等）、`genui_plugin_contract`（manifest ↔ contributions 对齐、组件与 schema 一致、不覆盖提示词符号）、`genui_off_degradation`（清空提示词注册表后：base prompt 仍在且无 GenUI 段、`ensure_brief_spec` 仍产出 spec） | `py -3.14 scripts/verify_provider_pluginization.py` → **16/16 通过**；`-k genui` → 3/3 |

### Batch 4 / 5.2 / 6.1 / 6.3 / 6.4 进度

| 任务 | 状态 | 落地内容 | 证据 |
|---|---|---|---|
| **T4.1** 动作语义文档化 | ✅ 已完成 | 渲染器 `ACTION_LABEL` 上方写明"白名单 ≠ 宿主支持；当前只实现 ask；confirm 必须二次确认"；`AgentPanel.onGenUIAction` 同步；插件 PROMPT 增加"加一个点不动的按钮比不加更差"；两仓 `AGENTS.md` #26 各加一条 | `tsc --noEmit` 0；PROMPT `词法白名单` 命中 |
| **T4.2** 实现 select/confirm | ⏭️ 跳过（默认建议：不做） | 需产品拍板（§7-Q2）；`confirm` 若做必须带二次确认 | — |
| **T5.1** 表格本地排序/筛选 | ⏸ 未做 | 需前端交互代码 + vitest 验证（本 goal 未授权 vitest）；保留在缺口表 | 记入 `docs/genui.md` §6 |
| **T5.2** 组件扩充评估 | ✅ 已完成（文档） | `docs/genui.md` §5.1：keyvalue/image/tabs 不扩理由；chart/echart 首版不扩（体积须实测、option 直通需受控子集 schema）；mermaid/svg/scene3d/plot 已排除 | 无代码 diff（符合任务要求） |
| **T6.1** `PlanReviewPanel` 处置 | ✅ 已完成（不接入不删除） | 顶部加注释：**无挂载点**、规划书确认现役入口是 `PlanCard`、接入会产生第二个确认入口、复活前须回答两个问题 | `tsc --noEmit` 0 |
| **T6.2** 真实模型 3 类选题实测 | ⏳ 待人工 | 需真实 LLM + 浏览器观察（日志看"已降级/兜底"） | 见 `docs/genui.md` §6 |
| **T6.3** 文档同步 | ✅ 已完成 | 新增 `docs/genui.md`（契约/上限/动作/产出路径/加组件 6 条/缺口/自检）；`api_reference.md` 增「GenUI 展示字段」；`frontend-backend-parity.md` 增 §12；两仓 AGENTS #26 同步；客户端 docs 镜像 | `grep genui` 四份文档均命中 |
| **T6.4** 性能/窄屏/深色 | ⏳ 待人工 | 需浏览器实测（贴 64KB spec、100 行表格、375px、深色对比度） | 见 `docs/genui.md` §6 |

| **T5.1** 表格本地排序/筛选 | ✅ 已完成（测试未跑） | `GenUISpecRenderer` 抽出 `GenUITableNode`：≥8 行显示筛选框与可点表头；纯函数 `sortTableRows`（数字列按数值）/`filterTableRows`（跨列、大小写不敏感）导出供测试；删掉占位导出 `useGenUILocalState`（注释说明原因） | `tsc --noEmit` 0；vitest 4 例**已写未跑** |

### 全量 pytest 实跑（2026-11-07，Batch 1–6 合并后）

```powershell
cd J:\Clipwright; py -3.14 -m pytest tests -q -rf
# 30 failed, 5017 passed, 7 skipped, 1 xfailed in 818.19s
```

**30 个里有 1 个是本轮引入的真回归，已修**：

| 失败 | 根因 | 处理 |
|---|---|---|
| `test_plan_first.py::TestFieldDrift::test_prompt_contains_every_field` | T1.4 把 `prompts.py` 的简报常量改成 `{{BRIEF_TEMPLATE}}` 占位后，该用例仍断言"字段名出现在**常量原文**里" | 改为断言**渲染后**的提示词（`requirements_agent._brief_system_prompt()`）——测的仍是"实际发给模型的提示词不缺字段"；该文件复跑 **22 passed** |

**其余 29 个与既有基线逐项对齐**（`docs/provider-pluginization-verification-ledger-2026-11-03.md`「pytest 实跑」§2 的分布表）：
降级/最终收据 6（receipts 3 + to_master 1 + final_receipt 2）、golden 4、需求侧 6
（stream_chat 3 + brief_ready 1 + legacy_plan 2）、plugin_review_fixes 2、零散 11。

> 口径：**「pytest 全绿」仍不成立**（29 个既存失败）；**「本改造新增失败 = 0」成立**（唯一新增已修）。
> 修后按构造应为 **29 failed / 5018 passed**。

### P4.2 / 审阅覆盖层（2026-11-07 用户裁定：「select/confirm 要实现，confirm 必须带二次确认，审阅覆盖接 GENUI」）

| 项 | 落地 | 证据 |
|---|---|---|
| `select` 后端校验 | `_clean_select_options`：只收非空字符串、去重保序、**纯截断**（40 字，不加"已截断"标记）、总数封顶 6；无可用选项 → 整个动作丢弃（节点保留） | `TestSelectAction` 4 例 |
| `select` 前端 | `validate.ts` 同规则；渲染器产出**选项按钮组**，点击回传 `{value}` | vitest `select 渲染选项组…`（genui 30 passed） |
| `confirm` | 宿主按事件 `scope`（`brief`/`plan`）路由到 `confirmBrief`/`confirmPlan`，**先 `window.confirm` 二次确认**，`busy` 不响应，`scope` 缺失不猜 | `confirm 可点并把 scope 一起回传` |
| 契约 | `MAX_SELECT_OPTIONS=6`、`MAX_OPTION_CHARS=40` 进 `genui_contract.json` 两仓副本 + 双端断言 | `ContractSingleSource` + 自检 `genui_contract_sync` |
| 审阅覆盖层 | `ReviewPanel` 渲染 `brief.genui` 与 `planGenui`（`EditorLayout` 传 `productionPlan.genui`），新增 `hideActions` **不渲染任何动作** | vitest `hideActions…` |
