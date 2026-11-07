# GenUI 能力插件化迁移 · Coding Plan

> 日期：2026-11-07  
> 前置文档：[`genui-plugin-migration-prd-2026-11-07.md`](genui-plugin-migration-prd-2026-11-07.md)（PRD，裁定 A1–A8、不变量 C1–C14、风险 R1–R8、未核实 U1–U7）  
> 目标仓：`J:\Clipwright`（engine）、`J:\Clipweight-Client`（前端宿主），两仓当前均在 `feat/master-agent`  
> 施工原则：**每个任务都必须能单独编译 + 单独测试 + 单独回滚**；不允许"整批一起才能跑"的中间态。

---

## 0. 施工前必读

### 0.1 本计划与现状的关系

本文**推翻**以下既有结论（不是补充，是替换），执行前请先接受这些事实：

| 旧结论 | 出处 | 迁移后 |
|---|---|---|
| 「插件只贡献一条 `PROMPT`，不校验不渲染」 | `docs/genui.md:85`、`plugins/genui/main.py:7-16` | 插件仍不校验不渲染，但**新增 2 条 `display_spec` 数据贡献**（契约 + 配方） |
| 「插件禁用时宿主用 `build_brief_genui()` 兜底」 | `AGENTS.md:153-155`、`scripts/verify_provider_pluginization.py:271-291` | **删除兜底**：禁用即无 spec（PRD §3.6 口径 A） |
| 「单一真相 = `clipwright/schema/genui_contract.json`」 | `docs/genui.md:20-21`、`tests/clipwright/test_genui_spec.py:524-573` | 单一真相 = `plugins/genui/contract.json` |
| 「`CreativeBrief.genui` / `ProductionPlan.genui`」 | `clipwright/schema/requirements.py:73-107` | `display_specs: dict[str, dict]`（老键 `genui` 读路径归一） |

### 0.2 每批开工自检（照 `docs/pluginization-plan-2026-09-10.md` §0.4 的纪律）

```powershell
# 1) 两仓干净、同分支
cd J:\Clipwright; git status --porcelain; git branch --show-current
cd J:\Clipweight-Client; git status --porcelain; git branch --show-current

# 2) 基线绿（任一条红则停，先修基线）
cd J:\Clipwright
py -3.14 -m pytest tests/clipwright/test_genui_spec.py -q          # 迁移前基线
py -3.14 scripts/verify_provider_pluginization.py -k genui
cd J:\Clipweight-Client
npx tsc --noEmit; npx vitest run src/features/genui

# 3) 目标区段仍是计划描述的样子（行号漂移就先改本计划再动代码）
Select-String -Path clipwright\services\requirements_service.py -Pattern "_brief_hash|_with_genui_specs|ensure_brief_spec|ensure_plan_spec"
Select-String -Path clipwright\schema\genui.py -Pattern "^def |^GENUI_|^MAX_"
```

### 0.3 分支与提交纪律

- 两仓各开一支 `feat/genui-display-spec`，**按批次一一对应提交**（B1 后端一次、B2 两仓各一次、B3 后端一次、B4 前端一次、B5 两仓、B6 后端）。
- 提交信息前缀：`refactor(genui):` / `feat(display_spec):` / `test(display_spec):` / `docs(genui):`。
- **不允许**跨批次混提：B3 的字段泛化与 B2 的契约搬家必须能分别 `git revert`。
- 不 commit、不打 tag（本次任务明确要求）。

---

## 1. 批次总览

| 批次 | 主题 | 归属 | 任务数 | 人日 | 依赖 | 关键路径 |
|---|---|---|---|---|---|---|
| **B0** | 契约评审与冻结（Gate） | 两仓（文档） | 2 | 0.5 | — | ✅ |
| **B1** | engine 通用扩展点 `display_spec` | 后端 | 5 | 2.0 | B0 | ✅ |
| **B2** | 契约搬家 + 清洗器通用化 | 后端 + 插件 + 跨仓 | 5 | 3.0 | B1 | ✅ |
| **B3** | 配方搬家 + 字段泛化 + 指纹 | 后端 + 插件 | 6 | 4.5 | B2 | ✅ |
| **B4** | 前端契约下发 + 挂载迁移 | 前端 | 5 | 3.0 | B2（契约形状冻结） | ✅（与 B3 并行） |
| **B5** | 禁用验收 + 门禁 + 文档 | 两仓 | 4 | 2.5 | B3 + B4 | ✅ |
| **B6** | 清理收尾 + 判据执行 | 后端 | 3 | 1.5 | B5 | ✅ |
| | | | **30** | **17.0** | | |

**关键路径**（单人串行）：B0 → B1 → B2 → B3 → B5 → B6 = **14.0 人日**；B4（3.0）在 B2 之后与 B3 并行，不增加墙钟。
**双人并行**（后端 / 前端）：B0+B1+B2（5.5）→ max(B3 4.5, B4 3.0)=4.5 → B5 2.5 → B6 1.5 = **14.0 人日** 墙钟；单人串行 = **17.0 人日**。

---

## 2. 冻结的规格（实施前必须照抄，不得"视情况而定"）

### 2.1 扩展点

- `ContributionKind.DISPLAY_SPEC = "display_spec"`（第 18 类）。
- `Contribution.object is None`；全部信息在 `meta`。
- 注册冲突：同 `spec_id` 第二次注册 → REJECT（既有 `_validate` 行为，`plugins/contribution.py:186-194`）；**禁止** `override_builtin` 覆盖 display_spec（dispatch 里显式拒绝）。
- dispatch：**不进** `_WIRED_KINDS`；进新增的 `_DATA_ONLY_KINDS` 并做形状校验。
- 子进程档：进 `loader.py` 的 `_DECLARATIVE_KINDS`（`loader.py:526-528`）。

### 2.2 `plugins/genui/contract.json`（与前端副本**逐字节一致**）

```json
{
  "version": 1,
  "components": ["text","list","callout","badge","stat","progress","table","steps","divider","row","col","card","accordion"],
  "containers": ["row","col","card","accordion"],
  "actions": ["ask","select","confirm"],
  "tones": ["info","success","warning","error"],
  "badge_tones": ["success","warn","danger","accent"],
  "text_sizes": ["display","h1","h2","h3","body","muted","caption"],
  "limits": {
    "max_nodes": 100, "max_depth": 6, "max_text_chars": 4000, "max_spec_bytes": 65536,
    "max_array_items": 100, "max_table_cols": 12, "max_action_payload_chars": 2000,
    "max_select_options": 6, "max_option_chars": 40
  },
  "specs": {
    "brief": { "spec_id": "brief", "source": "brief", "order": 100, "trust": "sanitize" },
    "plan":  { "spec_id": "plan",  "source": "plan",  "order": 110, "trust": "recipe_only" }
  }
}
```

- **必须新增 `badge_tones`**：现有 `clipwright/schema/genui_contract.json` 漏了它，而 `genui.py:42` 有 `GENUI_BADGE_TONES`（PRD §2.4 已记录该缺陷）。不补，插件就无法教模型 badge 可以用 `accent`。
- `trust` 语义：`sanitize` = 模型给了就洗、洗空则用配方；`recipe_only` = **忽略模型给的 spec**，只用配方（对应规划书"确认对象不可被展示层编造"）。

### 2.3 `plugins/genui/recipe.json`（**只给 engine**，不同步到前端）

```json
{
  "brief": {
    "title": "创意简报",
    "nodes": [
      { "type": "text", "content": { "field": "title" }, "size": "h2", "skip_if_empty": "content" },
      { "type": "text", "content": { "field": "overview" }, "size": "body", "skip_if_empty": "content" },
      { "type": "table", "columns": ["维度", "内容"],
        "rows": { "pairs": [
          { "label": "目标受众", "field": "target_audience" },
          { "label": "核心信息", "field": "core_message" },
          { "label": "风格方向", "field": "style_direction" },
          { "label": "结构建议", "field": "structure_suggestion" },
          { "label": "预估时长", "field": "duration_estimate" }
        ] }, "skip_if_empty": "rows" },
      { "type": "text", "content": "必须出现的关键元素", "size": "h3",
        "when": { "field": "key_elements", "non_empty": true } },
      { "type": "list", "items": { "field": "key_elements", "limit": 20 }, "skip_if_empty": "items" },
      { "type": "callout", "tone": "warning", "title": "特殊要求与红线",
        "content": { "join": { "field": "special_requirements", "sep": "；", "limit": 10 } },
        "skip_if_empty": "content" }
    ]
  },
  "plan": {
    "title": "制作规划书",
    "nodes": [
      { "type": "row", "items": [
        { "type": "stat", "label": "场景数",
          "value": { "coalesce": [ { "field": "scene_count" }, { "count": "raw_scenes" } ] } },
        { "type": "stat", "label": "总时长",
          "value": { "fmt_sec": { "coalesce": [ { "field": "total_duration_sec" },
                                                { "sum_of": { "field": "raw_scenes", "value": "duration_sec" } } ] } } }
      ] },
      { "type": "table", "columns": ["#", "场景", "时长", "关键词"],
        "rows": { "foreach": { "field": "raw_scenes", "item": "scene", "index": "i" },
                  "row": [ { "index_plus_one": "i" },
                           { "item": "scene.title" },
                           { "fmt_sec": { "item": "scene.duration_sec" } },
                           { "join": { "item": "scene.keywords", "sep": "、", "limit": 3 } } ] },
        "skip_if_empty": "rows" },
      { "type": "callout", "tone": "info", "content": { "field": "material_summary" },
        "skip_if_empty": "content" }
    ]
  }
}
```

### 2.4 recipe 算子（**11 个 + 2 个节点级开关，语义固定，禁止扩展**）

| 算子 | 语法 | 语义 | 禁止 |
|---|---|---|---|
| literal | `"文本"` / `12` / `true` | 原样 | — |
| field | `{"field":"a.b"}` | 按 `.` 分段 `dict.get`；缺失/非标量 → `""` | `getattr` / 索引表达式 / 调用 |
| item | `{"item":"scene.title"}` | 仅在 `foreach` 内有效 | 逃出作用域 |
| index_plus_one | `{"index_plus_one":"i"}` | 仅在 `foreach` 内有效，输出 `i+1` | 逃出作用域 |
| join | `{"join":{"field":"k","sep":"、","limit":3}}` | list 里只取 `str` 项，`limit` 截断后 join | 对 dict 求值 |
| pairs | `{"pairs":[{"label":"…","field":"…"}]}` | 生成 rows，跳过值为空的行 | 任意表达式 |
| foreach | `{"foreach":{"field":"list","item":"n","index":"i"},"row":[…]}` | 逐项求值成二维 rows | 嵌套超过 2 层 |
| count | `{"count":"raw_scenes"}` | list 长度（非 list → 0） | — |
| sum_of | `{"sum_of":{"field":"raw_scenes","value":"duration_sec"}}` | 数值求和（`float()` 失败当 0） | 对非 list 求值 |
| fmt_sec | `{"fmt_sec": <expr>}` | 数字 → `"{:.0f}s"`；非法 → `"0s"` | — |
| coalesce | `{"coalesce":[<expr>, …]}` | 返回第一个非空（`""`/`None`/`[]` 算空；`0` **不算**空） | — |

节点级开关（不产出值，只决定节点留不留）：

| 开关 | 语法 | 语义 |
|---|---|---|
| when | `"when": {"field": "key_elements", "non_empty": true}` | 求值 `view` 上该路径；不满足则不输出该节点 |
| skip_if_empty | `"skip_if_empty": "<产出键名>"`（如 `content`/`rows`/`items`） | 产出物里该键为空则不输出该节点 |

**安全约束（写进模块 docstring，review 必查）**：
- 路径解析只 `str.split(".")` + `dict.get`，**禁止** `getattr` / `eval` / `exec` / `__import__` / 下标表达式；
- 求值结果**必须**再过 `sanitize_display_spec(..., contract)`——配方产出的节点与模型产出的节点走同一道闸（防配方注入非法组件）；
- 解释器里**不得出现** `brief` / `plan` / `genui` 字面量（PRD R2）。

---

## 3. 批次详情

### B0 · 契约评审与冻结（0.5 人日，关键路径 · 归属：两仓文档）

#### T0.1 评审 `display_spec` kind
- **文件**：`docs/genui-plugin-migration-prd-2026-11-07.md`（新增 §11「契约评审决议」）、`docs/pluginization-plan-2026-09-10.md`（§12.3 kind 表由 14 行补到 18 行，加 `display_spec` 行）
- **动作**：逐条确认 PRD §3.1 的 N1–N7；把结论与反对意见写进 PRD §11；在 §12.3 表里写明"消费者 = `services/display_spec`"。
- **验收**：
  - `Select-String -Path docs\pluginization-plan-2026-09-10.md -Pattern "display_spec"` 有 1 行以上命中
  - PRD §11 存在且每条有结论（不是"待定"）
- **依赖**：无　**关键路径**：是

#### T0.2 冻结契约与算子
- **文件**：本文件 §2.1–§2.4（如需修订先改这里再改 PRD）
- **动作**：确认本文件的 contract.json / recipe.json / 算子表就是最终形状；任何后续变动必须先改本文件 §2 并在 PRD §10 记一条变更。
- **验收**：PRD §3.1 的 meta schema 与本文 §2.1–§2.3 一致（`Select-String` 对比 `spec_id` / `trust` / `badge_tones` 三个键都出现）
- **依赖**：T0.1　**关键路径**：是

---

### B1 · engine 通用扩展点（2.0 人日，关键路径 · 归属：后端）

> **本批不改变任何生产行为**。产出物没有任何调用点，只有新模块与测试。

#### T1.1 注册新 kind
- **文件**：`clipwright/plugins/contribution.py`
- **动作**：`ContributionKind` 内（`:44` 之后）新增 `DISPLAY_SPEC = "display_spec"`，注释：「纯数据扩展点（第 18 类）：`object` 必须为 `None`，契约与配方全在 `meta`；engine 从不调用插件对象（PRD §3.3）」。同步改 `:23` 的"17 类"为"18 类"。
- **验收**：`py -3.14 -c "from clipwright.plugins.contribution import ContributionKind as K; print(K('display_spec').value)"` → 输出 `display_spec`，退出码 0
- **依赖**：B0　**关键路径**：是

#### T1.2 dispatch 形状校验（**不能靠"未知 kind 默认放行"**）
- **文件**：`clipwright/plugins/contribution_dispatch.py`
- **动作**：
  1. 新增模块级 `_DATA_ONLY_KINDS = {ContributionKind.DISPLAY_SPEC}`（放 `:52` 之后）；
  2. `register()`（`:55`）在 `:64` 的 provider 分支**之前**插入：`if item.kind in _DATA_ONLY_KINDS: return _validate_data_only(item)`；
  3. 新增 `_validate_data_only(item) -> Optional[str]`：`object` 必须 `None`；`meta` 必须 `json.dumps` 成功；`meta["spec_id"]` 非空且 `== item.id`；必填键 `{version, components, containers, actions, tones, badge_tones, text_sizes, limits, source, trust, order}` 齐全；`trust ∈ {"sanitize","recipe_only"}`；`limits` 必须包含 §2.2 的 9 个键且为正整数；任何不满足返回**可读中文原因**（与 `:69-70` 的 provider 报错风格一致）；
  4. `_WIRED_KINDS`（`:32-42`）**不加** `DISPLAY_SPEC`。
- **验收**：
  - `py -3.14 -m pytest tests/clipwright/test_display_spec_plugin.py -q` 全绿（本批新建，见 T1.5）
  - 负例自检：`py -3.14 tests/_scratch_check_display_spec_decl.py`（或直接在测试里）——缺 `limits` → 返回原因含 "limits"
- **依赖**：T1.1　**关键路径**：是

#### T1.3 子进程档直通
- **文件**：`clipwright/plugins/loader.py`
- **动作**：`:526-528` 的 `_DECLARATIVE_KINDS` 加 `ContributionKind.DISPLAY_SPEC`（注释：纯数据，无需代理对象）。
- **验收**：`py -3.14 -c "import clipwright.plugins.loader"` 0；`Select-String -Path clipwright\plugins\loader.py -Pattern "DISPLAY_SPEC"` 命中 1 处
- **依赖**：T1.1　**关键路径**：否（但同批交付）

#### T1.4 契约读取模块
- **文件**（新建）：
  - `clipwright/services/display_spec/__init__.py` — 本批只导出 `Contract` / `normalize_contract` / `list_contracts` / `get_contract`（`sanitize_display_spec` 在 B2-T2.3 加入 `__init__`，`ensure_display_specs` 在 B3-T3.4 加入）
  - `clipwright/services/display_spec/contract.py` — `@dataclass(frozen=True) Contract`（字段同 meta schema）+ `normalize_contract(meta: dict) -> tuple[Contract | None, list[str]]` + `get_contract(spec_id)` / `list_contracts()`（从 `ContributionRegistry.list(ContributionKind.DISPLAY_SPEC)` 读，**无缓存**——注册表本身就是内存表；如需缓存必须挂 `unregister_plugin` 失效）
- **动作**：契约读取**不做 I/O**、不读文件、不 import 插件模块。`spec id` 大小写敏感。
- **验收**：
  - `py -3.14 -c "from clipwright.services.display_spec import list_contracts; print(list_contracts())"` → `[]`（此时还没有插件声明），退出码 0
  - 新测试 `tests/clipwright/test_display_spec_plugin.py::TestContractRead`：注册 fake 贡献后 `get_contract("brief")` 返回 `Contract`；`unregister_plugin` 后返回 `None`
- **依赖**：T1.2　**关键路径**：是

#### T1.5 声明文档 + 单元测试
- **文件**：`clipwright/schema/plugin.py`（`ContributionDecl` docstring `:27-31` 与 `kind` 字段描述 `:33-35`：17→18）、`tests/clipwright/test_display_spec_plugin.py`（新建）
- **动作**：写 10 条用例：①合法注册并 `list_contracts` 可见；②`object` 非 None → 拒；③meta 不可序列化 → 拒；④缺 `limits` → 拒；⑤`trust` 非法值 → 拒；⑥`spec_id != id` → 拒；⑦同 id 二次注册（不同 owner）→ REJECT；⑧`override_builtin` 也不能覆盖 → 拒；⑨子进程声明经 `_register_declarations` 直通（用 `Contribution(kind, id, None, meta)` 模拟）；⑩`_WIRED_KINDS` 不含 DISPLAY_SPEC。
- **验收**：`py -3.14 -m pytest tests/clipwright/test_display_spec_plugin.py -q` → 10 passed；`py -3.14 scripts/verify_provider_pluginization.py -k provider_owners` 不回归
- **依赖**：T1.1–T1.4　**关键路径**：是

---

### B2 · 契约搬家 + 清洗器通用化（3.0 人日，关键路径 · 归属：后端 + 插件 + 跨仓）

#### T2.1 契约文件进插件（含补齐 `badge_tones`）
- **文件**：新建 `plugins/genui/contract.json`（内容 = 本文 §2.2）；`clipwright/schema/genui_contract.json` **暂不删**（B3 删）
- **动作**：把现 `clipwright/schema/genui_contract.json` 的内容 + `badge_tones` + `specs`（两行）迁移过去。
- **验收**：`py -3.14 -c "import json;d=json.load(open('plugins/genui/contract.json',encoding='utf-8'));print(sorted(d));print(d['specs']['plan']['trust'])"` → `['actions','badge_tones','components','containers','limits','specs','text_sizes','tones','version']` / `recipe_only`
- **依赖**：B1　**关键路径**：是

#### T2.2 插件声明两条 `display_spec` 贡献
- **文件**：`plugins/genui/plugin.yaml`、`plugins/genui/main.py`
- **动作**：
  1. `plugin.yaml:12-13` 追加 `- { kind: display_spec, id: brief }` 与 `- { kind: display_spec, id: plan }`；
  2. `main.py`：删掉 `COMPONENTS` 硬编码元组（`:27-30`），改为模块级 `CONTRACT = json.loads((Path(__file__).with_name("contract.json")).read_text(encoding="utf-8"))`（**插件读自己的文件，engine 不读**）；
  3. `contributions()`（`:70-81`）返回 3 条：1 条 `PROMPT`（不变）+ 2 条 `DISPLAY_SPEC`，meta = `{**shared_fields, **CONTRACT["specs"][sid]}`，`shared_fields` = `version/components/containers/actions/tones/badge_tones/text_sizes/limits`；
  4. PROMPT 正文里的组件清单改为 `"、".join(CONTRACT["components"])`，上限数字改为从 `CONTRACT["limits"]` 插值（防文案与契约漂移）。
- **验收**：
  - `py -3.14 -c "from plugins.genui import main as M; print([ (c.kind.value,c.id) for c in M.GenUIPlugin().contributions()])"` → 3 条
  - `py -3.14 -m pytest tests/clipwright/test_genui_spec.py::TestPluginContract -q` 需同步改断言（见 T2.5）
- **依赖**：T2.1　**关键路径**：是

#### T2.3 清洗器通用化（信任边界留 engine）
- **文件**：新建 `clipwright/services/display_spec/sanitizer.py`；修改 `clipwright/schema/genui.py`
- **动作**：
  1. 把 `clipwright/schema/genui.py:98-397`（`_as_text`/`_clip`/`_clean_payload`/`_clean_action`/`_clean_select_options`/`_clean_string_list`/`_clean_list_items`/`_clean_table`/`_clean_steps`/`_clean_node`/`sanitize_spec`）**逐行搬**到 `sanitizer.py`，签名改为 `sanitize_display_spec(raw, contract: Contract, *, source: str = "") -> tuple[dict|None, list[str]]`；
  2. 所有 `GENUI_*` / `MAX_*` 常量引用替换为 `contract.xxx`（组件集合 → `contract.components`，上限 → `contract.limits.max_nodes` …）；
  3. `_clean_action` 的动作白名单 → `contract.actions`；`select` 选项上限 → `contract.limits.max_select_options` / `max_option_chars`；
  4. `clipwright/schema/genui.py` 删掉被搬走的部分，保留 `build_brief_genui`（`:403-437`）/`build_plan_genui`（`:440-480`）/`load_contract`（`:483-493`）/`ensure_brief_spec`/`ensure_plan_spec`/`spec_debug_summary`，并让 `sanitize_spec` 变成**过渡薄壳**：`def sanitize_spec(raw, *, source=""): return sanitize_display_spec(raw, get_contract("brief") or _EMPTY, source=source)`（B3 删除整文件）。
  > 过渡薄壳只服务 `schema/requirements.py:20` 与 `tests` 的既有 import；它**不允许**在生产写入路径继续被调用（B3 全部替换）。
  > **导入方向**：`clipwright/services/__init__.py` 是单行 docstring（已核实，无 import），所以 `schema/` → `services/display_spec` 不构成环；但薄壳里仍用**函数内惰性 import**（与该文件既有的 `logger` 惰性 import 风格一致，`genui.py:490`、`:504`），避免将来 `services/` 增加 import 时把环引进 schema。
- **验收**：
  - `py -3.14 -m pytest tests/clipwright/test_display_spec_sanitizer.py -q` 全绿（新建，见 T2.5）
  - `Select-String -Path clipwright\services\display_spec\sanitizer.py -Pattern "GENUI_|MAX_[A-Z]"` → **0 命中**（证明无语义常量残留）
  - `py -3.14 -c "import clipwright.main"` 0
- **依赖**：T1.4　**关键路径**：是

#### T2.4 verify 脚本扩展（门禁先行）
- **文件**：`scripts/verify_provider_pluginization.py`
- **动作**：
  1. `check_genui_contract_sync`（`:218-235`）：后端路径改 `plugins/genui/contract.json`；
  2. `check_genui_plugin_contract`（`:238-268`）：改名为 `check_display_spec_plugin_contract`；断言 3 条贡献（1 PROMPT + 2 DISPLAY_SPEC）、`len(items)==3`、`declared == actual`、每条 DISPLAY_SPEC 的 `spec_id == id`、`COMPONENTS` 删除后改为 `set(CONTRACT["components"]) == set(shared["components"])`、仍断言"未注册 genui 提示词符号覆盖"；
  3. 新增 `@check("display_spec_contract_frontend_subset")`：读前端 `src/features/genui/contract.json` 的 `components`，断言 ⊆ 前端 `types.ts:17-20` 的常量集合（用正则抓 `GENUI_COMPONENT_TYPES = [...]` 块），并断言 `actions` ⊆ `GENUI_ACTION_KINDS` 块；前端仓库不在本机时打印跳过（沿用 `:230-232` 的既有约定）。
- **验收**：`py -3.14 scripts/verify_provider_pluginization.py -k display_spec` → 退出码 0，3 条 check 全绿
- **依赖**：T2.1–T2.3　**关键路径**：是

#### T2.5 后端测试重组（第一批：清洗器 + 契约）
- **文件**：`tests/clipwright/test_display_spec_sanitizer.py`（新建，从 `test_genui_spec.py::TestSanitize` 迁 12 例 + 补 4 例）；`tests/clipwright/test_genui_spec.py`（`TestContractSingleSource` 改为从 `plugins/genui/contract.json` 读，`TestPluginContract` 改为 3 条贡献）
- **动作**：
  - 12 例逐条保留语义（合法通过/缺版本/未知版本整块丢/未知组件降级/危险键剔除/动作白名单/文本截断/表格列上限/深嵌套丢弃/节点预算/超大整块丢/非对象输入）；
  - 新增 4 例：①契约里没有 `html` → 传 `{"type":"html"}` 必降级为占位；②契约把 `max_nodes` 改成 2 → 第 3 个节点被丢；③契约 `actions` 只有 `ask` → `confirm` 动作被丢；④同一份 raw 在两个不同 contract 下得到不同结果（证明契约驱动，不是硬编码）。
- **验收**：`py -3.14 -m pytest tests/clipwright/test_display_spec_sanitizer.py tests/clipwright/test_genui_spec.py -q` 全绿
- **依赖**：T2.3　**关键路径**：是

---

### B3 · 配方搬家 + 字段泛化 + 指纹（4.5 人日，关键路径 · 归属：后端 + 插件）

> **批内顺序不可调**：T3.1（指纹）必须**先于** T3.5（字段泛化）与 T3.6（写入接线）合入。

#### T3.1 `_brief_hash` 排除整个展示容器（**先做**）
- **文件**：`clipwright/services/requirements_service.py`
- **动作**：`:101-117` 内 `payload = {k: v for k, v in brief.items() if k != "genui"}` → 模块级常量 `_DISPLAY_SPEC_KEYS = frozenset({"genui", "display_specs"})` + `payload = {k: v for k, v in brief.items() if k not in _DISPLAY_SPEC_KEYS}`；注释写明 `genui` 是 2026-11-03~07 落库老键、`display_specs` 是新容器，两者都不得进指纹（C10）。
- **验收**：`py -3.14 -m pytest tests/clipwright/test_display_spec_wiring.py::TestBriefHash -q` 全绿（迁既有 `TestBriefHash` 4 例 + 新增 2 例：注入 `display_specs` 不改 hash；`genui` 与 `display_specs` 同时注入仍不改 hash）
- **依赖**：B2　**关键路径**：是（最高优先）

#### T3.2 recipe 解释器
- **文件**：新建 `clipwright/services/display_spec/recipe.py`
- **动作**：`build_spec_from_recipe(recipe: dict, view: dict, contract: Contract, *, title: str) -> dict | None`；实现 §2.4 的 9 个算子 + `skip_if_empty`；产出节点列表后**必须**调 `sanitize_display_spec({"version":1,"title":title,"items":items}, contract)`；路径解析用 `_get_path(view, "a.b")`（`split(".")` + `dict.get`）；模块 docstring 写死安全约束（禁 `getattr`/`eval`）。
- **验收**：
  - `py -3.14 -m pytest tests/clipwright/test_display_spec_recipe.py -q` → 15 passed（11 算子各 1 + 嵌套 foreach 1 + `when` 1 + `skip_if_empty` 1 + 非法组件被清洗 1）
  - `Select-String -Path clipwright\services\display_spec\recipe.py -Pattern "eval\(|exec\(|getattr\(|__import__"` → 0 命中
- **依赖**：B2　**关键路径**：是

#### T3.3 插件配方（从现有构造器逐节点翻译）
- **文件**：新建 `plugins/genui/recipe.json`（内容 = 本文 §2.3）；`plugins/genui/main.py`（`contributions()` 的 `DISPLAY_SPEC` meta 增加 `recipe` 字段，从 `recipe.json` 读对应 key）
- **动作**：把 `clipwright/schema/genui.py:403-437` 的 brief 节点序列与 `:440-480` 的 plan 节点序列**逐节点**翻译成 §2.3 的形状，包括：brief 的 5 行 key-value 表、`key_elements` 列表（上限 20）、`special_requirements` 的 `；` join（上限 10）、plan 的两个 `stat`、场景表 4 列、`material_summary` callout。
- **验收**：
  - `py -3.14 -c "import json;d=json.load(open('plugins/genui/recipe.json',encoding='utf-8'));print(len(d['brief']['nodes']),len(d['plan']['nodes']))"` → `6 4`
  - `py -3.14 -m pytest tests/clipwright/test_display_spec_recipe.py::TestGenuiRecipes -q` 全绿（用**内联的旧实现期望 JSON 快照**断言，不 import 旧模块）
  - **等价性口径（写进测试注释）**：在「`raw_scenes` 全部为 dict」的输入下，recipe 输出必须与迁移前 `build_brief_genui` / `build_plan_genui` 的输出**结构等价**（节点类型/顺序/列名/文本逐字节相等）。含非 dict 项的极端输入允许一处差异：旧实现按 dict 行计数 `scene_count`，recipe 用 `count(raw_scenes)` 计入非 dict 项 —— 该差异必须用 `pytest.mark.parametrize` 单列一条用例显式记录，不得静默。
- **依赖**：T3.2　**关键路径**：是

#### T3.4 通用装配器
- **文件**：新建 `clipwright/services/display_spec/assemble.py`
- **动作**：`ensure_display_specs(source: str, payload: dict, *, scenes: list | None = None) -> dict`：
  1. 取该 `source` 对应的全部 `display_spec` 契约（`list_contracts()` 过滤 `contract.source == source`）；
  2. 无契约 → 返回 `payload` 不动（禁用插件时的合法状态，记 `info` 日志）；
  3. 对每个契约：`trust=="recipe_only"` → 忽略 `payload["display_specs"]` 里同名项，直接用 recipe；否则先 `sanitize_display_spec(payload["display_specs"][sid])`，空则 recipe；
  4. 写 `payload["display_specs"][sid] = spec`（只碰这一个键）；`spec is None` 时**不写**该键；
  5. 返回值只用于日志：`debug_summary`。
- **验收**：`py -3.14 -m pytest tests/clipwright/test_display_spec_assembly.py -q` → 8 passed（无契约不动 / 有契约补 / recipe_only 忽略模型 / 只碰 display_specs 键 / 异常不阻断 / 多契约按 order / 空 recipe / 契约坏不抛）
- **依赖**：T3.2　**关键路径**：是

#### T3.5 字段泛化 + 形状收口
- **文件**：`clipwright/schema/requirements.py`、`src/types/persona.ts`
- **动作**：
  1. `CreativeBrief.genui`（`:73-83`）→ `display_specs: dict[str, dict[str, Any]] = Field(default_factory=dict)`；validator 只做：值是 dict、键是非空 str、键数 ≤ 8、`json.dumps` ≤ 128KB，否则抛 `ValueError`；**不再 import `sanitize_spec`**（删 `:20`）；
  2. `ProductionPlan.genui`（`:101-107`）同样处理；
  3. `src/types/persona.ts:313`、`:326`：`genui?: unknown` → `display_specs?: unknown`（保持 `unknown`，强制走校验器），并加一行 `/** @deprecated 2026-11-03~07 老数据键，读时归一 */ genui?: unknown;`
- **验收**：`py -3.14 -m pytest tests/clipwright/test_display_spec_wiring.py::TestSchemaShape -q` 全绿；`py -3.14 -c "from clipwright.schema.requirements import CreativeBrief as C; print('genui' in C.model_fields, 'display_specs' in C.model_fields)"` → `False True`
- **依赖**：T3.1　**关键路径**：是

#### T3.6 生产接线 + 读路径 + 删旧文件
- **文件**：`clipwright/services/requirements_service.py`、新建 `clipwright/services/display_spec/legacy.py`、删除 `clipwright/schema/genui.py` 与 `clipwright/schema/genui_contract.json`、`clipwright/agents/requirements_agent.py`
- **动作**：
  1. `:1187` / `:1292`（简报主/修订）：`from clipwright.services.display_spec import ensure_display_specs` → `brief_data = ensure_display_specs("brief", _new_brief)`；
  2. `:2795`（`_translate_plan`）：`ensure_display_specs("plan", result, scenes=scenes)`；
  3. `:2467`（legacy plan）：`ensure_display_specs("plan", payload, scenes=scenes)`；
  4. `_with_genui_specs`（`:3841-3867`）→ `_with_display_specs(session)`：浅拷贝 + 对 brief/plan 各调一次 `ensure_display_specs` + `legacy.normalize(payload)`（把老 `genui` → `display_specs[spec_id]`，仅当 `display_specs` 缺失）；调用点 `:767-785` 改名；
  5. `legacy.py`：`LEGACY_SPEC_KEY = "genui"` + `normalize_legacy(payload, contracts)`（只读不写回，返回新 dict）；
  6. 删除 `clipwright/schema/genui.py`、`clipwright/schema/genui_contract.json`；
  7. `requirements_agent.py`：`:160` 改为 `self._normalize_brief(result["brief_draft"])`；删 `_ensure_brief_genui`（`:164-173`）；`:245/:258/:262-270` 删 `_plan_genui`（该模块生产不可达，只保留字段归一）。
- **验收**：
  - `py -3.14 -m pytest tests/clipwright/test_display_spec_wiring.py -q` 全绿（含 3 条新用例：`_translate_plan` 桩返回带非法 genui → 最终 spec 来自 recipe；`_with_display_specs` 不改入参对象；老会话 `genui` 被归一）
  - `Test-Path clipwright/schema/genui.py` → `False`
  - `py -3.14 -c "import clipwright.main"` 0
  - `Select-String -Path clipwright\services\display_spec\*.py -Pattern "\"brief\"|\"plan\""` → 只允许出现在 `assemble.py` 的日志文案，**不得**出现在 `if` 条件（人工 review）
- **依赖**：T3.3–T3.5　**关键路径**：是

---

### B4 · 前端契约下发 + 挂载迁移（3.0 人日，关键路径 · 归属：前端）

> 可在 B2 完成后启动（契约字段已冻结），与 B3 并行。

#### T4.1 契约获取（复用既有缓存与失效广播）
- **文件**：新建 `src/features/genui/contractSource.ts`
- **动作**：
  - `interface DisplaySpecContract`（含 `spec_id/source/order/trust/components/containers/actions/tones/badge_tones/text_sizes/limits`）；
  - `fetchDisplaySpecContracts(): Promise<Map<string, DisplaySpecContract>>` → `getApiClient().get('/api/plugin/contributions?kind=display_spec')`，从 `contributions[].meta` 组装；30s TTL 缓存在模块级 `Map`；
  - `invalidateDisplaySpecContracts()`；并在模块加载时 `onPluginSlotsInvalidated(...)`（复用 `src/features/plugins/usePluginSlots.ts:42-45`）订阅清缓存；
  - `useDisplaySpecContracts(): Map<string, DisplaySpecContract | undefined>` hook；
  - `resolveDisplaySpecs(obj: unknown, surface: 'brief' | 'plan'): Array<{ specId: string; raw: unknown }>`：优先 `obj.display_specs`（每键一项，键即 specId），否则回退 `obj.genui`（视为 `[{ specId: surface, raw: obj.genui }]`）；
  - 失败静默返回空 Map（插件面不阻塞宿主，沿用 `usePluginSlots.ts:77-79` 语义）。
- **验收**：`npx tsc --noEmit` 0；`npx vitest run src/features/genui` 全绿（新增 `contractSource.test.ts`：成功/失败/缓存/失效/`resolveDisplaySpecs` 四种输入）
- **依赖**：B2　**关键路径**：是

#### T4.2 校验器改为契约驱动
- **文件**：`src/features/genui/validate.ts`
- **动作**：`validateGenUISpec(raw, contract: DisplaySpecContract | null, opts?)`、`hasGenUI(raw, contract)`；删除 `MAX_*` / `GENUI_TONES` / `GENUI_TEXT_SIZES` / `CONTAINER_TYPES` / `GENUI_COMPONENT_TYPES` / `GENUI_ACTION_KINDS` 的硬编码引用（改为读 `contract`）；`contract == null` → 直接返回 `{spec:null, problems:['无可用的 display_spec 契约']}`；`types.ts` 的常量保留但**只作为前端已实现原语的编译期闭包**（供 CI 子集断言与类型收敛），不再当校验规则。
- **验收**：`npx vitest run src/features/genui` 全绿（`genui.test.tsx` 里所有用例改为传入 `contract` fixture）；负例：`validateGenUISpec(validSpec, null).spec === null`
- **依赖**：T4.1　**关键路径**：是

#### T4.3 渲染器接契约 + 未知原语降级
- **文件**：`src/features/genui/GenUISpecRenderer.tsx`
- **动作**：props 增 `contract?: DisplaySpecContract | null`（默认 `null` → 不渲染）；`useMemo(() => validateGenUISpec(rawSpec, contract), [rawSpec, contract])`；未知组件的 `default` 分支保留（渲染 `（暂不支持的组件：x）` 占位，不抛）。
- **验收**：`npx vitest run src/features/genui` 全绿（新增 2 例：契约声明 `mermaid` → 占位文本；契约缺失 → `data-testid="genui-spec"` 不存在）
- **依赖**：T4.2　**关键路径**：是

#### T4.4 三处挂载点迁移
- **文件**：`src/features/agent/AgentPanel.tsx`、`src/features/agent/ReviewPanel.tsx`、`src/layouts/EditorLayout.tsx`
- **动作**：
  - `AgentPanel.tsx:187` / `:196`：`genui={(m.creative_brief as {display_specs?:unknown}).display_specs}` → 改为传整个 `m.creative_brief` / `m.production_plan`（让 `BriefCard`/`PlanCard` 用 `resolveDisplaySpecs` 处理兼容）；
  - `BriefCard`（`:1991-2011`）、`PlanCard`（`:2086-2105`）：用 `useDisplaySpecContracts()` + `resolveDisplaySpecs(obj)`，对每个可解析 spec 渲染一个 `GenUISpecRenderer`（key = specId），`data-testid` 保持 `brief-genui` / `plan-genui`（**测试锚点不能变**）；`scope` 仍是 `"brief"` / `"plan"`；
  - `ReviewPanel.tsx:227-229` / `:284-286`：同样改，保持 `hideActions`；props 从 `planGenui` 改为 `plan?: unknown`（或保留 prop 名但传整个对象，二选一，**选改 prop 名以免误用**）；
  - `EditorLayout.tsx:363`：`planGenui={...}` → `plan={reviewMode === 'plan' ? productionPlan : undefined}`。
- **验收**：
  - `npx tsc --noEmit` 0
  - `npx vitest run src/features/genui src/features/agent` 全绿
  - 新增 3 例（`genui.test.tsx` 或 `AgentPanel.test.tsx`）：①`display_specs` 有值 → `brief-genui` 存在；②只有老 `genui` → 仍渲染；③两者都没有 → 不存在 `brief-genui` 但标题/受众字段仍在
- **依赖**：T4.3　**关键路径**：是

#### T4.5 前端契约副本降级为测试夹具
- **文件**：`src/features/genui/contract.json`、`src/features/genui/genui.test.tsx`
- **动作**：副本内容更新为 `plugins/genui/contract.json` 的逐字节副本；测试用它构造 `Contract` fixture；`genui.test.tsx:186-201` 的 `ContractSingleSource` 改为「fixture 与 `types.ts` 的已实现原语集合一致」（保证"前端能渲染的"与"契约声明的"不脱节），不再断言后端常量。
- **验收**：`npx vitest run src/features/genui` 全绿；`py -3.14 scripts/verify_provider_pluginization.py -k genui_contract_sync` 退出码 0
- **依赖**：T2.1（契约文件）、T4.2　**关键路径**：是

---

### B5 · 禁用验收 + 门禁 + 文档（2.5 人日 · 归属：两仓）

#### T5.1 自检脚本三条 check
- **文件**：`scripts/verify_provider_pluginization.py`
- **动作**：
  1. 新增 `@check("display_spec_kind_registered")`：断言 `ContributionKind("display_spec")` 存在、`_WIRED_KINDS` 不含它、`_DECLARATIVE_KINDS` 含它、默认 profile 下 `list_contracts()` 至少 1 条且 `spec_id` 集合 == `{"brief","plan"}`；
  2. 改写 `check_genui_off_degradation`（`:271-291`）为 `check_display_spec_off_degradation`：monkeypatch `ContributionRegistry` 让 display_spec 贡献消失（模拟插件禁用）→ 断言 ①简报 prompt 仍可用 ②`"GenUI 展示规范" not in prompt` ③`ensure_display_specs("brief", {"title":"T","overview":"O"})` **不含** `display_specs`（口径 A：禁用即无 spec）④`brief["title"]` 未被改动（G1）；
  3. 新增 `@check("display_spec_no_engine_path")`：执行 PRD §8 的 D1/D2/D4（文件不存在、无 `schema.genui` import、无 `GENUI_`/`MAX_SELECT_OPTIONS` 常量），D3 用"命中 ≤5 且 0 处 import"；
- **验收**：`py -3.14 scripts/verify_provider_pluginization.py -k display_spec` → 退出码 0，4 条 check（含 T2.4 的 2 条）全绿
- **依赖**：B3 + B4　**关键路径**：是

#### T5.2 测试文件收口（后端改名 + 双端补齐）
- **文件**：删除 `tests/clipwright/test_genui_spec.py`，其用例按职责分到 `tests/clipwright/test_display_spec_{sanitizer,recipe,wiring,plugin}.py`；前端 `src/features/genui/genui.test.tsx` 保留文件名（前端目录不改名，PRD §7-U6）
- **动作**：逐类搬迁对照表——

| 原类（`test_genui_spec.py`） | 去向 |
|---|---|
| `TestSanitize`（12） | `test_display_spec_sanitizer.py`（B2-T2.5 已做） |
| `TestBuilders`（4） | `test_display_spec_recipe.py::TestGenuiRecipes`（等价性快照） |
| `TestSchemaWiring`（5） | `test_display_spec_wiring.py::TestSchemaShape`（去掉 `test_prompt_template_lists_genui_field`） |
| `TestAgentFallback`（3） | **删除**（口径 A 下无兜底） |
| `TestPluginContract`（4） | `test_display_spec_plugin.py::TestPluginContract`（3 条贡献） |
| `TestBriefHash`（4） | `test_display_spec_wiring.py::TestBriefHash`（+2 例） |
| `TestEnsureSpecWiring`（5） | `test_display_spec_wiring.py::TestEnsureSpecs` |
| `TestPromptPlaceholderAndInjection`（4） | `test_display_spec_wiring.py::TestPrompt`（保留"服务层消费 plugin registry"与"无插件时只用 base"两例） |
| `TestPlanTranslateWiring` | `test_display_spec_wiring.py::TestPlanTranslate` |
| `TestOldSessionBackfill`（4） | `test_display_spec_wiring.py::TestLegacyBackfill`（+1 例：老 `genui` 归一） |
| `TestContractSingleSource`（4） | **删除**（契约单一真相移到插件，改由 verify 脚本 `genui_contract_sync` 守） |
| `TestFallbackAcrossGenres` | `test_display_spec_recipe.py::TestRecipesAcrossGenres`（3 类选题仍出可用结构） |
| `TestSelectAction`（4） | `test_display_spec_sanitizer.py::TestSelectAction` |
- **验收**：`py -3.14 -m pytest tests/clipwright/test_display_spec_sanitizer.py tests/clipwright/test_display_spec_recipe.py tests/clipwright/test_display_spec_wiring.py tests/clipwright/test_display_spec_plugin.py tests/clipwright/test_display_spec_assembly.py -q` 全绿；`py -3.14 -m pytest tests/clipwright -q -x` 无新增红
- **依赖**：B3 + B4　**关键路径**：是

#### T5.3 文档更新（后端）
- **文件**：`docs/genui.md`（重写）、`docs/api_reference.md`、`docs/frontend-backend-parity.md`、`docs/pluginization-plan-2026-09-10.md`（§12.3）、`docs/genui-plugin-coding-plan-2026-11-07.md`（顶部加 `> **已被 2026-11-07 迁移计划取代**（能力已进插件），历史记录仅供追溯`）
- **动作**：
  - `docs/genui.md`：新结构 = ①能力归属表（插件提供什么 / engine 做什么 / 前端做什么，即 PRD §3.7 的表）②契约在哪（`plugins/genui/contract.json` + `recipe.json`）③怎么加组件（4 步 + 前端必须同步）④怎么加 spec_id（3 步，前端不用改）⑤禁用行为（口径 A）⑥两道闸分工 ⑦自检命令
  - `docs/api_reference.md`：加「`display_specs` 字段」小节（形状、上限、与 `GET /api/requirements/session/{id}` 的关系、只读、权威数据在上层）+「`GET /api/plugin/contributions?kind=display_spec`」小节
  - `docs/frontend-backend-parity.md`：加一行「展示 spec：后端 `sanitize_display_spec` ↔ 前端 `validateGenUISpec`；契约 `plugins/genui/contract.json` ↔ `src/features/genui/contract.json`（逐字节）」
  - `docs/pluginization-plan-2026-09-10.md` §12.3：补 `display_spec` 行（消费者 = `services/display_spec`）
- **验收**：`Select-String -Path docs\*.md -Pattern "display_spec"` 在 genui.md / api_reference.md / frontend-backend-parity.md / pluginization-plan 四份里都有命中；`docs/genui.md` 里不再出现「禁用插件后宿主用 build_brief_genui 兜底」
- **依赖**：T5.1　**关键路径**：否（但同批交付）

#### T5.4 两仓 `AGENTS.md` 修订
- **文件**：`J:\Clipwright\AGENTS.md`（#26，`:130-164`）、`J:\Clipweight-Client\AGENTS.md`（对应小节）
- **动作**：#26 改写为——
  1. 能力归属：契约 + 配方 + 规范在 `plugins/genui/`（2 条 `display_spec` + 1 条 `prompt`），engine 只有 `display_spec` 扩展点 + 通用清洗器 + 通用装配器；
  2. 新增不变量引用 `display_spec` kind（第 18 类，纯数据，`object` 必须 `None`）；
  3. 字段名 `display_specs`，老键 `genui` 只读兼容；
  4. **禁用插件 = 没有 GenUI 卡片**（推翻早前 Q8），并写明 G1/G2/G3 三条保证；
  5. 动作三种与"白名单 ≠ 可点"（沿用原文）；
  6. `_brief_hash` 排除 `{"genui","display_specs"}`；
  7. 前端：原语是编译期闭包，新原语必须发前端版本。
- **验收**：`Select-String -Path AGENTS.md -Pattern "display_spec"` ≥3 命中；`Select-String -Path AGENTS.md -Pattern "build_brief_genui"` → 0 命中
- **依赖**：T5.3　**关键路径**：是（文档是交付物的一部分）

---

### B6 · 清理收尾 + 判据执行（1.5 人日 · 归属：后端）

#### T6.1 删除残留转发与死代码
- **文件**：`clipwright/agents/requirements_agent.py`
- **动作**：确认 `grep -rn "translate_scenes\|_ensure_brief_genui\|_plan_genui" clipwright/` 只剩定义（无调用点）→ 删除这些方法与 `:12-13` 的 docstring 里"与 schema/genui.py 共用函数"的描述；`_normalize_brief`（`:175-191`）保留（它做字段归一，与 display spec 无关）。
- **验收**：`py -3.14 -c "import clipwright.agents.requirements_agent"` 0；`grep -rn "genui" clipwright/agents/` → 0
- **依赖**：B5　**关键路径**：是

#### T6.2 清理临时壳与文档残留
- **文件**：`clipwright/schema/genui.py`（若 B3 未删干净）、`clipwright/schema/genui_contract.json`、`tests/` 下任何 `from clipwright.schema.genui import`
- **动作**：`Select-String -Path clipwright -Recurse -Pattern "schema.genui|genui_contract"` → 逐条清掉；`Test-Path` 两文件均 False。
- **验收**：`py -3.14 -m pytest tests/clipwright -q` 无 import 错误；`Test-Path clipwright/schema/genui.py` False
- **依赖**：T6.1　**关键路径**：是

#### T6.3 执行迁移完成判据并留证
- **文件**：`docs/genui.md`（新增「迁移完成证据」节，贴命令与输出）
- **动作**：逐条跑 §4 的判据，把真实输出（含命中行）贴进文档；若 D3 命中 >5，逐条解释为什么必须留。
- **验收**：§4 全部命令退出码 0；文档有贴图/贴文本证据
- **依赖**：T6.2　**关键路径**：是

---

## 4. 迁移完成的判据（怎么证明"engine 里已经没有 genui 专属链路"）

```powershell
cd J:\Clipwright

# ── D1 无专属实现文件 ─────────────────────────────
Test-Path clipwright/schema/genui.py                 # 期望 False
Test-Path clipwright/schema/genui_contract.json      # 期望 False

# ── D2 无专属导入 ────────────────────────────────
Get-ChildItem clipwright -Recurse -Include *.py | Select-String -Pattern "schema\.genui|schema import genui"
# 期望：无输出

# ── D3 字面量只剩老数据键兼容 ─────────────────────
Get-ChildItem clipwright -Recurse -Include *.py,*.json | Select-String -Pattern "genui" -CaseSensitive:$false
# 期望：总命中 ≤ 5，且只允许下面三类（行号以实施后为准）：
#   requirements_service.py:  _DISPLAY_SPEC_KEYS = frozenset({"genui", "display_specs"})
#   requirements_service.py:  # genui = 2026-11-03~07 落库老键，读路径归一
#   display_spec/legacy.py:    LEGACY_SPEC_KEY = "genui"
# 期望：0 处 import、0 处 `if ... == "genui"` 之类的分支

# ── D4 无专属常量 ────────────────────────────────
Get-ChildItem clipwright -Recurse -Include *.py | Select-String -Pattern "GENUI_|MAX_SELECT_OPTIONS|MAX_OPTION_CHARS"
# 期望：无输出

# ── 契约单一真相在插件 ────────────────────────────
Test-Path plugins/genui/contract.json   # True
Test-Path plugins/genui/recipe.json     # True

# ── 行为门禁 ────────────────────────────────────
py -3.14 -m pytest tests/clipwright/test_display_spec_sanitizer.py tests/clipwright/test_display_spec_recipe.py `
                tests/clipwright/test_display_spec_wiring.py tests/clipwright/test_display_spec_plugin.py `
                tests/clipwright/test_display_spec_assembly.py -q       # 期望：全绿
py -3.14 -c "import clipwright.main"                                    # 期望：0
py -3.14 scripts/verify_provider_pluginization.py -k display_spec       # 期望：0（含 genui_contract_sync）
```

```powershell
cd J:\Clipweight-Client

npx tsc --noEmit                                                        # 期望 0
npx vitest run src/features/genui src/features/agent src/features/plugins  # 期望全绿
# 生产代码不得 import 死契约
Get-ChildItem src -Recurse -Include *.ts,*.tsx | Select-String -Pattern "contract.json"
# 期望：仅测试文件命中
```

**一句话判据**：D1–D4 全过 = engine 里只剩"扩展点命名空间 + 通用机制 + 一个老数据键字面量"，没有 genui 实现、没有 genui 常量、没有 genui 分支。

---

## 5. 测试矩阵（谁守哪一侧）

| 断言 | 后端文件 | 前端文件 | 跨仓 |
|---|---|---|---|
| 契约组件白名单（13 类）与宿主一致 | `test_display_spec_plugin.py::TestPluginContract` | `genui.test.tsx::ContractSingleSource`（fixture ↔ 已实现原语） | `verify_provider_pluginization.py::genui_contract_sync`（逐字节） |
| 上限（100/6/4000/64KB/100/12/2000/6/40） | `test_display_spec_sanitizer.py` | `genui.test.tsx`（同一 fixture） | `display_spec_contract_frontend_subset` |
| 未知组件降级为占位 | ✅ | ✅ | — |
| 危险键剔除 | ✅ | ✅ | — |
| 动作白名单 + select 选项 | ✅ | ✅ | — |
| 版本不符整块丢弃 | ✅ | ✅ | — |
| recipe 等价于旧构造器 | ✅（快照） | — | — |
| 前端契约缺失 → 不渲染 | — | ✅ | — |
| 插件禁用 → 无 spec 且字段完好 | ✅（`display_spec_off_degradation`） | ✅（mock 端点空） | — |
| `_brief_hash` 排除整容器 | ✅（`TestBriefHash`） | — | — |
| 老 `genui` 归一 | ✅（`TestLegacyBackfill`） | ✅（老键仍渲染） | — |
| engine 无专属链路 | ✅（`display_spec_no_engine_path`） | — | — |

---

## 6. 回滚矩阵

| 回滚点 | 需要同时回滚的东西 | 回滚后系统状态 | 风险 |
|---|---|---|---|
| **B1** | 无（无调用点） | 回到迁移前，`display_spec` kind 不存在 | 无 |
| **B2** | `plugins/genui/{contract.json,main.py,plugin.yaml}` + `scripts/verify_provider_pluginization.py`（同一次提交） | 清洗器通用化白做，生产仍走 `schema/genui.py`；契约文件回到 engine | 低（无行为变化） |
| **B3** | 只回滚后端；**保留** B2 的插件契约与前端副本 | `schema/genui.py` 复活 + 旧字段 `genui` 复活；插件仍声明 display_spec 但没人消费（`list_contracts` 有值、无调用者） | **需要**：B3 提交里必须**同时**保留 `_with_*` 的双读（`genui` 与 `display_specs`），否则回滚后 B3 期间写入的会话卡片会丢 |
| **B4** | 只回滚前端 | 前端仍读 `genui`，看不到 `display_specs` → 卡片消失（但字段还在） | 中：必须与 B3 的"双读"配合，或用 B4 的 `resolveDisplaySpecs` 兼容层（该层同时读两键，回滚到 B3 状态仍能渲染） |
| **B5** | 文档与测试；**不要**回滚 `verify` 脚本的 check（否则门禁失效） | 文档与代码不一致 | 低 |
| **B6** | 无（只删死代码） | 回到 B5 状态 | 无 |

**回滚铁律**：
1. 每个批次一个提交，提交内**不跨批次**（B2 例外：契约文件 + 插件声明 + verify 脚本必须在同一提交，否则 CI 中间态必红）。
2. B3 之后任何回滚，都要求读路径保留双读（`genui` + `display_specs`）—— 这条兼容代码保留到下一个大版本。
3. 不允许"先删 `schema/genui.py` 再接新装配器"的中间态（会同时断两条链）。

---

## 7. 人日与关键路径汇总

| 批次 | 人日 | 关键路径 | 可并行对象 |
|---|---|---|---|
| B0 | 0.5 | ✅ | — |
| B1 | 2.0 | ✅ | — |
| B2 | 3.0 | ✅ | — |
| B3 | 4.5 | ✅ | B4 |
| B4 | 3.0 | ✅ | B3 |
| B5 | 2.5 | ✅ | — |
| B6 | 1.5 | ✅ | — |
| **合计** | **17.0** | | |

- **单人串行墙钟**：17.0 人日（关键路径 14.0 + B4 未并行时的 3.0）。
- **双人（后端 / 前端）墙钟**：B0+B1+B2 = 5.5 → max(4.5, 3.0) = 4.5 → B5 2.5 → B6 1.5 = **14.0 人日**。
- **风险缓冲**：建议 +15%（≈ 2.5 人日），主要给 R1（插件加载时序）与 R2（解释器边界用例）。**报价按 19.5 人日**。

---

## 8. 执行顺序速查（给下一个 agent 的第一句话）

> 先做 B0 评审（不改代码）；再从 B1 开始——B1 只加扩展点、**不加任何调用点**，`pytest tests/clipwright/test_display_spec_plugin.py -q` 必须绿。B2 把契约搬进 `plugins/genui/contract.json`（**记得补 `badge_tones`**，现有契约漏了它）并把清洗器改成契约驱动，`verify_provider_pluginization.py -k display_spec` 必须绿。B3 的第一件事是 `_brief_hash` 排除 `{"genui","display_specs"}`（**先于**任何写入 `display_specs` 的改动），然后才是 recipe 解释器与字段泛化。B4 可与 B3 并行，但必须先等 B2 的契约字段冻结。B5 的 `check_display_spec_off_degradation` 是"禁用即无 spec"的唯一证据，别写成"禁用仍有兜底"。B6 跑 §4 判据并把输出贴进 `docs/genui.md`。
