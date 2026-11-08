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
| **B7** | 插件外壳化整改（一）：协议类与双注册归位 | 后端 + 插件 | 6 | 10.0 | B0（文档）；可与 B1–B6 并行 | ✅（见 §7） |
| **B8** | 插件外壳化整改（二）：失效钩子 / 渲染器 / 死插件 | 后端 + 插件 + 文档 | 7 | 10.0 | B7（共享 provider 门面与 AGENTS）；B5-T5.4 之后 | ✅（见 §7） |
| | | | **43** | **37.0** | | |

**关键路径**（单人串行）：B0 → B1 → B2 → B3 → B5 → B6 = **14.0 人日**；再串 B7（10.0）+ B8（10.0）
= **34.0 人日**；B4（3.0）在 B2 之后与 B3 并行，不增加墙钟。
**双人并行**（后端 / 前端）：B0+B1+B2（5.5）→ max(B3 4.5, B4 3.0)=4.5 → B5 2.5 → B6 1.5 = **14.0 人日**
墙钟；B7/B8 是后端活，同一后端串行则墙钟 **34.0 人日**，总人日仍 **37.0**（B4 前端并行）。
**三名后端**（一人专做 B7/B8）：墙钟 = max(14.0, 10.0+10.0) = **20.0 人日**，总人日 **37.0**。

> B7/B8 的来源与判定证据见 [`plugin-shell-audit-2026-11-07.md`](plugin-shell-audit-2026-11-07.md)；
> 裁定见 [PRD §12](genui-plugin-migration-prd-2026-11-07.md#12-同类问题排查插件外壳化审计2026-11-07-追加)。
> **B7/B8 与 B1–B6 无文件级交集**（B0–B6 不碰 `services/llm.py` / `services/voice.py` / `providers/` /
> `animation/` / `api/render.py`），因此可以与 B1 之后任意时点并行；**唯有 `AGENTS.md` 必须串行**
> （B5-T5.4 先合，B7-T7.6 / B8-T8.7 后补）。

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

### B7 · 插件外壳化整改（一）：协议类与双注册归位（10.0 人日 · 归属：后端 + 插件）

> **来源**：[`plugin-shell-audit-2026-11-07.md`](plugin-shell-audit-2026-11-07.md) §3.2–§3.5、§4.1、§4.3；
> 裁定见 [PRD §12](genui-plugin-migration-prd-2026-11-07.md#12-同类问题排查插件外壳化审计2026-11-07-追加) R2。
> **本批不改 genui 任何文件**；也不改 B1–B6 的任何交付物（唯一交集是 `AGENTS.md`，见 T7.6 的依赖）。
> **批内顺序**：T7.1–T7.4 可并行（不同 provider 家族），T7.5 **必须最后**（要等所有实现搬完才能删分支），
> T7.6 跟在 T7.5 之后。

#### T7.1 TTS 三协议类搬进插件（3.0 人日）
- **要改的插件文件**：`plugins/tts_qwen/provider.py`（新建）、`plugins/tts_cosyvoice/provider.py`（新建）、
  `plugins/tts_minimax/provider.py`（新建）、三者 `main.py`（factory 改为本模块类）。
- **要改的 engine 文件**：`clipwright/services/voice.py`（删 `QwenTTSProvider:371-454` /
  `CosyVoiceProvider:455-551` / `MiniMaxProvider:552-656`，**保留** `BaseVoiceProvider:328`、
  `VoiceStorage:70`、`PublicUploadManager:209`、`TtsCredentials:303`、`VoiceService`）、
  `clipwright/providers/tts/__init__.py`（`TtsProviderEntry` 增 `default_model` 已存在，不再改动）。
- **动作**：
  1. 三个类逐行搬进各自插件（**不改行为**，只改模块路径）；插件内 `from clipwright.services.voice import BaseVoiceProvider, TtsCredentials`；
  2. `voice.py._model_for():729-734` 删除 → 调用点改 `entry.default_model()`（`_get_provider` 已拿到 entry）；
  3. `voice.py:771` 的 `if provider_name == "qwen-tts"` 改为能力位：`BaseVoiceProvider.build_audio_ref(path) -> str`
     （默认返回本地绝对路径；qwen 覆盖为 base64 data URI）；
  4. 三个插件的 `_default_model()` 保持不变（读 settings 仍是宿主配置，**允许**）。
- **验收**：
  ```powershell
  py -3.14 -c "import clipwright.services.voice as v; print([n for n in ('QwenTTSProvider','CosyVoiceProvider','MiniMaxProvider') if hasattr(v,n)])"   # 期望 []
  Get-ChildItem clipwright -Recurse -Include *.py | Select-String -Pattern 'qwen-tts|cosyvoice|minimax' | Where-Object { $_.Path -notmatch 'config' }
  # 期望：只剩错误文案/别名兼容，0 处 import、0 处 `if provider ==`
  py -3.14 scripts/verify_provider_pluginization.py            # 期望 13/13
  py -3.14 -m pytest tests/clipwright/test_voice_service.py tests/clipwright/test_tts_providers.py -q   # 文件名实施前核对；打桩点改到插件模块
  ```
- **依赖**：无（可与 B1 并行）　**关键路径**：B7 内是

#### T7.2 LLM 两协议类搬进插件（2.0 人日）
- **要改的插件文件**：`plugins/llm_anthropic/provider.py`（新建）、`plugins/llm_openai_compatible/provider.py`（新建）、两者 `main.py`。
- **要改的 engine 文件**：`clipwright/providers/llm/`（删 `anthropic_provider.py` / `openai_provider.py`，
  保留 `providers/base.py` 协议与访问器）、`providers/llm/__init__.py`（删 `ensure_llm_providers_registered()` 的内置注册，
  该函数改为 `no-op` 或删除并同步 `api/providers.py:55,60`、`api/settings.py:77-79` 的调用）。
- **动作**：类搬到插件；`services/llm.py:727`、`:897`、`:950` 的 `elif provider == "anthropic"` 删除
  （`providers/base.py` 的 `llm_normalize_messages` / `llm_tool_result_message` / `llm_tool_schemas`
  已提供最小语义，缺方法时访问器给可读告警）；`config.py:75` 的 `Literal[...]` 改 `str`。
- **验收**：
  ```powershell
  Select-String -Path clipwright\services\llm.py -Pattern 'provider == \"anthropic\"'   # 期望 0
  py -3.14 -c "import clipwright.providers.llm"                                          # 0（或只剩协议 re-export）
  py -3.14 scripts/verify_provider_pluginization.py -k provider_owners                   # owners 仍为两插件
  py -3.14 -m pytest tests/clipwright/test_llm_provider_protocol.py -q
  ```
- **依赖**：无　**关键路径**：B7 内是

#### T7.3 embedding / rerank 实现搬进插件（1.5 人日）
- **要改的插件文件**：`plugins/embedding_providers/providers.py`（新建）、`plugins/rerank_providers/providers.py`（新建）、两者 `main.py`。
- **要改的 engine 文件**：`clipwright/providers/embedding/__init__.py`、`providers/rerank/__init__.py`
  （类搬走；`ensure_*_registered()` 与 `resolve_*` 里的懒注册删除）；`api/providers.py:65-66` 与
  `api/settings.py:83-85` 只读注册表，不再 `ensure_*`。
- **动作**：`RemoteEmbedder` / `SentenceTransformerEmbedder` 的调用封装搬进插件；engine 保留
  `build_http_client`（`providers/transport.py`）与 `parse_rerank_response`（属平台解析）。
- **验收**：
  ```powershell
  py -3.14 -c "from clipwright.providers.embedding import embedding_provider_names as n; print(n())"  # 未启用插件时 []
  py -3.14 -c "from clipwright.providers.rerank import rerank_provider_names as n; print(n())"        # 同上
  py -3.14 -m pytest tests -q -k "provider or embedding or rerank"                                    # 无新增红
  ```
- **依赖**：无　**关键路径**：否（但同批交付）

#### T7.4 STT 后端归位 + `whisper_stt` 收窄（2.0 人日）
- **要改的插件文件**：`plugins/stt_providers/main.py`（成为唯一 owner：探测 + `_call_*`）、
  `plugins/whisper_stt/main.py`（删 `whisper_transcribe_local` 工具，只留 `transcribe_and_align` 技能）。
- **要改的 engine 文件**：`clipwright/providers/stt/__init__.py`（删 `ensure_stt_providers_registered()`
  与两份 `_call_*`）、`services/stt.py`（`:214-283` 的 `_transcribe_whisper/_transcribe_faster_whisper`
  搬进 `stt_providers`；`STTService.align()` 与 `_ensure_audio` 保留）、`tool/transcribe.py`（内置
  `whisper_transcribe` 保留——它是另一条"文件→SRT"的工具面，与 provider 不冲突）。
- **动作**：`resolve_stt_backends()` 只读注册表，禁用插件时返回空 → 调用方按"能力缺失"处理
  （与 PRD §3.6 口径 A 一致）；`stt_providers` 不得再 import engine 的探测函数。
- **验收**：
  ```powershell
  py -3.14 -c "import clipwright.providers.stt as s; print(s.resolve_stt_backends(object()))"  # 未启用插件 []
  py -3.14 -c "from clipwright.tool.registry import ToolRegistry; print(ToolRegistry.get('whisper_transcribe_local'))"  # None
  py -3.14 -m pytest tests -q -k "stt or transcribe"                                           # 无新增红
  ```
- **依赖**：无　**关键路径**：否

#### T7.5 清 engine vendor 分支与枚举（1.0 人日，**必须最后**）
- **要改的 engine 文件**：`services/llm.py`（T7.2 已删）、`services/voice.py`（T7.1 已删）、
  `config.py:75/511/535`、`api/providers.py:76`（别名表搬到 `providers/tts` 或插件）、
  `rag/embedder.py:176,180`、`services/vision.py:163`、`animation/mg/visual_eval.py:165`、`services/agent_loop.py:129`。
- **动作**：把按 id 的分支改为按**能力位/协议方法**；`Literal` 枚举改 `str` + 启动期注册表校验
  （未知值 → `""` + 一条 info 日志，不抛）；别名兼容集中到能力方（插件或 `providers/*` 门面）。
- **验收**：
  ```powershell
  Get-ChildItem clipwright -Recurse -Include *.py |
    Select-String -Pattern '"anthropic"|"ollama"|"cosyvoice"|"minimax"|"qwen-tts"' |
    Where-Object { $_.Path -notmatch 'providers\\' -and $_.Path -notmatch '__pycache__' }
  # 期望 ≤3 处，且全部是错误文案/兼容注释（0 处分支条件、0 处 Literal）
  py -3.14 -c "import clipwright.main"     # 0
  py -3.14 scripts/verify_provider_pluginization.py   # 13/13
  ```
- **依赖**：T7.1–T7.4　**关键路径**：是（B7 的收口）

#### T7.6 门禁与声明文档（0.5 人日）
- **要改的文件**：`scripts/verify_provider_pluginization.py`（新增
  `@check("no_vendor_branches")`：上述 grep 作为 CI 断言）、`AGENTS.md`（#24 补一句"protocol impl lives in
  plugin"）、`docs/pluginization-plan-2026-09-10.md` §12.3（provider 行标注 owner=插件）。
- **动作**：先合 B5-T5.4（它重写 #26），再改本项，避免同文件冲突。
- **验收**：`py -3.14 scripts/verify_provider_pluginization.py -k no_vendor_branches` → 0；
  `Select-String -Path AGENTS.md -Pattern "协议实现"` ≥1。
- **依赖**：T7.5、B5-T5.4　**关键路径**：否（但同批交付）

---

### B8 · 插件外壳化整改（二）：失效钩子 / 渲染器 / 死插件（10.0 人日 · 归属：后端 + 插件 + 文档）

> **来源**：审计 §3.7–§3.15、§4.2、§4.4、§4.5、§3.10。
> **批内顺序**：T8.1/T8.2 可并行；T8.5 依赖 T8.4（同改 `api/render.py`）；T8.6 依赖 T8.5（hook 契约）。
> **开工前需拍板**：审计 §7 的 Q1（平台预设禁用后是否保留）、Q3（cloud_render 删还是搬）。
> Q1 未定则 T8.4 只做"engine 两份表合一 + 补 hook 入参"，不动"禁用后是否保留"。

#### T8.1 `headless_chrome` 实现搬进插件（2.0 人日）
- **要改的插件文件**：`plugins/headless_chrome/shell.py`（新建，承接 `find_shell/install_shell/
  install_by_download/probe_version/apply_env`）、`main.py`（`renderer_backend` 的 object 改为本模块实现）。
- **要改的 engine 文件**：`clipwright/services/headless_browser.py`（缩为 facade：读贡献 → 调方法；
  删 `HEADLESS_SHELL_ID:30`；`_system_chrome_path` 保留）、`api/render.py:1155-1183`（端点改调 facade，
  URL 不变）。
- **动作**：`renderer_backend` 合同扩展为 `{resolve(), install(), apply_env(env), probe_version()}`；
  `find_shell()` 遍历注册表而不是认死 id。
- **验收**：
  ```powershell
  Get-ChildItem clipwright -Recurse -Include *.py | Select-String 'chrome_headless_shell'    # 期望 0
  py -3.14 -c "from clipwright.services import headless_browser as h; print(len(open(h.__file__,encoding='utf-8').readlines()))"  # 期望 < 120（facade）
  # 运行期：禁用插件后 GET /api/render/browser 的 mode == system-chrome-headless-cpu
  ```
- **依赖**：无　**关键路径**：否

#### T8.2 `logic_animations` 渲染器归位（2.0 人日）
- **要改的插件文件**：`plugins/logic_animations/diagrams/`（从 engine 搬 11 个渲染函数：
  `_radar/_gantt/_venn3/_heatmap/_sankey/_concept/_codeblock/_datatable/_quote/_compcard/_orgchart`）、
  `main.py`（`_DESCRIPTIONS` 与 prompt 恢复 12 种；manifest 描述同步）。
- **要改的 engine 文件**：`clipwright/animation/diagram_svg.py`（删 11 个渲染函数与
  `renderer_map` 里的对应项；`:301-305` 的 presets 列表改由注册表动态合并；保留 `_inject_motion`
  与 `render()` 分派）。
- **动作**：搬移按**逐函数对照**做，行为等价（旧测试可作快照）；搬完 engine 侧 `renderer_map`
  只剩 13 个"真正内置"类型（diagram/comparison/sequence/timeline/tree/venn/bar/pie/line/
  sequence_diagram/flow_chart 等）。
- **验收**：
  ```powershell
  Select-String -Path clipwright\animation\diagram_svg.py -Pattern 'def _radar|def _gantt|def _sankey|def _heatmap'   # 0
  py -3.14 -c "from clipwright.animation.diagram_svg import DiagramRenderer as R; print(len(R.get_supported_presets()))"  # 插件禁用时减少 12
  py -3.14 -m pytest tests -q -k "diagram"     # 无新增红
  ```
- **依赖**：无　**关键路径**：否

#### T8.3 `kinetic_typography` 失效修复（1.0 人日）
- **要改的插件文件**：`plugins/kinetic_typography/main.py`（删 `ctx["catalog"]` 死钩子与 CSS 字符串；
  6 个动画改写成带 keyframes 的 `AnimationDef`，`gradient_fill` / `perspective_3d` 按审计 §3.11 下线并在
  manifest 说明）。
- **要改的 engine 文件**：无（`animation/renderer.py:70-75` 的通用渲染已够用）。
- **验收**：`Select-String -Path plugins\kinetic_typography\main.py -Pattern 'catalog'` → 0；
  `py -3.14 -c "from clipwright.animation.registry import AnimationRegistry as A; print([(d.animation_id, len(d.keyframes)) for d in A.list_by_plugin('kinetic_typography')])"` → 每个非空。
- **依赖**：无　**关键路径**：否

#### T8.4 `lottie_animations` 降级（0.3 人日）
- **要改的插件文件**：`plugins/lottie_animations/main.py`（删两个形状错误的 hook 与外链 CDN 模板；
  只保留 prompt + 本地目录说明；manifest description 改为"本地 Lottie 素材目录（渲染未接入）"）。
- **要改的 engine 文件**：无。
- **验收**：`Select-String -Path plugins\lottie_animations\main.py -Pattern 'cdnjs|renderers'` → 0；
  `GET /api/plugin/contributions` 中该插件只剩 `prompt`。
- **依赖**：无　**关键路径**：否

#### T8.5 `platform_export` 真接线 + hook 契约表（1.5 人日）
- **要改的插件文件**：`plugins/platform_export/main.py`（6 个预设改以 `export_preset` kind 贡献，
  meta 带 `width/height/fps/bitrate/thumbnail_count/vertical`；PRE/POST hook 改读新入参）。
- **要改的 engine 文件**：`api/render.py`（`:603` PRE_RENDER 入参补 `{"task_id","settings","preset"}`；
  `:645-648` POST_RENDER 补 `preset`；`list_presets`/`_resolve_settings` 合并"内置 + `export_preset` 贡献"；
  删 `:973-976 _PLATFORM_EXPORT_COUNTS` 的平台项）。`docs/pluginization-plan` §12.3 补
  `export_preset` 的消费者（现在这张表里该 kind **零消费方**）。
- **动作**：Q1 若定"禁用即无平台预设"，则 `_EXPORT_PRESETS:1091` 只留 `default/draft` 等非平台项；
  若定"保留基础 3 个"，则保留并标注为内置 fallback。
- **验收**：
  ```powershell
  Select-String -Path clipwright\api\render.py -Pattern '_PLATFORM_EXPORT_COUNTS'   # 0
  # 运行期：GET /api/render/presets 的 keys == 内置 ∪ 插件；禁用插件后按 Q1 结果变化
  ```
- **依赖**：T8.4 无关；依赖 Q1 拍板　**关键路径**：否（但用户可见性最高）

#### T8.6 `cloud_render` 清理 + 双注册收口（1.2 人日）
- **要改的插件文件**：删除 `plugins/cloud_render/`（Q3 选 A）。
- **要改的 engine 文件**：无（`services/remote_render.py` 保留，`api/render.py:225-227` 保留——
  远程渲染仍由 `remote_render_url` 驱动）；`docs/pluginization-plan` §12.3 标注"远程渲染暂为宿主能力"。
- **动作**：顺带把 T7.3/T7.4 遗留的"双注册"断言固化：`verify` 脚本新增
  `@check("no_duplicate_builtin_registration")`（扫描 `ensure_*_registered` 是否仍在注册同一批 id）。
- **验收**：`Test-Path plugins/cloud_render` → False；
  `Select-String -Path clipwright -Recurse -Include *.py -Pattern 'cloud_render'` → 0；
  `py -3.14 scripts/verify_provider_pluginization.py -k no_duplicate` → 0。
- **依赖**：B7-T7.3 / T7.4　**关键路径**：否

#### T8.7 `mg_animations` / `local_material` / `voice_ext`（2.0 人日）
- **要改的插件文件**：`plugins/mg_animations/main.py`（`AnimationDef.properties_meta["mg_json"]` 携带 JSON 全文）、
  `plugins/local_material/local_folder.py`（新建，承接 `LocalFolderSource`）、
  `plugins/voice_ext/main.py`（新增 `tts_provider` 贡献：ElevenLabs/Azure 改 `BaseVoiceProvider` 子类，
  `extended_tts` 工具保留为兼容别名）。
- **要改的 engine 文件**：`animation/mg_renderer.py:591-647`（删三条 `plugins/...` 路径，改从
  `AnimationRegistry` 读 `mg_json`）、`material/local_source.py`（删类与 `source_from_env`；
  `material/__init__.py:13` 去导出）、`services/voice.py`（无需改，provider 面已通用）。
- **验收**：
  ```powershell
  Select-String -Path clipwright\animation\mg_renderer.py -Pattern 'plugins'          # 0
  py -3.14 -c "import clipwright.material as m; print(hasattr(m,'LocalFolderSource'))"  # False
  # 运行期：GET /api/providers 的 tts 列表含 elevenlabs/azure
  ```
- **依赖**：B7（provider 面）　**关键路径**：否
- **同任务内顺带收尾（0.2 已含在 2.0 里）**：`AGENTS.md` #26 增一句「插件 prompt 不得替 engine 宣称能力
  （`logic_animations`/`kinetic_typography`/`lottie_animations` 曾犯）」；#19 增一句「`web_search`/`web_fetch`
  仍是内置工具，插件化另立目标」。验收：`Select-String -Path AGENTS.md -Pattern "不得替 engine 宣称"` ≥1。

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
| **B7** | 单任务回滚（T7.1–T7.4 各自独立）：插件文件 + 被删的 engine 类**必须同一次提交**；T7.5 最后合 | 协议类回到 engine、插件回到"只有注册"的半壳态；功能不断（engine 类还在） | 低（无数据面变更） |
| **B8** | 按任务回滚；T8.5 涉及 `api/render.py` 与 3 个插件，必须同提 | 回到 B7 状态：钩子继续空转、渲染器回 engine | 中：T8.2 的渲染器搬家若只回滚一半，`preset` 会两边都找不到 → 先回滚 engine 侧再回滚插件侧 |

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
| B7 | 10.0 | ✅（B6 之后；或与 B1+ 并行） | 与 B1–B6 无文件交集 |
| B8 | 10.0 | ✅（B7 之后） | 与 B1–B6 无文件交集；T8.1–T8.4 可并行 |
| **合计** | **37.0**（43 任务） | | |

- **单人串行墙钟**：**37.0 人日**（原 17.0 + B7 10.0 + B8 10.0；其中 B4 的 3.0 可与 B3 并行，故关键路径 34.0）。
- **双人（后端 / 前端）墙钟**：原 B0–B6 = 14.0；B7/B8 是后端活，同一后端串行 → **34.0 人日** 墙钟；
  总人日仍 **37.0**（B4 与 B3 并行不增墙钟）。
- **三名后端**（一人专做 B7/B8）：墙钟 = max(14.0, 10.0+10.0) = **20.0 人日**，总人日 **37.0**。
- **风险缓冲**：建议 +15%（≈ 5.5 人日），主要给 R1（插件加载时序）、R2（解释器边界用例）、
  B7 的协议类搬移（打桩点迁移）与 B8-T8.5（hook 入参契约变动影响 3 个插件）。
  **报价按 42.5 人日**（原 19.5 + 23.0）。

> 与 §1 的口径一致：**任务数 30 → 43，人日 17.0 → 37.0，单人墙钟 17.0 → 37.0，双人墙钟 14.0 → 34.0**。
> 只做 genui 迁移（B0–B6）的报价仍是 **19.5 人日**；B7/B8 另立合同/里程碑更清楚，不要混进首付款。

---

## 8. 执行顺序速查（给下一个 agent 的第一句话）

> 先做 B0 评审（不改代码）；再从 B1 开始——B1 只加扩展点、**不加任何调用点**，`pytest tests/clipwright/test_display_spec_plugin.py -q` 必须绿。B2 把契约搬进 `plugins/genui/contract.json`（**记得补 `badge_tones`**，现有契约漏了它）并把清洗器改成契约驱动，`verify_provider_pluginization.py -k display_spec` 必须绿。B3 的第一件事是 `_brief_hash` 排除 `{"genui","display_specs"}`（**先于**任何写入 `display_specs` 的改动），然后才是 recipe 解释器与字段泛化。B4 可与 B3 并行，但必须先等 B2 的契约字段冻结。B5 的 `check_display_spec_off_degradation` 是"禁用即无 spec"的唯一证据，别写成"禁用仍有兜底"。B6 跑 §4 判据并把输出贴进 `docs/genui.md`。
>
> **B7 与 B1–B6 的关系**：B7/B8 是**另立批次**，与 B0–B6 **没有文件级交集**（B0–B6 不碰
> `services/llm.py` / `services/voice.py` / `providers/` / `animation/` / `api/render.py`），
> 所以 B7 可以在 B1 合入后的任意时点开工；但**唯一必须串行的是 `AGENTS.md`**——B5-T5.4 先合，
> B7-T7.6 与 B8-T8.7 后补，否则同文件冲突。若单人串行，建议 B6 → B7 → B8（B8 至少两处依赖 B7：
> provider 门面与 `verify` 脚本）。**B6 的 §4 判据在 B7/B8 之后要重跑**：B7 会删
> `services/voice.py` 的 provider 类、B8 会删 `services/headless_browser.py` 的实现体，
> 这两份删除**不能**被误判成"genui 回退"。B7/B8 的判定证据与整改理由见
> [`plugin-shell-audit-2026-11-07.md`](plugin-shell-audit-2026-11-07.md)。

---

## 9. 实施进度（滚动更新）

> 只记"哪条任务真的落地了 + 证据"，细节仍看 §3。目标态：PRD §8 的 D1–D4 全部可跑通。

| 批次/任务 | 状态 | 落地内容 | 证据 |
|---|---|---|---|
| **B0** T0.1 / T0.2 | ✅ 已完成 | PRD §11 契约评审决议（N1–N7 全通过、无待定）；`docs/pluginization-plan` §12.3 kind 表 14 → 18 行（顺便补回漏登的 animation/embedding/rerank） | `Select-String docs\pluginization-plan-2026-09-10.md -Pattern display_spec` → 2 行命中 |
| **B1** T1.1 | ✅ 已完成 | `ContributionKind.DISPLAY_SPEC = "display_spec"`（第 18 类，注释写明 object 恒 None、engine 不执行插件对象） | `py -3.14 -c "from clipwright.plugins.contribution import ContributionKind as K; print(K('display_spec').value)"` → `display_spec` |
| **B1** T1.2 | ✅ 已完成 | `contribution_dispatch._DATA_ONLY_KINDS` + `_validate_data_only()`（11 类形状校验：object None / meta 可序列化 / spec_id==id / trust / source / version·order 整数 / 六组枚举非空字符串 / containers ⊆ components / limits 9 键正整数 / recipe_only 必须有 recipe），并**先于** provider 分支执行、显式拒绝 override | `_WIRED_KINDS` 不含 DISPLAY_SPEC（测试断言）；`_DATA_ONLY_KINDS == {display_spec}` |
| **B1** T1.3 | ✅ 已完成 | `loader._DECLARATIVE_KINDS` 加 `DISPLAY_SPEC`（子进程档直通，纯数据无需代理对象） | `Select-String loader.py -Pattern DISPLAY_SPEC` → 2 行命中；`py_compile` 0 |
| **B1** T1.4 | ✅ 已完成 | 新模块 `clipwright/services/display_spec/{__init__,contract}.py`：`Contract` 冻结 dataclass + `normalize_contract()` + `get_contract()` / `list_contracts()`（读 `ContributionRegistry`，**不缓存、不读文件、不 import 插件**）；必填键常量从 dispatch 共享，避免第二份真相 | `list_contracts()` → `[]`（此时还没有插件声明） |
| **B1** T1.5 | ✅ 已完成 | `schema/plugin.py` 文档 17→18 类；新建 `tests/clipwright/test_display_spec_plugin.py`（**15 例**：读取/卸载隐藏、8 类形状拒绝、冲突与 override 拒绝、wired 不变量、归一纯函数性） | `py -3.14 -m pytest tests/clipwright/test_display_spec_plugin.py -q` → **15 passed**；插件相关 8 个文件合计 **166 passed / 2 failed**（2 个是既存 `test_plugin_review_fixes`）；自检 **16/16**；`import clipwright.main` ok |

**本批不改变任何生产行为**（没有任何调用点），与计划 §3 B1 的声明一致。

### Batch 2 进度

| 任务 | 状态 | 落地内容 | 证据 |
|---|---|---|---|
| **T2.1** 契约文件进插件 | ✅ 已完成 | 新建 `plugins/genui/contract.json`：补齐漏掉的 `badge_tones`、新增 `specs.map`（brief=`sanitize`/100，plan=`recipe_only`/110）；engine 的 `genui_contract.json` 暂留（B3 删） | 插件契约键 = actions/badge_tones/components/containers/limits/specs/text_sizes/tones/version |
| **T2.2** 两条 display_spec 贡献 | ✅ 已完成 | `plugin.yaml` 加 `display_spec: brief/plan`；`main.py` 删掉 `COMPONENTS` 硬编码，改为插件自读 `contract.json` + `recipe.json`；`contributions()` 返回 **3 条**（1 PROMPT + 2 DISPLAY_SPEC，`object=None`）；PROMPT 的组件清单与上限数字全部从契约插值 | `GenUIPlugin().contributions()` → `[('prompt','genui_requirements_prompt'), ('display_spec','brief'), ('display_spec','plan')]` |
| **T2.3** 清洗器通用化 | ✅ 已完成 | 新建 `services/display_spec/sanitizer.py`：闭包捕获 `contract.components/actions/tones/badge_tones/text_sizes/limits`，**零产品常量**（大小写敏感 grep 0 命中）；`schema/genui.py` 只留过渡薄壳 `sanitize_spec()`（按注册表契约清洗，无契约则整块不可用 + 可读原因）与两个构造器（构造器新增 `_self_sanitize()`：无契约时原样返回，B3 随构造器一起删） | `Select-String sanitizer.py -Pattern "GENUI_|MAX_[A-Z]" -CaseSensitive` → **0**；`import clipwright.main` 0 |
| **T2.4** 门禁先行 | ✅ 已完成 | verify 脚本：`genui_contract_sync` → `display_spec_contract_sync`（比对 `plugins/genui/contract.json` ↔ 前端副本）；`genui_plugin_contract` → `display_spec_plugin_contract`（3 条贡献 + 每条契约过 `normalize_contract` + recipe 非空 + plan trust=recipe_only）；新增 `display_spec_contract_frontend_subset`（契约 components/actions ⊆ 前端已实现集合） | 自检 **17/17**（原 16 + 新增 1；两项改名） |
| **T2.5** 测试重组 | ✅ 已完成 | 新建 `tests/clipwright/test_display_spec_sanitizer.py`：12 例迁自 `TestSanitize` + 4 例契约驱动（无 html→降级、max_nodes=2→第 3 个丢、actions=[ask]→confirm 丢、同 raw 两契约不同结果）；`test_genui_spec.py` 改为**显式注册插件契约**（fixture，验证真实 meta 能过校验）+ 契约单一真相改读插件文件 + 插件断言 3 条贡献/含 badge_tones | 后端 4 文件 **114 passed**；requirements 全族 **103 passed / 4 failed**（4 个全是既存失败） |
| **T3.3 提前** | ⚠️ 顺序调整 | `plugins/genui/recipe.json` 必须与 B2 一起落：`trust=recipe_only` 在注册期就要求 `recipe.nodes`，否则插件加载会把 `plan` 贡献判为非法（B1 校验器的严格性使然）。配方只是数据，解释器仍在 B3-T3.2 | `plan` 契约注册通过，recipe 6+3 节点 |

**前端**：`src/features/genui/contract.json` 已同步为插件契约的逐字节副本（`display_spec_contract_sync` 通过）；`npx tsc --noEmit` 0；`npx vitest run src/features/genui` **30 passed**（运行期仍用 `validate.ts` 常量，B4 改为端点下发）。

### Batch 3 进度（进行中）

| 任务 | 状态 | 落地内容 | 证据 |
|---|---|---|---|
| **T3.1** `_brief_hash` 排除整个展示容器 | ✅ 已完成 | `_DISPLAY_SPEC_KEYS = frozenset({"genui", "display_specs"})`，指纹按显式键集合剔除（不用"值像 spec"的启发式）；只剩展示层时指纹为空 | `test_display_spec_wiring.py::TestBriefHash` **7 passed**（含注入 `display_specs`、双键同时注入、spec 值变化不改指纹） |
| **T3.2** recipe 解释器 | ✅ 已完成 | 新建 `services/display_spec/recipe.py`：11 算子（literal/field/item/index_plus_one/join/pairs/foreach/count/sum_of/fmt_sec/coalesce）+ 节点开关 `skip_if_empty`/`when` + `field`/`item` 可选 `limit`；未知算子抛 `RecipeError`，**无 eval/exec/getattr**；产出必须过 `sanitize_display_spec` | `test_display_spec_recipe.py` **20 passed**（11 算子 + 嵌套 foreach + when + skip_if_empty + 未知算子 + 非法组件被清洗） |
| **T3.3** 插件配方等价性 | ✅ 已完成 | 插件 `recipe.json` 的 brief/plan 输出与迁移前 `build_brief_genui` / `build_plan_genui` **逐字节相等**（实测 `old == new` 均为 True）；已知差异（非 dict 场景行：`count` 计入、但产出一行空值）用独立用例显式固定 | `TestGenuiRecipes` 4 例 |

| **T3.4** 通用装配器 | ✅ 已完成 | 新建 `services/display_spec/assemble.py`：按 `source` 取契约（按 order）、无契约原样返回、`recipe_only` 忽略模型 spec、只写 `display_specs` 一个键、异常只记 warning 不阻断。 | `test_display_spec_assembly.py` **10 例** |
| **T3.5** 字段泛化 + 形状收口 | ✅ 已完成 | `CreativeBrief` / `ProductionPlan` 的 `genui` → `display_specs: dict[str, dict]`；validator 只做形状收口（值必须 dict、键非空、≤8 项、≤128KB），**不再 import 任何插件知识**；前端 `src/types/persona.ts` 同步（`display_specs` + `@deprecated genui`） | `TestSchemaShape` 3 组（含 4 个负例）；`tsc --noEmit` 0 |
| **T3.6** 生产接线 + 读路径 + 删旧文件 | ✅ 已完成 | 4 处服务接线改为 `ensure_display_specs("brief"/"plan", …)`；`_with_genui_specs` → `_with_display_specs`（老键经 `legacy.normalize_legacy` 归一、浅拷贝、只补不存）；Agent 删掉两个转发方法；**删除 `clipwright/schema/genui.py` 与 `genui_contract.json`**；自检 `genui_off_degradation` → `display_spec_off_degradation`（A6 口径） | `TestReadPath` 4 例 + `TestTranslatePlanWiring` 2 例；自检 **17/17** |

**PRD §8 判据实测（D1–D4）**

| 判据 | 命令 | 结果 |
|---|---|---|
| D1 无专属实现文件 | `Test-Path clipwright/schema/genui.py` / `genui_contract.json` | 两者 **False** |
| D2 无专属导入 | 递归 grep `from clipwright.schema.genui` / `schema.genui` | **0 命中** |
| D3 字面量收口 | 递归 grep `"genui"` | **2 处**，均为老数据键兼容（`legacy.LEGACY_SPEC_KEY`、`_brief_hash` 的 `_DISPLAY_SPEC_KEYS`） |
| D4 无专属常量 | 递归 grep `GENUI_` | **0 命中** |

**engine 侧现在的 `display_spec` 模块（全部通用、无产品知识）**：`contract.py` 148 行、`sanitizer.py` 372 行、`recipe.py` 256 行、`assemble.py` 80 行、`legacy.py` 26 行。

> ⚠️ **过渡期可见回归**：B3 之后后端写 `display_specs`，而前端仍读 `genui` —— 因此卡片的结构化视图在 B4 完成前会**消失**（数据仍在库里，只是没渲染）。B4 必须紧接着做。

### Batch 4 进度（前端契约下发与挂载迁移）

| 任务 | 状态 | 落地内容 | 证据 |
|---|---|---|---|
| **T4.1** 契约获取 | ✅ 已完成 | 新建 `src/features/genui/contractSource.ts`：`GET /api/plugin/contributions?kind=display_spec` → `Map<specId, DisplaySpecContract>`，30s TTL + 在途去重 + 复用 `onPluginSlotsInvalidated` 失效广播；失败静默空 Map；`resolveDisplaySpecs(obj, surface)` 优先 `display_specs`、回退老键 `genui` | `contractSource.test.ts` **10 例**（解析/跳过坏条目/缓存/失效/失败/四种 resolve 输入） |
| **T4.2** 校验器契约驱动 | ✅ 已完成 | `validate.ts` 重写为契约驱动：组件/动作/色调/字号/9 个上限全部来自 `contract`；**无契约 → `spec: null` + 可读原因**；`MAX_*` 常量保留但只作"前端已实现原语"的闭包；契约声明但前端未实现的原语 → 占位文本（不再误落 accordion 分支） | `tsc --noEmit` 0；genui 测试改为传契约 fixture（内含"契约声明 mermaid → 占位""无契约 → 不渲染"两例） |
| **T4.3** 渲染器接契约 | ✅ 已完成 | `GenUISpecRenderer` 新增 `contract` prop，`useMemo` 按契约校验；契约缺失整块不渲染 | 同上 |
| **T4.4** 三处挂载点 | ✅ 已完成 | 新增 `DisplaySpecsBlock`（解析 → 取契约 → 逐条渲染，`data-testid` 保持 `brief-genui`/`plan-genui`）；`BriefCard`/`PlanCard` 改为传**整个业务对象**，`ReviewPanel` 的 `planGenui` → `plan` 且保留 `hideActions`，`EditorLayout` 传 `productionPlan` | `DisplaySpecsBlock.test.tsx` **4 例**（display_specs / 老 genui / 都没有 / 契约拉不到）；genui+agent+layouts **12 文件 135 passed / 5 skipped** |
| **T4.5** 契约副本降级为夹具 | ✅ 已完成 | `src/features/genui/contract.json` 仍是插件契约的逐字节副本（跨仓 `display_spec_contract_sync` 守）；测试用它构造 `CONTRACT` fixture | 自检 17/17 |
