# GenUI 能力插件化迁移 PRD

> 日期：2026-11-07  
> 角色：规划专家（本文只做设计与裁定，不含实现）  
> 目标仓：`J:\Clipwright`（engine，Python 3.14 + FastAPI）、`J:\Clipweight-Client`（前端宿主，React 19 + TS）  
> 配套：[coding plan](genui-plugin-migration-coding-plan-2026-11-07.md)  
> 前序文档：[`docs/genui-plugin-prd-2026-11-03.md`](genui-plugin-prd-2026-11-03.md)、[`docs/genui-plugin-coding-plan-2026-11-07.md`](genui-plugin-coding-plan-2026-11-07.md)、[`docs/genui.md`](genui.md)、[`docs/pluginization-plan-2026-09-10.md`](pluginization-plan-2026-09-10.md)

---

## 0. 一句话结论

**GenUI 的"知识"（组件白名单、上限、动作词表、卡片怎么拼、教模型怎么写的规范）全部搬进插件 `plugins/genui/`，engine 只保留一个纯数据的通用扩展点 `display_spec` + 一个契约驱动的通用清洗器 + 一个通用装配器；engine 不再出现任何 `genui` 字面量（只留老数据键名做兼容），也不执行插件提供的代码。**

代价必须有人承担，本文不回避：**接受"禁用/缺失插件 = 没有 GenUI 卡片"**（放弃 2026-11-03 Q8 的"禁用不失卡片结构"口径），换来 engine 侧零专属链路。字段、状态机、指纹、确认流程不受任何影响。

---

## 1. 需求裁定与可判定定义

### 1.1 用户原话与三种解读

> 「所以 genui 应该是插件实现啊，你为什么又要动 engine？engine 只需要提供一个插件接口即可，主要功能都要由插件实现。」

这句话有三种可能的强度，必须选一种再谈"可行不可行"：

| 解读 | 含义 | 是否本 PRD 采用 | 说明 |
|---|---|---|---|
| **强读**：插件实现"一切"，含清洗器、渲染器 | engine 连清洗器都要调插件 | ❌ 不采用 | 会把"信任边界"交给被约束方（§3.3 反例），且渲染器是前端编译期产物，插件根本交付不了（§4-①） |
| **中读（采用）**：能力主体在插件；engine 只给接口与不可外包的安全机制 | 白名单/上限/动作/卡片配方/提示词在插件；清洗与装配是**通用机制**（不认识 genui） | ✅ **采用** | 满足"engine 里没有 genui 专属链路"，同时保住 #26 的两道闸 |
| **弱读**：把提示词留在插件就够了 | 现状 | ❌ 不采用 | 现状就是弱读：`clipwright/schema/genui.py` 549 行仍是 engine 专属实现，用户反对的正是它 |

### 1.2 「engine 里已经没有 genui 专属链路」的可执行定义

不是靠感觉，靠四条可执行判据（判据的完整命令与期望输出见 §8）：

- **D1 无专属实现文件**：`clipwright/schema/genui.py`、`clipwright/schema/genui_contract.json` 不再存在。
- **D2 无专属导入**：`clipwright/` 下不存在 `from clipwright.schema.genui import ...` 或 `import ...genui`。
- **D3 字面量收口**：`clipwright/` 下 `genui` 的命中只剩**老数据键名兼容**（≤5 处字符串字面量 + 注释），且都不在业务分支里。
- **D4 无专属常量**：engine 里不存在 `GENUI_*` / `MAX_SELECT_OPTIONS` 这类硬编码的组件名单/上限；这些值运行时来自 `ContributionRegistry` 里 `display_spec` 贡献的 `meta`。

---

## 2. 现状证据（全部为已读到的代码，行号可直接核对）

### 2.1 engine 里的 genui 专属物

| 物件 | 位置 | 规模/性质 |
|---|---|---|
| 版本、13 类白名单、容器集合、动作词表 | `clipwright/schema/genui.py:26`、`:29-32`、`:35`、`:38` | 纯常量 |
| 色调/字号枚举 | `clipwright/schema/genui.py:41-43`（`GENUI_TONES` / `GENUI_BADGE_TONES` / `GENUI_TEXT_SIZES`） | 纯常量 |
| 9 个资源上限 + 危险键 | `clipwright/schema/genui.py:46-59` | 纯常量 |
| 清洗器 | `clipwright/schema/genui.py:345-397`（`sanitize_spec`），逐组件分支 `:225-342`，辅助 `:98-222` | 逻辑，安全关键 |
| 确定性构造器 | `clipwright/schema/genui.py:403-437`（`build_brief_genui`）、`:440-480`（`build_plan_genui`） | 逻辑，纯函数 |
| 契约文件 | `clipwright/schema/genui_contract.json`（33 行：version/components/containers/actions/tones/text_sizes/limits） | 数据 |
| 契约读取 | `clipwright/schema/genui.py:483-493`（`load_contract`） | I/O |
| 装配函数 | `clipwright/schema/genui.py:511-523`（`ensure_brief_spec`）、`:526-536`（`ensure_plan_spec`） | 逻辑 |
| 调试摘要 | `clipwright/schema/genui.py:539-549`（`spec_debug_summary`） | 逻辑 |
| Schema 字段 + 校验器 | `clipwright/schema/requirements.py:20`（import）、`:73-83`（brief）、`:101-107`（plan） | 字段 + validator |
| 生产接线（简报主路径） | `clipwright/services/requirements_service.py:1180-1188` | 调用点 |
| 生产接线（简报修订路径） | `clipwright/services/requirements_service.py:1288-1293` | 调用点 |
| 生产接线（legacy 规划书） | `clipwright/services/requirements_service.py:2444-2467` | 调用点 |
| 生产接线（规划书主路径） | `clipwright/services/requirements_service.py:2790-2796`（`_translate_plan`） | 调用点 |
| 读路径补 spec | `clipwright/services/requirements_service.py:3841-3867`（`_with_genui_specs`），调用点 `:767-785` | 逻辑 |
| 简报指纹排除 | `clipwright/services/requirements_service.py:101-117`（`payload = {k: v for k, v in brief.items() if k != "genui"}` 在 `:110`） | 逻辑，C10 |
| 提示词拼装 | `clipwright/services/requirements_service.py:3870-3894`（`_brief_system_prompt` → `PluginPromptRegistry.get_for_agent("requirements")`） | 逻辑 |
| Agent 转发（生产不可达） | `clipwright/agents/requirements_agent.py:160`、`:164-173`、`:245`、`:258`、`:262-270` | 转发 |

统计（已执行）：`clipwright/` 目录（`.py`/`.json`）中 `genui` 命中 **86 处**，分布为 `schema/genui.py` 42、`services/requirements_service.py` 20、`agents/requirements_agent.py` 13、`schema/requirements.py` 11。这四份文件就是"专属链路"的全部。

### 2.2 插件侧现状（唯一产物）

| 物件 | 位置 | 现状 |
|---|---|---|
| manifest | `plugins/genui/plugin.yaml:1-13`（`api_version: "2"`、`permissions: []`、`override_builtin: false`、单条 `prompt` 声明 `:12-13`） | 只声明一条 PROMPT |
| 贡献 | `plugins/genui/main.py:70-81`（`ContributionKind.PROMPT`，`meta={agent, prompt, priority:6}`） | 只教模型"能写什么" |
| 组件元组 | `plugins/genui/main.py:27-30`（`COMPONENTS`） | 仅用于与 engine 常量对账 |
| 规范正文 | `plugins/genui/main.py:34-67`（1312 字） | 唯一真正的"能力知识"，已经在插件里 |

**关键事实（正面）**：生产简报提示词 `requirements_service.py:404-451` 的 `CREATIVE_BRIEF_SYSTEM` 本地常量**不含 `genui` 字样**，`brief_prompt_template()`（`schema/requirements.py:131-140`）也不在生产路径上（`_brief_system_prompt` 用的是上面那份本地常量）。也就是说：**模型之所以会写 `genui`，唯一来源就是插件那条 PROMPT**。这条事实意味着"插件已经是知识的唯一来源"，迁移只是把 engine 里那套实现也搬过去，不涉及"模型行为可能变化"的大风险。

### 2.3 前端侧现状

| 物件 | 位置 | 现状 |
|---|---|---|
| 类型契约 | `src/features/genui/types.ts:14-30`（版本/13 类/动作/色调/字号）、`:117-150`（节点 union） | 硬编码常量 |
| 运行时校验 | `src/features/genui/validate.ts:24-42`（上限）、`:44-48`（集合）、`:358-397`（`validateGenUISpec`）、`:390`（64KB）、`:400-402`（`hasGenUI`） | 硬编码常量 |
| 渲染器 | `src/features/genui/GenUISpecRenderer.tsx:521-532`（入口）、`:22-37`（props 含 `supportedActions`/`scope`/`hideActions`）、表格本地排序筛选 `:215-280` | 13 类原语实现 |
| 契约副本 | `src/features/genui/contract.json` | **只被测试用**（`genui.test.tsx:169-201`），运行期不读 |
| 简报挂载 | `src/features/agent/AgentPanel.tsx:1991-2011`（`data-testid=brief-genui` 在 `:2002`；props 传 `genui`） | 传 `m.creative_brief.genui`（`:187`） |
| 规划书挂载 | `src/features/agent/AgentPanel.tsx:2086-2105`（`data-testid=plan-genui` 在 `:2097`） | 传 `m.production_plan.genui`（`:196`） |
| 动作回落 | `src/features/agent/AgentPanel.tsx:746` 起（`ask`/`select`/`confirm` 三条固定通道） | 已实现 |
| 审阅只读 | `src/features/agent/ReviewPanel.tsx:227-229`、`:284-286`（`hideActions`） | 已实现 |
| 审阅传入 | `src/layouts/EditorLayout.tsx:363`（`planGenui={productionPlan?.genui}`） | 已实现 |
| 类型声明 | `src/types/persona.ts:313`、`:326`（`genui?: unknown`） | 刻意 `unknown` |

**关键事实（负面）**：`contract.json` 在前端**只服务测试**。真正的运行期校验用的是 `validate.ts` 里的硬编码常量 —— 所以"契约单一真相"目前只是**构建期对账**，不是运行期下发。

### 2.4 扩展面与权限模型现状

| 事实 | 位置 |
|---|---|
| 17 个 `ContributionKind`，注释写明"名单冻结；新增需走契约评审" | `clipwright/plugins/contribution.py:22-44` |
| 冲突策略只有 REJECT / OVERRIDE，内置不可覆盖 | `clipwright/plugins/contribution.py:47-50`、`:186-194` |
| "声明即授权"：未在 manifest 声明的 `(kind,id)` 被拒 | `contribution.py:124-138`、`:176-185`；装载点 `plugins/base.py:86-95`、`loader.py:519-524` |
| dispatch 对未接线 kind 直接放行（facade-only） | `contribution_dispatch.py:55-74`（`:73-74` 返回 `None` 视为成功） |
| provider 类贡献有注册期协议校验（可复用的失败模式） | `contribution_dispatch.py:64-71` |
| 权限白名单校验 | `loader.py:74-77`（比对 `settings.plugin_allowed_permissions`），默认值 `["network","fs_read","fs_write","http","noop","orchestrate"]`（`config.py:60-62`） |
| 进程内插件 = importlib 直接 import 入口模块 | `loader.py:272-279`、`:912-937` |
| 子进程档存在（唯一真实隔离） | `loader.py:269-270` → `:429-503`；声明转代理 `:505-561`；纯数据 kind 直通集合 `_DECLARATIVE_KINDS` 在 `:526-528` |
| 插件启停持久化 | `loader.py:795-819`；前端开关后失效缓存 `src/features/settings/PluginsPanel.tsx:225`、`:251` |
| 契约拉取端点（可复用） | `GET /api/plugin/contributions`（`api/plugin.py:330-429`），前端已在用（`src/features/plugins/usePluginSlots.ts:64-85`，30s TTL、`:29-45` 失效广播） |
| 内置贡献镜像只镜像**注册表里已有**的条目 | `plugins/builtin_contributions.py:1-16`（因此 display_spec 不会被"意外镜像成内置"→ 插件不在就真的没有） |
| 前端 slot 名单 15 个、新增需 SlotRegistry 评审 | `src/features/plugins/slots.ts:14-47` |
| 受信任 bundle 档（iframe）默认关闭，禁止 `import()` 插件 JS 进宿主 | `docs/pluginization-plan-2026-09-10.md` §D-4（`:635-641`） |

**已发现的既有缺陷（迁移时必须补齐）**：`GENUI_BADGE_TONES`（`genui.py:42` = `{success,warn,danger,accent}`）**没有进契约文件**。`genui_contract.json` 只有 `tones`，无 `badge_tones`；`tests/clipwright/test_genui_spec.py:548-559` 也只断言了 `tones`。后果：契约当前**无法完整表达"badge 能写哪些色"**，任何"插件按契约教模型"的方案都会漏掉 badge 的 `accent`。

---

## 3. 设计裁定（逐条：结论 + 理由 + 反例代价 + 落地位置）

### 3.1 A1 · 扩展点形态：新增 `ContributionKind.DISPLAY_SPEC = "display_spec"`

**结论**：新增一个**纯数据**的 kind，名为 `display_spec`（不是 `spec_renderer`，也不是 `genui`）。它声明的是"某个展示面（spec_id）的规格契约 + 卡片配方"。

**为什么不复用现有 kind**：

| 候选 | 反例代价 |
|---|---|
| `prompt` | `PluginPromptRegistry` 的内容会被拼进模型提示词（`requirements_service.py:3892-3893`、`requirements_agent.py:147-150`）。把白名单/上限/配方塞进 PROMPT 的 meta，要么泄漏进提示词（模型开始背 JSON 上限表），要么在注册表里再加一层过滤 —— 等于用错误的数据类型硬塞。 |
| `ui_slot` | `ui_slot` 的消费方是 `PluginLayoutRenderer`（插件**静态表单**：`UILayout/widgets`）。`AGENTS.md:130-164`（#26）已明令"两套 schema 不能混"；且 slot 渲染器根本不认识 `text/table/stat` 这类节点。混用会让 `usePluginSlots.ts:47-61` 的解析器把两种形状塞进同一个 union。 |
| `hook` | hook 是**事件点**（`HookPoint` + 调用时机），契约是**静态数据**，没有"何时触发"的语义。用 hook 拿契约等于每次都要问一次"有没有人注册了这个 point"。 |
| `ui_slot` + 新 slot 名 | 新增 slot 要改前端宿主代码并走 SlotRegistry 评审（`slots.ts:1-11`），而 `display_spec` 的卖点恰恰是**新 spec_id 不需要改前端**（§3.4）。 |

**命名理由（三个候选被否的具体原因）**：
- `spec_renderer`：插件**没有渲染能力**。渲染器是前端编译期的 React 组件（`GenUISpecRenderer.tsx`），插件无法交付、宿主也禁止 `import()` 插件 JS（§D-4）。名字许诺做不到的事，下一个人会照着它去实现一个假的 renderer。
- `genui`：把具体产品名钉进通用契约。下一次出现第二种展示形态（例如"时间线视图"）就要再加一个 kind，而它其实只是另一个 `spec_id`。
- `display_spec`：描述"提供一份只读展示规格"，不承诺渲染、不承诺校验权。

**契约评审要点（必须逐条过，因为 kind 名单冻结在 `contribution.py:23`）**：

| 编号 | 评审要求 | 理由/证据 |
|---|---|---|
| N1 | `Contribution.object` 必须为 `None`；`meta` 必须可 `json.dumps`（无 callable / 类 / 文件句柄） | 零信任（§3.3）；子进程档可直通（`loader.py:541-544` 的 `_DECLARATIVE_KINDS` 先例） |
| N2 | 同一 `spec_id` 只允许一个 owner；**禁止** `override_builtin` 覆盖别人的 `display_spec` | 展示契约被第三方替换 = 悄悄改变信任边界。复用 `_validate` 的 REJECT（`contribution.py:186-194`），并在 dispatch 里显式拒绝 OVERRIDE |
| N3 | 注册期不得产生 I/O（契约从插件目录读，是插件自己在 `contributions()` 里读，engine 不读文件） | 保持 engine 导入期零 I/O，避免加载顺序耦合 |
| N4 | meta 形状固定（见下方 schema），缺键/类型错 → 注册期拒绝 + 写错误通道 | 复用 provider 的失败模式（`contribution_dispatch.py:64-71`） |
| N5 | 不进 `_WIRED_KINDS`（没有后端注册表），走 facade-only，但**必须在 dispatch 里显式校验**，不能靠"未知 kind 直接返回 None"默认放行（`contribution_dispatch.py:73-74`） | 否则一个形状错误的契约会静默生效，直到渲染期才炸 |
| N6 | 契约里的 `components` / `actions` 必须 ⊆ 宿主已实现集合（前端 `GENUI_COMPONENT_TYPES` / `GENUI_ACTION_KINDS`），由跨仓 CI 断言 | 前端原语是编译期闭包（§3.4） |
| N7 | **不需要** SlotRegistry 式评审流程 | slot 是宿主 UI 的物理挂载点（新增要改前端宿主代码）；`display_spec` 不是挂载点，新 `spec_id` 不改前端。但要登记进 `docs/pluginization-plan` §12.3 的 kind 表（14→17→18）与两仓 `AGENTS.md` |

**`display_spec` 的 meta schema（冻结）**：

```json
{
  "spec_id": "brief",                    // 与 contribution.id 相同（冗余但便于消费方直读）
  "version": 1,
  "source": "brief",                     // 能装配哪类源数据：brief / plan（一贡献一源）
  "components": ["text", "list", "..."],
  "containers": ["row", "col", "card", "accordion"],
  "actions": ["ask", "select", "confirm"],
  "tones": ["info", "success", "warning", "error"],
  "badge_tones": ["success", "warn", "danger", "accent"],   // ← 补齐 §2.4 的既有缺陷
  "text_sizes": ["display", "h1", "h2", "h3", "body", "muted", "caption"],
  "limits": { "max_nodes": 100, "max_depth": 6, "max_text_chars": 4000,
              "max_spec_bytes": 65536, "max_array_items": 100, "max_table_cols": 12,
              "max_action_payload_chars": 2000, "max_select_options": 6, "max_option_chars": 40 },
  "order": 100,
  "trust": "sanitize",                   // sanitize（brief）= 模型给了就洗、洗空用配方
                                         // recipe_only（plan）= 模型给的 spec 一律不看
  "recipe": { "title": "创意简报", "nodes": [ ... ] }   // 配方与契约同文件声明，实现见 coding plan §2.3
}
```

> plan 面的契约用 `"trust": "recipe_only"`；brief 面用 `"trust": "sanitize"`。两个面是**两条贡献**（id 分别 `brief` / `plan`），共享同一份白名单/上限。

**落地位置**：`clipwright/plugins/contribution.py`（枚举加一项）、`clipwright/plugins/contribution_dispatch.py`（形状校验）、`clipwright/plugins/loader.py:526-528`（加入 `_DECLARATIVE_KINDS`，让子进程档也能声明）、`clipwright/schema/plugin.py:27-59`（`ContributionDecl` 文档说明 17→18）。

---

### 3.2 A2 · 能力搬家判定表（逐项：搬 / 留但通用化 / 留专属）

| # | 物件 | 判定 | 落地 | 不可替代的理由 / 反例代价 |
|---|---|---|---|---|
| 1 | 版本、13 类白名单、容器/动作/色调/字号、9 个上限 | **搬进插件**（`plugins/genui/contract.json`） | 插件 `contributions()` 读取 → `meta` | 这是"能力方知识"。留在 engine 就是一条 genui 专属常量表，直接违反 D4 |
| 2 | `sanitize_spec()` + 逐组件清洗分支（`genui.py:98-397`） | **留 engine，通用化** → `display_spec.sanitize(raw, contract)` | 新模块 `clipwright/services/display_spec/sanitizer.py` | **信任边界不可外包**。反例：第三方 `display_spec` 插件提供 `sanitize=lambda raw: raw`，engine 就会把任意节点下发。前端第二道闸（`validate.ts`）会拦一部分，但（a）"后端写入已消毒"这个前提不成立后，任何新消费者（导出、缓存、日志、第三方 API）都吃未消毒数据；（b）前端兜底不能替代后端，否则 #26 的双道闸退化为单道 |
| 3 | `build_brief_genui()` / `build_plan_genui()`（`genui.py:403-480`） | **搬进插件**，以**声明式 recipe** 表达 | 插件 `contract.json` 的 `recipe`；engine 只留通用解释器 | 这是真正的"能力主体"（卡片怎么拼、展示哪几个字段）。留在 engine 就是"engine 知道 brief 该用 2 列表格摆 5 个字段" —— 纯 genui 知识 |
| 4 | `ensure_brief_spec()` / `ensure_plan_spec()`（`genui.py:511-536`） | **拆两半**：策略搬（`trust: recipe_only` 进契约）、机制留（通用 `ensure_display_specs(source, payload)`） | `clipwright/services/display_spec/assemble.py` | "规划书不看模型给的 spec"是**产品策略**（用户确认的对象不能被展示层编造），属于能力方；"洗 → 若无则按配方生成 → 写回"是**机制**，与 genui 无关 |
| 5 | `load_contract()`（`genui.py:483-493`） | **删掉**（engine 不再读契约文件） | — | 契约来源变成 `ContributionRegistry`；engine 里再留一个文件读取就是第二条真相 |
| 6 | `spec_debug_summary()`（`genui.py:539-549`） | **留 engine，改名通用**（`display_spec.debug_summary`） | 同模块 | 纯日志工具，无 genui 语义 |
| 7 | `CreativeBrief.genui` / `ProductionPlan.genui` 字段 + validator（`schema/requirements.py:73-107`） | **改形状 + 降级 validator**：`display_specs: dict[str, dict]`；validator 只做形状收口 | `schema/requirements.py` | pydantic validator 在**模型导入期**执行，而契约来自**运行时插件注册表** → schema 模块被迫依赖插件加载顺序（隐式全局耦合，import 顺序敏感）。契约级清洗必须挪到服务层装配点（§3.5） |
| 8 | `genui_contract.json`（engine 侧单一真相） | **搬 → `plugins/genui/contract.json`**；前端副本继续逐字节比对 | 插件目录 + verify 脚本 | 单一真相必须在能力方。engine 侧留副本 = 第三份真相 |
| 9 | `plugins/genui/main.py` 的 PROMPT | **留**（现状即目标态） | 不变 | 已经是插件里的能力知识 |
| 10 | 4 处生产接线 + `_with_genui_specs` | **改调通用装配器** | `requirements_service.py:1187`、`:1292`、`:2467`、`:2795`、`:3841` | 接线是宿主职责，但不是 genui 职责 —— 改成 `ensure_display_specs("brief"/"plan", ...)` 后 engine 不再出现 genui 名 |
| 11 | `_brief_hash` 排除（`requirements_service.py:110`） | **留 engine**（业务判定），排除集合扩为 `{"genui","display_specs"}` | 同文件 | C10「展示层不得影响业务判定」是 engine 的责任；交给插件等于让能力方决定"什么算业务变化" |
| 12 | 前端渲染器 / 校验器 / 挂载点 | **留宿主** | 前端 | 插件交付不了 React（§4-①） |
| 13 | 前端 `contract.json`（仅测试用） | **留作测试夹具 + 跨仓对账**，运行期改用端点下发 | `src/features/genui/contract.json` | 运行期必须单一真相（端点），构建期副本只做对账（防两仓漂移） |

**搬不动/不该搬的汇总理由（一句话版）**：
- 清洗器 = **信任边界**（不可外包给被约束方）；
- 渲染器 = **宿主编译期产物**（插件无法交付）；
- `_brief_hash` 排除 = **业务判定**（不能让能力方定义业务指纹）；
- pydantic validator 里做契约清洗 = **导入期拿不到运行时注册表**。

---

### 3.3 A3 · 零信任边界：engine **不执行**插件提供的校验/构造代码

**结论**：`display_spec` 贡献只能是数据。engine 从不 `callable()` 插件的任何对象。

**为什么现有权限模型覆盖不了"插件提供校验器"**（逐条给证据）：

| 机制 | 实际能约束什么 | 能否约束"插件自带校验器" |
|---|---|---|
| `manifest.permissions` + `check_permissions`（`loader.py:74-77`） | 只在**加载前**比对 `settings.plugin_allowed_permissions`（`config.py:60-62`）这个白名单字符串集合 | ❌ 这是**声明检查**，不是执行约束。进程内档用 `importlib` 直接 import（`loader.py:272-279`），之后插件代码与 engine 同进程同权限 |
| `ContributionRegistry.declared_scope`（`contribution.py:124-138`） | 只约束"能注册哪些 `(kind,id)`" | ❌ 不约束代码运行期行为 |
| `permission_tier`（`AGENTS.md:73-87` #12） | **Tool 级**声明，被 `capability_matrix` 用于给 agent **工具面**收口（`TIER_MAP` 已清空且必须保持为空） | ❌ 与插件 Python 代码能做什么无关 |
| `runtime: subprocess`（`loader.py:269-270`、`:429-503`） | **唯一真实隔离**：宿主不 import 插件代码，贡献经代理（`:505-561`） | ✅ 但代价大（IPC、代理协议扩展），且当前代理只支持数据类直通与少量方法 |

**反例代价（必须写清）**：假设允许插件提供 `sanitize(raw) -> (spec, problems)`。
1. 插件 A 返回 `raw` 原样 → 后端不再可信；`html`/未知组件直接进库。
2. 前端第二道闸（`validate.ts:44-48`）会拦 → **但**这说明"后端写入已消毒"这个 #26 的前提被推翻；任何新消费者（导出、日志、缓存、将来别的 API）都吃未消毒数据。
3. 插件 A 的实现可以 `os.environ[...]` 读凭据（同进程）—— **这不是迁移引入的新风险，但迁移不能建立在对它的依赖上**。
4. 插件 A 进入死循环 / 抛异常 → 每次简报写入都卡住或失败；engine 侧现在没有任何"调用插件代码带超时"的机制（`loader.py` 无此路径）。

**若将来确实要插件自定义清洗**（不在本 PRD 范围，写清条件）：必须同时满足 ①`runtime: subprocess`；②宿主定义纯函数协议（入参 `raw`+`contract`，出参 `spec`+`problems`，全部 JSON）；③宿主侧强制超时（`runtime_config.call_timeout`，`schema/plugin.py:112-115` 已存在该字段）；④宿主对插件输出**再洗一遍**（插件输出仍不可信）；⑤失败/超时必须降级为"无 spec"，不得阻断写入。**成本估算：子进程代理协议扩展 + 超时 + 二次清洗，≥3 人日，且收益仅"配方可以更复杂"——本 PRD 判定不值。**

**代价谁承担**：若坚持"插件自带校验器" → 代价由宿主（信任边界崩塌）与用户（安全）承担。本 PRD 明确拒绝，并给出替代（数据化契约 + 通用清洗器 + recipe）。

---

### 3.4 A4 · 契约下发与未知原语

**结论**：运行期**唯一**契约来源是 `GET /api/plugin/contributions?kind=display_spec`（`api/plugin.py:330-429`）；前端把它缓存 30s、插件启停后失效；**未知原语一律降级为占位文本，绝不渲染、绝不动态加载**。

**为什么不"构建期同步"**：插件可运行时启停（`loader.py:795-819`，前端开关 `PluginsPanel.tsx:215-225`）。构建期快照必然与运行态不一致 → 用户在插件页关掉 genui，前端仍按旧快照渲染 —— 正是要消灭的"第二真相"。

**为什么不"仍然写死"**：写死 = engine 一份、前端一份、测试再对账一次，三处漂移点。用户要求的"能力在插件"就无法成立。

**为什么复用 `/api/plugin/contributions` 而不是新端点**：
- 该端点已是"所有 kind 的统一出口"，且已透传 `meta`（`api/plugin.py:377-388`）；
- 前端已有缓存/在途去重/失效广播（`usePluginSlots.ts:29-45`、`:64-85`、`:99-125`），新端点要再写一遍；
- 插件启停后的失效广播已接好（`PluginsPanel.tsx:225`、`:251`）。
- 注意：`parseContributions`（`usePluginSlots.ts:47-61`）只对 `ui_slot` 做 slot 名白名单校验，其它 kind 只要有字符串 `id` 就会透传 —— 所以 `display_spec` 能被带出来，但**必须新增专用解析器**（不能复用 `SlotContribution` 类型）。

**未知原语怎么办（硬边界）**：

| 场景 | 行为 | 依据 |
|---|---|---|
| 插件契约声明了前端没有的原语（如 `mermaid`） | 后端通用清洗器**按契约放行**（它只认契约）；前端按未知组件降级为占位文本 `（暂不支持的组件：mermaid）`，兄弟节点保留 | 后端 `sanitize` 对未知 kind 的既有语义（`genui.py:237-240`）、前端 `validate.ts` 的 `default` 分支语义 |
| 插件想让它真的渲染 | **必须发前端版本**：新增原语要改 `types.ts`/`validate.ts`/`GenUISpecRenderer.tsx` 并走单独安全评审（无 HTML、无外链、无脚本） | `AGENTS.md:151-152`（首版不开放 mermaid/echart/plot/scene3d/svg/image，要开必须单独安全评审） |
| 插件声明了新 `spec_id` | **不需要改前端**：前端渲染 `display_specs` 里所有"契约已加载"的 spec，按契约 `order` 排序 | §3.5 |

**反例代价**：如果允许"前端动态加载插件提供的原语实现"，就是 `docs/pluginization-plan-2026-09-10.md` §D-4 的受信任 bundle 档（iframe + `postMessage` + 签名强制 + 管理员开关）。把它当 GenUI 的默认档 = 给编辑器开一个 XSS 面，且与 `AGENTS.md:151-152` 直接冲突。

**跨仓一致性由 CI 守**：`scripts/verify_provider_pluginization.py:218-235`（`genui_contract_sync`）现在比对 `clipwright/schema/genui_contract.json` ↔ 前端副本；迁移后改成 `plugins/genui/contract.json` ↔ 前端副本，并**新增**一条"契约 components/actions ⊆ 前端已实现集合"的断言。

---

### 3.5 A5 · 数据形状与老数据兼容

**结论**：泛化为 `display_specs: dict[str, dict]`（按 `spec_id` 索引），挂在 brief / plan 两个 dict 上；老键 `genui` 在**读路径**归一，不写迁移脚本。

**形状**：

```jsonc
// session.creative_brief
{
  "title": "…", "overview": "…",
  "display_specs": {
    "brief": { "version": 1, "title": "创意简报", "items": [ … ] }
  }
}
// session.production_plan
{ "markdown_content": "…", "raw_scenes": [ … ],
  "display_specs": { "plan": { "version": 1, "title": "制作规划书", "items": [ … ] } } }
```

**为什么键名是 `display_specs` 而不是 `genui_spec`**：`display_specs` 表达"这是一组展示规格"，键是 `spec_id`（由契约声明），不是产品名。前端因此不需要知道"brief 面用的是 genui 这个产品"。

**为什么不是更激进的 `dict[str, Any]`**：值必须是 spec 对象（`{version, items}`），否则清洗器无法判别类型。容器只做"多 spec 并列"，不做"任意展示形态"。

**老数据（2026-11-03~07 落库会话带 `genui` 键）**：

| 环节 | 做法 | 理由 |
|---|---|---|
| 读 | `get_session` 的补丁函数改名 `_with_display_specs`：若 `display_specs` 缺失且 `genui` 是合法 spec（按当前契约洗一遍仍非空）→ 填 `display_specs[spec_id]`；**浅拷贝、只补不存** | 保持 `requirements_service.py:767-785` 既有语义（"读操作不产生写"），避免读路径把展示层写进 Mongo |
| 写 | 只写 `display_specs`，**不再写 `genui`** | 单一写入形状；双写会造两个真相 |
| Mongo | **不做批量迁移** | `creative_brief` / `production_plan` 在存储层就是 `dict`（`schema/agent.py:165`、`:168`），没有 schema 版本字段；批量改写收益 < 风险。归一在读路径一次性完成，写路径自然收敛 |
| 老数据在插件禁用时 | `display_specs` 补不出来（拿不到契约）→ 前端不渲染，字段完好 | 与 §3.6 口径 A 一致 |

**`_brief_hash` 怎么排除"整个展示容器"**：

```python
# clipwright/services/requirements_service.py（改造后）
_DISPLAY_SPEC_KEYS = frozenset({"genui", "display_specs"})   # genui=2026-11-03~07 老键

payload = {k: v for k, v in brief.items() if k not in _DISPLAY_SPEC_KEYS}
```

- 用**显式键集合**而不是"值像 spec 就排除"的启发式：可测试、可解释、不会误伤业务字段。
- 配套测试（必须）：对同一 brief，注入/移除任一同名键、把值替换成任意 spec，`_brief_hash` 恒不变；只改一个业务字段，hash 必须变。
- 注意时间顺序：**`display_specs` 写入路径必须晚于排除逻辑落地**，否则会重演 2026-11-07 计划 §0.3-⑧ 记录的"指纹漂移 → 规划书被误作废"事故。

**`CreativeBrief` / `ProductionPlan` 模型**：

| 项 | 改法 | 理由 |
|---|---|---|
| 字段 | `genui: dict` 删除 → `display_specs: dict[str, dict[str, Any]]`（默认 `{}`） | 单一形状 |
| validator | 只做**形状收口**：值必须是 `dict`；键必须是非空 `str`；键数 ≤ 8；`json.dumps` 长度 ≤ 128KB（防炸） | 模型层拿不到运行时契约（§3.2-#7） |
| 契约级清洗 | 挪到服务层装配点 `ensure_display_specs()` | 那里插件已加载 |
| 对提示词骨架的影响 | `brief_field_names()`（`schema/requirements.py:127-129`）与 `brief_json_skeleton()`（`:131-140`）会把 `display_specs` 列进骨架（占位 `{}`）。**这不会改变模型行为**，因为生产简报提示词用的是 `requirements_service.py:404-451` 的本地常量（不含 `genui` 也不含骨架），模型对 `display_specs` 的认知全部来自插件 PROMPT | 已核实：`_brief_system_prompt`（`:3884`）只用本地 `CREATIVE_BRIEF_SYSTEM` |

---

### 3.6 A6 · 插件禁用后的行为：接受"禁用即无 GenUI"（口径 A）

**两个口径与代价**：

| | 口径 A（采用）：禁用即无 GenUI | 口径 B（放弃）：engine 保留确定性兜底 |
|---|---|---|
| 表现 | 插件禁用 → 简报/规划书**没有**结构化卡片，只剩字段与 markdown | 插件禁用 → 卡片照旧出结构（engine 兜底构造） |
| 与用户要求 | ✅ 一致：engine 无 genui 专属链路 | ❌ 冲突：兜底构造器就是 engine 里的 genui 专属实现（549 行里最核心的 80 行） |
| 与 2026-11-03 Q8 | ❌ 推翻"禁用只失去模型规范、不失去卡片结构" | ✅ 维持 Q8 |
| 实现复杂度 | 低 | 高：同一张卡有两个来源（插件配方 + engine 兜底），两份必然漂移；且"禁用后行为"变成需要长期维护的第二条路径 |
| 漂移风险（具体） | 无 | 高：改了插件配方忘了 engine 兜底 → 同一个 brief 在插件启停两种状态下长得不一样，用户会认为"卡片随机变化" |
| 失败模式 | 插件加载失败/被禁 → 静默无卡片（**必须靠自检 + 日志兜住**） | 插件配方坏 → 兜底顶上，掩盖配置错误 |

**推荐 A，四理由**：
1. 用户要求的直接推论：能力主体在插件 ⇒ 能力不在时就没有该能力。这与 `AGENTS.md:56-62`（#3：`dub_script` 自 2026-11-03 起由插件提供，没有该插件时配音能力整体不可用，是"裁定 3 的预期，不是缺失兜底"）是**同一条既定裁定**的延伸 —— 项目里已有先例，且写进了 AGENTS.md。
2. 展示层不是业务能力：`AGENTS.md:131-134`（#26）第一句就是"spec 丢了、坏了、插件被禁用，业务照常"。口径 A 恰恰把这句话落实得最彻底。
3. 双来源必然漂移（上表最后两行）。
4. 前端已实现"无 spec → 整块不渲染、字段照常"（`AgentPanel.tsx:2001`、`:2096` 的 `hasGenUI(...) &&`），口径 A 不需要任何前端改造。

**口径 A 必须配套的三条硬保证（写进测试）**：
- **G1** 禁用插件不得改变 brief/plan 的任何非展示字段：`ensure_display_specs` 只碰 `display_specs` 键。
- **G2** 禁用插件不得改变 `_brief_hash`、会话状态机、确认流程：hash 排除整容器（§3.5）。
- **G3** 禁用插件后前端不白屏：字段区、markdown 区照常渲染（既有行为）。

**缓解（把"静默无卡片"变成可观测）**：
- 启动自检：`scripts/verify_provider_pluginization.py` 断言默认 profile 里 `display_spec` 贡献存在且契约形状合法（新 check）；
- 降级日志：`ensure_display_specs` 在"契约缺失"时记一条 `info`（不是 warning，因为禁用是合法状态），与现有 `_log_problems`（`genui.py:499-508`）风格一致；
- `plugins/genui` 属默认插件目录（`config.py:34-35` `plugin_dir = Path("plugins")`，`loader.py:191-202 discover`），随包分发默认启用；只有用户显式禁用才进入无卡片状态。

---

### 3.7 A7 · 端到端数据流（重画，逐步标注归属）

```
① 模型产出
   模型在 brief_draft / plan 里给 <spec_id> 数据
   └─ 提示词来自插件 PROMPT 贡献 ──► [plugin] 提供规范；[engine] 拼进 system prompt（通用机制）
        engine 拼装点：requirements_service._brief_system_prompt():3884-3894

② 清洗
   engine 从 ContributionRegistry 取 display_spec 契约 → 通用清洗器应用契约
   └─ [plugin] 提供契约（components/limits/actions）
      [engine] 执行清洗（信任边界，不可外包）

③ 装配（兜底）
   清洗后为空 → 按契约的 recipe 生成；plan 面 trust=recipe_only 时不看模型给的 spec
   └─ [plugin] 提供 recipe；[engine] 通用解释器执行

④ 落库
   写 session.creative_brief.display_specs[spec_id] / production_plan.display_specs[spec_id]
   └─ [engine] 写路径；[engine] 指纹排除 display_specs（C10）

⑤ 读路径
   get_session → _with_display_specs（浅拷贝、只补不存、老键 genui 归一、契约缺失则不补）
   └─ [engine]；[plugin] 只提供契约

⑥ 下发
   GET /api/requirements/session/{id}（dict 直通，无 response_model 过滤）
   GET /api/plugin/contributions?kind=display_spec（契约）
   └─ [engine] 两个端点；[plugin] 契约内容

⑦ 前端渲染
   取契约（30s 缓存 + 启停失效）→ validateGenUISpec(raw, contract) → GenUISpecRenderer
   └─ [frontend] 全部；[plugin] 不参与渲染

⑧ 动作回传
   ask → sendChat；select → sendChat("选择："+value)；confirm → 卡片确认回调（二次确认 + scope）
   └─ [frontend] 宿主回调固定；[engine] 无感知；[plugin] 只声明"有哪些动作"（词表）
```

**一句话**：**插件提供"是什么"（契约 + 配方 + 规范），engine 执行"能不能"（清洗 + 装配 + 指纹 + 落库），前端执行"长什么样"（渲染 + 动作）。**

---

### 3.8 A8 · 回滚与并存：**不双写，读路径双读**

| 问题 | 结论 | 理由 |
|---|---|---|
| 迁移期要不要双写 `genui` + `display_specs` | ❌ 不双写 | 双写造两个写入真相，且回滚时"该删哪个"变成模糊问题 |
| 老数据怎么办 | ✅ 读路径双读（`genui` → `display_specs` 归一） | 一次读时归一的成本 = 一个函数；数据迁移的成本 = 不可逆改写线上会话 |
| 按提交粒度怎么回滚 | 按批次单仓 `git revert`，每批**不跨仓**（除 B2 的契约文件与 verify 脚本，它们在同一次 B2 提交里） | 见 coding plan §6 回滚矩阵 |
| 最危险的回滚点 | B3（字段泛化 + recipe 搬家）。若 B3 回滚而 B2 未回滚：契约已在插件、engine 仍在用 `schema/genui.py` → 文档与代码不一致但**功能不断**（旧文件还在） | 因此 B3 的回滚要求"契约文件同步回滚到 engine"或"保留插件契约、engine 旧文件继续工作"两种都可接受；coding plan 明确选后者 |
| 回滚后老数据 | 写路径已只写 `display_specs` 的会话，回滚到旧代码后前端读 `genui` 读不到 → **需要 B3 的"兼容读"保留期** | 故 B3 必须在**读路径同时兼容 `genui` 与 `display_specs`**，且这条兼容代码至少保留到下一个版本 |

---

## 4. 不可行清单（写清楚不可行在哪、替代方案、代价谁承担）

| # | 做不到的事 | 不可行的具体原因（证据） | 替代方案 | 代价谁承担 |
|---|---|---|---|---|
| ① | 插件交付 React 渲染器 | 前端是独立仓、编译期打包（`J:\Clipweight-Client`）；`docs/pluginization-plan-2026-09-10.md` §D-4（`:635-641`）明确禁止 `import()` 插件 JS 进宿主；唯一动态档是 iframe bundle（默认关闭 + 签名强制 + 管理员开关） | 契约声明 + 宿主渲染器 + 未知原语降级为占位 | 想扩展原语的插件作者：必须发前端版本（跨仓提 PR），否则其原语只能显示占位 |
| ② | 插件提供校验器代码 | 进程内档与 engine 同进程同权限（`loader.py:272-279`）；`permissions` 只是声明检查（`loader.py:74-77`）；无超时机制 —— §3.3 已逐条反驳 | 数据化契约 + engine 通用清洗器；远期可选 subprocess 档（条件见 §3.3 末） | 若将来选 subprocess：宿主承担 IPC 复杂度与延迟；用户承担"第三方代码仍在进程外运行"的运维复杂度 |
| ③ | 插件定义 `_brief_hash` 的排除规则 | 业务判定归 engine（C10） | engine 保留 `_DISPLAY_SPEC_KEYS` 常量 | engine 永远保留这一行"知道 genui 老键"的代码 —— 这是**允许保留**的，因为它不是 genui 能力，而是数据兼容 |
| ④ | 插件让新原语"立刻可渲染" | 前端原语是编译期闭包（`types.ts:17-20`） | 无 | 插件作者：必须等前端发布 |
| ⑤ | 插件把 spec 直接 POST 到自己的 endpoint | C3（`AGENTS.md:145`）：spec 绝不携带 endpoint/URL/method；前端动作只绑宿主回调（`AgentPanel.tsx:746`） | 动作词表仍是宿主的 `ask/select/confirm` | 不会妥协 —— 这是安全红线 |
| ⑥ | 插件接管 pydantic validator | 模型导入期拿不到运行时注册表；schema → 插件的反向依赖会造成 import 顺序耦合 | 服务层装配点清洗 | engine 保留一个"形状收口"validator（不含契约知识） |

**"全部能力进插件"这句话的准确边界**：**知识性能力（写什么、长什么样、用什么上限、怎么兜底拼）全部进插件；安全性与业务判定机制（清洗、指纹、状态机、渲染）留在宿主。** 这不是妥协话术，是 `AGENTS.md:130-164`（#26）已经写死的两条纪律的必然结果：①"两道闸"必须由两侧各自实现；②"展示层不得影响业务判定"。

---

## 5. 不变量（迁移后的版本，C1–C14）

| 编号 | 内容 | 迁移后落点 |
|---|---|---|
| C1 | spec 只有 `version: 1` + 契约白名单里的组件 | 契约（插件）`plugins/genui/contract.json`；清洗器只认契约 |
| C2 | 上限：100 节点 / 6 层 / 4000 字 / 12 列 / 100 行 / 2000 载荷 / 64KB | 契约 `limits` |
| C3 | 动作只认 `ask/select/confirm`；spec 绝不携带 endpoint/URL/method | 契约 `actions`；前端 `AgentPanel.tsx:746` 固定通道 |
| C4 | 双道校验：后端写入洗一遍、前端渲染前再洗一遍；改规则两边一起改 | engine `display_spec/sanitizer.py`；前端 `validate.ts`；跨仓 CI |
| C5 | 未知组件降级为占位文本，兄弟节点保留 | 清洗器 + 前端 `default` 分支 |
| C6 | 版本不符整块丢弃、回落原卡片 | 清洗器 + 前端 |
| C7 | 权威数据不在 spec 里 | brief 字段 / `markdown_content` / `raw_scenes` |
| C8 | 加字段不升版本；换结构才升版本 | 契约 `version` |
| C9 | 插件只声明"能写什么"，不渲染、不校验、不覆盖内置 | `display_spec` 纯数据 kind（N1） |
| C10 | 展示层不得影响业务判定：整容器不进 `_brief_hash`、不影响状态机、不触发确认 | `_DISPLAY_SPEC_KEYS`（engine） |
| C11（新） | **`display_spec` 贡献必须可 JSON 序列化，且 engine 从不调用插件对象** | dispatch 形状校验 + code review 检查项 |
| C12（新） | **契约的 `components`/`actions` ⊆ 宿主已实现集合** | 跨仓 CI 断言 |
| C13（新） | **禁用插件 → 无 spec，且 G1/G2/G3 成立** | 自检脚本 `display_spec_off_degradation` |
| C14（新） | **契约单一真相在插件**：engine 目录下不得再出现 `*contract*.json` 形式的展示契约 | §8 D1 |

---

## 6. 风险登记

| ID | 风险 | 触发条件 | 影响 | 缓解 |
|---|---|---|---|---|
| R1 | **可用性从"编译期常量"变成"运行期插件状态"** | 插件加载失败 / 顺序问题（写入早于贡献注册）/ 被禁用 | 简报静默无 spec，用户看到"卡片消失"，没有任何报错 | 启动自检断言默认 profile 有契约；`ensure_display_specs` 缺失时记 `info` 日志；`PluginsPanel` 里给 genui 加"禁用会失去结构化卡片"的说明文案 |
| R2 | **recipe 解释器成为新的 genui 知识载体** | 解释器里出现"brief 该用两列表格"这类语义 | 等于把 engine 专属链路换个名字留着 | 解释器只实现 11 个**无语义**算子（`literal`/`field`/`item`/`index_plus_one`/`join`/`pairs`/`foreach`/`count`/`sum_of`/`fmt_sec`/`coalesce`）+ 1 个节点级开关（`when`/`skip_if_empty`），不出现 `brief`/`plan`/`genui` 字面量；`grep` 判据盯住（coding plan §2.4） |
| R3 | **通用清洗器与前端校验器漂移** | 只改一边 | 后端放行、前端不认（或反之） | 跨仓 CI（C4/C12）；两端各自对着同一份契约做测试 |
| R4 | **`_brief_hash` 漂移导致规划书被误作废** | 先加 `display_specs` 写入、后加排除 | 用户每次确认都被判"简报变了" | 排除逻辑先落地（B3-T1），写入路径后落地（B3-T4）；hash 测试先行 |
| R5 | **老会话读路径补丁改成"隐式写"** | 忘记浅拷贝 | 下一轮 `_persist` 把展示层写进 Mongo | 保留 `_with_*` 的浅拷贝契约 + 断言"入参对象未被改"的测试（既有 `TestOldSessionBackfill` 已有此断言可复用） |
| R6 | **契约里 `badge_tones` 等键迁移时漏盖** | 照抄现有 `genui_contract.json`（它缺 `badge_tones`） | 插件教错色板 / 契约不完整，badge 的 `accent` 丢失 | 迁移时必须补 `badge_tones` 并加断言（§2.4 已记录该缺陷） |
| R7 | **`plugins/genui` 的 `contributions()` 变成 2 条后，`verify` 脚本"只应 1 条"的断言变红** | 忘记改 `scripts/verify_provider_pluginization.py:258` | CI 门禁失败，阻塞其他批次 | B2 里同提交改断言（`len(items) == 2`） |
| R8 | **前端 30s 契约缓存导致"启用插件后要等半分钟才出卡片"** | 未接 `invalidatePluginSlots()` | 用户以为没生效 | `PluginsPanel.tsx:225/:251` 已调用；新增的契约缓存必须挂在同一个失效广播上（`onPluginSlotsInvalidated`，`usePluginSlots.ts:42-45`） |

**最大风险 = R1**（详见最终答复）。

---

## 7. 未核实清单（不猜，给核实方法）

| ID | 未核实项 | 怎么核实 |
|---|---|---|
| U1 | 生产环境是否存在**非默认** `CLIPWRIGHT_PLUGIN_ALLOWED_PERMISSIONS` / `plugin_require_signature=true` 的部署，会影响插件加载 | `grep -rn "PLUGIN_ALLOWED_PERMISSIONS" .env* scripts/ docs/`；再 grep `PLUGIN_REQUIRE_SIGNATURE`（两条分开跑，避免表内管道符）；问运维 |
| U2 | `plugins/genui` 在各部署里是否都处于启用态（`PluginData/plugins/genui/.enabled`） | `Get-ChildItem PluginData/plugins -Recurse -Filter .enabled`；本地实测 |
| U3 | 子进程档（`runtime: subprocess`）下 `display_spec` 声明能否走通（`_DECLARATIVE_KINDS` 加入后） | 写一个 `runtime: subprocess` 的最小插件跑 `py -3.14 -m pytest tests/test_plugin_contributions.py -q`（文件名实施前核对） |
| U4 | 新增 `display_spec` kind 后，`/api/plugin/contributions` 的 `usePluginSlots` 全量缓存是否会被大契约拖慢 | 实测：契约 ~3KB × 1 个插件，可忽略；若多插件再评估 |
| U5 | `tests/clipwright/test_genui_spec.py` 里 `TestPlanTranslateWiring` / `TestPromptPlaceholderAndInjection` 在去掉 `schema/genui.py` 后的具体改写量 | 实施前 `grep -n "from clipwright.schema.genui" tests/clipwright/test_genui_spec.py` 逐条列出 |
| U6 | 前端 `src/features/genui` 目录是否要改名 | 本 PRD 决定**不改名**（改名会动 `AgentPanel`/`ReviewPanel`/`EditorLayout` 与 5 个文件，收益为零）。若后续统一命名再单独做 |
| U7 | `requirements_agent.py` 是否真的零生产调用点（迁移时可以直接删转发） | 已在前序计划 §0.3-③ 核实为死代码；迁移时用 `grep -rn "translate_scenes" clipwright/` 与 `grep -rn "_ensure_brief_genui" clipwright/` 各确认一次 |

---

## 8. 迁移完成判据（命令 + 期望输出，两仓都要能跑）

```powershell
cd J:\Clipwright

# D1 —— engine 不再有 genui 专属实现文件
Test-Path clipwright/schema/genui.py            # 期望：False
Test-Path clipwright/schema/genui_contract.json # 期望：False

# D2 —— 无专属导入
Get-ChildItem clipwright -Recurse -Include *.py | Select-String -Pattern "schema\.genui|import genui"
# 期望：无输出

# D3 —— 字面量只剩老数据键兼容（≤5 处，且都不是 import/分支条件）
Get-ChildItem clipwright -Recurse -Include *.py,*.json | Select-String -Pattern "genui" -CaseSensitive:$false
# 期望输出仅允许以下几类（行号以实施后为准）：
#   clipwright/services/requirements_service.py:  _DISPLAY_SPEC_KEYS = frozenset({"genui", "display_specs"})
#   clipwright/services/requirements_service.py:  # 2026-11-03~07 落库的老会话用 `genui` 键
#   clipwright/services/display_spec/legacy.py:   LEGACY_SPEC_KEY = "genui"
# 期望：总命中 ≤ 5，且 0 处 import

# D4 —— engine 无 genui 专属常量
Get-ChildItem clipwright -Recurse -Include *.py | Select-String -Pattern "GENUI_|MAX_SELECT_OPTIONS|MAX_OPTION_CHARS"
# 期望：无输出

# 契约单一真相只在插件
Test-Path plugins/genui/contract.json      # 期望：True
Test-Path plugins/genui/recipe.json        # 期望：True（或内联在 contract.json 里，二选一）

# 行为不回归
py -3.14 -m pytest tests/clipwright/test_display_spec_spec.py tests/clipwright/test_display_spec_plugin.py `
                tests/clipwright/test_display_spec_wiring.py -q     # 期望：全绿
py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q           # 期望：文件已删除或改名为 display_spec 版本
py -3.14 scripts/verify_provider_pluginization.py -k genui          # 期望：退出码 0
```

```powershell
cd J:\Clipweight-Client

npx tsc --noEmit                                   # 期望：0
npx vitest run src/features/genui src/features/agent src/features/plugins
                                                   # 期望：全绿
# 前端运行期不读死契约：contract.json 只被测试 import
Get-ChildItem src -Recurse -Include *.ts,*.tsx | Select-String -Pattern "contract.json"
# 期望：仅 src/features/genui/genui.test.tsx（或 __tests__）命中，生产代码 0 命中
```

**"证明 engine 里没有 genui 专属链路"的一句话判据**：D1–D4 全过 —— 没有专属文件、没有专属导入、没有专属常量、没有专属分支，只剩一个老数据键名字面量与一句注释。

---

## 9. 待拍板问题（每条给默认建议）

| # | 问题 | 默认建议 |
|---|---|---|
| Q1 | 新增 `ContributionKind.DISPLAY_SPEC`（18 类）还是把契约塞进现有 `prompt` meta？ | **新增**（§3.1；塞 prompt 会泄漏进模型提示词） |
| Q2 | 接受"禁用插件 = 没有 GenUI 卡片"吗？（推翻 2026-11-03 Q8） | **接受**（§3.6 口径 A；与 #3 的 TTS 先例一致） |
| Q3 | 卡片配方用"声明式 recipe"（新解释器 ~150 行）还是"engine 保留参数化构造函数"？ | **recipe**（后者仍是 engine 持有 genui 知识） |
| Q4 | `display_specs` 键名与"每对象一个容器"的形状是否接受？ | **接受**（§3.5） |
| Q5 | 前端 `src/features/genui/` 目录是否改名 `displaySpec`？ | **不改**（收益为零，动 5+ 文件） |

---

## 10. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-07 | 首版。裁定 A1–A8；不可行清单 6 条；不变量 C1–C14；风险 R1–R8；未核实 U1–U7 |
| 2026-11-07 | 追加 §12「同类问题排查：插件外壳化审计」（44 个插件全量判定：26 真 / 13 半壳 / 5 空壳 / 0 未核实）。裁定 R1–R3：B0–B6 **不扩面**，外壳化整改另立 B7/B8；§1–§11 原有裁定不变 |

---

## 11. 契约评审决议（B0-T0.1，2026-11-07）

评审对象：新增第 18 类 `ContributionKind.DISPLAY_SPEC`。逐条结论（不是"待定"）：

| 编号 | 决议 | 落点 / 反对意见 |
|---|---|---|
| **N1** `object` 必须为 `None`、`meta` 必须可 `json.dumps` | **通过** | `contribution_dispatch._validate_data_only`（B1-T1.2）在注册期强制；子进程档直通先例是 `loader.py` 的 `_DECLARATIVE_KINDS`。反对意见：有人会想"顺手带个 callable 方便"，已由 PRD §3.3 明确拒绝——engine 不执行插件对象 |
| **N2** 同 `spec_id` 单 owner，禁止 `override_builtin` | **通过** | 复用 `contribution.py` 的 REJECT，并在 dispatch 里对 `DISPLAY_SPEC` 显式拒绝 OVERRIDE（B1-T1.2 第 3 步）。理由：展示契约被第三方替换 = 悄悄改变信任边界 |
| **N3** 注册期零 I/O | **通过** | 契约由插件自己在 `contributions()` 里读文件后放进 `meta`，engine 不读插件目录（`services/display_spec/contract.py` 不做任何文件 I/O） |
| **N4** meta 形状固定，缺键/类型错注册期拒绝 + 走错误通道 | **通过** | 必填键 `{version, components, containers, actions, tones, badge_tones, text_sizes, limits, source, trust, order, spec_id}`；`trust ∈ {sanitize, recipe_only}`；`limits` 9 个键且为正整数 |
| **N5** 不进 `_WIRED_KINDS`，但 dispatch 必须显式校验（不得靠"未知 kind 默认放行"） | **通过** | 新增 `_DATA_ONLY_KINDS` 分支，先于 provider 分支判断；否则形状错的契约会静默生效到渲染期才炸 |
| **N6** 契约 `components`/`actions` ⊆ 宿主已实现集合，由跨仓 CI 断言 | **通过（有条件）** | `scripts/verify_provider_pluginization.py` 新增断言（B5）；反例：前端原语是编译期闭包，插件声明 `mermaid` 只会被降级成占位文本——这是**有意的硬边界**，不是缺陷 |
| **N7** 不需要 SlotRegistry 式评审 | **通过** | `display_spec` 不是宿主 UI 的物理挂载点：新 `spec_id` 不改前端代码。但必须登记进 `docs/pluginization-plan-2026-09-10.md` §12.3 的 kind 表（已补：14 → 18 行）与两仓 `AGENTS.md` |

**冻结结论**：`DISPLAY_SPEC = "display_spec"` 作为**纯数据扩展点**成立；engine 侧只保留通用
「契约读取 + 清洗 + 装配 + 渲染」机制，GenUI 的白名单/上限/配方/教模型规范全部在 `plugins/genui`。
任何后续对 kind 形态的变更，必须先改 coding plan §2 并在本文 §10 记一条变更。

**同期确认的两处既有缺陷**（迁移必须一并修掉，已写进 coding plan §2.2）：

1. `clipwright/schema/genui_contract.json` 漏了 `badge_tones`（而 `genui.py:42` 有 `GENUI_BADGE_TONES`）——不补，插件无法表达 badge 的 `accent`；
2. 前端 `src/features/genui/contract.json` **只被测试引用**（`genui.test.tsx:169`），运行期校验是 `validate.ts` 的硬编码常量——"契约单一真相"目前只是构建期对账，B4 改为端点下发。

---

## 12. 同类问题排查：插件外壳化审计（2026-11-07 追加）

> 触发：用户原话「同时检查现有插件，哪些本应该由插件实现，engine 只提供接口的插件，变成了主要功能由
> engine 提供，插件只是个外壳。整合到 PRD 和 coding plan 中。」
> 全量证据（44 行清单、逐插件 file:line、整改方案与人日）在
> [`plugin-shell-audit-2026-11-07.md`](plugin-shell-audit-2026-11-07.md)，本节只压缩成**裁定**。

### 12.1 一句话结论与统计

genui 不是孤例，而是**同一类病的最大样本**。44 个插件中：

| 判定 | 数量 | 代表 |
|---|---|---|
| 真插件（能力主体在插件） | **26** | `web_media`、`pv_engine`、`tts_feature`、`ai_*`、素材源类、category 类 |
| 半壳（插件登记、实现类在 engine） | **13** | `tts_cosyvoice`/`tts_qwen`/`tts_minimax`、`llm_anthropic`/`llm_openai_compatible`、`embedding_providers`/`rerank_providers`、`headless_chrome`、`logic_animations`、`mg_animations`、`kinetic_typography`、`local_material`、`whisper_stt` |
| 空壳（登记了但主体在 engine / 根本无人消费） | **5** | `genui`、`platform_export`、`cloud_render`、`lottie_animations`、`stt_providers` |
| 未核实 | **0** | — |

三条结构性病因（比单个插件更重要）：

1. **2026-11-02 的 provider 插件化只搬了注册与声明**：交付汇总自己写明「协议适配类仍在 engine」
   （`provider-pluginization-delivery-summary-2026-11-02.md:20-21`、`:97-103`），于是 8 个 provider
   插件全是「插件提供工厂、类在 `clipwright/`」。
2. **engine 里仍有 15 处 vendor 字面量 / 9 处 vendor 分支**（`services/llm.py:727,897,950`、
   `services/voice.py:729-734,771`、`config.py:75,511,535` 等）——只要 engine 还按 provider 名分支，
   搬类只是换位置，违反 #24。
3. **钩子没有契约**：`api/render.py:603` 的 PRE_RENDER 只传 `{"task_id"}`，而 `cloud_render` /
   `platform_export` / `shortform_category` 都在读 `context["settings"]`/`["platform"]` —— 三个插件
   的贡献**自注册之日起从未生效**。

### 12.2 三条裁定（哪些本次修 / 另立 / 不修）

| 编号 | 裁定 | 内容与理由 |
|---|---|---|
| **R1** | **B0–B6 不扩面** | 本文 §1–§11 的裁定、A1–A8、C1–C14、U1–U7 **一律不变**。外壳化整改与 `display_spec` 无文件交集（B0–B6 只碰 `schema/genui.py`、`requirements_service.py`、`display_spec/`、前端 `features/genui/`；B7/B8 碰 `services/llm.py`、`services/voice.py`、`providers/*`、`animation/*`、`api/render.py`），**强行合批只会让 B3 的字段泛化与 provider 搬家互相干扰回滚**。唯一"本次一起做"的是一句文档裁定：B6 跑 §4 的 D1–D3 判据时，`_DISPLAY_SPEC_KEYS = {"genui","display_specs"}` 必须保留（它是老数据兼容，不因 B7 清理 vendor 字面量而被顺手删）。 |
| **R2** | **另立 B7 / B8 两个批次** | B7「协议类归位」（**10.0 人日 / 6 任务**）：TTS 三件套 + LLM 两件套 + embedding/rerank + STT 后端搬进插件，同时清掉 §12.1-② 的 vendor 分支与 `Literal` 枚举、补"no_vendor_branches"门禁。B8「失效钩子与渲染器归位」（**10.0 人日 / 7 任务**）：`headless_chrome`、`logic_animations`、`kinetic_typography`、`lottie_animations`、`platform_export`、`cloud_render`、`mg_animations`、`local_material`、`voice_ext`、`whisper_stt`，以及 hook 逐 point 契约表。两批合计 **20.0 人日 / 13 任务**，全计划由 **30 任务 / 17.0 人日 → 43 任务 / 37.0 人日**。批次详情见 coding plan §1/§3/§7。 |
| **R3** | **明确不修（并写清理由）** | ① `web_media` 的落盘/SSRF 边界：`plugins/web_media/html_images.py:15-17` 已声明「落盘复用 `services/web_assets.fetch_and_store`（robots/SSRF/去重/大小限制属于平台能力）」，与本 PRD §3.3 的信任边界同构，**不搬**；② `example_*`/`sandbox_demo`/`subagent_demo` 是契约样本，不按"业务插件"要求；③ `web_search`/`web_fetch` **不是插件**（engine 内置工具 `tool/web_search_tool.py:46,132`），不属"外壳化"，属 #19 的另一笔欠账，**另立目标（≥4.0 人日）**；④ `cloud_render` 的 B 档（把 `services/remote_render.py` 23KB 搬成 `renderer_backend` 插件）**暂不做**，先按审计 §3.9 的 A 档删除死插件。 |

### 12.3 与本文既有判据的对应关系

| 本文判据 | 泛化后的判据（审计 §1.2） | 本轮落点 |
|---|---|---|
| D1 无专属实现文件 | **S1** engine 有专属实现类/模块 | B7 逐类搬移；B8-T8.1 渲染器 |
| D2 无专属导入 | **S2** 插件 import engine 实现 | B7 全部任务 |
| D3 字面量收口（≤5 处老键） | **S4** vendor 字面量/分支（15 处 → 0） | B7-T7.5 |
| D4 无专属常量 | **S3** 双实现/双注册（6 处 → 0） | B7-T7.1–T7.4；B8-T8.6 |
| —（新） | **S5** engine 反向知道插件目录/ID（3 处） | B8-T8.1（headless）/ T8.7（mg） |
| —（新） | **S6** 贡献无人消费（钩子键名不匹配） | B8-T8.4 / T8.5 / T8.6 |
| C9（插件只声明，不渲染不校验） | 不变 | 整改**不**让插件提供清洗器/渲染器，见审计 §3 每项"搬不动" |
| C14（契约单一真相在插件） | 同理：**能力的实现也应在插件** | B7/B8 的目标 |

### 12.4 对 B0–B6 的影响评估

- **文件级**：零交集（清单见 R1）。B7-T7.5 会改 `config.py` / `services/llm.py` / `services/voice.py`，
  B0–B6 一行都不碰这三份。
- **文档级**：B5-T5.4 改 `AGENTS.md` #26；B8-T8.7 追加 #26 的"插件提示词不得替 engine 宣称能力"一句
  与 #19 的欠账说明。**两者必须串行**（同一份文件），建议 B5-T5.4 先合、B8 后补。
- **验收级**：B6 的 §4 判据（`Test-Path clipwright/schema/genui.py` 等）在 B7/B8 之后**必须重跑**，
  因为 B7 会删除 `services/voice.py` 的类、B8 会删除 `services/headless_browser.py` 的实现体，
  两份"删除清单"不能被误判成"genui 回退"。该注意点已写进 coding plan §8。
- **排期级**：B7/B8 可与 B1 之后并行；单人串行时排在 B6 之后（coding plan §7）。

### 12.5 需要在开工前拍板的问题

见审计 §7（3 条，各带默认建议）：**Q1 平台预设禁用后是否保留** / **Q2 logic_animations 搬还是认** /
**Q3 cloud_render 删除还是搬**。三条都不阻塞 B7 的第一批任务（T7.1–T7.4），但阻塞 B8-T8.4/T8.5。
