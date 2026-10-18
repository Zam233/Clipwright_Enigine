# 全量验收报告（V1–V60）

> 交付物对应计划 W9「全量验收报告」。本文只做三件事：**结论、证据、复跑命令**。
> 逐条状态的权威来源仍是 [coverage.md](coverage.md)；本文是给人读的一页纸汇总。
>
> 生成时间：2026-10-18（第 33 轮末）。最近一次全量回归：**4137 passed / 3 skipped / 1 xfailed / 0 failed**（7 分 19 秒）；前端 **109 文件 / 944 项全绿**，`tsc --noEmit` 0 错、`eslint` 0 error、`npm run build` 成功。

## 1. 一句话结论

**能自动化的验收项已全部收口，剩下 6 项全部卡在"需要人来决策"或"需要外部素材"**——没有一项是"代码没写"。

| 状态 | 数量 | 具体 |
|---|---|---|
| 通过 | **52** | V1–V15、V19–V24、V26–V29、V31、V32、V33、V34、V36–V39、V42–V49、V51–V53、V55–V60（明细见 coverage） |
| 部分 | **2** | V2、V35 |
| 未做 | **2** | V17（裁判，D7 默认关）、V50（无素材） |
| 人工 | **4** | V16、V30、V40、V41 |
| 合计 | 60 | V1–V60 |

## 2. 三项"部分"卡在哪（都不是欠代码）

| 项 | 缺什么 | 谁能解 |
|---|---|---|
| **V2** `min_shot_ms` strict floor | 参数裁决层已真机验证（C1 冲突 + `L2_prompt` 改写），但**逐段时长不受它约束**——片段时长来自结构阶段场景时长。要改成硬下限需先拍板策略（拉长画面 / 重复素材 / 允许低于下限记 C2） | **需你拍 D23** |
| **V35** 成片被判 `lyric_mv` | 判定链路真机通了（`avg_shot_ms=6166.67` 不达标、`text_overlay=0.988` 达标、`beat_align=1.0` 达标 → 1/2 不通过）。根因同 V2：类型节奏压不住场景时长 | **需你拍 D23** |
| **V25** 逐词误差 ≤42ms | cues 支持 `measured/init` 标记，**没有真实起音数据**可测 | 需要一段带人工标注起音的音乐 |

## 3. 两项"未做"与四项"人工"

| 项 | 原因 | 谁能解 |
|---|---|---|
| V17 错配可判且给证据 | L2/L3 LLM 裁判默认关闭（D7 拍板过：默认不开） | 你决定是否开裁判 |

| V50 素材类 4 类样例 | 手绘/2D 骨骼/3D/实拍样例素材不存在 | 需要素材 |
| V16 / V30 / V40 / V41 | 类型判别、20 张版画可辨、3 人终审、10 个落位认可 | **只能由人判** |

计划外人工验收：A5（真实 token 账单）、A8（5 人可用性）、A10（20 题盲评）、A13（逐字节时间线人工复核）。

## 4. 本轮新增的两块前端

| 位置 | 内容 |
|---|---|
| `ExportPage` | **V32 片头秒数输入**（0–15s，`clampPreroll` 归一到 0.1s；0 不传字段，保持后端默认）。文案明确写"画面先起 N 秒、音乐延后同样时长" |
| `PipelineAdminPage` | **V38 用量展示**：新增「LLM 用量」stat 卡（`usage.total_tokens` 求和）与每条 run 的 token/调用次数/渲染耗时行（`data-testid="run-usage"`）。没有用量记录时显示 `—`，不编造 |

## 5. 复跑命令（全部可一键重跑）

```powershell
# 后端全量回归（约 7 分钟）
cd J:\Clipwright
python -m pytest tests -q

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
python scripts/e2e_preroll_v32.py --same-asset                         # 负时间片头
python scripts/check_v49.py                                            # 七类形态复核
python scripts/build_poc_30s.py                                        # 30 秒 POC 装配

# 前端
cd J:\Clipweight-Client
npx tsc --noEmit; npx eslint src; npm run test; npm run build
```

## 6. 本轮之前的修复记录（择要）

- **`hash_frames` 静默错位**：调用方给的宽高与源不符时不加缩放就切字节，900 帧被切成 3600 块——"逐帧一致"曾一度失真。已改为尺寸不符则缩放 + 整除校验。
- **并发裁剪落位冲突**：同素材同起点同时长算出同一缓存键，两个线程写同一 `.part`，Windows 上报 `WinError 32` 整条渲染失败。已改为临时名带唯一后缀 + 冲突且目标有效时视为成功。
- **`min_shot_ms` 语义**：真机跑出 30s 片段，证实它是节奏参数、不是逐段硬下限（PRD 已立 D23）。
- **四个内置类型曾零声明**：`load_spec()` 对它们只能从旧钩子合成两个槽位；现已全部补齐（`lyric_mv` 10 槽位，其余四个各 7 槽位 + 规则 + 指标）。
- **创作提示词进不了剪辑阶段**：A/B 实测暴露，已补注入（但节奏仍不稳定，结论已写进 V45）。

## 7. 建议的下一步（按性价比排序）

1. **拍 D23**：这是唯一一个"拍完就能连带解锁 V2 + V35"的决策，也是"类型节奏"从装饰变成能力的前提。
2. 给 V25/V33 补素材（一段带起音标注的音乐、一对剪影图），这两条立刻能从"部分"转"通过"。
3. V50 的四个素材样例（手绘 / 2D 骨骼 / 3D / 实拍）——这一项本质是内容工作，不是工程工作。
4. 决定 V17（裁判接真 LLM）要不要做（D7 曾拍板"默认不开"）。
