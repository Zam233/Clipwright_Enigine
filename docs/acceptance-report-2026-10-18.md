# 全量验收报告（V1–V60）

> 交付物对应计划 W9「全量验收报告」。本文只做三件事：**结论、证据、复跑命令**。
> 逐条状态的权威来源仍是 [coverage.md](coverage.md)；本文是给人读的一页纸汇总。
>
> 生成时间：2026-10-18（第 39 轮更新：D23 拍板落地 + V17 真 LLM 裁判 + V35 清零 + 前端 e2e 全绿）。最近一次全量回归见 `coverage.md` 头部（本轮：后端 **4326 passed / 4 skipped / 1 xfailed / 0 failed**；前端 **110 文件 / 950 项全绿**、`tsc --noEmit` 0 错、`playwright` e2e **54 passed**）。

## 1. 一句话结论

**60 项里 55 项有自动化证据，0 项"部分"、0 项"未做"；剩下 5 项只能由人判定**（V16/V30/V40/V41 主观验收 + V50 需人提供四类真实片源）。D23 已拍板并落地（"保总时长"优先），V2/V17/V35 随之清零。

| 状态 | 数量 | 具体 |
|---|---|---|
| 通过 | **55** | V1–V15、**V17**、V19–V29、V31–V39、V42–V49、V51–V53、V55–V60，以及本轮补齐的 **V2 / V35**（明细见 coverage） |
| 部分 | **0** | — |
| 未做 | **0** | — |
| 人工 | **5** | V16、V30、V40、V41、**V50**（片源需人提供） |
| 合计 | 60 | V1–V60 |

## 2. 本轮清零的三项（原"部分/未做"）

| 项 | 原来卡在哪 | 现在怎么解 |
|---|---|---|
| **V2** `min_shot_ms` strict floor | 只剩"默认策略"这个决定 | **D23 拍板**：低于下限 `merge`（相邻场景合并，镜头变少变长，**总时长不变**）设为默认。`test_edit_agent_shot_rhythm.py`：floor=9s 的 24s 规划 → 片段全部 ≥9s 且总和仍 24.0s；`extend` 对照则总和 >24s。真机 `e2e_shot_floor_d23.py`：`record` 5 条 C2 / `extend` 45s / `merge` 最小片段 15s |
| **V17** 错配可判且给证据 | 裁判没接真 LLM | 新增 `services/genre_judge.py`（真 LLM + 结构化 JSON + 时间线分轨摘要），开关仍按 D7 默认关。真机 3/3：`mv_shaped` passed 0.90；**L1 两项全过但字幕是口播稿的 `talk_slice` → mismatch 0.96**（V17 的核心一格）；`long_take` mismatch 0.99 |
| **V35** 成片被判 `lyric_mv` | 类型节奏压不住场景时长（avg 6166.7ms） | 上界 `split` 策略（超长场景切分，保总时长）+ 类型自洽（`lyric_mv` 上限 8000/6000/4000 → 3200/3000/2000；另修 `kichiku_fastcut` / `digital_review` 的 low 档基准）。真机复跑 **avg_shot_ms 2926.83 ✓**、text_overlay 0.98 ✓、`mismatches: []` |

## 3. 五项"人工"与计划外人工

| 项 | 原因 | 谁能解 |
|---|---|---|
| V50 素材类 4 类样例 | 四类（手绘/2D/3D/实拍）片源的授权与出处必须可追溯，**不伪造** | 你提供片源；`scripts/check_v50_material_samples.py` 已就绪（现状如实报 4 类缺失） |
| V16 / V30 / V40 / V41 | 类型判别、20 张版画可辨、3 人终审、10 个落位认可 | **只能由人判** |

计划外人工验收：A5（真实 token 账单）、A8（5 人可用性）、A10（20 题盲评）、A13（逐字节时间线人工复核）。

## 4. 本轮新增的前端

| 位置 | 内容 |
|---|---|
| `ExportPage` | **V32 片头秒数输入**（0–15s，`clampPreroll` 归一到 0.1s；0 不传字段，保持后端默认）。文案明确写"画面先起 N 秒、音乐延后同样时长" |
| `PipelineAdminPage` | **V38 用量展示**：新增「LLM 用量」stat 卡（`usage.total_tokens` 求和）与每条 run 的 token/调用次数/渲染耗时行（`data-testid="run-usage"`）。没有用量记录时显示 `—`，不编造 |
| `HomePage` | **D23 镜头下限策略**四档选择（`data-testid=shot-floor-policy`，默认 off），非 off 才下发 `shot_floor_policy`；4 项源码级约束测试防"忘下发" |

## 5. 复跑命令（全部可一键重跑）

```powershell
# 后端全量回归（约 7 分钟；RAG 单测要离线，否则会去 HuggingFace 拉模型元数据）
cd J:\Clipwright
$env:HF_HUB_OFFLINE=1; python -m pytest tests -q

# 门禁工具
python -m tools.lint_determinism examples/mv_poc
python -m tools.hash_frames out/poc30s/poc30s_a.mp4 out/poc30s/poc30s_b.mp4 --width 960 --height 540
python -m tools.check_flash out/archetypes/lyric_graphic/a.mov --fps 30
python -m tools.check_sync --video renders/v32_preroll.mp4 --audio _cache/v32/song.wav --preroll 1
python -m tools.gate_check --bundle examples/mv_poc/plate_shot --dist out/dist/plate_shot

# 真机端到端（都要真 LLM / Mongo / ffmpeg）
python scripts/e2e_pipeline_probe.py --timeout 480                     # 基础管线 + 提示词裁决
python scripts/e2e_pipeline_probe.py --scenario floor --set min_shot_ms=300
python scripts/e2e_pipeline_probe.py --with-plan --writeback           # 规划漂移 + 写回
python scripts/ab_creative_prompt.py --pairs 2                         # 提示词 A/B
python scripts/check_genre_v35.py --audio-sec 30 --bpm 128 --script "..."   # 类型判定
python scripts/e2e_legacy_plan.py                                      # 老会话 legacy plan
python scripts/e2e_preroll_v32.py --same-asset                         # 负时间片头（V32）
python scripts/check_v49.py                                            # 七类形态复核
python scripts/check_v33_registration.py                               # 剪影配准（V33）
python scripts/check_v34_4k.py                                         # 4K 单帧锯齿（V34，渲两版约 40s）
python scripts/check_v25_word_timing.py                                # 逐词误差 / init 清单（V25）
python scripts/e2e_shot_floor_d23.py                                   # D23 三种策略真机对照
python scripts/build_poc_30s.py                                        # 30 秒 POC 装配

# 前端
cd J:\Clipweight-Client
npx tsc --noEmit; npx eslint src; npm run test; npm run build
```

## 6. 本轮之前的修复记录（择要）

- **`hash_frames` 静默错位**：调用方给的宽高与源不符时不加缩放就切字节，900 帧被切成 3600 块——"逐帧一致"曾一度失真。已改为尺寸不符则缩放 + 整除校验。
- **并发裁剪落位冲突**：同素材同起点同时长算出同一缓存键，两个线程写同一 `.part`，Windows 上报 `WinError 32` 整条渲染失败。已改为临时名带唯一后缀 + 冲突且目标有效时视为成功。
- **起音检测漏拍**：`ONSET_WINDOW_SEC=0.3` 同时当"回看长度"与"事件长度"，导致上一声之后 0.3 秒内的起音永远检不到（120BPM 漏拍）。已解耦为 `ONSET_LOOKBACK_SEC=0.04`、去重窗口 200ms→50ms（V25）。
- **`min_shot_ms` 语义**：真机跑出 30s 片段，证实它是节奏参数、不是逐段硬下限（PRD 已立 D23，三个候选已实现为开关）。
- **四个内置类型曾零声明**：`load_spec()` 对它们只能从旧钩子合成两个槽位；现已全部补齐（`lyric_mv` 10 槽位，其余四个各 7 槽位 + 规则 + 指标）。
- **创作提示词进不了剪辑阶段**：A/B 实测暴露，已补注入（但节奏仍不稳定，结论已写进 V45）。

## 7. 交付物清单（本轮新增，按层）

| 层 | 数量 | 代表文件 |
|---|---|---|
| 服务 | 17 | `type_prompts.py`（10 槽位注入）、`prompt_authority.py`、`plan_first.py` + `plan_validator.py` + `plan_renderer.py`、`mv_cues.py`、`mv_material.py`、`mv_bundle.py`、`genre_conformance.py`、`trait_collector.py`、`creative_prompt.py`、`prompt_distill.py`、`preroll.py`、`shot_floor.py`、`type_switch.py` |
| 工具 | 11 | `lint_determinism.py`、`hash_frames.py`、`check_flash.py`、`check_sync.py`、`pack_dist.py`、`gate_check.py`、`creative_ab.py`、`aliasing.py`、`registration_check.py`、`plate_check.py` |
| 脚本 | 17 | `e2e_pipeline_probe.py`、`ab_creative_prompt.py`、`check_genre_v35.py`、`e2e_legacy_plan.py`、`e2e_preroll_v32.py`、`e2e_shot_floor_d23.py`、`check_v49.py`、`check_v33_registration.py`、`check_v34_4k.py`、`check_v25_word_timing.py`、`build_poc_30s.py`、`render_archetypes.py`、`render_plate_shot.py`、`render_mv_poc.py`、`smoke_llm_prompt.py` |
| 类型插件 | 6 | `lyric_mv.py`（首个全契约类型）+ 四个内置类型补齐规范 + `base.with_slots` 迁移辅助 |
| 测试 | 49 个新文件 | 覆盖类型规范、裁决、规划、MV 素材/卡点、闸门、前端接线 |
| 前端 | 8 处 | `TypeSpecEditor`、`ChatForgeDrawer`、`CreativePromptField`、`PlanReviewPanel`、`TypeConformancePanel`、`ExportPage`（片头）、`PipelineAdminPage`（用量）、`HomePage`（D23 策略） |
| 文档 | 4 份 | `mv-pv-prd-2026-10-18.md`（单一真相）、`mv-pv-dev-plan-2026-10-18.md`（WBS + 38 轮执行日志）、`coverage.md`（逐条状态）、本报告 |

## 8. 现在要交付，只差两件事（都不是工程欠账）

1. **V50 的四类素材样例**（手绘 / 2D / 3D / 实拍）。纯内容工作，且**片源必须由人提供**（授权与出处可追溯）。机制已就绪：`local_material` 插件 + `LocalFolderSource` 把这四类当成本地素材源，`scripts/check_v50_material_samples.py` 校验"四类齐全 + 每类能被检索命中 + 不串味"，补齐即转通过。
2. **五项人工验收排期**（V16 类型判别 / V30 版画可辨 / V40 三人终审 / V41 落位认可 / V50 素材样例），另加计划外 A5 / A8 / A10 / A13。

D23 已拍板（`merge` + `split`，都保总时长），V2 / V17 / V35 因此清零；V17 仍是"要不要默认开裁判"的产品开关（D7 默认关，随时可开）。

## 9. 建议的下一步（按性价比排序）
1. **提供 V50 的四类素材**（手绘 / 2D / 3D / 实拍），丢进 `examples/material_samples/{hand_drawn,2d,3d,live_action}/` 后跑 `python scripts/check_v50_material_samples.py`。
2. 安排五项人工验收（V16 / V30 / V40 / V41 / V50）与计划外四项（A5 / A8 / A10 / A13）。
3. 决定 V17 的裁判是否默认开启（`CLIPWRIGHT_GENRE_JUDGE_ENABLED=true` 或前端类型页按钮）；真机取证脚本 `scripts/e2e_genre_judge_v17.py` 已能 3/3 命中期望。
4. 若想收紧"类型节奏"，可把 `shot_cap_policy` 也开放到前端（当前后端默认 `split`，无需操作）。
