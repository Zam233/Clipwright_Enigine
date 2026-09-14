# 整数时间基座（timebase）

> 代码：`clipwright/utils/timebase.py`　测试：`tests/clipwright/test_timebase.py`（114 条）
> 验证脚本：`python scripts/verify_timebase.py`
> 背景与选型来自 [OpenCut 深读](./opencut-video-use-hyperframes-deepdive-2026-09-13.md) §1.2 O1。

## 1. 为什么要把时间整数化

时间线用**浮点秒**表示（`schema/timeline.py` 的 `start_sec` / `duration_sec`）。
同一个时间点如果经两条路径算出——「上一段的 `start + duration`」与「下一段的 `start`」
——在 `30000/1001` 帧率下**不保证严格相等**。

实测（`scripts/verify_timebase.py`，29.97fps，200000 个帧边界）：

| 表示 | `frame*(k-1) + frame != frame*k` 的次数 | 占比 |
|---|---|---|
| 浮点秒 | 41202 | **20.6%** |
| 整数 tick | 0 | 0% |

后果是整个代码库散落的容差：「相邻 / 有间隙 / 有重叠」只能近似判定，
而这个容差有时候比被检测对象还大（例如切点淡化的相邻窗口是 50ms，而要检测的是 30ms 淡化）。

整数 tick 让「同一时间点」有唯一表示：相等判定就是 `==`，相邻判定就是
`end_ticks == next_start_ticks`，不需要 eps。

## 2. 常量选型：为什么是 120000

`TICKS_PER_SECOND = 120000`，因为所有常用帧率的**帧周期都是整数 tick**：

| 帧率 | tick/帧 | 帧率 | tick/帧 |
|---|---|---|---|
| 24 | 5000 | 23.976 = 24000/1001 | 5005 |
| 25 | 4800 | 29.97 = 30000/1001 | 4004 |
| 30 | 4000 | 59.94 = 60000/1001 | 2002 |
| 50 | 2400 | 119.88 = 120000/1001 | 1001 |
| 60 | 2000 | 100 / 120 | 1200 / 1000 |

`1e9` 与 `90000` 都无法整除 1001 系帧率。唯一非精确项是 `48000/1001`（47.952，2502.5 tick），
在标准帧率表里被显式标记（`exact_ticks_per_frame is None`）。

## 3. 换算纪律（有实测支撑）

- **秒 → tick**：`seconds_to_ticks` 走 `Decimal(repr(sec))` 四舍五入（默认 half-even）。
  直接 `int(sec * 120000)` 会截断：毫秒刻度上 651/19999（3.3%）、帧刻度上 569/19999（2.8%）
  的取值少 1 tick（`1.007s → 120839`，应为 `120840`；`65/30s → 259999`，应为 `260000`）。
- **累加**：时间累加一律在 tick 整数上做，不要再经过浮点。10000 次 15 帧累加，
  浮点得 `5005.000000000789`，tick 得 `600600000`（精确落在帧边界）。
- **tick → 秒**：`ticks_to_seconds` 用 `Fraction` 精确求值后转 float，跨平台确定性。
- **边界**：浮点秒只允许出现在 API 出入参、ffmpeg 参数、用户可见文本这类边界上。
- ⚠️ tick 粒度 `1/120000 s ≈ 8.33µs`，比部分现存容差（`1e-6`）**粗**。
  **量化为 tick 之后就按 tick 相等判定，不要再叠 eps**，否则等于把刚消掉的近似请回来。

## 4. API

### `FrameRate`（有理数帧率）

```python
from clipwright.utils.timebase import FrameRate

FrameRate.parse("30000/1001")     # 分数写法
FrameRate.from_fps(29.97)         # 29.97 → 30000/1001（标准帧率吸附，整数优先）
FrameRate.from_fps(30.0)          # 30（不会被吸附成 30000/1001）
FrameRate(30).ticks_per_frame     # 4000
FrameRate(30000, 1001).is_ntsc    # True（1001 系，时间码默认 drop-frame）
FrameRate(30000, 1001).frame_start_ticks(3)   # 12012
```

吸附是「整数优先」的：30.0 与 30000/1001 的相对差只有 0.0999%，落在吸附容差内，
若不先判整数就会把 30fps 时间线误判成 29.97。

### 换算与吸附

```python
seconds_to_ticks(1.007)              # 120840（Decimal 四舍五入）
ticks_to_seconds(180000)             # 1.5（Fraction 精确）
frame_to_ticks(1800, "30000/1001")   # 7207200
ticks_to_frame(7207200, "30000/1001")# 1800
snap_ticks(4005, "30000/1001")       # 4004（floor/ceil/nearest 三种模式）
is_frame_aligned(3.336666666666667, "30000/1001")   # True
```

### 时间码（交付 / EDL）

```python
ticks_to_timecode(7207200, "30000/1001")   # '00:01:00;02'（drop-frame 自动启用）
ticks_to_timecode(0, 30)                   # '00:00:00:00'
timecode_to_ticks("00:10:00;00", "30000/1001")  # 71999928
```

## 5. 接线点

| 位置 | 用法 |
|---|---|
| `Timeline.frame_rate` / `total_duration_ticks` / `frame_count` / `frame_aligned_duration_sec()` / `timecode()` | 派生属性，**不进入 `model_dump()`**，前后端契约不变（有测试锁定） |
| `RenderService` 的转场漂移校正 | `transition_shift_ticks()` / `shift_ticks_for()` / `corrected_start_sec()`（纯函数，可单测）；视觉 overlay 吸附帧边界（`CLIPWRIGHT_FRAME_ALIGN_OVERLAY=0` 关闭），**音频不吸附** |
| `RenderService._apply_default_cut_fades` | 「相邻」= tick 差 ≤ `_ADJACENT_WINDOW_TICKS`（由 `_ADJACENT_WINDOW_SEC` 派生，6000 tick = 50ms）；窗口不可小于 30ms 淡化时长 |
| `timeline_view.cut_times_from_segments` | tick 判重（不做毫秒取整，否则会把吸附好的帧边界挪走）；可传 `frame_rate` 按帧吸附 |

### 5.1 输入通道与真值（第二阶段）

| 方向 | 通道 | 真值 |
|---|---|---|
| 请求体 → 服务端 | `start_ticks` / `duration_ticks` / `source_offset_ticks`（Clip）、`fps_num` / `fps_den`（Timeline） | 折算成浮点秒/`fps`；**秒字段优先** |
| 服务端 → 响应/落库 | 只有 `start_sec` / `duration_sec` / `source_offset_sec` / `fps` | 唯一序列化真值 |

tick 输入**不落库、不回写 JSON**（有测试锁定）——同一段时间若同时存两种表示，
改一个忘另一个就是静默漂移；需要精确 tick 时用派生属性现算
（`Clip.start_ticks` / `duration_ticks` / `end_ticks`、`Timeline.total_duration_ticks`）。
`Timeline.adjacency_issues()` 用 tick 判定相邻，返回 `overlap` / `gap` 条目；
`QualityAgent` 把它接成质检项：视频轨重叠 → warning(structure)，间隙 → info(structure)。

## 5.2 前端统一换算（O1 第二阶段）

代码：`J:\Clipweight-Client\src\lib\mediaTime.ts`；测试：`src/lib/mediaTime.test.ts`（31 条）
+ `src/stores/previewStore.test.ts`（7 条）。与后端同一份语义（tick=120000、标准帧率表、
标准帧率吸附**整数优先**、`ticksPerFrame` 不可整除时 half-even）。

| 用途 | 前端 API |
|---|---|
| 秒 ↔ tick | `secondsToTicks` / `ticksToSeconds`（branded `MediaTime`） |
| 帧 ↔ tick/秒 | `frameToTicks` / `ticksToFrame` / `frameToSeconds` / `secondsToFrame` / `frameDistance` |
| 帧率 | `parseFrameRate` / `fromFps` / `frameRateName` / `isNtsc` / `ticksPerFrame` / `frameDuration` |
| 吸附与判定 | `snapTicks` / `snapToFrame` / `isFrameAligned` |
| 时间码 | `formatTimecodeFromSeconds` / `formatTimecodeFromTicks` / `timecodeToTicks`（NTSC 自动 drop-frame） |
| 相邻性 | `findAdjacencyIssues`（与后端 `adjacency_issues` 同一判定） |

已替换的散落换算（不再是 `* fps` / `1 / fps`）：

- `src/lib/utils.ts` 的 `formatTimecode`（改为走 tick + drop-frame）；
- `src/stores/previewStore.ts` 的 `stepForward` / `stepBackward` / `seekToEnd`；
- `src/features/keyboard/useGlobalKeybindings.ts` 的 nudge / trim / slip / slide（6 处 `1 / fps`）；
- `src/layouts/EditorLayout.tsx` 的帧号显示、`src/features/timeline/engine/renderers.ts` 的标尺帧号。

**实测到的真实缺陷（已修）**：旧的按帧步进是 `Math.round(t * fps) ± 1`，用的是**最近**帧——
播放头落在帧内（点击/拖拽/播放到一半的常态）时会连同偏移一起多走一格。
实测 29.97fps 下 1000 个「帧内 60% 位置」样本，前进 **1000/1000** 次都跳到了 `k+2` 而不是 `k+1`
（`mediaTime.test.ts` 里以对比断言锁定）。新的 `frameStep` 语义：帧边界上前后各一帧，
帧内前进到下一个帧边界、后退到当前帧起点——与常见剪辑器一致，且永远落在帧网格上。

## 6. 尚未完成（第三阶段）

- 命令式撤销（命令对象带 tick 时间、与 O3「选择快照 + 历史面板」联动）；
- 渲染前是否**自动**把所有 clip 时间吸附到帧边界（需要一并决定音频如何跟随，
  否则会动到 A/V 相对位置）——目前只做 overlay/切点的吸附，clip 时间保持不变量；
- 前端 `Timeline` 类型是否携带 `fps_num/fps_den`（当前只在请求方向支持分数帧率，
  前端内部仍以 `fps: number` 为真值）。

## 7. 验证

```bash
python scripts/verify_timebase.py     # VERIFY PASS，打印 §1–§3 的全部实测数字
python -m pytest tests/clipwright/test_timebase.py tests/clipwright/test_timeline_adjacency.py -q
python scripts/e2e_smoke.py           # SMOKE PASS（帧吸附不改变真实成片）

# 前端（工作目录 J:\Clipweight-Client）
npx tsc --noEmit
npx vitest run src/lib/mediaTime.test.ts src/stores/previewStore.test.ts
```
