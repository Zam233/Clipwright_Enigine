# 声明式展示 spec（`display_spec`）：能力在插件，机制在 engine

> 2026-11-07 能力迁移后的现状说明。设计背景见 [迁移 PRD](genui-plugin-migration-prd-2026-11-07.md) 与
> [coding plan](genui-plugin-migration-coding-plan-2026-11-07.md)（后者是当时的施工计划，最终形态以本文为准）。

## 1. 能力归属（一句话：插件有知识，engine 有机制，前端有渲染器）

| 层 | 拥有什么 | 具体位置 |
|---|---|---|
| **插件**（`plugins/genui/`） | 组件白名单、容器/动作/色调/字号枚举、9 个上限、卡片**配方**、教模型的规范 | `contract.json`、`recipe.json`、`main.py`（2 条 `display_spec` + 1 条 `prompt` 贡献） |
| **engine** | 扩展点 + 通用清洗器 + 通用装配器 + 配方解释器 + 老键归一 | `plugins/contribution.py`（第 18 类 `DISPLAY_SPEC`）、`services/display_spec/{contract,sanitizer,recipe,assemble,legacy}.py` |
| **前端（宿主）** | 契约获取、第二道校验、渲染器、挂载点 | `src/features/genui/{contractSource,validate,GenUISpecRenderer,DisplaySpecsBlock}.*` |

判据（PRD §8）：`clipwright/` 里不再有 genui 的**实现**、**导入**、**常量**；`"genui"` 字面量只剩**老数据键兼容**两处。

## 2. 契约在哪

- `plugins/genui/contract.json`（**单一真相**）：`version/components/containers/actions/tones/badge_tones/text_sizes/limits` + `specs{brief,plan}`。
- `plugins/genui/recipe.json`：卡片配方（**只给 engine**，不下发前端、不进模型提示词）。
- 前端 `src/features/genui/contract.json` 是插件契约的**逐字节副本**，只用于测试夹具与跨仓对账（自检 `display_spec_contract_sync`）；运行期契约来自 `GET /api/plugin/contributions?kind=display_spec`。

`trust` 语义：`sanitize`（模型给了就洗、洗空用配方）/ `recipe_only`（**忽略**模型给的 spec，只用配方——规划书用这个，避免"看着漂亮卡片确认一份 markdown 里没有的方案"）。

## 3. 怎么加一个组件

1. 前端先实现原语：`types.ts` 加节点类型、`validate.ts` 加清洗分支、`GenUISpecRenderer.tsx` 加渲染分支（**必须有独立安全评审**：无 HTML/脚本/外链）；
2. `plugins/genui/contract.json` 的 `components` 加进去（若是容器，同时加 `containers`）；
3. 后端无需改动（清洗器按契约白名单工作）；
4. 自检 `display_spec_contract_frontend_subset` 会校验"契约声明的 ⊆ 前端已实现的"——**先发前端版本**，否则契约能声明但只会渲染成占位文本。

## 4. 怎么加一个 spec_id（新展示面）

1. 插件 `contract.json` 的 `specs` 加一项（`spec_id/source/order/trust`，`recipe_only` 必须配 `recipe.json` 配方）；
2. 插件 `main.py` 的 `contributions()` 多返回一条 `Contribution(DISPLAY_SPEC, "<id>", None, meta)`；
3. 后端在有该 `source` 的写入点调用 `ensure_display_specs("<source>", payload)`（若 `source` 是新的，还需加一个装配源）；
4. **前端不用改**：前端按 `display_specs` 的键遍历、用契约渲染。

## 5. 插件被禁用时（口径 A）

**禁用插件 = 没有展示 spec**（2026-11-07 用户裁定：能力归插件，engine 不再保留任何兜底构造）。三条保证：

- **G1 业务字段不动**：`ensure_display_specs()` 在无契约时**原样返回 payload**、连空容器都不创建；
- **G2 指纹不漂**：`_brief_hash` 排除 `{"genui", "display_specs"}`，展示层永远不影响业务判定；
- **G3 写入不阻断**：装配器里任何异常只记 warning 并跳过该契约，业务写入照常完成。

前端同理：拿不到契约 → `validateGenUISpec(raw, null)` 返回 `spec: null` → 整块不渲染（不猜白名单）。

## 6. 两道闸的分工

| 闸 | 位置 | 职责 |
|---|---|---|
| 第一道（写入） | `services/display_spec/sanitizer.py`（契约驱动、零产品常量） | 未知组件降级为占位、危险键剔除、动作白名单、超限截断/整块丢弃 |
| 第二道（渲染前） | `src/features/genui/validate.ts` | 同样的契约规则再来一遍（旧缓存/历史 session/被改过的响应都不可信） |

**清洗器必须留在 engine**：插件若自带 `sanitize=lambda raw: raw`，后端就不再可信（PRD §3.3）；前端第二道闸不能替代后端。

## 7. 自检命令

```powershell
cd J:\Clipwright
py -3.14 -m pytest tests/clipwright/test_display_spec_sanitizer.py tests/clipwright/test_display_spec_recipe.py `
    tests/clipwright/test_display_spec_wiring.py tests/clipwright/test_display_spec_plugin.py `
    tests/clipwright/test_display_spec_assembly.py tests/clipwright/test_display_spec_prompt.py -q
py -3.14 scripts/verify_provider_pluginization.py -k display_spec   # 6 条 check

cd J:\Clipweight-Client
npx tsc --noEmit
npx vitest run src/features/genui
```

## 8. 迁移完成证据（2026-11-08 实跑）

| 判据 | 命令 | 输出 |
|---|---|---|
| **D1** 无专属实现文件 | `Test-Path clipwright/schema/genui.py` / `genui_contract.json` | 两者 `False` |
| **D2** 无专属导入 | 递归 grep `from clipwright.schema.genui` | **0 命中** |
| **D3** 字面量收口 | 递归 grep `"genui"` | **2 命中**：`services/display_spec/legacy.py:12`（老键常量）、`services/requirements_service.py:103`（指纹排除集合）——两处都是老数据键兼容 |
| **D4** 无专属常量 | 递归 grep `GENUI_` | **0 命中** |
| 自检 | `py -3.14 scripts/verify_provider_pluginization.py` | **19/19 通过**（含 `display_spec_kind_registered` / `display_spec_no_engine_path` / `display_spec_off_degradation`） |
| 后端测试 | `pytest tests/clipwright/test_display_spec_*.py` | **110+ passed**（清洗器 20 / 配方 20 / 接线 19 / 插件 15 / 装配 10 / 提示词 4） |
| 前端 | `vitest run src/features/genui` | **47 passed**（渲染器 32 / 契约源 10 / 挂载块 4 / 其它 1） |

## 9. 与旧文档的关系

- `docs/genui-plugin-prd-2026-11-03.md`（旧 PRD）与 `docs/genui-plugin-coding-plan-2026-11-07.md`（第一轮计划）
  **已被 2026-11-07 迁移计划取代**：当时的能力在 engine、字段叫 `genui`；现在能力在插件、字段叫 `display_specs`。
  两份旧文档保留仅供追溯，不要按它们改代码。
- 本文是**唯一**的当前态说明；`AGENTS.md` #26 是它的浓缩版不变量。
