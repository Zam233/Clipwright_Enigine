# GenUI：创意简报 / 规划书的只读结构化展示

> 配套：[PRD](genui-plugin-prd-2026-11-03.md)、[coding plan](genui-plugin-coding-plan-2026-11-07.md)。
> 本文是**给实施者看的现状说明**（契约、产出路径、加组件条件、已知缺口），不是设计稿。

## 1. 它是什么（以及三条硬纪律）

一份 `version: 1` 的受控 JSON（`items` 树），让创意简报卡与规划书卡能出现表格 / 指标 / 步骤
这类结构化视图。参考实现是 DSH 的 `dsh-genui`，本仓只取**安全子集**。

1. **只读展示层，不拥有权威数据**：用户确认与下游执行的对象永远是 `CreativeBrief` 字段、
   规划书 `markdown_content` / `raw_scenes`。spec 丢了、坏了、插件被禁用，业务照常。
2. **不信任模型输出**：写入前 `sanitize_spec()`、渲染前 `validateGenUISpec()`，两道闸；
   未知组件降级为占位文本、危险键剔除、非法动作丢弃、超限截断、版本不符整块丢弃。
3. **展示层不得影响业务判定（C10）**：`genui` 必须从 `_brief_hash` 排除——否则同一份简报
   "补过 / 没补过"会算出不同指纹，把刚生成的规划书误作废。

## 2. 数据契约

单一真相：`clipwright/schema/genui_contract.json`；前端 `src/features/genui/contract.json`
是**逐字节副本**（`scripts/verify_provider_pluginization.py::genui_contract_sync` 把关）。

### 2.1 组件白名单（13 类）

| type | 必填字段 | 可选字段 | 说明 |
|---|---|---|---|
| `text` | `content` | `size`(display/h1/h2/h3/body/muted/caption)、`tone` | 段落文本，`display` 一条回答最多一次（本仓不强制） |
| `list` | `items`（字符串或 `{title,desc}`） | — | 要点罗列 |
| `callout` | `content` | `tone`(info/success/warning/error)、`title` | 提示块 |
| `badge` | `label` | `tone`(success/warn/danger/accent) | 状态胶囊 |
| `stat` | `label`、`value` | `delta`（`-` 自动红、`+` 绿） | 关键指标 |
| `progress` | `value`(0–100) | `label`、`valueLabel` | 进度条 |
| `table` | `columns`、`rows` | — | ≤12 列 / ≤100 行；单元格一律转字符串 |
| `steps` | `steps`（字符串或 `{title,desc}`） | `current` | 步骤/阶段 |
| `divider` | — | `label` | 章节分隔 |
| `row` / `col` | `items`（子节点） | — | 容器，行内 flex / 纵向 flex |
| `card` | `items` | `title`、`tone` | 分组卡片 |
| `accordion` | `items`（`{title,items}`） | — | 原生 `<details>`，本地展开 |

### 2.2 上限（两仓同值，改则同时改）

| 常量 | 值 | 说明 |
|---|---|---|
| `MAX_NODES` | 100 | 节点总数，超出丢弃 |
| `MAX_DEPTH` | 6 | 嵌套深度 |
| `MAX_TEXT_CHARS` | 4000 | 单个文本字段 |
| `MAX_SPEC_BYTES` | 65536 | **清洗后**整块（字符数近似字节） |
| `MAX_ARRAY_ITEMS` | 100 | 列表/表格行 |
| `MAX_TABLE_COLS` | 12 | 表格列 |
| `MAX_ACTION_PAYLOAD_CHARS` | 2000 | 动作载荷 |

### 2.3 动作（白名单 ≠ 可点）

只允许 `ask` / `select` / `confirm`。**是否可点由宿主传的 `supportedActions` 决定**：
当前宿主只支持 `ask`（点击把 `payload.prompt` 发回**当前需求会话**）；`select`/`confirm`
渲染为禁用态。spec **禁止**携带 endpoint / URL / HTTP method / 任意能力名。

## 3. 产出路径（2026-11-07 接线完成）

| 场景 | 入口 | 策略 |
|---|---|---|
| 简报（生产） | `requirements_service._handle_gathering` 落库前 → `ensure_brief_spec` | 模型给了就洗；没给/给坏就 `build_brief_genui` 从字段确定性补 |
| 简报（修订） | `_handle_gathering` 修订分支 | 同上 |
| 规划书（两条主路径） | `requirements_service._translate_plan` 返回前 → `ensure_plan_spec` | **只**用确定性构造器（看 `raw_scenes`，**不看**模型给的 genui） |
| 规划书（legacy） | `_legacy_plan_from_scenes` | 同一函数 |
| 读取老会话 | `get_session` → `_with_genui_specs` | **浅拷贝 + 只补不存**，不回写 Mongo / 内存会话 |
| 提示词 | `requirements_service._brief_system_prompt()`（生产）<br>`requirements_agent._brief_system_prompt()`（Agent，当前无生产调用点） | base 提示词 + `PluginPromptRegistry.get_for_agent("requirements")`；`prompts.py` 的简报常量含 `{{BRIEF_TEMPLATE}}` 占位，运行时展开 |

## 4. 插件与"禁用后照常"

`plugins/genui/` 只贡献一条 `PROMPT`（`agent: requirements`），教模型"能写什么"。
它**不渲染、不校验、不覆盖内置提示词**。

- 插件在：生产简报 prompt 末尾多一段「GenUI 展示规范」，模型可能产出 spec；
- 插件被禁用：只是少了这段说明 → 模型不产出 spec → `ensure_brief_spec` 用确定性兜底；
  **卡片照常出结构**。自检 `genui_off_degradation` 就是这条的证据。

## 5. 如何加一个组件（缺一不可）

1. 零新依赖，或依赖可树摇且首屏 gzip 增量 ≤ 30KB（给出 `npm run build` 前后体积对比）；
2. 不加载任何模型可控的远端资源（无 URL 字段，或 URL 必须过宿主白名单 + 同源/签名）；
3. 独立安全评审结论（XSS / SSRF / 原型污染 / 资源耗尽各一条）；
4. 后端清洗器 + 前端校验器 + `genui_contract.json` 三处同步，且两仓测试各补正例/负例；
5. 插件 `plugins/genui/main.py::COMPONENTS` 同步（有测试盯住）；
6. 两仓 `AGENTS.md` #26 的白名单数量同步（13 → N）。

### 5.1 组件扩充评估（2026-11-07，纸面评估，未实现）

| 候选 | 结论 | 理由 |
|---|---|---|
| `keyvalue` | 不扩 | 与 `table`（2 列）+ `list{title,desc}` 重复；简报构造器已用 2 列表格表达键值 |
| `image` | 不扩 | 需要域名白名单 / 短时签名 URL / 代理策略；PRD §4.5 首版禁用外链资源 |
| `tabs` | 不扩 | 静态只读卡片里 tabs 会藏内容；`accordion` 已覆盖分层折叠 |
| `chart` / `echart` | **不扩（首版）** | 需图表库：体积（`echarts` 量级 ~300KB+ gzip，**须实测**）、懒加载、许可证；且 `echart` 的 `option` 直通等于把任意配置交给模型，必须先定义受控子集 schema。`stat` + `progress` + `table` 已能表达简报/规划书的全部数字 |
| `mermaid` / `svg` / `scene3d` / `plot` | 不扩 | 已在 PRD §4.5 / §8 与 `AGENTS.md` #26 明确排除 |

## 6. 已知缺口（截至 2026-11-07）

| 缺口 | 状态 |
|---|---|
| 表格本地排序/筛选（计划 T5.1） | **已做（测试未跑）**：表格 ≥8 行时出现筛选框与可点表头（数字列按数值排序、跨列子串筛选、显示 `N/M 行`）；纯函数 `sortTableRows` / `filterTableRows` 导出；`useGenUILocalState` 占位导出已删 |
| 真实模型回归（T6.2） | **待人工**：3 类选题实测，确认模型产出 vs 兜底、是否有未知组件降级 |
| 性能/窄屏/深色主题实测（T6.4） | **待人工**：贴 64KB 的 spec 首屏耗时、100 行表格、375px 下横向滚动、深色对比度 |
| `requirements_service.py:396` 第三份 `CREATIVE_BRIEF_SYSTEM` | **既有违规未动**：字段集比 `CreativeBrief` 宽（前端 `BriefCard` 依赖），与 `prompts.py` 那份合并属独立清理 |
| `select` / `confirm` 实现（T4.2 可选） | **未做（默认不建议）**：`confirm` 若做必须带二次确认，不能让模型写的按钮单击推进状态机 |

## 7. 自检命令

```powershell
# 后端：清洗器/构造器/接线/契约同源/插件一致性（45+ 例）
cd J:\Clipwright; py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q

# 后端：插件化 + GenUI 自检（含 genui_contract_sync / genui_plugin_contract / genui_off_degradation）
py -3.14 scripts/verify_provider_pluginization.py -k genui

# 前端：类型 + 渲染器/校验器用例（vitest 按项目约定由人执行）
cd J:\Clipweight-Client; npx tsc --noEmit; npx vitest run src/features/genui
```
