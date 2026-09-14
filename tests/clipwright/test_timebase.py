"""整数时间基座测试（O1 第一阶段）。

覆盖 `clipwright/utils/timebase.py`、`Timeline` 的时间基属性、以及两处接线
（`RenderService._apply_default_cut_fades` 的相邻判定、`cut_times_from_segments`
的帧对齐）。
"""

from __future__ import annotations

from fractions import Fraction

import pytest

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services.render import (
    RenderService,
    corrected_start_sec,
    frame_align_overlay_enabled,
    shift_ticks_for,
    transition_shift_ticks,
)
from clipwright.services.timeline_view import cut_times_from_segments
from clipwright.utils.timebase import (
    STANDARD_FRAME_RATES,
    TICKS_PER_SECOND,
    FrameRate,
    TimebaseError,
    as_frame_rate,
    format_ticks,
    frame_to_ticks,
    is_frame_aligned,
    seconds_to_ticks,
    snap_seconds_to_frame,
    snap_ticks,
    ticks_to_frame,
    ticks_to_seconds,
    ticks_to_timecode,
    timecode_to_ticks,
)


# ── 常量与标准帧率表 ──────────────────────────────────


class TestConstantsAndRates:
    def test_ticks_per_second(self) -> None:
        assert TICKS_PER_SECOND == 120_000

    def test_all_standard_rates_except_hfr_have_integral_frame_ticks(self) -> None:
        """120000 的选型依据：除 47.952（48000/1001）外全部整除。"""
        non_exact = []
        for rate in STANDARD_FRAME_RATES:
            if rate.exact_ticks_per_frame is None:
                non_exact.append(rate.name)
            else:
                assert rate.exact_ticks_per_frame * rate.numerator == TICKS_PER_SECOND * rate.denominator
        assert non_exact == ["48000/1001"]

    @pytest.mark.parametrize(
        ("spec", "expected_ticks"),
        [
            ("24", 5000),
            ("25", 4800),
            ("30", 4000),
            ("50", 2400),
            ("60", 2000),
            ("24000/1001", 5005),   # 23.976
            ("30000/1001", 4004),   # 29.97
            ("60000/1001", 2002),   # 59.94
            ("120000/1001", 1001),  # 119.88
            ("100", 1200),
            ("120", 1000),
        ],
    )
    def test_frame_tick_table(self, spec: str, expected_ticks: int) -> None:
        assert FrameRate.parse(spec).ticks_per_frame == expected_ticks

    def test_non_exact_rate_rounds_but_is_flagged(self) -> None:
        rate = FrameRate(48000, 1001)
        assert rate.exact_ticks_per_frame is None
        assert rate.ticks_per_frame == 2502  # 2502.5 → half-even

    def test_second_is_divisible_for_standard_rates(self) -> None:
        for rate in STANDARD_FRAME_RATES:
            if rate.exact_ticks_per_frame is None:
                continue
            assert TICKS_PER_SECOND % rate.ticks_per_frame == 0 or rate.denominator != 1


# ── FrameRate 构造 ────────────────────────────────────


class TestFrameRateParse:
    def test_integer_float_stays_integer(self) -> None:
        """30.0 不能被吸附成 30000/1001（两者相对差 0.0999% < 吸附容差）。"""
        assert FrameRate.from_fps(30.0).name == "30"
        assert FrameRate.from_fps(24.0).name == "24"

    @pytest.mark.parametrize(
        ("fps", "expected"),
        [
            (29.97, "30000/1001"),
            (29.97002997002997, "30000/1001"),
            (23.976, "24000/1001"),
            (23.98, "24000/1001"),
            (59.94, "60000/1001"),
            (119.88, "120000/1001"),
            (25.0, "25"),
        ],
    )
    def test_ntsc_written_decimally_snaps_to_exact_rational(self, fps: float, expected: str) -> None:
        assert FrameRate.from_fps(fps).name == expected

    def test_unknown_rate_kept_as_reduced_fraction(self) -> None:
        rate = FrameRate.from_fps(27.5)
        assert rate.name == "55/2"
        assert rate.ticks_per_frame == 4364  # 120000*2/55 = 4363.63…
        assert rate.exact_ticks_per_frame is None

    def test_parse_accepts_all_forms(self) -> None:
        rate = FrameRate.parse("30000/1001")
        assert FrameRate.parse(rate) is rate
        assert FrameRate.parse(30) == FrameRate(30)
        assert FrameRate.parse("30") == FrameRate(30)
        assert FrameRate.parse(" 29.97 ") == FrameRate(30000, 1001)
        assert FrameRate.parse(Fraction(30000, 1001)) == FrameRate(30000, 1001)
        assert as_frame_rate(None) == FrameRate(30)

    @pytest.mark.parametrize("bad", ["", "abc", "30/0", True, [], object()])
    def test_parse_rejects_garbage(self, bad: object) -> None:
        with pytest.raises(TimebaseError):
            FrameRate.parse(bad)

    @pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf")])
    def test_from_fps_rejects_non_positive_or_non_finite(self, bad: float) -> None:
        with pytest.raises(TimebaseError):
            FrameRate.from_fps(bad)

    def test_constructor_validates_sign(self) -> None:
        with pytest.raises(TimebaseError):
            FrameRate(0)
        with pytest.raises(TimebaseError):
            FrameRate(30, 0)

    def test_precision_guard_on_float_denominator(self) -> None:
        """分母必须整型：小数分母会被显式拒绝，避免 30.000001/1 这类隐式帧率混进来。"""
        with pytest.raises(TimebaseError):
            FrameRate(30000, 1001.0)  # type: ignore[arg-type]


class TestFrameRateProperties:
    def test_ntsc_flags(self) -> None:
        ntsc = FrameRate(30000, 1001)
        assert ntsc.is_ntsc and ntsc.drop_frame and ntsc.nominal_fps == 30
        plain = FrameRate(30)
        assert not plain.is_ntsc and not plain.drop_frame and plain.nominal_fps == 30

    def test_frame_duration_seconds(self) -> None:
        assert FrameRate(30).frame_duration_sec == pytest.approx(1 / 30)
        assert FrameRate(30000, 1001).frame_duration_sec == pytest.approx(1001 / 30000)

    def test_frame_index_and_start_ticks(self) -> None:
        rate = FrameRate.parse("30000/1001")
        assert rate.frame_start_ticks(3) == 12012
        # 帧内任意 tick 都属于该帧
        assert rate.frame_index(12012) == 3
        assert rate.frame_index(12012 + 4003) == 3
        assert rate.frame_index(12012 + 4004) == 4

    def test_is_aligned(self) -> None:
        rate = FrameRate.parse("30000/1001")
        assert rate.is_aligned(4004)
        assert not rate.is_aligned(4005)
        assert rate.is_aligned(0)


# ── tick 换算 ─────────────────────────────────────────


class TestTickConversion:
    def test_integer_seconds_are_exact(self) -> None:
        assert seconds_to_ticks(1) == 120_000
        assert seconds_to_ticks(0) == 0

    def test_frame_period_round_trips(self) -> None:
        assert seconds_to_ticks(1 / 30) == 4000
        assert seconds_to_ticks(1001 / 30000) == 4004

    def test_float_noise_does_not_leak(self) -> None:
        """1.007s * 120000 = 120839.99999999999；直接 int() 会少 1 tick。

        毫秒刻度上实测 651/19999 个值（3.3%）、帧刻度上 569/20000 个值（2.8%）
        都有这个截断误差——所以换算必须走 `Decimal(repr(sec))` 四舍五入。
        """
        assert seconds_to_ticks(1.007) == 120_840
        assert int(1.007 * TICKS_PER_SECOND) == 120_839
        assert seconds_to_ticks(65 / 30) == 260_000
        assert int((65 / 30) * TICKS_PER_SECOND) == 259_999

    def test_fraction_and_decimal_string_exact(self) -> None:
        assert seconds_to_ticks(Fraction(1001, 30000)) == 4004
        assert seconds_to_ticks("7.007") == 840_840
        assert seconds_to_ticks(Fraction(1, 3)) == 40_000

    def test_half_even_rounding(self) -> None:
        half_tick = Fraction(1, 2 * TICKS_PER_SECOND)
        assert seconds_to_ticks(half_tick) == 0
        assert seconds_to_ticks(Fraction(3, 2 * TICKS_PER_SECOND)) == 2

    def test_negative_supported(self) -> None:
        assert seconds_to_ticks(-1) == -120_000

    @pytest.mark.parametrize("bad", [True, None, object(), "abc", float("nan"), float("inf")])
    def test_invalid_input_raises(self, bad: object) -> None:
        with pytest.raises(TimebaseError):
            seconds_to_ticks(bad)

    def test_ticks_to_seconds_is_exact_for_rational(self) -> None:
        assert ticks_to_seconds(4004) == pytest.approx(1001 / 30000)
        assert ticks_to_seconds(180_000) == 1.5
        assert ticks_to_seconds(0) == 0.0

    def test_ticks_to_seconds_rejects_non_integer(self) -> None:
        with pytest.raises(TimebaseError):
            ticks_to_seconds(1.5)  # type: ignore[arg-type]

    def test_frame_conversions_round_trip(self) -> None:
        rate = FrameRate.parse("30000/1001")
        for frame in (0, 1, 29, 30, 17982, 107892):
            assert ticks_to_frame(frame_to_ticks(frame, rate), rate) == frame


class TestSnapping:
    @pytest.mark.parametrize(
        ("ticks", "mode", "expected"),
        [
            (4005, "floor", 4004),
            (4005, "ceil", 8008),
            (4005, "nearest", 4004),
            (6007, "nearest", 8008),
            (4004, "nearest", 4004),
            (0, "nearest", 0),
        ],
    )
    def test_modes(self, ticks: int, mode: str, expected: int) -> None:
        assert snap_ticks(ticks, FrameRate.parse("30000/1001"), mode=mode) == expected

    def test_negative_ticks_floor_towards_minus_infinity(self) -> None:
        rate = FrameRate(30)
        assert snap_ticks(-1, rate, mode="floor") == -4000
        assert snap_ticks(-1, rate, mode="ceil") == 0

    def test_unknown_mode_raises(self) -> None:
        with pytest.raises(TimebaseError):
            snap_ticks(1, 30, mode="sideways")

    def test_snap_seconds_and_alignment(self) -> None:
        rate = FrameRate.parse("30000/1001")
        assert snap_seconds_to_frame(1.0, rate) == pytest.approx(1.001, abs=1e-9)
        assert is_frame_aligned(ticks_to_seconds(4004), rate)
        assert not is_frame_aligned(0.001, rate)
        assert not is_frame_aligned("garbage", rate)


# ── 边界相等性（本模块存在的理由） ────────────────────────


class TestBoundaryEquality:
    def test_ticks_make_shared_boundaries_exactly_equal(self) -> None:
        """`上一段起点 + 时长` 与 `下一段起点` 在 tick 上恒等。"""
        rate = FrameRate.parse("30000/1001")
        tpf = rate.ticks_per_frame
        for k in range(1, 20_000):
            prev_end = frame_to_ticks(k - 1, rate) + tpf
            next_start = frame_to_ticks(k, rate)
            assert prev_end == next_start

    def test_float_arithmetic_breaks_that_equality(self) -> None:
        """浮点下同一时间点常不严格相等（实测 200k 边界中约 20%）。

        这条测试锁定"为什么需要 tick"：断言浮点确实存在不等的情况，
        而不是断言某个具体数值——上限只跑到 5000 个边界以保证耗时可控。
        """
        frame = 1001 / 30000
        mismatches = sum(
            1 for k in range(1, 5000) if (frame * (k - 1) + frame) != frame * k
        )
        assert mismatches > 0, "浮点帧边界被期望存在不严格相等的情况"

    def test_drift_accumulation_is_exact_over_10000_transitions(self) -> None:
        """10000 次累加（每次 15 帧）后仍精确落在帧边界（浮点累加只能靠容差）。"""
        rate = FrameRate.parse("30000/1001")
        fifteen_frames = 1001 / 30000 * 15
        ticks = 0
        for _ in range(10_000):
            ticks += seconds_to_ticks(fifteen_frames)
        assert is_frame_aligned(ticks, rate)
        assert ticks == seconds_to_ticks(5005.0)
        assert ticks == rate.frame_start_ticks(150_000)


# ── 时间码 ────────────────────────────────────────────


class TestTimecode:
    def test_non_drop_frame_30fps(self) -> None:
        assert ticks_to_timecode(frame_to_ticks(0, 30), 30) == "00:00:00:00"
        assert ticks_to_timecode(frame_to_ticks(1800, 30), 30) == "00:01:00:00"
        assert ticks_to_timecode(frame_to_ticks(107892, 30), 30) == "00:59:56:12"
        assert ticks_to_timecode(frame_to_ticks(108000, 30), 30) == "01:00:00:00"

    def test_ntsc_defaults_to_drop_frame(self) -> None:
        rate = FrameRate.parse("30000/1001")
        assert ticks_to_timecode(frame_to_ticks(0, rate), rate) == "00:00:00;00"
        # 60 秒整：drop-frame 跳过 2 个标号
        assert ticks_to_timecode(frame_to_ticks(1800, rate), rate) == "00:01:00;02"
        # 每 10 分钟不丢
        assert ticks_to_timecode(frame_to_ticks(17982, rate), rate) == "00:10:00;00"
        assert ticks_to_timecode(frame_to_ticks(107892, rate), rate) == "01:00:00;00"

    def test_ntsc_can_use_non_drop_frame(self) -> None:
        rate = FrameRate.parse("30000/1001")
        assert ticks_to_timecode(frame_to_ticks(1800, rate), rate, drop_frame=False) == "00:01:00:00"

    def test_59_94_drops_four_labels(self) -> None:
        rate = FrameRate.parse("60000/1001")
        assert ticks_to_timecode(frame_to_ticks(3600, rate), rate) == "00:01:00;04"

    def test_drop_frame_rejected_on_integer_rate(self) -> None:
        with pytest.raises(TimebaseError):
            ticks_to_timecode(0, 30, drop_frame=True)

    @pytest.mark.parametrize(("tc", "fps"), [("00:00:00;00", "30000/1001"), ("00:01:00;02", "30000/1001"),
                                             ("00:10:00;00", "30000/1001"), ("01:00:00;00", "30000/1001"),
                                             ("00:00:00:00", 30), ("00:01:00:00", 30), ("00:59:56:12", 30)])
    def test_timecode_round_trip(self, tc: str, fps: object) -> None:
        rate = as_frame_rate(fps)
        assert ticks_to_timecode(timecode_to_ticks(tc, rate), rate) == tc

    def test_semicolon_forces_drop_frame(self) -> None:
        rate = FrameRate.parse("30000/1001")
        assert timecode_to_ticks("00:01:00;02", rate, drop_frame=False) == frame_to_ticks(1800, rate)

    @pytest.mark.parametrize("bad", ["", "1:2:3", "aa:bb:cc:dd", "00:00:00:30", "00:60:00:00", "00:00:60:00"])
    def test_invalid_timecode(self, bad: str) -> None:
        with pytest.raises(TimebaseError):
            timecode_to_ticks(bad, 30)


class TestFormatting:
    def test_compact_and_fixed(self) -> None:
        assert format_ticks(400_400) == "3.33667"
        assert format_ticks(400_400, precision=3) == "3.337"
        assert format_ticks(1_800_000) == "15"
        assert format_ticks(0) == "0"


# ── Timeline 接线 ─────────────────────────────────────


def _timeline(fps: float, clips: list[tuple[float, float]]) -> Timeline:
    track = Track(
        id="t1", index=0, kind="video",
        clips=[
            Clip(id=f"c{i}", asset_id=f"a{i}", track_id="t1", kind=ClipKind.VIDEO,
                 start_sec=s, duration_sec=d, source="a.mp4")
            for i, (s, d) in enumerate(clips)
        ],
    )
    return Timeline(fps=fps, tracks=[track])


class TestTimelineTimebase:
    def test_frame_rate_and_ticks(self) -> None:
        tl = _timeline(29.97, [(0.0, 10.01)])
        assert tl.frame_rate.name == "30000/1001"
        assert tl.ticks_per_second == TICKS_PER_SECOND
        assert tl.total_duration_ticks == 1_201_200
        assert tl.to_ticks(1.5) == 180_000
        assert tl.to_seconds(180_000) == 1.5

    def test_frame_count_and_aligned_duration(self) -> None:
        tl = _timeline(30.0, [(0.0, 2.0)])
        assert tl.frame_count == 60
        assert tl.frame_aligned_duration_sec() == pytest.approx(2.0)
        off = _timeline(30.0, [(0.0, 2.004)])
        assert off.frame_count == 60            # 60.12 帧 → 已完整播出的帧数
        assert off.frame_aligned_duration_sec() == pytest.approx(61 / 30)  # 补齐到 61 帧

    def test_timecode_helper(self) -> None:
        tl = _timeline(29.97, [(0.0, 1.0)])
        assert tl.timecode(1.001) == "00:00:01;00"

    def test_empty_timeline(self) -> None:
        tl = Timeline()
        assert tl.total_duration_ticks == 0
        assert tl.frame_count == 0
        assert tl.frame_rate == FrameRate(30)

    def test_serialized_shape_unchanged(self) -> None:
        """属性不进入 JSON——前后端契约不变（浮点秒字段仍是唯一来源）。"""
        payload = _timeline(29.97, [(0.0, 5.0)]).model_dump()
        assert "frame_rate" not in payload
        assert "total_duration_ticks" not in payload
        assert "frame_count" not in payload


# ── RenderService 接线 ────────────────────────────────


class TestCutFadeAdjacencyOnTicks:
    def _voice(self, **kwargs: object) -> dict:
        base = {"is_voice": True, "is_bgm": False, "fade_in": None, "fade_out": None}
        base.update(kwargs)
        return base

    def test_adjacent_segments_get_no_fade(self) -> None:
        voices = [
            self._voice(start=0.0, dur=2.0),
            self._voice(start=2.0, dur=2.0),
        ]
        RenderService._apply_default_cut_fades(voices)
        # 首段尾与次段首相接 → 都不补淡化；外侧边界补
        assert voices[0]["fade_in"] == RenderService._CUT_FADE_DEFAULT_SEC
        assert voices[0]["fade_out"] == 0.0
        assert voices[1]["fade_in"] == 0.0
        assert voices[1]["fade_out"] == RenderService._CUT_FADE_DEFAULT_SEC

    def test_float_noise_at_boundary_still_counted_as_adjacent(self) -> None:
        """上一段 end 与下一段 start 差 1e-13 秒（浮点噪声）仍判相邻。"""
        voices = [
            self._voice(start=0.1, dur=0.2),
            self._voice(start=0.1 + 0.2 + 1e-13, dur=0.2),
        ]
        RenderService._apply_default_cut_fades(voices)
        assert voices[0]["fade_out"] == 0.0
        assert voices[1]["fade_in"] == 0.0

    def test_audible_gap_gets_fade(self) -> None:
        voices = [
            self._voice(start=0.0, dur=2.0),
            self._voice(start=2.2, dur=2.0),
        ]
        RenderService._apply_default_cut_fades(voices)
        assert voices[0]["fade_out"] == RenderService._CUT_FADE_DEFAULT_SEC
        assert voices[1]["fade_in"] == RenderService._CUT_FADE_DEFAULT_SEC

    def test_window_constants_are_consistent(self) -> None:
        assert RenderService._ADJACENT_WINDOW_TICKS == seconds_to_ticks(RenderService._ADJACENT_WINDOW_SEC)
        assert RenderService._ADJACENT_WINDOW_SEC >= RenderService._CUT_FADE_DEFAULT_SEC

    def test_other_group_does_not_suppress_fade(self) -> None:
        voices = [
            self._voice(start=0.0, dur=2.0),
            self._voice(start=2.0, dur=2.0, is_voice=False, is_bgm=True),
        ]
        RenderService._apply_default_cut_fades(voices)
        assert voices[0]["fade_out"] == RenderService._CUT_FADE_DEFAULT_SEC
        assert voices[1]["fade_in"] == RenderService._CUT_FADE_DEFAULT_SEC

    def test_explicit_values_are_not_overwritten(self) -> None:
        voices = [
            self._voice(start=0.0, dur=1.0, fade_in=0.0, fade_out=0.5),
            self._voice(start=3.0, dur=1.0),
        ]
        RenderService._apply_default_cut_fades(voices)
        assert voices[0]["fade_in"] == 0.0
        assert voices[0]["fade_out"] == 0.5


class TestTransitionDriftCorrection:
    """`render.py` 的漂移校正（提取为纯函数后可直接测）。"""

    def _segments(self, starts: list[float], transitions: dict[int, float]) -> list[dict]:
        return [
            {"start_sec": s, "transition_duration_sec": transitions.get(i, 0.0)}
            for i, s in enumerate(starts)
        ]

    def test_shifts_accumulate_before_each_segment(self) -> None:
        """`transition_in` 属于**该段起点处**的转场：第 i 段的漂移只累计 i 之前的转场。"""
        segs = self._segments([0.0, 5.0, 10.0, 15.0], {1: 0.5, 2: 0.5, 3: 0.5})
        shifts = transition_shift_ticks(segs)
        assert shifts == [0, 0, 60_000, 120_000]
        assert [ticks_to_seconds(t) for t in shifts] == [0.0, 0.0, 0.5, 1.0]

    def test_shift_lookup_picks_containing_segment(self) -> None:
        segs = self._segments([0.0, 5.0, 10.0], {1: 0.4, 2: 0.4})
        shifts = transition_shift_ticks(segs)
        assert shift_ticks_for(0.0, segs, shifts) == 0
        assert shift_ticks_for(4.999, segs, shifts) == 0
        assert shift_ticks_for(5.0, segs, shifts) == 0        # 边界点：转场刚开始
        assert shift_ticks_for(10.0, segs, shifts) == 48_000  # 第二处转场完成后
        assert shift_ticks_for(99.0, segs, shifts) == 48_000
        assert shift_ticks_for(1.0, [], []) == 0

    def test_garbage_transition_duration_counts_as_zero(self) -> None:
        segs = [
            {"start_sec": 0.0},
            {"start_sec": 5.0, "transition_duration_sec": "x"},
            {"start_sec": 10.0, "transition_duration_sec": None},
            {"start_sec": 15.0, "transition_duration_sec": -1},
        ]
        assert transition_shift_ticks(segs) == [0, 0, 0, 0]

    def test_correction_is_exact_and_frame_aligned(self) -> None:
        """29.97 时间线：0.5s 转场后 overlay 起点落在整帧上（15 帧 = 0.5005s 附近）。"""
        rate = FrameRate.parse("30000/1001")
        segs = self._segments([0.0, 5.005, 10.01], {1: 0.5, 2: 0.5})
        shifts = transition_shift_ticks(segs)
        corrected = corrected_start_sec(10.01, segs, shifts, 29.97)
        assert corrected is not None and corrected < 10.01
        assert is_frame_aligned(corrected, rate)

    def test_audio_correction_is_not_snapped(self) -> None:
        """align=False 时只做 tick 减法：结果可不在帧边界上（音频无帧概念）。"""
        rate = FrameRate.parse("30000/1001")
        segs = self._segments([0.0, 5.005, 10.01], {1: 0.5, 2: 0.0})
        shifts = transition_shift_ticks(segs)
        snapped = corrected_start_sec(10.01, segs, shifts, 29.97, align=True)
        raw = corrected_start_sec(10.01, segs, shifts, 29.97, align=False)
        assert raw == pytest.approx(9.51)          # 10.01 − 0.5 秒漂移
        assert snapped is not None
        assert abs(snapped - raw) < 1001 / 30000   # 吸附幅度 < 1 帧
        assert is_frame_aligned(snapped, rate)
        assert not is_frame_aligned(raw, rate)

    def test_alignment_can_be_disabled_by_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLIPWRIGHT_FRAME_ALIGN_OVERLAY", "0")
        assert not frame_align_overlay_enabled()
        segs = self._segments([0.0, 5.005, 10.01], {1: 0.5, 2: 0.0})
        shifts = transition_shift_ticks(segs)
        assert corrected_start_sec(10.01, segs, shifts, 29.97) == corrected_start_sec(
            10.01, segs, shifts, 29.97, align=False
        )

    def test_negative_result_clamped_to_zero(self) -> None:
        segs = self._segments([0.0, 0.1, 0.2], {1: 5.0, 2: 0.0})
        shifts = transition_shift_ticks(segs)
        assert shifts == [0, 0, 600_000]
        assert corrected_start_sec(0.2, segs, shifts, 30) == 0.0

    @pytest.mark.parametrize("bad", ["x", None, object()])
    def test_invalid_start_returns_none(self, bad: object) -> None:
        segs = self._segments([0.0, 5.0], {1: 0.5})
        shifts = transition_shift_ticks(segs)
        assert corrected_start_sec(bad, segs, shifts, 30) is None


class TestCutTimesOnTicks:
    def test_sub_tick_difference_is_one_cut(self) -> None:
        segs = [{"start_sec": 0.0}, {"start_sec": 3.0}, {"start_sec": 3.0 + 1e-9}]
        assert cut_times_from_segments(segs) == [3.0]

    def test_tiny_non_zero_head_still_skipped(self) -> None:
        assert cut_times_from_segments([{"start_sec": 1e-9}]) == []
        assert cut_times_from_segments([{"start_sec": 1e-3}]) == [0.001]

    def test_frame_rate_snaps_cuts_to_frame_boundaries(self) -> None:
        rate = FrameRate.parse("30000/1001")
        segs = [{"start_sec": 0.0}, {"start_sec": 3.35}]
        raw = cut_times_from_segments(segs)
        snapped = cut_times_from_segments(segs, frame_rate=rate)
        assert raw == [3.35]
        tpf_sec = 1001 / 30000
        assert snapped[0] != raw[0]
        assert abs(snapped[0] - 3.35) < tpf_sec
        assert is_frame_aligned(snapped[0], rate)

    def test_bad_frame_rate_falls_back_to_raw(self) -> None:
        segs = [{"start_sec": 0.0}, {"start_sec": 2.0}]
        assert cut_times_from_segments(segs, frame_rate=0) == [2.0]

    def test_garbage_tolerant(self) -> None:
        segs = [{"start_sec": None}, {"start_sec": "x"}, {"start_sec": -1.0}, {"start_sec": 1.0}]
        assert cut_times_from_segments(segs) == [1.0]
