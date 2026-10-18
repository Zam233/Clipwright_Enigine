# MV/PV 能力开发计划（据 `mv-pv-prd-2026-10-18.md` v1.2 拆解）

> 一句话：**先证伪，再铺开**。用 2 天 + 1 周证明"确定性场景脚本 + 三道闸"这条路走得通（门 1），再按三条并行轨道铺开 50 人日的工作；任何一道门不过，就地停或缩，不硬推。

- 状态：可执行草案
- 日期：2026-10-18
- 依据：`docs/mv-pv-prd-2026-10-18.md`（Part I 机制 / Part II 能力 / Part III 覆盖审计）
- 需求编号引用：PRD 的 `D1–D22`（决策）、`F1–F14`（功能）、`C1–C13`（能力）、`M0–M8`（里程碑）、`V1–V60`（验收）

---

## 0. 一页结论

| 项 | 结论 |
|---|---|
| 总工作量（WBS 明细求和） | **≈66 人日**：后端 A（渲染）26.5 ＋ 后端 B（Agent/规划/素材）26 ＋ 前端 8 ＋ 内容/美术 6 |
| 与 PRD 粗估的差异 | PRD 写 "Part I 12 + Part II 34–38 ≈ 46–50"，此处 **60 人日（开发）+ 6（内容）**。差异原因：① 前端（8）与素材线（5.5–6）在 PRD 里没单列为独立工作量；② 渲染任务被拆细（R-01…R-19），细粒度估算通常高于里程碑级粗估。**以本表为准**，PRD 的 34–38 作为量级参考 |
| 工期（建议配置） | **2 后端 + 1 前端 + 0.5 内容 ≈ 10–11 周**（3 人 × 10 周 = 150 人日产能，实际负载约 45–55%，缺口是联调/返工/验收缓冲）；1 后端 + 0.5 前端 ≈ 18–20 周 |
| 关键路径 | `Spike → M0(P0 并行) → M1 → M3 → 门 3 → M6/M8 → M7 → 交付` |
| 三道门（不可跳过） | **门 1** 渲染可行（W2）→ **门 2** 画面质量（W3）→ **门 3** POC 过三闸（W5）→ **门 4** 交付完整（W8） |
| 最贵的一条 | M1 版画/印刷库（4 人日）：三墨分版、错版、降帧、撕裂、字符版画——**这是"像不像 MV"的全部来源** |
| 最容易假的一条 | "提示词定义了类型"（P0）：必须用"请求传违规值 → 成片被顶回边界"来验，不接受"代码里有这个字段" |
| 未决项阻塞 | D14（闪光超标处置）阻塞门 3 判定；D13（主 agent 写场景代码）阻塞 M0 收口；D10（逐词来源）阻塞 M2 排期；D12/D15 可后置 |
| 停下来的条件 | 门 1 不过 → 停整条渲染路线；门 3 不过 → 停 M1/M3 加码，回到最小可用；任一 milestone 超期 >50% → 触发范围裁剪而非延期 |

**建议开工顺序（前 3 件事）**

1. **Spike A**（0.5 天）：`scene_script` 合成类型 + `seek(t)` 调 `renderFrame`，渲 5 秒，两次哈希一致。
2. **V53 修字段漂移**（0.5 天）：`CreativeBrief` 与 prompt 字段对齐 + 单测（这是独立小胜利，且是 M8 的前置）。
3. **D14 拍板 + 闪光检测脚本原型**（1 天）：先用一个 10 秒纯色闪片验证检测器能不能报出违规——**先有量表，再谈治理**。

---

## 1. 计划边界与前提

### 1.1 已定前提（PRD 已拍板，本计划直接采用）

| 编号 | 结论 | 对计划的影响 |
|---|---|---|
| D1 | 三级冲突：品味级用户赢 / 规范级类型赢 / 同一性级换类型 | P1 前端要三态呈现，不能只做"黄条" |
| D3 | 提示词 ≤4000 字符/类型 | 校验器必须在上线前落地，否则后期批量清理 |
| D4 | 5 处硬编码**本期清除** | 排进 P1，且必须留一个周期的兜底日志（可测） |
| D7 | LLM 同一性裁判默认关、L1 指标恒开 | `genre_conformance` 必须支持"零 LLM 成本"模式 |
| D9 | 背景透明度：动画 agent 提案、主 agent 裁决 | M0 的场景脚手架要带 `composition_mode` 参数与事件 |
| D17 | 素材类纳入范围（识别 + 落位） | 独立轨道 S（M6），不是"顺手做" |
| D18/D19 | 去驱动二选一 + 内置提示词对话 | 前端轨道独立排 M7 |
| D20–D22 | plan-first、维度由 `plan_schema` 定、规划书确定性渲染 | M8 必须先有 schema 再有生成，顺序不可颠倒 |

### 1.2 未决项与默认兜底（不阻塞开工）

| 编号 | 事项 | 若 W1 结束仍未定，按此默认执行 |
|---|---|---|
| D10 | 逐词时间来源 | 默认"whisper 打底 + onset 校正"，全部标 `init`，进"待耳朵确认"清单 |
| D12 | 4K 策略 | 默认"仅 2 张 hero 矢量化，其余 ≤1.75x"，4K 导出需人工确认 |
| D13 | 主 agent 能否直接写场景代码 | 默认"能，但只允许写 `compositions/` 下自有分区，且必须过三道闸" |
| D14 | 闪光超标处置 | 默认 **拒绝交付**（按 PRD 推荐值执行；若要改，最晚门 3 前定） |
| D15 | 渲染预算 | 默认 1080p60 单支 ≤30 分钟、4K 只渲指定帧；超预算降半帧率采样 |

### 1.3 团队与环境

| 角色 | 人数 | 主要负载 |
|---|---|---|
| 后端 A（渲染/创意） | 1 | M0–M5（场景脚本、版画库、卡点、腐坏、4K） |
| 后端 B（Agent/规划） | 1 | P0–P2、M6、M8 |
| 前端 | 1 | 类型编辑器、创作页、ChatForge 抽屉、规划确认页 |
| 内容/美术（0.5） | 0.5 | 4 个内置类型的规范撰写、MV 示例工程包、版画设计、字体授权核对 |

**环境前置**（S0 一天内确认）：HyperFrames/playwright 可用、`ffmpeg` 可用、Node ≥20、Python ≥3.12、磁盘预留 ≥50GB（逐帧渲染产物）、CI 能跑 lint + pytest + 前端三件套。

---

## 2. 风险前置：三个 Spike（≤3 天，先做）

| ID | 目标 | 判定 | 失败含义 |
|---|---|---|---|
| **SP1** | `scene_script` 能渲：一个 5 秒合成，`renderFrame(t)` 画圆与文字，两次渲染逐帧哈希一致 | 渲出 150 帧、sha256 全等 | 确定性路线不成立 → 整条 Part II 停 |
| **SP2** | 成本实测：1080p60 一段 30 秒纯 HTML 场景的渲染时长 / 磁盘 / 峰值内存 | 单段 ≤8 分钟、≤3GB 产物 | 超 3 倍 → 必须先做降采样与缓存，M0 延期 |
| **SP3** | 三墨分版可行性：Python 侧对一张 PNG 做墨色分版 + alpha 清理 + 三版导出 | 三版可独立着色、边缘无脏点 | 挪到 JS 侧或改用 SVG 输入（影响 M1 设计） |

> Spike 代码允许丢弃，**但结论必须写成 `docs/spike-results-2026-10-18.md` 三行**（结论 / 数字 / 影响），否则等于没做。

---

## 3. 工作分解（WBS）

任务字段：`ID｜名称｜范围｜落点｜依赖｜验收｜估算(人日)`

### 轨道 R：渲染与创意（后端 A，26.5 人日）

| ID | 任务 | 范围 | 落点 | 依赖 | 验收 | 估 |
|---|---|---|---|---|---|---|
| R-01 | scene_script 合成类型 | `compositions/<n>/{index.html,scene.js}` 约定；`seek(t)` 调 `renderFrame(t,ctx)`；`data-composition-id` 注册 | `animation/hf_timeline.py`（扩展）、`animation/scene_renderer.py`（新） | SP1 | V21、V22 | 2 |
| R-02 | 确定性纪律工具 | lint 扫 `Date.now/Math.random/setTimeout/rAF/fetch`；运行时冻结 `Math.random/Date`；`prng(t,salt)` | `tools/lint_determinism.py`、`animation/scene_runtime.js`、`animation/scene_prng.js` | R-01 | V23 | 1 |
| R-03 | 逐帧哈希闸 | 同工程两次渲染逐帧比对，输出差异帧清单 | `tools/hash_frames.py` | R-01 | V22 | 0.5 |
| R-04 | **Day-1 聊天界面场景** | 标题栏 + 线程 + 3 条消息按拍落下 + 光标按 `t` 闪 + 状态灯；背景模式可配 | `examples/mv_poc/chat/*` | R-01 | 门 1 | 1.5 |
| R-05 | 30 秒 POC 装配 | 界面 3 镜 + 版画 2 镜 + 代码 1 镜；出 `report.json` | `examples/mv_poc/` | R-01…R-04、M-02 | 门 3 | 1.5 |
| R-06 | 版画库：三墨分版 | `inks(bitmap,palette)`：按最近墨色分三版、每版独立角色/位移/显影 | `animation/plate/inks.py` | SP3 | V28 | 1.5 |
| R-07 | 版画库：纸底与错版 | `ground()`（噪点/点阵/光斑/裁切标记）、`misregistration(px)` | 同上 | R-06 | V28、V29 | 1 |
| R-08 | 版画库：降帧与撕裂 | `decimate(fps)`、`tear(dir,holdFrames≥6)` | 同上 | R-06 | V29 | 1 |
| R-09 | 字符版画与 RGB 分离 | `glyphPlate(text,gradient)`、`rgbSplit(px)` | 同上 | R-06 | V30 | 1 |
| R-10 | 剪影管线与配准 | `cleanAlpha(16)`、派生图按实际比例、配准表 `{s,tx,ty}` | `services/silhouette.py`、`assets/character/registration.json` | R-06 | V33 | 1 |
| R-11 | 歌词模型与字幕带 | `lyrics.json`（行/词/音节/关键词）、字幕带构件（位置字号锁定） | `components/lyrics/*`、`schema/lyrics.py` | R-01 | V25 | 1.5 |
| R-12 | 四个文字动画 | 逐词出现、逐块印上（结巴）、字母下垂、终端流 | `components/lyrics/*` | R-11 | V30 相关 | 1.5 |
| R-13 | 卡点服务 | `build_grid(bpm,offset)`、`snap(t,unit)`、`build_cues(lyrics,audio)` | `services/mv_cues.py` | 已有 onset/bpm | V24、V25 | 2 |
| R-14 | 腐坏特效 | `decimate/tear/rgbSplit` 组合 + "界面镜头内插版画再回界面"辅助 | `components/decay/*` | R-08、R-09 | V29 | 1.5 |
| R-15 | 四个专属转场 | `winclose / zoom / shatter / slices` | `components/transitions/*` | R-14 | V29 | 1.5 |
| R-16 | 闪光检测器 | 逐帧亮度直方图 + 大面积占比 + 边界扫描；输出违规秒数 | `tools/check_flash.py` | — | V26、V27 | 1.5 |
| R-17 | 4K 与矢量化 | hero 资产描路径、4K 单帧导出、边缘检查 | `tools/vectorize.py` | R-10 | V34 | 2 |
| R-18 | 编排与工程包载入 | `mv.json/cues.json/lyrics.json` 载入 + `dist/` 六件套生成 | `services/mv_bundle.py`、`tools/pack_dist.py` | R-05…R-13 | V37、V38 | 2 |
| R-19 | 同步检查 | 负时间片头偏移、音画同步误差 | `tools/check_sync.py` | R-18 | V32 | 1 |

### 轨道 A：Agent 与规划（后端 B，20 人日）

| ID | 任务 | 范围 | 落点 | 依赖 | 验收 | 估 |
|---|---|---|---|---|---|---|
| A-01 | **V53 字段漂移修复**（先做） | `CreativeBrief` 补 `core_message/structure_suggestion/key_elements/special_requirements`；或改 prompt 对齐；加集合断言单测 | `schema/requirements.py`、`agents/requirements_agent.py` | — | V53 | 0.5 |
| A-02 | 提示词/spec 数据模型 | `PromptSlot/ParamRule/SignatureTrait/TypeDeclares/PromptBundle/PlanSchema` | `schema/category_spec.py`（新） | — | — | 1 |
| A-03 | `PromptAuthority` 裁决器 | L0–L5、L2 先定域、5 种 mode、C1–C10 分类、三产物（params/prompts/traits） | `services/prompt_authority.py`（新） | A-02 | V2、V5、V18 | 2 |
| A-04 | 槽位注入与旧钩子归一 | `get_prompt_bundle()` + 适配器（`str|list[str]|list[dict]` 归一）；身份层段注入各 Agent | `category/base.py`、`agents/*.py` | A-03 | V1、V6、V15 | 1.5 |
| A-05 | 剪辑钳制点接管 | `edit_agent` 改读裁决产物，删本地 5000 兜底 | `agents/edit_agent.py:280-320,738-745` | A-03 | V2 | 1 |
| A-06 | 自定义类型契约与校验 | `TypeDefinition` 加 4 字段 + `extra=forbid`；悬空数字/规则冲突/指标矛盾/越权检测 | `api/type_maker.py` | A-02 | V8、V9、V10、V11、V12、V20 | 1.5 |
| A-07 | 动态类型读提示词 | `DynamicCategoryPlugin.get_prompt_bundle()` | `category/dynamic.py` | A-04 | V8 | 0.5 |
| A-08 | `declares` 迁出 5 处硬编码 | 读 `declares`；旧集合兜底 + deprecation 日志（下期删） | `structure_agent.py`、`material_agent.py`、`quality_agent.py` | A-04 | V7 | 1 |
| A-09 | 指标采集与同一性校验 | `trait_collector`（结构/剪辑期指标）+ `genre_conformance`（L1 恒开、L2/L3 默认关） | `services/trait_collector.py`、`services/genre_conformance.py`（新） | A-03 | V16、V17 | 2 |
| A-10 | 四个端点 | `validate / resolve / prompt-resolution / genre-check` | `api/type_maker.py`、`api/pipeline.py` | A-06、A-09 | V13 | 1 |
| A-11 | `get_type_spec` 进能力面 | 只读能力 + 能力矩阵重生成 | `tool/surface.py`、`docs/capability-matrix.md` | A-04 | 覆盖率 100% | 0.5 |
| A-12 | **M8 PlanDocument 契约** | `PlanDocument`（sections/screen_kinds/beats/material_strategy/risks/acceptance/custom_sections） | `schema/plan.py`（新） | A-02 | V52 | 1 |
| A-13 | `plan_schema` 校验器 | required/optional/custom_allowed；段落连续/时长偏差/卡点自洽 | `services/plan_validator.py`（新） | A-12 | V54、V56、V57 | 1.5 |
| A-14 | 规划动态生成 | 需求 agent 按 `plan_schema` 生成结构化 plan（替换写死 10 字段的 prompt） | `agents/requirements_agent.py` | A-12、A-13 | V54 | 1.5 |
| A-15 | `plan_renderer` 确定性渲染 | plan → `plan.md`（模板可插），同 plan 两次渲染逐字节一致 | `services/plan_renderer.py`（新） | A-12 | V52、V59 | 1 |
| A-16 | 结构阶段读 plan | `StructureAgent` 从 plan 读段落目的/画面类型/卡点；**空 plan 明确失败** | `agents/structure_agent.py` | A-14 | V55 | 1 |
| A-17 | 漂移留痕 | `plan_drift` 事件（谁改/哪段/原因）+ plan 回写 | `services/plan_drift.py`（新）、`api/requirements.py` | A-16 | V58 | 1 |
| A-18 | 老数据兼容 | 只有 scenes → 反向生成 `legacy:true` plan | `services/requirements_service.py` | A-16 | V60 | 0.5 |

### 轨道 S：素材（后端 B 兼，5.5–6 人日）

| ID | 任务 | 范围 | 落点 | 依赖 | 验收 | 估 |
|---|---|---|---|---|---|---|
| S-01 | MV 标签集与批量索引 | 面向 MV 的标签（特写手部/舞台/纸纹理…）+ 对给定目录批量索引 | `services/content_index.py`（扩展） | 已有索引 | V41 前置 | 2 |
| S-02 | 乐句级 query 构造 | 歌词行/关键词 → 检索 query；返回带分数候选 | `services/content_query.py`（扩展） | S-01 | V41 | 1 |
| S-03 | 按拍点落位 | 切点先落拍网格，再在拍内选 in 点；记 `matched_by` + 分数 | `services/scene_grounding.py`（扩展） | S-02、R-13 | V41、V42 | 1.5 |
| S-04 | 授权台账与缺口 | 素材来源/授权字段、"不可用区间"、`material_gap` 显式化 | `api/asset.py`、`services/asset_governance.py` | — | V42、V43 | 1–1.5 |

### 轨道 F：前端（前端 8 人日）

| ID | 任务 | 范围 | 落点 | 依赖 | 验收 | 估 |
|---|---|---|---|---|---|---|
| F-01 | 类型编辑器扩展 | 身份层 3 槽位（genre 必填）+ 阶段 7 槽位 + 规则表 + 指标表 | `src/pages/admin/TypeMakerPage.tsx`、`src/services/api/typeMaker.ts` | A-06 | V1、V20（前端侧） | 2 |
| F-02 | 冲突/变形预览 | 保存前 `resolve`；`type_deformation` 时**不给豁免按钮**，给"切换类型/按规范调整" | 同上 | A-10 | V18 | 1 |
| F-03 | 类型符合度展示 | 管线详情「类型符合度」一栏（指标 + 证据） | Pipeline 详情页 | A-09 | V16 前端侧 | 1 |
| F-04 | **创作页改造（§II.12）** | 去「配音/视觉驱动」开关；加提示词区（`prompt/style_refs/avoid`） | `src/pages/HomePage.tsx` | M7 后端 | V44、V45 | 1.5 |
| F-05 | ChatForge 抽屉 | 右侧常驻按钮 → 抽屉；上下文注入（歌/类型/提示词/素材摘要）；结论回填 + 留痕 | `src/features/chatforge/ChatForgeDrawer.tsx`、`src/services/api/chatForge.ts` | 后端 `scope=mv_prompt` + `commit-prompt` | V46、V47、V48 | 1.5 |
| F-06 | 规划确认页 | brief 与 plan 双栏；结构化字段可编辑；自定义维度必填 reason；缺口显著展示 | `src/pages/PlanReviewPage.tsx` | A-14、A-15 | V59 前端侧 | 1 |

### 内容轨道（内容/美术 0.5 人，6 人日）

| ID | 任务 | 产物 | 估 |
|---|---|---|---|
| C-01 | 4 个内置类型的规范 | `knowledge_longform / digital_review / kichiku_fastcut / vlog_daily` 各写 `genre / anti_patterns / lexicon / signature_traits` | 2 |
| C-02 | `lyric_mv` 类型规范 | 身份层文案 + 7 条指标 + `plan_schema`（含 `beats` 必填） | 1 |
| C-03 | MV 示例工程包 | 30 秒 POC 的 `mv.json/cues.json/lyrics.json/chat_script.json` + 说明 | 1.5 |
| C-04 | 版画美术 | 9 张剪影三墨化验收、茄子/番茄字符画设计、专色校验 | 1 |
| C-05 | 字体授权核对 | OFL 白名单、`font_roles` 映射、CREDITS 模板 | 0.5 |

---

## 4. 依赖与关键路径

### 4.1 关键路径

```
SP1 ──► R-01 ──► R-02/R-03 ──► R-04 ──► 门 1
                                   │
                    ┌──────────────┴──────────┐
                 R-06..R-10 (M1)            R-11..R-13 (M2)
                    │                          │
                    └────────► R-05 ◄──────────┘
                               │
                           门 3 (POC 过三闸)
                               │
              ┌────────────────┼─────────────────┐
        R-14/R-15 (M3)      S-01..S-03 (M6)    A-12..A-18 (M8)
                               │                 │
                          R-18/R-19          A-16 → F-06
                               │
                           门 4 (交付)
```

### 4.2 并行冲突点（单写者规则，必须写进开发约定）

| 共享物 | 冲突场景 | 规则 |
|---|---|---|
| `components/`（构件库） | 前端 F-04/F-05 与渲染 R-11/R-12 同时改 | 前端只读 `components/`，渲染方是唯一写者；接口由 R-01 的 `renderFrame` 契约固定 |
| `cues.json` | R-13 与 S-03 都写 | R-13 是唯一写者；S-03 只读 |
| `docs/capability-matrix.md` | A-11 与任何新增能力 | 由能力矩阵脚本重生成，禁止手改 |
| `docs/api_reference.md` / `docs/frontend-backend-parity.md` | A-10/A-11/A-17 都有新端点 | 每个 PR 内自己那一节，禁止整文件重排 |
| `schema/*.py` | A-02 与 A-12 并行加模型 | 两个新文件（`category_spec.py`、`plan.py`），不改既有文件 |

---

## 5. 周计划（2 后端 + 1 前端 + 0.5 内容）

| 周 | 后端 A（渲染） | 后端 B（Agent/规划） | 前端 | 内容 | 周末可演示物 |
|---|---|---|---|---|---|
| **S0**（2 天） | SP1/SP2/SP3 | A-01 修字段漂移；定 D14 | 环境与契约对齐 | 字体授权初核 | Spike 结论一页纸 |
| **W1** | R-01, R-02, R-03, R-04 | A-02, A-03 起 | F-01 骨架 | C-01 起 | **聊天界面 5 秒场景 + 哈希全等** |
| **W2** | R-04 收口, R-05 起 | A-03 收口, A-04, A-05 | F-01 收口 | C-01 | **门 1**：聊天界面 30 秒出画面、确定性通过 |
| **W3** | R-06, R-07, R-10 | A-06, A-07, A-08 | F-02 | C-04 起 | **门 2**：三墨分版/纸底/错版单帧检查图 |
| **W4** | R-11, R-12, R-13 | A-09, A-10 | F-03 | C-02 | 字幕带 + 关键词 + 拍点表（`cues.json`） |
| **W5** | R-08, R-09, R-14, R-15, R-05 | A-11；A-12 起 | F-04 | C-03 起 | **门 3**：30 秒 POC 过三道闸 + `report.json` 数字 |
| **W6** | R-16（闪光）, R-18 起 | A-12, A-13, A-14 | F-04 收口 | C-03 | 闪光检测报告 + 结构化 plan 示例 |
| **W7** | R-17, R-19 | A-15, A-16, A-17 | F-05 | C-05 | `plan.md` 确定性渲染 + ChatForge 抽屉可用 |
| **W8** | R-18 收口 | A-18, S-01–S-03 | F-06 | C-04 收口 | **门 4**：`dist/` 六件套 + 素材落位样例 |
| **W9** | 修尾 + V21–V43 复核 | S-04 + V52–V60 复核 | V44–V48 复核 | 交付文档 | 全量验收报告 |

> 计划里 W5 的 M3 与 W6 的 R-16 有交叠：**闪光检测器必须早于腐坏特效收口**——腐坏效果最容易触发闪耀，先有量表再调效果。

---

## 6. 四道门（门禁与 Kill criteria）

| 门 | 时间 | 通过条件（缺一不可） | 不过怎么办 |
|---|---|---|---|
| **门 1 渲染可行** | W2 末 | ① 聊天界面 30 秒场景渲出 1080p60；② 两次渲染逐帧哈希全等；③ lint 能拦下 `Math.random` 注入；④ 单段渲染 ≤8 分钟（SP2 结论） | **停**。重新评估渲染路线（可能是 HyperFrames 的 worker/seek 用法问题），暂停 M1 之后所有投入 |
| **门 2 画面质量** | W3 末 | ① 三墨可独立取用、alpha<16 已清零；② 错版 10/18/28px 可视化正确；③ 9 张剪影配准叠加偏差 ≤1px；④ 人工抽检"不看字能认出 3/5" | 砍到最小可用（只做 inks + ground + tear），其余排到 M1.5；**不许**用"占位图"糊过去 |
| **门 3 POC 过三闸** | W5 末 | ① 30 秒 POC 三画面各 ≥1 段；② 确定性哈希全等；③ 拍点命中率 ≥95%、误差 ≤1 帧；④ 闪光 violations = 0（且检测器先用"故意闪片"校准过，能报出已知违规）；⑤ `report.json` 数字非空 | 若 ④ 不过：先改效果（不是先放宽标准）；若 ③ 不过：`cues.json` 标 `init` 并降级为行级时间；**两项都不过则停整条 Part II** |
| **门 4 交付完整** | W8 末 | ① `dist/` 六件套齐全；② 素材落位抽检 ≥8/10；③ `brief.md/plan.md` 与源对象一致；④ 全量 pytest + 前端三件套绿 | 只交付"能跑的子集"，明确标注未完成项，不装作完成 |

### Kill / 裁剪规则（超期时优先砍什么）

| 情况 | 裁剪顺序（先砍后保） |
|---|---|
| 超期 ≤20% | 砍 M5（4K）→ 砍 R-12 四个文字动画中后两个 |
| 超期 20–50% | 再砍 M3 四个专属转场（只留 `slices` + `tear`）→ 砍 R-09 `rgbSplit` |
| 超期 >50% | 停 M1 加码，只交付"最小版画集"；M8 只做 `sections + material_strategy` 两维 |
| 门 1 不过 | **不裁剪，直接停**（后续投入会全部作废） |

---

## 7. 工程质量纪律（Definition of Done）

每个任务完成的最小标准（缺一项视为未完成）：

1. **代码**：通过 `ruff --select E9,F63,F7,F82,F823`（0 命中）；类型注解齐全；
2. **单测**：新增/修改逻辑有对应测试；涉及"不许假功能"的点必须写"反例测试"（如传违规值被拦住）；
3. **回归**：`python -m pytest tests -q` 全绿（基线 3566 passed / 3 skipped / 1 xfailed）；
4. **前端**：`npx tsc --noEmit` + `npm run test` + `npm run build` + eslint 0 error；
5. **文档**：按 AGENTS.md 的文档维护规则同步 `docs/`（api_reference / frontend-backend-parity / README 索引）；新增能力必须重跑 `python -m clipwright.services.capability_matrix --out docs/capability-matrix.md`；
6. **留痕**：涉及裁决/漂移/冲突的逻辑必须写事件（`prompt_resolution` / `param_conflict` / `plan_drift` / `composition_mode`）；
7. **PR**：一支任务一个 PR，标题带任务 ID（如 `R-06 版画库：三墨分版`），描述里贴验收证据（截图/数字/测试名）。

### 新增的 CI 门（本计划首次上线）

| 门 | 触发 | 命令 |
|---|---|---|
| 确定性 lint | 任何 `examples/**/scene.js` 变更 | `python -m tools.lint_determinism examples/` |
| 逐帧哈希 | 门 1/门 3 前手动，W6 起进夜间 | `python tools/hash_frames.py examples/mv_poc` |
| 闪光检查 | 同上 | `python tools/check_flash.py out/poc.mp4` |

> CI 里**不跑**逐帧渲染（太慢），只跑 lint；渲染类验收靠"里程碑打点 + 留档产物"。

---

## 8. 分工到人

| 人 | 主责 | 交接点 |
|---|---|---|
| 后端 A | R-01…R-19 | 与前端在 R-01 契约、R-11 `lyrics.json` 结构上对齐；与内容在 R-06 墨色阈值上对齐 |
| 后端 B | A-01…A-18、S-01…S-04 | 与后端 A 在 R-13 `cues.json`（唯一写者 R-13）对齐；与前端在 A-10 端点契约对齐 |
| 前端 | F-01…F-06 | 依赖 A-06（类型契约）、A-10（端点）、A-14/A-15（plan 渲染结果） |
| 内容/美术 | C-01…C-05 | C-02 的 `lyric_mv` 规范必须在 W4 前给到，否则 A-09 的指标无值可测 |

**协作节奏**：周一 30 分钟对齐（只讲"卡在哪、要不要改范围"）；周五出"本周可演示物 + 门禁进度"；跨轨接口变更必须当天在 `docs/mv-pv-prd-2026-10-18.md` 对应章节改一行，**不接受口头约定**。

---

## 9. 工期三档

| 档 | 配置 | 工期 | 交付范围 |
|---|---|---|---|
| **现实档（推荐）** | 2 后端 + 1 前端 + 0.5 内容 | **10–11 周** | 门 1–4 全过 + V1–V60 主体 |
| 乐观档 | 同上 + 后端 A 全职压到 W5、内容提前交付 | 8–9 周 | 同上，M5（4K）与部分文字动画后置 |
| 悲观档 | 1 后端 + 0.5 前端 | 18–20 周 | 只到门 3（POC 可用），M6/M8 延后 |

> 口径说明：66 人日 ÷ 3 人 ≈ 22 个工作日（≈4.5 周）是**纯产能下限**；实际工期取决于关键路径串行（门 1→门 2→门 3 不能压缩）与联调返工，故取 10–11 周。

**成本口径提醒**：本计划的人日只含"人"，不含模型调用与渲染算力。M0–M8 期间会持续跑 LLM（规划生成、版画描述、场景代码），建议在 W2 前把 `pipeline_budget_usd` 与 `unknown_cost_calls` 的记账打开（PRD Part I G5/V38 已要求入账）。

---

## 10. 未决项与阻塞（按阻塞时间排序）

| 编号 | 事项 | 阻塞谁 | 最晚决策时间 | 默认兜底 |
|---|---|---|---|---|
| **D14** | 闪光超标：拒绝交付 vs 标记交付 | 门 3 判定、R-16 行为 | **W1 末** | 拒绝交付 |
| **D13** | 主 agent 能否直接写场景代码 | M0 收口、S-03 | W2 末 | 能（限自身分区 + 过三闸） |
| **D10** | 逐词时间来源 | M2（R-13）排期 | W3 末 | whisper 打底 + onset 校正，全标 init |
| **D12** | 4K 策略 | M5（R-17） | W6 末 | 仅 2 张 hero 矢量化 |
| **D15** | 渲染预算 | R-18、CI 夜间任务 | W6 末 | 1080p60 ≤30 分钟/支 |

---

## 11. 进度追踪

- **看板四列**：`待开工 / 进行中 / 待验收 / 已验收`（任务 ID 即卡片号，WBS 表为唯一真相）。
- **周报三行**：本周完成（任务 ID + 验收证据）／卡点（阻塞项 + 需要的决策）／下周计划（含门禁进度）。
- **里程碑存档**：每道门过时把产物存 `out/gates/gate{1..4}/`（视频、单帧、`report.json`、截图），**不留存就等于没过**。
- **PRD 变更联动**：PRD 新增/修改章节 → 计划里对应任务的"范围/验收/估算"必须在同一周内更新，并在周报里说明工期影响。

---

## 12. 执行状态（滚动更新）

### 12.1 已完成（第一轮）

| 任务 | 产物 | 证据 |
|---|---|---|
| **A-01** 字段漂移修复 | `schema/requirements.py` 重写：`CreativeBrief` 补齐 `core_message/structure_suggestion/key_elements/special_requirements/animation_intents`；提示词字段表与 JSON 骨架由 `brief_prompt_template()` **从模型生成**（结构上不可能再漂）；`RequirementsAgent._normalize_brief()` 显式登记未知字段 | `test_plan_first.py::TestFieldDrift`（4 项，含"字段集双向一致"断言） |
| **A-02** 规范契约 | `schema/category_spec.py`：10 槽位（含 `global→genre` 别名）、`ParamRule`（5 mode × strict/soft）、`SignatureTrait`、`TypeDeclares`（含 `plan_schema`、`requires`、`neighbors`）、`PromptBundle`（截断/预算/归一）、`PlanDocument` 全套 + 纯函数校验（悬空数字、规则自洽、指标与 genre 矛盾） | `test_category_spec.py`（31 项） |
| **A-03** 裁决器 | `services/prompt_authority.py`：L0–L5 分层、L2 规则后置生效、C1/C2/C6/C9 冲突记录、平台底线强制、变形判定（`min>max`、指标结构性不可满足）、提示词预算丢弃 | `test_prompt_authority.py`（19 项，含"用户传 300 被顶回 1200"与"鬼畜变形"两条硬验收） |
| **A-12/A-13/A-15** 规划内核 | `services/plan_validator.py`（必填维度/段落连续/时长偏差 10%/25%/卡点自洽/自定义维度需理由/legacy 反推）、`services/plan_renderer.py`（确定性 Markdown，brief + plan 两套） | `test_plan_first.py`（22 项，含"两次渲染逐字节一致"） |
| **R-01** 场景脚本合成 | `animation/scene_renderer.py`：全帧合成 HTML、`window.__scene.renderFrame(t,ctx)` 契约、`window.__timelines.seek(t)` 注册、运行时冻结（`Math.random`→prng、`Date.now`→0、定时器抛错）、`composition_mode()` 汇报合成路径（不替 agent 决策） | `test_scene_renderer.py`（12 项，含"生成的运行时自身 lint 干净"） |
| **R-02** 确定性 lint | `tools/lint_determinism.py`：12 条禁用模式（定时器/日期/随机/网络/存储/动态 import）、`deterministic-ok` 豁免、CLI 退出码 | `test_dev_tools.py::TestDeterminismLint`（7 项） |
| **R-03** 逐帧哈希 | `tools/hash_frames.py`：rgb24 流切帧 sha256、PNG 目录、两两比对（含长度不一致）、CLI | 同上（5 项） |
| **R-16** 闪光检测 | `tools/check_flash.py`：BT.601 亮度、大面积亮暗翻转判定、1 秒窗统计、`analyze_video`（ffmpeg 缩略解码）、CLI | 同上（9 项，含**"已知答案"校准：4Hz 闪片必须报违规**） |

### 12.2 验收口径与门禁

- 新增 99 个单测全绿；`ruff --select E9,F63,F7,F82,F823` 全绿。
- **SP1 已通过（真机）**：`hyperframes@0.8.171` 装好后，1 秒 @24fps 的场景两次渲染 **24/24 帧 sha256 全等**，单次约 5.6–5.7s（含浏览器启动）。详见 `docs/spike-results-2026-10-18.md`。
- 过程中撞到两个坑，已写进代码与文档：
  1. **全局冻结 `setTimeout`/`rAF` 会让 HyperFrames 直接渲染失败**（`Render failed`）——改为"作者脚本 IIFE 遮蔽"，平台继续用真全局；
  2. **`ais_available()` 新进程首调恒为 False**（非阻塞缓存），验收脚本必须用 `await_available()`。
- 门 1 剩余要件：**已全部补齐**。见 12.5。

### 12.5 门 1 通过（R-04 完成）

| 要件 | 证据 |
|---|---|
| scene_script 契约 | `animation/scene_renderer.py` + 16 项单测 |
| 确定性 lint | `tools/lint_determinism.py`（12 条规则）+ 7 项单测；`examples/mv_poc/chat/scene.js` lint 干净 |
| 逐帧哈希闸 | `tools/hash_frames.py` + 5 项单测 |
| **聊天界面场景** | `examples/mv_poc/chat/{scene.js,chat_script.json}` → **12s / 360 帧两次渲染全等**，闪光 0 违规，56.3MB |
| 成本 | 1080p60：22.5s / 5s（≈4.5 秒渲染 1 秒成片）；960×540@30：19.9–25.6s / 12s |
| 视觉核对 | t=8s 单帧：标题栏 + 状态灯 + 双向气泡 + 输入框 + 打字光标，确认是"一条对的聊天界面" |

**门 1 判定：通过。** 依据：`docs/spike-results-2026-10-18.md`、`out/mv_poc/report.json`。

### 12.6 本轮新增实现（A-04/A-05/A-06/A-07）

| 任务 | 内容 | 证据 |
|---|---|---|
| A-04 槽位归一变电压 | `category/base.py` 新增 `get_prompt_bundle/get_param_rules/get_signature_traits/get_declares`；旧钩子适配器把 `str` / `list[str]` / `list[dict]` 归一（**修 A1/A2 的 repr 注入**） | `test_type_spec_runtime.py::TestLegacyAdapter`（4 项，断言 `{`/`'role'` 不出现） |
| A-05 钳制点接管 | `services/type_spec_runtime.py`：`load_spec` / `resolve_shot_params` / `resolve_params` / `stage_text` / `clamp_profile`；`edit_agent` 改读裁决产物，冲突与变形风险写进 `notes` | 22 项（含"strict floor 把 300 顶回 1200 且记 C1"） |
| A-06 类型契约加固 | `TypeDefinition` 加 `prompts/param_rules/signature_traits/declares/waivers`，`extra=forbid`，槽位白名单校验；新增 `POST /api/type-maker/validate` 与 `/resolve` | "未知字段被拒""悬空数字被拒""global 别名可用"等 6 项 |
| A-07 动态类型读规范 | `category/dynamic.py` 读 YAML 的 `prompts/param_rules/signature_traits/declares`，非法项跳过不崩 | 3 项 |

### 12.7 下一轮

1. **M1 版画库**（R-06…R-10）：三墨分版 / 纸底 / 错版 / 降帧 / 撕裂 / 字符版画 / 配准——门 2 的主体；
2. **A-09**（指标采集 + 同一性校验 L1）+ **A-08**（`declares` 迁出 5 处硬编码）；
3. **R-11…R-13**（歌词模型 / 字幕带 / 拍点服务）；
4. 定位全量回归的顺序污染。

### 12.8 第 2 轮：M1 版画库（R-06…R-10）完成，门 2 证据已出

| 任务 | 实现 | 验收 |
|---|---|---|
| R-06 三墨分版 | `animation/plate/inks.py`：`clean_alpha`（alpha<16 清零）、`split_inks`（按加权 RGB 距离分版，可 tint）、`compose`（可带位移套印）、`layer_coverage` | 单测断言"三版像素数之和 = 全部不透明像素" |
| R-07 纸底与错版 | `plate/ground.py`：噪点/点阵/暗角/四角裁切标记，`RandomState(seed)` 确定性；`plate/degrade.py`：`misregistration`（10/18/28px + 露细红版 `_outline`） | 同 seed 两次渲染逐字节相同；错版可见红边 |
| R-08 降帧与撕裂 | `degrade.decimate_indices`（只依赖帧率）、`tear`（水平/竖直、断口红边）、`hold_frames_ok` | 单测覆盖取样规律与"保持 ≥6 帧" |
| R-09 字符版画 | `plate/glyph.py`：超采样（2x）+ gamma 密度 + 按墨色着色 + 轮廓字符 | 1920 单帧可读出"胸像"轮廓 |
| R-10 配准 | `plate/registration.py`：`Registration`、JSON 读写、`apply_registration`、`derive_size`（按实际比例） | 配准 JSON 往返、偏差计算 |
| R-17 前置 | `plate/vectorize.py`：边界像素 → 轮廓 → Douglas–Peucker 简化 → SVG path | 方块轮廓 ≤8 点、面积 300–420（20×20 掩膜） |

**门 2 证据**：`python -m tools.plate_check --json` → `out/plate/check_sheet.png`
（六格：纸底 / 三墨套印 / 错版 28px / 撕裂 / 字符版画 / RGB 分离），两次渲染
**逐字节一致**（sha256 `8b317bb0…`），单帧 `out/plate/glyph_1920.png` 可分辨轮廓。

**新增 39 项单测**（`test_plate_library.py`）。门 2 仍差两项人工判定：
"不看字能认出 3/5"（当前字符版画能认出胸像，需用真实剪影复测）与 9 张剪影配准 ≤1px（需要素材）。

### 12.9 第 3 轮：歌词/卡点服务 + 版画镜头真机集成（R-11/R-13 + M1 接入）

| 任务 | 实现 | 验收 |
|---|---|---|
| R-11 歌词模型 | `schema/lyrics.py`：`LyricsDoc → LyricLine → LyricWord`，行/词/音节/关键词；**不存歌词原文**，只存 `text_ref`；校验重叠与倒序 | 12 项单测（含"行重叠被拒""band=False 不走字幕带"） |
| R-13 拍点与 cues | `services/mv_cues.py`：`BeatGrid`（beat/half/bar 吸附、`error_frames`、`is_on_grid`）、`make_cue`（实测起音优先，其次网格吸附，否则标 `init`）、`cues_from_lyrics`、`build_cues`、`grid_hit_rate`、`save/load_cues` | 12 项单测（含 V24 命中率、cues JSON 往返、source=init 标记） |
| **版画镜头集成** | `examples/mv_poc/plate_shot/{scene.js,lines.json}` + `scripts/render_plate_shot.py`：版画库渲染 PNG → base64 内联 → 场景按 `t` 组合（错版在 2.5s 切入）+ 大字关键词 + 字幕带 + 亮度棘轮 | **180 帧两次渲染 sha256 全等、闪光 0 违规**；单帧核对：三墨胸像 + 红色错版边 + 字符纹理 + 衬线字幕 |

**两个只有抽帧才看得出来、已修掉的问题**：

1. **字符版画被盖住**：glyph 层压在墨层之下且墨层不透明 → 纹理等于没画；改为压在墨层之上（opacity 0.32）。
2. **关键词与头部重叠**属构图问题（不是 bug），已记录；正式类型规范里由 `genre` 的版式约定约束。

**产物**：`out/plate_shot/{a.mov,report.json,f_clean.png,f_broken.png}`（960×540 / 6s / 182MB）。

### 12.10 下一轮

1. **R-14/R-15**：腐坏组合（错版+降帧+撕裂）与四个专属转场（winclose/zoom/shatter/slices）；
2. **A-08/A-09**：`declares` 迁出 5 处硬编码 + 指标采集/同一性校验 L1；
3. **R-16 进流程**：把闪光检测接进渲染脚本的默认门禁（现在 plate/chat 两个脚本各自调用，需统一）；
4. 用 `mv_cues` 替换 POC 里写死的时间，让"卡点"从代码常量变成 cues 驱动。

### 12.11 第 4 轮：转场与腐坏组合（R-14/R-15）+ 类型声明迁出（A-08）

| 任务 | 实现 | 验收 |
|---|---|---|
| R-15 四个专属转场 | `plate/transitions.py`：`winclose`（向内收）/`zoom`（穿越）/`shatter`（切块外散）/`slices`（条带错位），统一签名 `(src, dst, p)`，全部纯函数 | 16 项单测：四个转场 p=0/p=1 端点、越界参数报错、确定性 |
| R-14 腐坏组合 | `transitions.decay_sequence`：按 `hold_modulus` 降帧 + 逐帧撕裂（offset 从 0 递增）；**offset=0 不画断口红边**（首帧必须与源一致） | 3 项单测（含本次实测踩到的首帧问题） |
| **A-08 declares 迁出** | `type_spec_runtime` 新增 `declare_flag` / `required_markers` / `search_hint`；5 处硬编码改为**声明优先 + 旧集合兜底 + 一次 deprecation 日志**：`quality_agent`（semantic_qa_default）、`structure_agent`（图表标记 / outro / opening）、`material_agent`（search_hint） | 18 项单测（含"旧集合仍生效"的零回归断言） |
| 回归定位 | 全量 verbose 跑证明**不是卡死**：进度线持续前进（14% → 55%），只是 Windows 下 verbose 输出让总时长从 17 分钟涨到 ~40 分钟 | 见 12.12 |

### 12.12 关于"全量回归挂起"的更正

上一轮我说"跑了 40 分钟没结束、怀疑挂起"，**这个判断是错的**：本轮用 `-v > log` 观察进度线，行数在 20 秒内从 1583 增至 2098，一路到 55%，只是**慢**（verbose 输出 + 逐行 flush 在 Windows 上开销很大）。

结论：**用 `-q` + 分片**跑回归，别用 `-v` 全量；`-v` 只用来定位单个可疑测试。

### 12.13 第 5 轮：指标采集 + 同一性校验 L1（A-09）

| 任务 | 实现 | 验收 |
|---|---|---|
| 指标采集 | `services/trait_collector.py`：9 个指标（`avg/base_shot_ms`、`repeat_shot_ratio`、`chart_count`、`argument_segment_count`、`text_overlay_ratio`、`illustration_clip_ratio`、`beat_align_ratio`、`keyframe_density`），全部确定性、缺键补 0 | 11 项单测（含速度换算、重复率、网格命中、空时间线） |
| 同一性校验 | `services/genre_conformance.py`：`evaluate_traits`（L1）+ `check_conformance`（strict→error / soft→warning；**L2/L3 默认 `skipped`**，裁判异常降级不阻断） | 10 项单测（含"默认关裁判必须显式 skipped""裁判异常降级"） |
| 质检接线 | `QualityAgent._check_type_conformance`（模块级函数）：类型未声明 `signature_traits` → 零变化；有声明 → strict 记 error、soft 记 warning、裁判跳过记 info；`bpm/beat_zero_sec` 存在时用网格算卡点命中 | 7 项单测 + 20 项既有质检回归全绿 |

**这一轮的设计要点**：`enable_genre_judge=False` 时报告里必须出现 `judge: "skipped"` 与"未做 LLM 同一性复核"——**不允许"看起来通过了"**。这是 D7 的落地方式：省钱可以，但要留下"没做复核"的痕迹。

### 12.14 第 6 轮：cue 驱动收口（M2/M3）+ `/genre-check` 端点（A-10）+ 全量回归结论

| 项 | 结果 |
|---|---|
| **全量回归** | `pytest tests -v > log`：**3775 passed / 1 failed / 3 skipped / 1 xfailed，6 分 53 秒**。上一轮记录的"5 个 `test_animation_agent_images` 失败"**本轮未复现**（顺序相关，非稳定失败） |
| 唯一失败 | `test_type_system.py::test_validate_definition_ok` —— **是我 A-06 引入的回归**：新校验强制 `genre` 必填，把没写 prompts 的老类型全判错。已修：**写了 prompts 才走新契约校验；完全没写视为未启用，不强制 genre**（与 PRD F6 的兼容承诺一致） |
| 修后验证 | `test_type_system.py` + `test_type_spec_runtime.py` 共 **26 passed** |
| M2/M3 收口 | `scripts/render_plate_shot.py` 增加 `build_cues_for_shot()`：歌词行 → `mv_cues.build_cues`（半拍吸附）→ **`cut_at` 来自 cues，不再是常量**。实测 130BPM 下 `cut_at=1.267s`（0.344 + 4×半拍），换 90BPM 会变 |
| 真机 | 180 帧两次渲染 sha256 全等、闪光 0 违规；`out/plate_shot/cues.json` 落盘（`source: init`，等实测起音替换） |
| A-10 端点 | `POST /api/type-maker/{type_id}/genre-check`：入参时间线 → 采集指标 → L1 判定（L2/L3 受 `enable_genre_judge` 控制），返回 `measured/ok/judge/mismatches/summary`；逻辑抽成 `run_genre_check()` 便于单测 | 
| 端点单测 | 8 项（缺时间线/非法时间线/无声明类型/严格不达标/快速类型通过/BPM 启用卡点指标/未知类型/摘要含"未做 LLM 复核"） |

**教训（已进记忆）**：用 `edit` 在函数体内插入 `def` 会把类/函数结构搞坏——本轮 `quality_agent` 与 `type_maker` 各踩一次，都是靠 `ast.parse` + 端点枚举发现的，不是测试发现的。

### 12.16 第 7 轮：工程包与交付（R-18/R-19）+ 端到端打包验证

| 任务 | 实现 | 验收 |
|---|---|---|
| R-18 工程包载入 | `services/mv_bundle.py`：`load_bundle`（mv/cues/lyrics/chat_script/plan/brief/report，坏文件记 `missing` 不抛）、`validate_bundle`（片段连续/网格非零/必需文件） | 13 项单测（含坏 cues 不崩、段间空洞、零 BPM） |
| R-18 交付打包 | `pack_dist`：storyboard / cues / plan.md / brief.md / report / selftest / assets_gap / CREDITS（+ 可选 preview），**确定性文本**；`tools/pack_dist.py` CLI | 5 项单测 + **真机打包**：`out/dist/plate_shot` 九个文件齐全、`missing: []` |
| R-19 同步检查 | `tools/check_sync.py`：`check_sync(video, audio, preroll)` 纯函数 + `probe_duration`（ffprobe）+ CLI；容差 0.05s | 6 项单测（含"漏掉片头偏移必须报不同步"） |
| 端到端 | plate_shot 工程包补齐 `mv.json / plan.json / brief.json`，渲染脚本回写标准形态 `cues.json`（`{grid, items}`）与 `report.json`；`ffmpeg` 转出 `preview.mp4` | `python -m tools.pack_dist ... --preview` → **`ok: true, missing: []`** |

**这轮修掉的一个接口不一致**：渲染脚本原先写的是 `{bpm, beat_zero_sec, items}`，而 `mv_cues.load_cues` 期望 `{grid: {bpm, beat_zero_sec}, items}`——**同一个文件两种形态**，打包时直接暴露成 `bundle_no_grid`。已统一：渲染脚本改用 `save_cues()` 落盘，工程目录与 `out/` 各存一份。

### 12.18 第 8 轮：plan-first 接线（A-16/A-17）

| 项 | 结果 |
|---|---|
| 新模块 | `services/plan_first.py`：`resolve_plan`（`_plan` / `plan` / `production_plan.plan` 三种来源）、`require_plan`（plan-first 缺计划 → `PlanMissing`）、`plan_prompt_block`（段落目的/画面/卡点/素材缺口渲染）、`detect_drift`（时长/段数/画面类型偏离）、`append_drift`（JSONL） |
| A-16 接线 | `StructureAgent.execute`：注入规划块；**plan_first=True 且无 plan → 直接返回 FAIL（`failure_reason=plan_missing`，warning 里带 V55）**，不退回自由生成 |
| A-17 接线 | 场景最终化后 `detect_drift` → 有偏离则按 `extra_params.plan_drift_path` 追加 JSONL，并把 `plan_drift: <code>` 写进 `_warnings`；留痕失败只 warning，不影响出片 |
| 测试 | 24 项新单测（plan 三来源、空 plan 报错含 V55、prompt 块含目的/画面/BPM/缺口、三类漂移、JSONL 追加、未开 plan_first 零变化）；structure 相关回归 **48 passed** |

**诚实标注**：`detect_drift` 的端到端调用（真的跑一次结构阶段并落 JSONL）尚未在管线里跑过——单测覆盖了纯函数与"缺 plan 必失败"路径，但"有 plan 时正常出片 + 有漂移时落盘"还缺一次集成验证，列入下一轮。

### 12.20 第 9 轮：F 轨启动（F-01/F-02 类型规范编辑器）

| 项 | 结果 |
|---|---|
| 前端契约 | `src/services/api/typeMaker.ts`：新增 `PromptSlot / ParamRule / SignatureTrait / TypeDeclares / ResolveResult / ValidateResult / GenreCheckResult`，以及 `validate()` / `resolve()` / `genreCheck()` 三个方法；导出 `IDENTITY_SLOTS / STAGE_SLOTS / SLOT_LABELS` |
| 新组件 | `src/features/settings/TypeSpecEditor.tsx`：身份层 3 槽位（genre 必填引导）+ 阶段层 7 槽位 + 参数规则表（可增删）+ 指标表（可增删）+ 字符计数（/4000）+ 校验/裁决预览/保存 |
| F-02 冲突预览 | 保存前先 `validate`（错误则拒存）；`resolve` 预览显示 C1/C2 冲突与丢弃槽位；**`type_deformation` 时只显示"这不是参数问题"与出路说明，不提供豁免按钮**（与 PRD 一致） |
| 接线 | `TypeMakerPage` 卡片新增「规范」图标 → 打开编辑器；保存后 `reload()` |
| 验证 | `npx tsc --noEmit` 通过；`npx eslint` 变更文件 0 error 0 warning；**前端全量 103 files / 907 tests passed**（新增 12 项）；`npm run build` 见下 |

**这轮踩到的两个前端约定**：① UI 组件必须从 `@/components/ui` 桶文件导入（`@/components/ui/Button` 会触发 TS1149 大小写冲突）；② `cn` 在 `@/lib/utils` 而不是 `@/lib/cn`。

### 12.22 第 10 轮：F-05 后端蒸馏 + 前端抽屉

| 层 | 交付 | 验证 |
|---|---|---|
| 后端 | `services/prompt_distill.py`：对话 → `{prompt, avoid, rationale}`。优先 LLM 结构化蒸馏；失败/未配置退**规则蒸馏**并标 `degraded=True`；否定句（不要/禁止/avoid…）自动进 `avoid`；去重、限 6 条、prompt 截断 4000 | 15 项单测（LLM 异常降级、空 payload 降级、空 transcript 不调 LLM、规则否定抽取） |
| 后端 | `api/chat_forge.py`：`ChatStartRequest` 增 `scope` / `context`；新增 `POST /api/persona/forge/chat/commit-prompt` | 端点枚举确认存在；chat_forge 相关回归 81 passed |
| 前端 | `services/api/chatForge.ts`（3 个方法）+ `features/chatforge/ChatForgeDrawer.tsx`：右侧抽屉、对话区、蒸馏结果、**「填入提示词区」回填 prompt+avoid+sessionId** | 5 项测试；tsc 通过 |
| 关键行为 | 会话建不起来时**明确降级提示且不再调消息接口**（不假装成功）；`degraded=True` 时界面标注"规则兜底（未用 LLM）" | 两条都有测试锁定 |

**仍未做（F-04）**：创作页去「配音驱动/视觉驱动」二选一 + 提示词区落位。抽屉与蒸馏已就绪，但**入口还没挂到 `HomePage`**——下轮第一件事。

### 12.24 第 11 轮：F-04 创作页改造完成

| 项 | 结果 |
|---|---|
| R1 去二选一 | `HomePage` 删除「配音驱动 / 视觉驱动」两个按钮；`mode` 改为**由材料自动派生**（`useEffect`: 有配音→voiceover，否则→visual），下游 `mode === 'voiceover'` 闸门行为不变（音色选择/自动配音/切分方式照常） |
| R2 提示词区 | 新增 `features/creation/CreativePromptField.tsx`：文本（≤4000）+ 避免项 chips（可单独移除）+ 字符计数 + 「和 AI 聊聊」按钮 |
| 数据落点 | `projectStore` 新增 `creativePrompt` / `creativeAvoid` 与 setter；`launch()` 时随任务写入（供需求阶段消费） |
| R3 抽屉入口 | `ChatForgeDrawer` 挂载在创作页；开屏注入 `type/topic/has_audio/existing_prompt/existing_avoid`；回填 `prompt + avoid` |
| 验证 | **前端 105 files / 918 tests 通过**（新增 6：组件 3 + 源码约束 3）；`tsc` 通过；`eslint` 0 error（余 3 条历史 warning）；`build` 7.24s 通过 |

**源码约束测试**（本轮新增的"反向验收"）：直接读 `HomePage.tsx` 源码，断言 `配音驱动` / `视觉驱动` 字样**不再出现**、`CreativePromptField` 与 `ChatForgeDrawer` 已挂载、`setMode(audio ? 'voiceover' : 'visual')` 存在。这类测试防的是"改回来"。

**尚未接进管线**：`creativePrompt` 只写进 projectStore，**需求阶段还没消费它**（真正生效需要后端从 `extra_params.creative_prompt` 读并合入类型规范/任务提示词）。下一轮补这一环。

### 12.25 下一轮

1. **提示词进管线**：需求/结构阶段读 `creative_prompt` 并合入 `plan`/类型规范，端到端跑一次；
2. **F-06**：规划确认页（brief/plan 双栏）；
3. **R-11/R-12 收口**：字幕带与文字动画抽成可复用构件；
4. **S 轨**：MV 标签集 + 乐句级 query。












### 12.3 已发现的遗留问题（待下一轮）

| 问题 | 现象 | 判断 |
|---|---|---|
| 全量回归偶发 5 个失败 | `test_animation_agent_images.py` 5 项在**全量跑**时失败，**单独跑 11/11 通过**；与新测试同跑也通过 | 顺序/状态污染，**不是本批代码引入**；需单独定位（下一轮用 `-p no:cacheprovider --tb=long` 缩小范围） |
| 全量回归耗时 | 3656 passed / 5 failed / 3 skipped / 1 xfailed，**17 分 01 秒** | 后续里程碑验收需拆成子集跑，否则每轮成本太高 |

### 12.4 下一轮

1. R-04（Day-1 聊天界面场景）落盘 `examples/mv_poc/chat/`，跑 30 秒 + 双渲染哈希，补齐门 1；
2. A-04/A-05（槽位注入 + 剪辑钳制点接管）——把裁决器接进 `edit_agent`，让 V2 从"单测过"变成"成片过"；
3. A-06（`type_maker` 契约 + `extra=forbid` + 校验接线）；
4. 定位上面那条顺序污染。

## 附 A 任务 → 验收 对照（速查）

| 任务 | 验收编号 | 任务 | 验收编号 |
|---|---|---|---|
| R-01/R-03 | V21、V22 | A-03 | V2、V5、V18 |
| R-02 | V23 | A-04 | V1、V6、V15 |
| R-05 | 门 3 | A-06 | V8–V12、V20 |
| R-06/R-07 | V28 | A-08 | V7 |
| R-08/R-09/R-15 | V29、V30 | A-09 | V16、V17 |
| R-10 | V33 | A-10 | V13 |
| R-11/R-13 | V24、V25 | A-12–A-18 | V52–V60 |
| R-16 | V26、V27 | S-01–S-03 | V41、V42 |
| R-17 | V34 | S-04 | V43 |
| R-18/R-19 | V32、V37、V38 | F-04–F-06 | V44–V48 |

## 附 B 第一天就能开始的三个动作

1. **`SP1`**：写一个 30 行的 `compositions/spike/index.html` + `scene.js`，用现有 `render_mg_html_to_mov()` 渲 5 秒，跑两次 sha256。结论写进 `docs/spike-results-2026-10-18.md`。
2. **`A-01`**：把 `CreativeBrief` 的字段补到与 prompt 一致（或改 prompt 对齐），并加一条"字段集包含"单测——**这是全表里最短的一条，也是唯一一条今天就能合进主干、明天就能证明"不再丢字段"的**。
3. **`R-16` 原型**：用现有 ffmpeg 生成一段 10 秒的 4Hz 黑白闪片，写 40 行脚本看能不能报出违规。检测器的"已知答案测试"是后面所有闪光闸的地基。
