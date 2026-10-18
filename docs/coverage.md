# 验收覆盖表（V1–V60）

> 计划 §12.37 承诺的交付物：把 PRD 的 60 项验收逐条落到"现在什么状态、证据在哪、还差什么"。
> **状态定义**：`通过` = 有自动化证据且已跑过；`部分` = 有实现/单测但缺真机或端到端；`未做` = 没有；`人工` = 只能由人判定。
>
> 生成于 2026-10-18。最近一次全量后端回归（第 30 轮末）：**4113 passed / 3 skipped / 1 xfailed / 0 failed**（7 分 14 秒，`python -m pytest tests -q`）；前端 **108 文件 / 939 项全绿**、`tsc --noEmit` 0 错、`eslint` 0 错、`npm run build` 成功。
> **2026-10-18 补记**：新增 `scripts/e2e_pipeline_probe.py` 真机端到端探针（真 LLM + Mongo + 完整管线 dry-run），V2/V3/V5/V45 的证据随之更新；补上此前遗漏的 **Part IV（V52–V60）** 逐条表；总数改为按本文件实际行数统计（此前"通过 34"与表内不符）。

## 总览

| 状态 | 数量 | 说明 |
|---|---|---|
| 通过 | 47 | 有自动化证据 |
| 部分 | 5 | 有实现或单测，缺真机/端到端/事件留痕 |
| 未做 | 3 | V17（裁判未接真 LLM）、V34（4K 单帧未渲）、V50（无素材） |
| 人工 | 4 | V16、V30、V40、V41 |
| 合计 | 59 + V49（部分 4/7） | V1–V60 共 60 行 |

---

## Part I：类型插件提示词化（V1–V20）

| 编号 | 验收项 | 状态 | 证据 / 差距 |
|---|---|---|---|
| V1 | 10 槽位可声明并注入对应阶段 | 通过 | 新增 `services/type_prompts.py`（固定序 原文 → 身份 → 阶段）；7 个阶段各自落地：`structure`（`compose_structure_prompt`）、`material`（`_category_search_hint` 回落 material 槽位首句）、`edit`（剪辑档案 prompt）、`animation`（`_mg_category_context["stage_prompt"]` → MG 上下文段落）、`audio`+`subtitle`（音频 Agent 同时管字幕，`compose_many`）、`quality`（语义质检 prompt）；身份层另注入 requirements。测试：`test_type_prompts.py` 8 项 + `test_stage_prompt_wiring.py` 5 项（edit/audio/quality 用打桩 LLM **捕获真实 system prompt**，animation 走 MG 上下文段落，structure 走模块函数）。**说明**：material 是唯一没有 LLM 消费者的阶段，规范落到检索式上。**第 30 轮补齐声明侧**：五个内置类型全部有了自己的规范——`lyric_mv` 10 槽位，`knowledge_longform` / `kichiku_fastcut` / `digital_review` / `vlog_daily` 各 7 槽位 + 2 条参数规则 + 2 条指标（`test_builtin_type_specs.py` 34 项，`validate_bundle` 全 0 问题）；真机 vlog_daily 运行的 `prompt_resolution.slots` 里出现 **7 个槽位**（此前只有旧钩子合成的 2 个） |
| V2 | `min_shot_ms` strict floor 生效 | 部分 | 单测：`test_prompt_authority.py`（传 300 → 1200）+ `test_type_spec_runtime.py`。**真机证据**（`scripts/e2e_pipeline_probe.py --scenario floor`）：探针类型声明 `min_shot_ms ≥ 1200 strict`，用户给 300 → trace 里 `prompt_resolution` 显示 `min_shot_ms=1200, source_layer=L2_prompt, overridden_by=prompt`，冲突 `C1`；同一探针把 floor 提到 1200 时时间线片段 35/55/70/55/40s。**已定位缺陷**：`min_shot_ms` 目前只钳制 LLM 剪辑档案的 `base_shot_ms` 与兜底时长，**不构成每段时长的硬下限**——floor=45000（合法区间）那次跑出 30/120/90/60s，首段低于下限（详见「本轮新定位的缺陷」） |
| V3 | strict 冲突 → C1 事件 + 前端黄条 | 通过 | 后端：C1 记录进 trace（真机 `prompt_resolution.detail.conflicts[0]`）。前端：`TypeConformancePanel` 新增 strict 冲突黄条 `type-conflict-banner` + 类型变形横幅 `type-deformation-banner`，`PipelineAdminPage` 运行详情内直接渲染；`TypeConformancePanel.test.tsx`（10 项），其中一项**用真机 trace 夹具**（`__fixtures__/trace-real-floor.json`，由探针产出）断言黄条出现且来源层显示"类型提示词" |
| V4 | soft 规则只记录 `param_conflict` | 通过 | `test_prompt_authority.py::test_soft_floor_keeps_user_value_and_records_c2` |
| V5 | L2 覆盖 L3 且标 `overridden_by: prompt` | 通过 | `test_prompt_authority.py::test_strict_floor_overrides_user_value`；**真机**：真机 trace 里 `min_shot_ms.overridden_by == "prompt"`、`source_layer == "L2_prompt"`（探针 `--scenario floor`） |
| V6 | `list[dict]` 结构模板渲染为可读文本 | 通过 | `test_type_spec_runtime.py::TestLegacyAdapter`（断言无 `{` / `'role'`） |
| V7 | 5 处硬编码迁出后新类型声明即生效 | 通过 | `test_declares_migration.py`（18 项）+ `declares` 旧集合兜底断言 |
| V8 | 自定义类型 YAML 含 prompts 保存往返不丢 | 通过 | `TypeDefinition.extra=forbid` + `test_type_spec_runtime.py::TestDynamicPluginSpec` |
| V9 | 未知字段 → 400 | 通过 | 同上 `test_unknown_field_rejected` |
| V10 | 悬空数字 → 保存被拒 | 通过 | `test_category_spec.py::TestDanglingNumbers` |
| V11 | 含「忽略以上指令」的类型提示词 → 400 | 通过 | `schema/category_spec.detect_prompt_injection()` 规则检测（忽略指令/泄露提示词/角色重置/越狱模式），`test_prompt_safety.py` |
| V12 | `param_rules` 越界键（如 `fps`）→ 400 | 通过 | `rule_key_allowed()` 白名单（剂量类 + `transition_weights./subtitle_/bgm_/pip_` 前缀），`test_prompt_safety.py` |
| V13 | 同输入两次裁决逐字节一致 | 通过 | `test_resolution_determinism.py`（4 项：同输入同字节、字典顺序无关、sort_keys 稳定、冲突顺序稳定） |
| V14 | 既有后端测试全绿 | 通过 | 全量 `python -m pytest tests -q`：**4113 passed / 3 skipped / 1 xfailed / 0 failed**（7 分 14 秒，第 30 轮末）；此前 A-06 的 genre 必填回归已修并复跑确认 |
| V15 | 身份层注入且顺序固定 | 通过 | `type_prompts.compose` 固定序（原文 → 身份 → 阶段）有单测断言索引关系；身份层实际注入 5 处：requirements、structure、edit、audio、quality（后三处用打桩 LLM 捕获 system prompt 验证内容与顺序） |
| **V16** | 三类型可判别 | **人工** | 需开 `enable_genre_judge` 或人工盲评 |
| V17 | 错配可判且给证据 | 未做 | 裁判（L2/L3）未接真实 LLM |
| V18 | `type_deformation` 不给豁免 | 通过 | `test_prompt_authority.py` + `TypeSpecEditor.test.tsx`（断言无"豁免"按钮） |
| V19 | 类型切换记录 `from_type/to_type` | 通过 | `services/type_switch.py`（`plan_switch` 校验两端类型、`append_switch` 写 JSONL）+ `POST /api/type-maker/switch`；`test_type_switch.py`（8 项） |
| V20 | `genre` 与指标矛盾 → 拒 | 通过 | `test_category_spec.py::TestTraits` |

---

## Part II：MV/PV 能力（V21–V48）

| 编号 | 验收项 | 状态 | 证据 / 差距 |
|---|---|---|---|
| V21 | 30 秒 POC 三画面各 ≥1 段 | 通过 | `scripts/build_poc_30s.py`：把三段**已过哈希闸**的确定性产物拼成一条 30 秒片——界面叙事 12s（`out/mv_poc/chat_a.mov`）+ 版画 8s（`out/plate_shot/a.mov`）+ 代码/数据 10s（`out/archetypes/code_data/a.mov`），960x540@30、h264。两遍装配**逐帧一致**（`frames_compared: 900`、`first_diff_frame: null`）、闪光 0 违规、时长 30.0s；报告 `out/poc30s/report.json`。**口径**：这是"拼装"（硬切），不做转场与卡点对齐——后者属 R-05 后续工作 |
| V22 | 逐帧哈希全等 | 通过 | 真机四次：24/24、300/300（1080p60）、180/180（版画）、360/360（聊天） |
| V23 | 确定性 lint 命中即失败 | 通过 | `tools/lint_determinism.py` + 7 项单测；**未接 CI** |
| V24 | 拍点命中率 ≥95% | 通过 | `services/mv_cues.grid_hit_rate` + 单测；POC report 未含该字段 |
| V25 | 逐词误差 ≤42ms | 部分 | cues 支持 `measured/init` 标记；**无实测起音数据** |
| V26 | 闪光 violations=0 | 通过 | 三个真机产物均 `flash_violations: []` |
| V27 | 闪光超标拒绝交付 | 通过 | `tools/check_flash.py` 退出码 + `gate_check` V26；**D14（拒绝 vs 标记）仍待拍板** |
| V28 | 三墨可独立取用、alpha<16 清零 | 通过 | `test_plate_library.py` + 检查图 `out/plate/check_sheet.png` |
| V29 | 错版/降帧/撕裂可参数化 | 通过 | `test_plate_library.py::TestDegrade`、`test_plate_transitions.py` |
| **V30** | 字符版画不看字能认出（20 张） | **人工** | 已有 1920 单帧可辨胸像（`out/plate/glyph_1920.png`），抽检要人做 |
| V31 | `scene.js` 不含歌词文本 | 通过 | 已修：场景数据拆成 `lines`（仅 i/t0/t1）+ `texts`（按索引取），`scene.js` 读 `data.texts[i]`；有源码断言 + 数据形状断言；重渲后帧哈希与修改前**完全一致**（行为等价） |
| V32 | 负时间片头音画同步 | 部分 | `tools/check_sync.py` + 6 项单测；**未做负时间片头实现** |
| V33 | 剪影配准 ≤1px | 部分 | `plate/registration.py` + 单测；**无真实剪影素材** |
| **V34** | 4K hero 特写无锯齿 | 未做 | 矢量化原语有（`vectorize.py`），4K 单帧未渲 |
| V35 | 成片被判 `lyric_mv` | 部分 | **判定链路真机通了**：新增内置类型 `lyric_mv`（10 槽位 + 2 条 strict 指标 + 4 条参数规则 + declares；`validate_bundle` 0 问题），启动时注册；`scripts/check_genre_v35.py` 拿真管线时间线调 `/api/type-maker/lyric_mv/genre-check` → 有指标、有逐条判定、`judge: skipped`（D7）。**真运行不达标**：avg_shot_ms **6166.67**（要求 ≤3200 strict）、text_overlay 0.988 ✓、beat_align **1.0** ✓ → 综合 1/2「不通过」。根因：片段时长由结构阶段场景时长决定，类型节奏参数压不下去（与 D23 同源）。**判定器有区分度**：`test_lyric_mv_category.py` 12 项，MV 形态时间线（8×3s + 文字覆盖 90%）判过、长镜头（1×20s）判不过 |
| V36 | 卡点子代理超时降级 | 通过 | 子代理超时/取消机制（前序批次）+ 单测 |
| V37 | 交付九件套齐全 | 通过 | `out/dist/plate_shot` 九文件、`gate_check` V37 通过 |
| V38 | token 与渲染时长入账 | 通过 | **token**：`pipeline_v2._usage_totals_from_steps` 汇总各 Agent `llm_usage` 成运行总账（`input/output/total/calls/by_agent`），`GET /api/pipeline/runs` 返回 `usage`；真机 dry-run 记到 **10859 tokens / 3 calls**（structure 5697+2465、material 645+597、edit 1133+322）。**渲染耗时**：渲染任务完成时写 `render_seconds`（wall-clock）并同步 Mongo（`api/render.py` 两个入队路径都补），真机 2 秒片子渲染 **2.4s**；`result.duration_sec` 是成片时长（2.0），两者分开记、避免同名混淆；CLI 侧另有 `report.json.render_sec` |
| V39 | 后端测试全绿 | 通过 | 同 V14（4113 passed / 0 failed） |
| **V40** | 人工终审（3 人 ≥2 说得出类型） | **人工** | — |
| **V41** | 素材落位 10 个 ≥8 认可 | **人工** | 代码侧已闭环（落位→写回→剪辑取用）；判据需真实素材与人 |
| V42 | 素材缺口显式化 | 通过 | `gate_check` V42 + `placement_report.gaps` |
| V43 | 授权不明不进成片 | 通过 | `test_mv_material.py::test_license_unclear_is_rejected_not_used` |
| V44 | 创作页无二选一 | 通过 | `CreativePromptField.test.tsx`（源码约束 3 项） |
| V45 | 提示词生效且留痕 | 通过 | **留痕**：trace `prompt_resolution`（真机，含值/来源层/冲突）。**到达模型**：`scripts/smoke_llm_prompt.py` 真 LLM 复述四个约束。**量化影响**：新增 `scripts/ab_creative_prompt.py`（对照组不带提示词、实验组带"版画质感，三墨套印，硬切为主；不要花哨转场"），多对真机 dry-run 实测——时间线元数据里的提示词关键词命中 **0 → 18/19/21/22**（四对里三对基线为 0、一对为 4），**而镜头节奏无稳定方向**（平均镜头 4.83=4.83、5.0=5.0、75.0 vs 6.0）。结论：提示词稳定改写"画面里放什么"，不稳定改写"怎么剪"。**副产物**：A/B 暴露"创作提示词进不了剪辑阶段"的真缺口，已补（`_llm_decide_edit_profile` 注入 `creative_prompt_block`，有测试） |
| V46 | 对话按钮打开抽屉带上下文 | 通过 | `ChatForgeDrawer.test.tsx`（5 项） |
| V47 | 结论回填且记 session_id | 通过 | 抽屉回填 + `AgentPanel.creativePrompt.test.ts` |
| V48 | 对话失败不阻塞创作 | 通过 | 抽屉测试（会话失败不调消息接口） |

---

## Part III：覆盖审计（V49–V51）

| 编号 | 验收项 | 状态 | 证据 / 差距 |
|---|---|---|---|
| V49 | 1–7 类形态各渲 ≥10s 片段 | 部分（4/7） | 已渲并过三闸：`typography`（排版动画）、`generative`（生成艺术）、`code_data`（代码与计数）、`vj_light`（VJ 光条）——各 240 帧、两次渲染全等、闪光 0 违规；`scripts/render_archetypes.py` + `out/archetypes/report.json`。**未渲**：界面叙事（已有 chat POC，可算 5/7）、版画拼贴（已有 plate_shot，可算 6/7）之外的纯图形歌词视频（7/7 需歌词时间轴） |
| **V50** | 素材类 4 类样例（手绘/2D/3D/实拍） | 未做 | 无素材 |
| **V51** | `coverage.md` 逐条标注 | 通过 | 本文件 |

---

## Part IV：M8 规划书（V52–V60）

| 编号 | 验收项 | 状态 | 证据 / 差距 |
|---|---|---|---|
| V52 | plan 由结构化对象渲染，同 plan 两次渲染逐字节一致 | 通过 | `services/plan_renderer.py`；`test_plan_first.py`（渲染两次比对 + 模板化标题）；无时间戳/随机量 |
| V53 | 字段漂移消除：`CreativeBrief` ⊇ prompt 声明字段集 | 通过 | 单测 `test_plan_first.py::TestBriefFieldDrift`（模板与 `model_fields` 双射、旧数据补默认、未知字段丢弃）；**真实产物检查**：`examples/mv_poc/plate_shot/brief.json`（真实落盘简报）9 个键 **0 个未知**、可被 `CreativeBrief(**d)` 正常解析 |
| V54 | 类型 `plan_schema.required` 缺项 → 拒绝 | 通过 | `plan_validator.validate_plan` + `test_plan_first.py`（缺必填维度/未知维度 400、`assert_confirmable` 抛错） |
| V55 | plan 是结构阶段上游：空 plan → 结构阶段明确失败 | 通过 | `services/plan_first.py` + `agents/structure_agent.py`（`plan_first=True` 且无 plan → `failure_reason=plan_missing`）；`test_plan_first_wiring.py` 断言 FAIL 与文案 |
| V56 | 段落不连续/重叠/时长偏差 >25% → 拒绝 | 通过 | `test_plan_first.py`（`plan_section_gap`/`plan_section_overlap`/`plan_section_not_from_zero`/`DRIFT_WARN 0.10`→`DRIFT_REJECT 0.25` 两段式） |
| V57 | `beats` 与音频 BPM 不一致 → 拒绝 | 通过 | `validate_plan(..., audio_grid=...)` + `test_plan_first.py::test_beats_mismatch/match` |
| V58 | 执行改段 → `plan_drift` 事件可查且 plan 被写回 | 通过 | 三通道齐：① trace 事件（`plan_first.emit_drift_events`）；② JSONL（`append_drift`）；③ **写回会话规划书**（`requirements_service.apply_plan_drift/write_back_plan_drift`，`proceed` 把 `session_id` 透传给管线，结构阶段 `await asyncio.to_thread(...)` 写回）。**真机端到端**（`scripts/e2e_pipeline_probe.py --with-plan --writeback`）：注入合法规划（2 段 0→20s、`plan_first`）→ 管线 `completed`，trace 2 条 `plan_drift`（`duration` + `section_count`），回读 `/api/requirements/session/{id}` 得 `production_plan._drift` **2 条**（code 齐全）、规划本体（`sections`）原样保留；时间线总长 20.0s = 规划目标时长。单测：`test_plan_drift_writeback.py`（11 项，含幂等去重/上限 20 条/无会话返回 0/写回失败不抛） |
| V59 | 交付同时含 `brief.md` 与 `plan.md`，内容与源对象一致 | 通过 | 真实交付 `out/dist/plate_shot/` 里 **`brief.md` 与 `plan.md` 都存在**，且与 `render_brief_markdown(brief)` / `render_plan_markdown(plan, brief)` 的输出**逐字节相等**（本轮比对）；新增 `test_mv_bundle.py::test_pack_md_content_matches_source_objects` 把这条固化为回归；`tools/gate_check.py` 的 V59 判定同时检查双文档存在 |
| V60 | 老 session（只有 scenes）→ 反向生成 `legacy` plan，不报错且下游可跑 | 通过 | `scripts/e2e_legacy_plan.py` 真机全链路：API 建会话 → 写成"老形态"（只有 `production_plan.raw_scenes`，状态 `brief_ready`）→ 只把 `_generate_plan` 换成返回 None（模拟翻译失败）→ API 发「确认」→ 状态转 `plan_ready`、`legacy: true`、3 段落、`markdown_content` 有内容、`scene_count=3`、**老 `raw_scenes` 保留**；随后 `POST /api/requirements/proceed` 真起管线 → **completed**（28.5s）。分支回归：`test_legacy_plan_session.py` 2 项（有 scenes 走 legacy、无 scenes 明确回退 `brief_ready` 不硬造）；另有 `test_legacy_plan.py` 4 项纯函数测试 |

---

## 本轮新定位的缺陷（已查清、未修）

| 缺陷 | 现象与证据 | 为什么先不修 |
|---|---|---|
| `min_shot_ms` 不构成逐段硬下限 | 探针 `--floor 45000 --max-ms 200000`（区间合法）：`prompt_resolution` 里 `min_shot_ms=45000 / L2_prompt`，但成片时间线四段是 **30/120/90/60s**，首段低于下限。代码层面原因明确：`edit_agent` 里 `min_shot_ms` 只进 `_clamp_llm_profile`（钳 `base_shot_ms`），片段时长由 `scene.duration_sec × duration_scale` 决定，没有再被 min 夹一次 | 逐段夹紧会改变总时长，与"配音时长优先对齐"（字幕/画面必须跟配音）冲突——需要先定策略：是拉长画面、还是重复素材、还是允许低于节奏下限但记 C2。已作为下一轮待办，不在此轮拍脑袋改 |

---

## 计划外的人工验收（无法自动化，必须安排人）

| 项 | 内容 | 来源 |
|---|---|---|
| A8 | 5 人可用性测试 | PRD 附录 |
| A10 | 20 题盲评（取更差结果） | PRD 附录 |
| A5 | 真实 token 账单核对 | PRD 附录 |
| A13 | 逐字节时间线对比脚本 + 人工复核 | PRD 附录 |
| 门 4 人工项 | V16 / V30 / V34 / V40 / V41 | 见上 |

---

## 如何复跑（命令清单）

```powershell
# 后端回归（建议分片，避免 17 分钟长跑）
cd J:\Clipwright
python -m pytest tests/clipwright -q -k "agent_kernel or prompt_authority or category_spec"
python -m pytest tests/clipwright -q -k "plate or mv_ or bundle or gate_check"

# 门禁工具
python -m tools.lint_determinism examples/mv_poc
python -m tools.hash_frames out/mv_poc/chat_a.mov out/mv_poc/chat_b.mov --width 960 --height 540
python -m tools.check_flash out/plate_shot/a.mov --fps 30
python -m tools.check_sync --video out/plate_shot/a.mov --audio audio/song.wav --preroll 5
python -m tools.gate_check --bundle examples/mv_poc/plate_shot --dist out/dist/plate_shot

# 真机渲染（会开浏览器，慢）
python scripts/render_mv_poc.py --seconds 12 --fps 30 --width 960 --height 540
python scripts/render_plate_shot.py --seconds 6 --fps 30 --width 960 --height 540 --bpm 130

# 真机端到端（真 LLM + Mongo，2 分钟内跑完，产出 out/e2e/report-*.json 与 trace-*.json）
python scripts/e2e_pipeline_probe.py --timeout 480                        # 内置类型直跑
python scripts/e2e_pipeline_probe.py --scenario floor --set min_shot_ms=300
python scripts/e2e_pipeline_probe.py --scenario floor --floor 45000 --max-ms 200000 --set min_shot_ms=300
python scripts/e2e_pipeline_probe.py --with-plan                       # V58：注入规划 → plan_drift 事件 + JSONL

# 前端
cd J:\Clipweight-Client
npx tsc --noEmit; npx eslint src; npm run test; npm run build
```

---

## 结论（一句话）

**能自动化的项目里：47 项通过、5 项部分、3 项未做（V17/V34/V50）、1 项部分达标（V49 4/7）；另有 4 项只能由人判定（V16/V30/V40/V41），以及 4 项计划外人工验收（A5/A8/A10/A13）。**
"部分"集中在三类：① 真机端到端（V2/V25/V32/V35/V45）；② 事件留痕与写回（V38/V45）；③ 需要素材或真落库的项（V33/V34/V41）。
"未做"三项里，V17 是类型裁判接真 LLM（依赖 D7 决策，默认不开），V50 需要外部素材，V34 需要 4K 单帧渲染窗口。
**第 27–28 轮收口**：V1/V15（10 槽位逐阶段注入 + 身份层固定序）、V38（token 真机 10859 入账 + 渲染耗时 render_seconds 真机 2.4s）、V45（A/B 量化）、V60（老会话→legacy plan→真管线跑通）。
**前一轮收口**：V21（30 秒 POC）、V53（真实 brief）、V58（三通道写回）、V59（交付双文档逐字节一致）。
**本轮修的隐患**：`tools/hash_frames.hash_video` 在调用方传入的宽高与源不一致时**不加缩放就按给定尺寸切字节**，把一帧切成四块（900 帧报成 3600）；已改为"尺寸不符则解码时缩放 + 字节数不整除即报错"，并补真机回归测试。
**仍未拍板**：PRD D23（`min_shot_ms` 是否作逐段硬下限）——真机已跑出 30s 片段，改实现前需要先定策略。
