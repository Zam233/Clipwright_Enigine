"""整数时间基座验证（O1 第一阶段）。

打印证据而非断言式"通过"——这个脚本的作用是把「为什么要把时间模型整数化」
变成可复核的数字，并核对新代码没有把既有行为改坏。

运行：``python scripts/verify_timebase.py``（退出码 0 = 全部 PASS）
"""

from __future__ import annotations

import sys
from fractions import Fraction

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from clipwright.services.render import (  # noqa: E402
    corrected_start_sec,
    frame_align_overlay_enabled,
    transition_shift_ticks,
)
from clipwright.utils.timebase import (  # noqa: E402
    STANDARD_FRAME_RATES,
    TICKS_PER_SECOND,
    FrameRate,
    frame_to_ticks,
    is_frame_aligned,
    seconds_to_ticks,
    ticks_to_seconds,
    ticks_to_timecode,
    timecode_to_ticks,
)

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def section(title: str) -> None:
    print(f"\n{title}")
    print("-" * len(title))


def main() -> int:
    print("整数时间基座验证（O1 第一阶段）")
    print(f"TICKS_PER_SECOND = {TICKS_PER_SECOND}")

    # ── 1. 标准帧率表 ────────────────────────────────
    section("1. 标准帧率表（帧周期是否整数 tick）")
    non_exact = []
    for rate in STANDARD_FRAME_RATES:
        tpf = rate.exact_ticks_per_frame
        flag = "exact" if tpf is not None else "ROUNDED"
        if tpf is None:
            non_exact.append(rate.name)
        print(f"    {rate.name:>11}  fps={rate.fps:<12.6f}  tick/帧={rate.ticks_per_frame:<6} {flag}")
    check("除 48/1.001（47.952）外全部为整数帧周期", non_exact == ["48000/1001"],
          f"非精确项={non_exact or '无'}")

    # ── 2. 边界相等性（整数化的核心理由） ───────────────
    section("2. 边界相等性：上一段 end 与下一段 start")
    rate = FrameRate.parse("30000/1001")
    frame_sec = 1001 / 30000
    n = 200_000
    float_mismatch = sum(1 for k in range(1, n + 1) if (frame_sec * (k - 1) + frame_sec) != frame_sec * k)
    tpf = rate.ticks_per_frame
    tick_mismatch = sum(
        1 for k in range(1, n + 1)
        if (frame_to_ticks(k - 1, rate) + tpf) != frame_to_ticks(k, rate)
    )
    pct = float_mismatch / n * 100
    print(f"    29.97fps 帧边界样本 {n:,}")
    print(f"    浮点秒不严格相等: {float_mismatch:,} ({pct:.1f}%)")
    print(f"    整数 tick 不相等: {tick_mismatch:,}")
    check("tick 下边界恒等", tick_mismatch == 0, f"浮点不相等占比 {pct:.1f}%")
    check("浮点下确实存在不相等（证明问题真实存在）", float_mismatch > 0)

    # ── 3. 浮点截断（换算纪律） ───────────────────────
    section("3. 秒→tick 的截断误差（为什么必须走 Decimal）")
    ms_bad = [i for i in range(1, 20_000) if int((i / 1000) * TICKS_PER_SECOND) != i * 120]
    frame_bad = [i for i in range(1, 20_000) if int((i / 30) * TICKS_PER_SECOND) != i * 4000]
    print(f"    毫秒刻度 int(sec*120000) 少 1 tick: {len(ms_bad)}/19999（{len(ms_bad)/19999*100:.1f}%）"
          f"  例: 1.007s → {int(1.007*TICKS_PER_SECOND)}（应为 {seconds_to_ticks(1.007)}）")
    print(f"    帧刻度  int(sec*120000) 少 1 tick: {len(frame_bad)}/19999（{len(frame_bad)/19999*100:.1f}%）"
          f"  例: 65/30s → {int((65/30)*TICKS_PER_SECOND)}（应为 {seconds_to_ticks(65/30)}）")
    check("Decimal 四舍五入后无截断误差",
          seconds_to_ticks(1.007) == 120_840 and seconds_to_ticks(65 / 30) == 260_000)

    # ── 4. 累加精度 ─────────────────────────────────
    section("4. 累加精度：10000 次 15 帧累加（29.97）")
    fifteen = 1001 / 30000 * 15
    float_sum = 0.0
    tick_sum = 0
    for _ in range(10_000):
        float_sum += fifteen
        tick_sum += seconds_to_ticks(fifteen)
    exact_ticks = rate.frame_start_ticks(150_000)
    print(f"    浮点累加 {float_sum!r}  帧对齐={is_frame_aligned(float_sum, rate)}")
    print(f"    tick 累加 {tick_sum} (= {ticks_to_seconds(tick_sum)}s)  帧对齐={is_frame_aligned(tick_sum, rate)}")
    check("tick 累加精确落在帧边界", tick_sum == exact_ticks and is_frame_aligned(tick_sum, rate))

    # ── 5. 转场漂移校正 ─────────────────────────────
    section("5. 转场漂移校正（overlay 起点）")
    segments = []
    for i in range(12):
        segments.append({
            "start_sec": round(i * 3.003, 6),
            "transition_in": "fade" if i else "",
            "transition_duration_sec": 0.5,
        })
    shifts = transition_shift_ticks(segments)
    print(f"    12 段 × 3.003s，每段 0.5s 转场；累计漂移(tick)={shifts}")
    aligned = 0
    max_shift = 0.0
    for seg in segments[1:]:
        start = seg["start_sec"]
        snapped = corrected_start_sec(start, segments, shifts, 29.97, align=True)
        raw = corrected_start_sec(start, segments, shifts, 29.97, align=False)
        if snapped is None or raw is None:
            continue
        if is_frame_aligned(snapped, rate):
            aligned += 1
        max_shift = max(max_shift, abs(snapped - raw))
    print(f"    吸附后落在整帧起点的段数: {aligned}/{len(segments)-1}")
    print(f"    吸附带来的最大位移: {max_shift*1000:.2f} ms（< 半帧 = {1001/30000/2*1000:.2f} ms）")
    print(f"    帧边界吸附开关: {frame_align_overlay_enabled()}")
    check("校正后 overlay 起点全部落在整帧起点", aligned == len(segments) - 1)
    check("吸附位移小于半帧", max_shift < 1001 / 30000 / 2 + 1e-9)

    # ── 6. 时间码 ───────────────────────────────────
    section("6. 时间码（交付 / EDL 用）")
    observed = 0
    for spec, tcs in (
        ("30000/1001", ["00:00:00;00", "00:01:00;02", "00:10:00;00", "01:00:00;00"]),
        ("60000/1001", ["00:00:00;00", "00:01:00;04"]),
        ("30", ["00:00:00:00", "00:01:00:00", "00:59:56:12"]),
        ("25", ["00:00:00:00", "00:01:00:00"]),
    ):
        r = FrameRate.parse(spec)
        for tc in tcs:
            ticks = timecode_to_ticks(tc, r)
            back = ticks_to_timecode(ticks, r)
            ok = back == tc
            observed += 1
            print(f"    {r.name:>11}  {tc}  <-> 帧 {r.frame_index(ticks):<8} {back}  {'ok' if ok else 'MISMATCH'}")
            if not ok:
                FAILURES.append(f"timecode {spec} {tc}")
    check(f"时间码往返全部一致（{observed} 组）", not FAILURES or "timecode" not in "".join(FAILURES))

    # ── 7. 既有行为不变 ─────────────────────────────
    section("7. 既有行为核对")
    common = Fraction(1, 1)
    check("1 秒 = 120000 tick", seconds_to_ticks(1) == common * TICKS_PER_SECOND)
    check("整数 tick 往返无损",
          all(seconds_to_ticks(ticks_to_seconds(t)) == t for t in (0, 1, 4004, 60_000, 120_000, 1_234_567)))

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"VERIFY FAIL — {len(FAILURES)} 项未通过: {FAILURES}")
        return 1
    print("VERIFY PASS — 整数时间基座全部核对通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
