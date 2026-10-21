# 前后端适配核查（2026-10-22）

> 触发原因：用户要求"检查前端是否适配了后端"。本文是**用可复跑脚本核出来的结论**，
> 不是人工翻代码的印象。前端仓库 `J:\Clipweight-Client`（117 个源文件改动前基线），
> 后端 `J:\Clipwright`。
>
> 一句话结论：**URL 层与方法层已完全对齐（224 条前端调用、0 条 404/405 风险）；
> 字段层查出 1 处真实漂移（`/health` 的组件状态前端整块没接），另有 3 处"后端有
> 能力、前端无入口"的缺口；全部已补并加了防回归测试。**

---

## 1. 怎么核的（三层，都可一键复跑）

```powershell
cd J:\Clipwright
python scripts/check_frontend_contract.py            # 路径 + 方法 + 字段
python scripts/check_frontend_contract.py --json     # 机器可读
python scripts/check_frontend_contract.py --layer fields
```

脚本做三层：

| 层 | 判什么 | 判错的后果 |
|---|---|---|
| **路径** | 前端字符串里的 `/api/...` 是否都在 FastAPI 路由表里 | 404 |
| **方法** | 前端 `.get/.post/.put/.delete/.patch` 是否被该路由允许 | 405 |
| **字段** | 人工登记的核心模型（后端响应 ↔ 前端 TS 接口）字段是否双向覆盖 | 静默 `undefined` |

**本轮先把哨兵修准了**（否则结论不可信）：

1. 早期正则把 `$` 排除在字符类外，导致 `` `/api/x/${id}/y` `` 这类**带参调用一条都没被检查**
   ——最容易写错的那批反而漏检。扫出的路径从 **114 → 224 条**。
2. 模板串里嵌模板串（`` `${a ? `?x=${b}` : ''}` ``）时，`\$\{[^}]*\}` 在内层 `}` 处截断，
   把 `: ''}` 留在路径里 → 造 3 条假 404。改为**花括号配平扫描**（`_collapse_templates`）。
3. 注释里的路径（文档说明）与 `url.includes('/api/asset/')`（判断不是请求）都会被误报，
   已分别用注释剥离与比较上下文识别排除。

对应单测：`tests/clipwright/test_frontend_contract.py`（10 项，含 4 个"必须报准/不许误报"的现场）。

## 2. 核查结果

| 检查项 | 结果 |
|---|---|
| 前端 `/api` 调用 | **224 条**（含模板串参数路径） |
| 后端路由 | 268 条 |
| 路径缺失（必定 404） | **0** |
| 方法不匹配（必定 405） | **0** |
| `HealthResponse` 字段 | ❌ 前端缺 7 个（`mongodb/llm/ffmpeg/hyperframes/queue/queue_pending/queue_running`）→ **已修** |
| `GenreCheckResult` 字段 | ✅ 10/10 一致 |
| `Timeline` 字段 | ✅ 一致 |
| 后端有、前端未调用 | 47 条（信息项：`/api/test/*`、`/api/learning/*`、`/api/market/*` 部分、`/metrics` 等，多为运维/实验端点） |
| 走 `/srv` 的账号/市场调用 | 已排除（属 8090 独立服务，不算本后端缺失） |

## 3. 修掉的 4 处"没适配"

### 3.1 `/health` 的组件状态整块丢失（真漂移）

后端 `/health` 逐项探测 mongo / llm / ffmpeg / hyperframes / 渲染队列，前端 `HealthResponse`
只声明 `{status, service}`，界面只显示"已连接/离线"——**连上了但哪一部分不可用看不见**。

- `src/types/api.ts`：补齐 8 个字段（并注明取值语义）；
- `src/pages/useBackendHealth.ts`：新增 `useBackendHealthDetail()`（保留整份响应）与
  `buildHealthComponents()`（只翻译后端上报的字段，缺字段就不出现）；`useBackendHealth()`
  内部复用它，**仍是单轮询器**；
- 首页「编排引擎」卡片：新增组件 chip 行（`data-testid="health-component-<name>"`），
  状态色 ok/warn/error，队列显示"运行/排队"；
- 测试：`useBackendHealth.test.tsx` +4 项（detail 保留字段、离线不伪造、只翻译上报字段、队列计数）、
  `e2e/home.spec.ts` 新增一条（5 个 chip 可见 + `hyperframes` 显示"未安装"），e2e 的 health mock
  也补成真实形状。

### 3.2 `genreCheck` 客户端有、界面无（V17 后端能力没入口）

后端 `/api/type-maker/{id}/genre-check` 已能判"这像不像该类型"（L1 恒开 + LLM 裁判可选），
前端 `typeMakerApi.genreCheck` 写好了却**没有任何页面调用**。

- `TypeMakerPage` 每张类型卡新增「类型复核」按钮 → 弹层：贴时间线 JSON（带示例）、
  勾选"用 LLM 复核"（默认不勾 = D7 默认关）、运行后展示 `summary`、逐条指标、
  判定说明、以及 `judge === 'mismatch'` 时的裁判证据 JSON；
- 默认**不**下发 `enable_genre_judge`，勾选才下发（与后端 D7 默认一致）；
- 400 的 `detail` 原样展示，不吞异常；
- 示例时间线经后端**真实校验**通过（`scripts/check_frontend_genre_sample.py`：
  `avg_shot_ms=3000 ✓ / text_overlay_ratio=1.0 ✓ / 类型符合度 2/2`）——不是"看起来能跑"的假样例；
- 测试：`TypeMakerPage.test.tsx` +5 项（弹层默认不勾、调用与渲染、勾选才下发、
  非法 JSON 本地拦截不发请求、400 detail 透传）。

### 3.3 `shot_cap_policy`（上界切分）没暴露

后端 D23 拍板后默认 `split`（超长场景切分成多镜，保总时长；V35 就靠它），前端只有下限选择器。

- `projectStore` + 首页创作面板：新增「镜头上限」选择（`data-testid="shot-cap-policy"`，
  默认 `default` = 不下发，跟随后端）；
- `AgentPanel`：非 `default` 才下发 `shot_cap_policy`；
- `AgentPanel.shotFloor.test.ts` 增一条源码级约束（字段/store/setter/下发条件都在）。

### 3.4 演示回退在冒充真实素材源

`AssetPanel` 的搜索演示回退把伪造结果标成 `source: 'pexels'`——后端这轮刚删掉两个假素材源，
前端不该再造一个；而且这些假 id 点"详情/收藏"会直接 404。

- 来源改标 `demo`，id 前缀 `demo_`；
- 结果区顶部提示「演示占位结果（非真实素材）」；
- 演示结果的「详情」「收藏」按钮**禁用**（不再对假 id 发请求）。

## 4. 明确不补的（附理由）

| 项 | 为什么 |
|---|---|
| 47 条"后端有、前端未调" | 多数是运维/实验端点（`/api/test/*`、`/api/learning/*`、`/metrics`、`/api/market/*` 的安装类），产品界面没有对应场景；有场景时再加客户端 |
| 管线运行时开启类型裁判 | 后端支持 `extra_params.enable_genre_judge`，但"开不开裁判"更该在**类型创作**时决定，已放在 3.2 的复核弹层；管线默认关（D7），不额外加开关避免误开成本 |
| `/srv` 下的账号/市场接口 | 属 8090 独立服务（`K:\Clipwright Server`），不在本后端路由表，脚本已整文件跳过 |

## 5. 复跑门禁

```powershell
cd J:\Clipwright
$env:HF_HUB_OFFLINE=1; python -m pytest tests -q     # 含 test_frontend_contract.py
python scripts/check_frontend_contract.py            # 期望：路径/方法/字段三层全 ✅

cd J:\Clipweight-Client
npx tsc --noEmit
npm run test          # 含 useBackendHealth / TypeMakerPage / AgentPanel.shotFloor
npm run lint
npx playwright test   # 含 home.spec 的组件健康用例
```
