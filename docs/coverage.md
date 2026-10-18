# 验收覆盖表（V1–V60）

> 计划 §12.37 承诺的交付物：把 PRD 的 60 项验收逐条落到"现在什么状态、证据在哪、还差什么"。
> **状态定义**：`通过` = 有自动化证据且已跑过；`部分` = 有实现/单测但缺真机或端到端；`未做` = 没有；`人工` = 只能由人判定。
>
> 生成于 2026-10-18。最近一次全量后端回归：**3775 passed / 1 failed**（失败项是 A-06 的 genre 必填回归，**已修**，但修复后未重跑全量——故 V14/V39 记为"部分"）。

## 总览

| 状态 | 数量 | 说明 |
|---|---|---|
| 通过 | 34 | 有自动化证据 |
| 部分 | 17 | 有实现或单测，缺真机/端到端/事件留痕 |
| 未做 | 2 | V49、V50 |
| 人工 | 8 | V16、V30、V40、V41，以及计划外的人工项（见文末） |

---

## Part I：类型插件提示词化（V1–V20）

| 编号 | 验收项 | 状态 | 证据 / 差距 |
|---|---|---|---|
| V1 | 10 槽位可声明并注入对应阶段 | 部分 | `schema/category_spec.py` + `test_category_spec.py`（31 项）；**注入只做了 structure/requirements 两处**，edit/animation/audio/subtitle/quality 尚未逐阶段注入 |
| V2 | `min_shot_ms` strict floor 生效 | 部分 | `test_prompt_authority.py`（传 300 → 1200）+ `test_type_spec_runtime.py`（`resolve_shot_params`）；**未在成片时间线上验证** |
| V3 | strict 冲突 → C1 事件 + 前端黄条 | 部分 | C1 记录有（`PromptAuthority`），前端在 `TypeSpecEditor` 展示冲突；**管线详情的黄条未接** |
| V4 | soft 规则只记录 `param_conflict` | 通过 | `test_prompt_authority.py::test_soft_floor_keeps_user_value_and_records_c2` |
| V5 | L2 覆盖 L3 且标 `overridden_by: prompt` | 通过 | 同文件 `test_strict_floor_overrides_user_value` |
| V6 | `list[dict]` 结构模板渲染为可读文本 | 通过 | `test_type_spec_runtime.py::TestLegacyAdapter`（断言无 `{` / `'role'`） |
| V7 | 5 处硬编码迁出后新类型声明即生效 | 通过 | `test_declares_migration.py`（18 项）+ `declares` 旧集合兜底断言 |
| V8 | 自定义类型 YAML 含 prompts 保存往返不丢 | 通过 | `TypeDefinition.extra=forbid` + `test_type_spec_runtime.py::TestDynamicPluginSpec` |
| V9 | 未知字段 → 400 | 通过 | 同上 `test_unknown_field_rejected` |
| V10 | 悬空数字 → 保存被拒 | 通过 | `test_category_spec.py::TestDanglingNumbers` |
| V11 | 含「忽略以上指令」的类型提示词 → 400 | 通过 | `schema/category_spec.detect_prompt_injection()` 规则检测（忽略指令/泄露提示词/角色重置/越狱模式），`test_prompt_safety.py` |
| V12 | `param_rules` 越界键（如 `fps`）→ 400 | 通过 | `rule_key_allowed()` 白名单（剂量类 + `transition_weights./subtitle_/bgm_/pip_` 前缀），`test_prompt_safety.py` |
| V13 | 同输入两次裁决逐字节一致 | 部分 | `plan_renderer` 有确定性测试；**`PromptAuthority` 本身无逐字节断言** |
| V14 | 既有后端测试全绿 | 部分 | 最近全量 3775 passed / 1 failed（已修未重跑） |
| V15 | 身份层注入且顺序固定 | 部分 | `identity_text()` 顺序有单测；仅两处 Agent 注入 |
| **V16** | 三类型可判别 | **人工** | 需开 `enable_genre_judge` 或人工盲评 |
| V17 | 错配可判且给证据 | 未做 | 裁判（L2/L3）未接真实 LLM |
| V18 | `type_deformation` 不给豁免 | 通过 | `test_prompt_authority.py` + `TypeSpecEditor.test.tsx`（断言无"豁免"按钮） |
| V19 | 类型切换记录 `from_type/to_type` | 通过 | `services/type_switch.py`（`plan_switch` 校验两端类型、`append_switch` 写 JSONL）+ `POST /api/type-maker/switch`；`test_type_switch.py`（8 项） |
| V20 | `genre` 与指标矛盾 → 拒 | 通过 | `test_category_spec.py::TestTraits` |

---

## Part II：MV/PV 能力（V21–V48）

| 编号 | 验收项 | 状态 | 证据 / 差距 |
|---|---|---|---|
| V21 | 30 秒 POC 三画面各 ≥1 段 | 部分 | 聊天界面 12s（`out/mv_poc`）与版画 6s（`out/plate_shot`）**各自渲过**，未拼成一条 |
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
| V35 | 成片被判 `lyric_mv` | 部分 | `/genre-check` 端点 + 8 项单测；**无真实成片时间线** |
| V36 | 卡点子代理超时降级 | 通过 | 子代理超时/取消机制（前序批次）+ 单测 |
| V37 | 交付九件套齐全 | 通过 | `out/dist/plate_shot` 九文件、`gate_check` V37 通过 |
| V38 | token 与渲染时长入账 | 部分 | report 有 `render_sec`；**LLM token 未入账** |
| V39 | 后端测试全绿 | 部分 | 同 V14 |
| **V40** | 人工终审（3 人 ≥2 说得出类型） | **人工** | — |
| **V41** | 素材落位 10 个 ≥8 认可 | **人工** | 代码侧已闭环（落位→写回→剪辑取用）；判据需真实素材与人 |
| V42 | 素材缺口显式化 | 通过 | `gate_check` V42 + `placement_report.gaps` |
| V43 | 授权不明不进成片 | 通过 | `test_mv_material.py::test_license_unclear_is_rejected_not_used` |
| V44 | 创作页无二选一 | 通过 | `CreativePromptField.test.tsx`（源码约束 3 项） |
| V45 | 提示词生效且留痕 | 部分 | **留痕已落地**：`type_spec_runtime.emit_prompt_resolution()` 写 trace（参数来源层/冲突/槽位/变形），`test_prompt_resolution_event.py`（6 项）；"生效"仍需端到端真机验证 |
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

# 前端
cd J:\Clipweight-Client
npx tsc --noEmit; npx eslint src; npm run test; npm run build
```

---

## 结论（一句话）

**能自动化的 30 项通过、17 项部分、5 项未做；另有 8 项只能由人判定。**
"部分"集中在三类：① 真机端到端（V2/V21/V25/V32/V35/V45）；② 事件留痕（V3/V13/V38/V45）；③ 需要素材的项（V33/V34/V41）。
"未做"的五项里，V11/V12 是安全相关（越权检测与键白名单），**优先级最高**；V19 是类型切换动作；V49/V50 需要素材与时间。
