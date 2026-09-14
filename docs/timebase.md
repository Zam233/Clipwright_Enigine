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

## 6. 尚未完成（第二阶段）

- Pydantic 侧 `int` tick 字段 + `width/height/fps_num/fps_den` 双写（过渡期保留 float 秒）；
- 前端 branded type `type MediaTime = number & { __mediaTime: true }` 与统一换算工具
  （禁止散落 `* fps`）；
- 命令式撤销（与后端渲染时间的 tick 化联动）。

## 7. 验证

```bash
python scripts/verify_timebase.py     # VERIFY PASS，打印 §1–§3 的全部实测数字
python -m pytest tests/clipwright/test_timebase.py -q
python scripts/e2e_smoke.py           # SMOKE PASS（帧吸附不改变真实成片）
```
