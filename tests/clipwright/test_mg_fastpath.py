"""V3：MG 快路径（PIL 逐帧，不经 Chrome）。

这一层最容易出的错是"**看起来差不多但语义不对**"——位置差几十像素、
缓动方向反了、透明度关键帧保持而不是回到底层值。因此测试分四组：

1. **纯解析**：颜色/长度/阴影/线性与径向渐变/transform-origin/缓动；
2. **关键帧语义**：per-property 时间线、隐式端点补齐、逐帧 easing（对齐 CSS）；
3. **资格判定**：白名单放行与拒绝（拒绝必须给出可读原因，不能静默降级）；
4. **像素级**：几何位置（center/left/right/top/bottom 锚点）、旋转方向、
   透明度、z 序、与 Chrome 语义的一致点（文本 `color` 被忽略）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from clipwright.animation.mg_fastpath import (
    CSS_NAMED_BEZIER,
    FastPathIneligible,
    _build_specs,
    _cubic_bezier,
    composite_frame,
    coverage_report,
    ease,
    fastpath_eligible,
    layout_specs,
    load_template_defs,
    parse_color,
    parse_gradient,
    parse_length,
    parse_radial_gradient,
    parse_shadow,
    parse_transform_origin,
    track_value,
)

pytest.importorskip("PIL")


# ── 1. 纯解析 ─────────────────────────────────────────────

class TestParseColor:
    @pytest.mark.parametrize("value,expect", [
        ("#fff", (255, 255, 255, 255)),
        ("#000000", (0, 0, 0, 255)),
        ("#4f8cff", (79, 140, 255, 255)),
        ("#4f8cff80", (79, 140, 255, 128)),
        ("#f00", (255, 0, 0, 255)),
        ("rgb(79,140,255)", (79, 140, 255, 255)),
        ("rgba(22,35,74,0.92)", (22, 35, 74, 235)),
        ("rgba(255, 255, 255, 0.25)", (255, 255, 255, 64)),
        ("transparent", (0, 0, 0, 0)),
        ("white", (255, 255, 255, 255)),
        ("rgba(100%,0%,0%,50%)", (255, 0, 0, 128)),
    ])
    def test_parses(self, value: str, expect) -> None:
        assert parse_color(value) == expect

    @pytest.mark.parametrize("value", [
        "", None, "var(--accent)", "currentColor",
        "linear-gradient(90deg,#fff,#000)", "rgb(1,2)", "#12345", "notacolor",
    ])
    def test_rejects(self, value) -> None:
        assert parse_color(value) is None

    def test_clamps_out_of_range(self) -> None:
        assert parse_color("rgba(300,-5,10,2)") == (255, 0, 10, 255)


class TestParseLength:
    @pytest.mark.parametrize("value,expect", [
        (12, 12.0), (12.5, 12.5), ("12", 12.0), ("12px", 12.0), ("-8px", -8.0),
        ("0", 0.0), (None, 0.0),
    ])
    def test_parses(self, value, expect) -> None:
        assert parse_length(value, 0.0) == expect

    @pytest.mark.parametrize("value", ["2em", "50%", "calc(100% - 20px)", "auto", "1fr"])
    def test_rejects(self, value) -> None:
        assert parse_length(value, 0.0) is None


class TestParseShadow:
    def test_single(self) -> None:
        sh = parse_shadow("0 0 30px rgba(79,140,255,0.8)")
        assert len(sh) == 1 and sh[0].blur == 30.0
        assert sh[0].color == (79, 140, 255, 204)

    def test_multiple_and_offsets(self) -> None:
        sh = parse_shadow("2px 4px 8px #000, -1px -1px 2px #fff")
        assert [s.dx for s in sh] == [2.0, -1.0]
        assert [s.dy for s in sh] == [4.0, -1.0]

    def test_color_first_order(self) -> None:
        sh = parse_shadow("rgba(0,0,0,0.5) 0 2px 6px")
        assert sh[0].dy == 2.0 and sh[0].color == (0, 0, 0, 128)

    def test_blur_clamped_non_negative(self) -> None:
        assert parse_shadow("0 0 -4px #000")[0].blur == 0.0

    def test_empty(self) -> None:
        assert parse_shadow(None) == [] and parse_shadow("") == []

    @pytest.mark.parametrize("value", [
        "inset 0 0 10px #000",                 # inset 不支持
        "0 0 10px 4px #000",                   # spread 不支持
        "0 0 #000",                            # 缺 blur 但有 2 长度 → 允许
        "garbage",
        "0 0 10px",
    ])
    def test_unsupported(self, value: str) -> None:
        """`0 0 #000` 是合法的（dx dy 颜色），这里逐条断言真实预期。"""
        res = parse_shadow(value)
        if value == "0 0 #000":
            assert res is not None and res[0].blur == 0.0
        else:
            assert res is None


class TestParseGradient:
    def test_linear_with_angle(self) -> None:
        g = parse_gradient("linear-gradient(135deg, #0a0e1a 0%, #16234a 55%, #0a0e1a 100%)")
        assert g is not None and g.angle_deg == 135.0
        assert len(g.stops) == 3
        assert g.stops[1][0] == pytest.approx(0.55)

    def test_to_direction(self) -> None:
        g = parse_gradient("linear-gradient(to right, #000, #fff)")
        assert g.angle_deg == 90.0

    def test_default_direction_is_to_bottom(self) -> None:
        g = parse_gradient("linear-gradient(#000, #fff)")
        assert g.angle_deg == 180.0

    def test_positionless_stops_distributed(self) -> None:
        g = parse_gradient("linear-gradient(90deg, #000, #888, #fff)")
        assert [round(p, 3) for p, _ in g.stops] == [0.0, 0.5, 1.0]

    def test_stops_sorted(self) -> None:
        g = parse_gradient("linear-gradient(90deg, #fff 100%, #000 0%)")
        assert g.stops[0][1] == (0, 0, 0, 255)

    @pytest.mark.parametrize("value", [
        "radial-gradient(#000,#fff)", "conic-gradient(#000,#fff)",
        "repeating-linear-gradient(90deg,#000,#fff)", "var(--bg)", "",
        "linear-gradient(90deg, #000)", "linear-gradient(90deg, var(--a), #fff)",
    ])
    def test_rejects(self, value: str) -> None:
        assert parse_gradient(value) is None


class TestParseRadialGradient:
    def test_ellipse_at_center(self) -> None:
        g = parse_radial_gradient("radial-gradient(ellipse at center, #16234a 0%, #0a0e1a 70%)")
        assert g is not None and g.center == (0.5, 0.5)
        assert len(g.stops) == 2

    def test_at_percent(self) -> None:
        g = parse_radial_gradient("radial-gradient(circle at 20% 80%, #000, #fff)")
        assert g.center == (0.2, 0.8)

    def test_no_position_clause(self) -> None:
        g = parse_radial_gradient("radial-gradient(#000, #fff)")
        assert g is not None and g.center == (0.5, 0.5)

    @pytest.mark.parametrize("value", [
        "radial-gradient(circle 300px at center, #000, #fff)",   # 显式尺寸不支持
        "radial-gradient(closest-side, #000, #fff)",
        "linear-gradient(90deg,#000,#fff)", "nonsense",
    ])
    def test_rejects(self, value: str) -> None:
        assert parse_radial_gradient(value) is None


class TestParseTransformOrigin:
    @pytest.mark.parametrize("value,expect", [
        (None, (0.5, 0.5)),
        ("", (0.5, 0.5)),
        ("center", (0.5, 0.5)),
        ("left top", (0.0, 0.0)),
        ("right bottom", (1.0, 1.0)),
        ("center bottom", (0.5, 1.0)),
        ("bottom center", (0.5, 1.0)),
        ("50% 100%", (0.5, 1.0)),
    ])
    def test_parses(self, value, expect) -> None:
        assert parse_transform_origin(value) == expect

    @pytest.mark.parametrize("value", ["20px 30px", "12px", "center garbage"])
    def test_rejects(self, value: str) -> None:
        assert parse_transform_origin(value) is None


class TestEasing:
    def test_linear(self) -> None:
        for t in (0.0, 0.25, 0.5, 1.0):
            assert ease(t, "linear") == pytest.approx(t)

    def test_endpoints_exact(self) -> None:
        for name in CSS_NAMED_BEZIER:
            assert ease(0.0, name) == pytest.approx(0.0)
            assert ease(1.0, name) == pytest.approx(1.0)

    def test_ease_in_is_below_linear(self) -> None:
        assert ease(0.3, "ease-in") < 0.3

    def test_ease_out_is_above_linear(self) -> None:
        assert ease(0.3, "ease-out") > 0.3

    def test_back_out_overshoots(self) -> None:
        assert max(ease(i / 100, "back-out") for i in range(101)) > 1.05

    def test_elastic_out_undershoots(self) -> None:
        assert min(ease(i / 100, "elastic-out") for i in range(101)) < -0.02

    def test_cubic_bezier_string_matches_array(self) -> None:
        a = ease(0.4, "cubic-bezier(0.175,0.885,0.32,1.275)")
        b = ease(0.4, [0.175, 0.885, 0.32, 1.275])
        assert a == pytest.approx(b)

    def test_unknown_falls_back_to_linear(self) -> None:
        assert ease(0.4, "steps(3)") == pytest.approx(0.4)
        assert ease(0.4, None) == pytest.approx(0.4)

    def test_clamped_outside_unit(self) -> None:
        assert ease(-1.0, "ease-in") == 0.0
        assert ease(2.0, "ease-in") == 1.0

    def test_bezier_x_monotonic_solve(self) -> None:
        # 控制点决定 x(t)，solver 必须能反解出接近输入的 t
        for t in (0.1, 0.35, 0.6, 0.9):
            assert _cubic_bezier(0.42, 0.0, 0.58, 1.0, t) == pytest.approx(
                ease(t, "ease-in-out"), abs=1e-9)


# ── 2. 关键帧语义 ─────────────────────────────────────────

class TestTrackValue:
    def test_linear_between_two_points(self) -> None:
        v = track_value([(0.0, 0.0), (2.0, 10.0)], 1.0, {}, 4.0, 0.0)
        assert v == pytest.approx(5.0)

    def test_missing_head_filled_with_underlying(self) -> None:
        """首关键帧在 1s，0→1s 应从**底层值**插值过来，而不是第一帧就是起点值。"""
        v = track_value([(1.0, 0.0), (2.0, 1.0)], 0.5, {}, 4.0, 1.0)
        assert v == pytest.approx(0.5)  # 底层 1 → 0 的中间

    def test_missing_tail_filled_with_underlying(self) -> None:
        """末关键帧在 1s（值 0.6），1→4s 应回到**底层值** 1，而不是停在 0.6。"""
        v = track_value([(0.0, 0.0), (1.0, 0.6)], 2.5, {}, 4.0, 1.0)
        assert 0.6 < v < 1.0
        assert v == pytest.approx(0.6 + (1.0 - 0.6) * 0.5)

    def test_explicit_end_holds_value(self) -> None:
        v = track_value([(0.0, 0.0), (4.0, 0.6)], 3.0, {}, 4.0, 1.0)
        assert v == pytest.approx(0.45)

    def test_single_keyframe_mid_animation(self) -> None:
        """只声明一个关键帧：底层值 → 该值 → 回到底层值（与 CSS 一致）。"""
        assert track_value([(2.0, 3.0)], 0.0, {}, 4.0, 0.0) == pytest.approx(0.0)
        assert track_value([(2.0, 3.0)], 2.0, {}, 4.0, 0.0) == pytest.approx(3.0)
        assert track_value([(2.0, 3.0)], 4.0, {}, 4.0, 0.0) == pytest.approx(0.0)

    def test_per_segment_easing_from_starting_keyframe(self) -> None:
        """ease-in 声明在起点关键帧上 → 该区间曲线下凹。"""
        pts = [(0.0, 0.0), (1.0, 1.0), (4.0, 1.0)]
        slow = track_value(pts, 0.25, {0.0: "ease-in"}, 4.0, 1.0)
        fast = track_value(pts, 0.25, {0.0: "ease-out"}, 4.0, 1.0)
        assert slow < fast

    def test_empty_track_returns_underlying(self) -> None:
        assert track_value([], 1.0, {}, 4.0, 7.0) == 7.0

    def test_unsorted_input_sorted(self) -> None:
        v = track_value([(2.0, 10.0), (0.0, 0.0)], 1.0, {}, 4.0, 0.0)
        assert v == pytest.approx(5.0)

    def test_clamped_before_and_after(self) -> None:
        pts = [(0.0, 2.0), (4.0, 8.0)]
        assert track_value(pts, -1.0, {}, 4.0, 0.0) == 2.0
        assert track_value(pts, 9.0, {}, 4.0, 0.0) == 8.0


# ── 3. 资格判定 ────────────────────────────────────────────

def _mg(elements, dur=3.0, style=None):
    d = {"duration_sec": dur, "width": 640, "height": 360, "elements": elements}
    if style is not None:
        d["style"] = style
    return d


def _kf(*pairs):
    """`_kf((0, {"opacity": 0}), (1, {"opacity": 1}))` → MG 关键帧 dict 列表。"""
    out = []
    for pair in pairs:
        t, props = pair
        out.append({"time": t, **props})
    return out


class TestEligibility:
    def test_all_builtin_templates_eligible(self) -> None:
        """内置模板必须 100% 覆盖——覆盖率是 V3 有没有意义的唯一度量。

        注意要带上模板声明的默认参数（管线里 `mg_params` 就是这么来的）；
        **不带参数**时有占位符残留，白名单会正确地判为"不适用"（见下方专项测试）。
        """
        defs = load_template_defs()
        with_params = [
            (name, mg, {k: (v.get("default") if isinstance(v, dict) else v)
                        for k, v in (mg.get("params") or {}).items()})
            for name, mg in defs
        ]
        rep = coverage_report([(n, m) for n, m, _ in with_params])
        # coverage_report 不支持逐条 params，这里手动逐条判定
        rejected = []
        for name, mg, params in with_params:
            ok, why = fastpath_eligible(mg, params)
            if not ok:
                rejected.append({"name": name, "reason": why})
        assert rep["total"] >= 11
        assert rejected == [], rejected

    def test_template_without_params_is_rejected(self) -> None:
        """回归：不给参数时模板含 `{text}` 等占位符 → 必须判为不适用（回退 Chrome）。"""
        defs = dict(load_template_defs())
        ok, why = fastpath_eligible(defs["mg_title_reveal"])
        assert not ok and "占位符" in why

    def test_text_and_shape_ok(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "bg", "background": "#000", "keyframes": _kf((0, {"opacity": 1}))},
            {"type": "text", "content": "hi", "x": "center", "y": "center",
             "keyframes": _kf((0, {"opacity": 0}), (1, {"opacity": 1}))},
        ]))
        assert ok, why

    def test_unknown_element_type_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "image", "src": "a.png", "keyframes": _kf((0, {"opacity": 1}))},
        ]))
        assert not ok and "image" in why

    def test_unknown_keyframe_prop_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "x",
             "keyframes": _kf((0, {"opacity": 1}), (1, {"color": "#fff"}))},
        ]))
        assert not ok and "color" in why

    def test_unknown_element_key_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "x", "filter": "blur(2px)",
             "keyframes": _kf((0, {"opacity": 1}))},
        ]))
        assert not ok and "filter" in why

    def test_transform_origin_supported(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "shape", "shape": "rect", "width": 10, "height": 10,
             "transform_origin": "center bottom",
             "keyframes": _kf((0, {"height": 0}), (1, {"height": 10}))},
        ]))
        assert ok, why

    def test_text_size_keyframes_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "x", "keyframes": _kf((0, {"width": 10}))},
        ]))
        assert not ok and "文本尺寸" in why

    def test_multiline_text_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "a\nb", "keyframes": _kf((0, {"opacity": 1}))},
        ]))
        assert not ok and "换行" in why

    def test_bad_color_value_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "bg", "background": "var(--bg)", "keyframes": _kf((0, {"opacity": 1}))},
        ]))
        assert not ok and "背景" in why

    def test_expressed_units_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "x", "font_size": "2em",
             "keyframes": _kf((0, {"opacity": 1}))},
        ]))
        assert not ok and "font_size" in why

    def test_unknown_easing_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "x",
             "keyframes": _kf((0, {"opacity": 0, "easing": "steps(3)"}))},
        ]))
        assert not ok and "缓动" in why

    def test_unsafe_font_family_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "x", "keyframes": _kf((0, {"opacity": 1}))},
        ], style={"font_family": "JetBrains Mono"}))
        assert not ok and "字族" in why

    def test_unknown_style_key_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "x", "keyframes": _kf((0, {"opacity": 1}))},
        ], style={"backdrop_filter": "blur(4px)"}))
        assert not ok and "未支持字段" in why

    def test_no_elements_rejected(self) -> None:
        assert fastpath_eligible({"duration_sec": 1, "elements": []})[0] is False

    def test_bad_duration_rejected(self) -> None:
        assert fastpath_eligible({"duration_sec": 0, "elements": [
            {"type": "text", "content": "x", "keyframes": _kf((0, {"opacity": 1}))}]})[0] is False

    def test_ring_without_stroke_rejected(self) -> None:
        ok, why = fastpath_eligible(_mg([
            {"type": "ring", "width": 40, "height": 40, "border_width": 0,
             "keyframes": _kf((0, {"opacity": 1}))},
        ]))
        assert not ok and "ring" in why

    def test_eligibility_never_raises(self) -> None:
        for weird in (None, [], "x", {"elements": "x"}, {"elements": [None]}):
            ok, why = fastpath_eligible(weird)
            assert ok is False and isinstance(why, str)

    def test_text_color_key_ignored_like_html_path(self) -> None:
        """HTML 路径把 text 的 `color` 排除 → 快路径也必须忽略（否则两条路径不一致）。"""
        ok, why = fastpath_eligible(_mg([
            {"type": "text", "content": "x", "color": "#f4f6fb",
             "keyframes": _kf((0, {"opacity": 1}))},
        ]))
        assert ok, why
        specs = _build_specs(_mg([
            {"type": "text", "content": "x", "color": "#f4f6fb",
             "keyframes": _kf((0, {"opacity": 1}))},
        ]), {})
        assert specs[0].font_color == (255, 255, 255, 255)


# ── 4. 像素级 ─────────────────────────────────────────────

def _frame(mg_def, t, size=(200, 100), params=None):
    specs = layout_specs(_build_specs(mg_def, params or {}), *size)
    return composite_frame(specs, size, t, float(mg_def["duration_sec"]))


def _alpha_bbox(img):
    return img.getchannel("A").getbbox()


class TestGeometry:
    def test_center_anchor_centers_the_box(self) -> None:
        """x/y=center 时元素盒中心应落在画布中心（含 -50% 位移）。"""
        mg = _mg([{"type": "shape", "shape": "rect", "width": 40, "height": 20,
                   "color": "#ffffff", "x": "center", "y": "center",
                   "border_radius": 0, "keyframes": _kf((0, {"opacity": 1}))}])
        box = _alpha_bbox(_frame(mg, 0.0))
        assert box == (80, 40, 120, 60)

    def test_center_offsets_shift_box(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 40, "height": 20,
                   "color": "#ffffff", "x": "center", "y": "center",
                   "x_offset": 30, "y_offset": -10, "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 1}))}])
        assert _alpha_bbox(_frame(mg, 0.0)) == (110, 30, 150, 50)

    def test_left_top_anchors(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 10, "height": 10,
                   "color": "#ffffff", "x": "left", "y": "top", "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 1}))}])
        assert _alpha_bbox(_frame(mg, 0.0)) == (20, 20, 30, 30)

    def test_right_bottom_anchors(self) -> None:
        """right/bottom 锚点：正 offset = 远离边缘（向画布内），与 left/top 对称。"""
        mg = _mg([{"type": "shape", "shape": "rect", "width": 10, "height": 10,
                   "color": "#ffffff", "x": "right", "y": "bottom", "border_radius": 0,
                   "x_offset": 25, "y_offset": 15, "keyframes": _kf((0, {"opacity": 1}))}])
        # x: 右边距 20+25=45 → box 右边界 200-45=155；y: 下边距 60+15=75 → 下边界 100-75=25
        assert _alpha_bbox(_frame(mg, 0.0)) == (145, 15, 155, 25)

    def test_absolute_coords(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 10, "height": 10,
                   "color": "#ffffff", "x": 40, "y": 30, "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 1}))}])
        assert _alpha_bbox(_frame(mg, 0.0)) == (40, 30, 50, 40)

    def test_translate_keyframe_moves_box(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 10, "height": 10,
                   "color": "#ffffff", "x": "center", "y": "center", "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 1, "translate_x": 20}),
                                    (3, {"opacity": 1, "translate_x": 20}))}])
        assert _alpha_bbox(_frame(mg, 0.0)) == (115, 45, 125, 55)

    def test_full_bleed_bg(self) -> None:
        mg = _mg([{"type": "bg", "background": "#123456",
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(20, 10))
        assert img.getpixel((0, 0)) == (0x12, 0x34, 0x56, 255)
        assert img.getpixel((19, 9)) == (0x12, 0x34, 0x56, 255)


class TestOpacityAndZOrder:
    def test_opacity_keyframe_applied(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 10, "height": 10,
                   "color": "#ffffff", "x": 0, "y": 0, "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 0}), (2, {"opacity": 1}))}], dur=2.0)
        assert _frame(mg, 0.0).getchannel("A").getextrema()[1] == 0
        mid = _frame(mg, 1.0).getpixel((5, 5))[3]
        assert 100 < mid < 160
        assert _frame(mg, 2.0).getpixel((5, 5))[3] == pytest.approx(255, abs=2)

    def test_zero_opacity_element_skipped(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 10, "height": 10,
                   "color": "#ffffff", "x": 0, "y": 0, "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 0}), (2, {"opacity": 0}))}], dur=2.0)
        assert _frame(mg, 1.0).getchannel("A").getextrema() == (0, 0)

    def test_bg_painted_below_other_elements(self) -> None:
        """bg 在文档里靠后也必须压在最底（HTML 里 bg 是 z-index:0）。"""
        mg = _mg([
            {"type": "shape", "shape": "rect", "width": 20, "height": 20,
             "color": "#ff0000", "x": 0, "y": 0, "border_radius": 0,
             "keyframes": _kf((0, {"opacity": 1}))},
            {"type": "bg", "background": "#0000ff", "keyframes": _kf((0, {"opacity": 1}))},
        ])
        img = _frame(mg, 0.0, size=(40, 20))
        assert img.getpixel((5, 5)) == (255, 0, 0, 255)     # 红块在上
        assert img.getpixel((30, 15)) == (0, 0, 255, 255)   # 背景在底

    def test_document_order_between_normal_elements(self) -> None:
        mg = _mg([
            {"type": "shape", "shape": "rect", "width": 20, "height": 20,
             "color": "#ff0000", "x": 0, "y": 0, "border_radius": 0,
             "keyframes": _kf((0, {"opacity": 1}))},
            {"type": "shape", "shape": "rect", "width": 20, "height": 20,
             "color": "#00ff00", "x": 0, "y": 0, "border_radius": 0,
             "keyframes": _kf((0, {"opacity": 1}))},
        ])
        assert _frame(mg, 0.0, size=(40, 20)).getpixel((5, 5)) == (0, 255, 0, 255)


class TestRotationAndScale:
    def test_rotate_direction_is_clockwise_for_positive(self) -> None:
        """CSS 正角 = 顺时针。100×100 盒子绕中心转 90° 后，原左中点应落到上方。"""
        mg = _mg([{"type": "shape", "shape": "rect", "width": 60, "height": 10,
                   "color": "#ffffff", "x": "center", "y": "center", "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 1, "rotate": 90}),
                                    (2, {"opacity": 1, "rotate": 90}))}], dur=2.0)
        box = _alpha_bbox(_frame(mg, 0.0, size=(100, 100)))
        # 60×10 转 90° → 10×60，居中
        assert box == (45, 20, 55, 80)

    def test_scale_keyframe_changes_area(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 20, "height": 20,
                   "color": "#ffffff", "x": "center", "y": "center", "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 1, "scale": 0.5}),
                                    (2, {"opacity": 1, "scale": 2.0}))}], dur=2.0)
        small = _alpha_bbox(_frame(mg, 0.0, size=(100, 100)))
        big = _alpha_bbox(_frame(mg, 2.0, size=(100, 100)))
        assert (small[2] - small[0]) == pytest.approx(10, abs=2)
        assert (big[2] - big[0]) == pytest.approx(40, abs=2)

    def test_scale_keeps_center_fixed(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 20, "height": 20,
                   "color": "#ffffff", "x": "center", "y": "center", "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 1, "scale": 0.5}),
                                    (2, {"opacity": 1, "scale": 2.0}))}], dur=2.0)
        for t in (0.0, 1.0, 2.0):
            b = _alpha_bbox(_frame(mg, t, size=(100, 100)))
            assert (b[0] + b[2]) / 2 == pytest.approx(50, abs=1.5)
            assert (b[1] + b[3]) / 2 == pytest.approx(50, abs=1.5)

    def test_transform_origin_bottom_grows_upward(self) -> None:
        """`transform-origin: center bottom` + height 关键帧 → 底边不动，向上长。"""
        mg = _mg([{"type": "shape", "shape": "rect", "width": 20, "color": "#ffffff",
                   "x": "center", "y": "center", "border_radius": 0,
                   "transform_origin": "center bottom", "height": 0,
                   "keyframes": _kf((0, {"opacity": 1, "height": 0, "scale": 1}),
                                    (2, {"opacity": 1, "height": 40, "scale": 1}))}], dur=2.0)
        b = _alpha_bbox(_frame(mg, 2.0, size=(100, 100)))
        assert b[3] == pytest.approx(70, abs=2)   # 底边固定在 50+20=70
        assert b[1] == pytest.approx(30, abs=3)   # 顶边向上到 30

    def test_width_keyframe_animates_bar(self) -> None:
        mg = _mg([{"type": "line", "width": 0, "height": 6, "color": "#ffffff",
                   "x": 10, "y": 10, "border_radius": 3,
                   "keyframes": _kf((0, {"opacity": 1, "width": 0}),
                                    (2, {"opacity": 1, "width": 60}))}], dur=2.0)
        assert _alpha_bbox(_frame(mg, 0.0, size=(100, 40))) is None
        b = _alpha_bbox(_frame(mg, 2.0, size=(100, 40)))
        assert (b[2] - b[0]) == pytest.approx(60, abs=2)


class TestShapes:
    def test_circle_is_round(self) -> None:
        mg = _mg([{"type": "circle", "width": 40, "height": 40, "color": "#ffffff",
                   "x": "center", "y": "center", "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(100, 100))
        assert img.getpixel((50, 50))[3] == 255      # 圆心实心
        assert img.getpixel((31, 31))[3] == 0        # 角上透明（不是方角）

    def test_ring_is_hollow(self) -> None:
        mg = _mg([{"type": "ring", "width": 40, "height": 40, "border_width": 4,
                   "border_color": "#ffffff", "x": "center", "y": "center",
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(100, 100))
        assert img.getpixel((50, 50))[3] == 0        # 中心空心
        assert img.getpixel((50, 31))[3] > 200       # 上边缘有环

    def test_arc_is_dome(self) -> None:
        """arc = border-radius 50% 50% 0 0：上半圆顶 + 下半矩形。"""
        mg = _mg([{"type": "arc", "width": 40, "height": 40, "border_width": 4,
                   "border_color": "#ffffff", "x": "center", "y": "center",
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(100, 100))
        assert img.getpixel((30, 68))[3] > 200       # 底边有横向描边
        assert img.getpixel((30, 32))[3] == 0        # 左上角（圆外）是空的

    def test_border_radius_rounded_rect(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 40, "height": 40,
                   "color": "#ffffff", "border_radius": 18, "x": "center", "y": "center",
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(100, 100))
        assert img.getpixel((50, 50))[3] == 255
        assert img.getpixel((31, 31))[3] == 0        # 圆角切掉了角

    def test_gradient_covers_box_at_all_angles(self) -> None:
        """回归：任意角度的线性渐变都必须**铺满**整个盒子（不能留透明未覆盖区）。

        旧实现"旋转斜坡带 + 中心裁切"，非 45° 时旋转后的画布装不下目标尺寸，
        未覆盖区被补齐为透明 → 渐变背景整片消失（实测整帧 43% 透明）。
        """
        from clipwright.animation.mg_fastpath import _ramp_image

        for angle in (0, 30, 45, 90, 135, 180, 270, 315):
            grad = parse_gradient(f"linear-gradient({angle}deg, #000000 0%, #ffffff 100%)")
            assert grad is not None
            img = _ramp_image(grad, 120, 60)
            alpha = img.getchannel("A")
            assert alpha.getextrema() == (255, 255), f"{angle}deg 渐变有透明区域"
            # 两端亮度必须拉开（斜坡方向正确，没有退化成纯色）
            px = img.load()
            corners = [px[2, 2][0], px[117, 2][0], px[2, 57][0], px[117, 57][0]]
            assert max(corners) - min(corners) > 100, f"{angle}deg 渐变方向退化: {corners}"

    def test_vertical_gradient_direction(self) -> None:
        """`180deg` = to bottom：上黑下白，且每列一致（纯竖直）。"""
        from clipwright.animation.mg_fastpath import _ramp_image

        grad = parse_gradient("linear-gradient(180deg, #000000 0%, #ffffff 100%)")
        img = _ramp_image(grad, 40, 100)
        top = img.getpixel((20, 1))[0]
        bottom = img.getpixel((20, 98))[0]
        assert top < 30 and bottom > 225
        assert abs(img.getpixel((2, 50))[0] - img.getpixel((37, 50))[0]) <= 4  # 同行同色

    def test_horizontal_gradient_direction(self) -> None:
        """`90deg` = to right：左黑右白。"""
        from clipwright.animation.mg_fastpath import _ramp_image

        grad = parse_gradient("linear-gradient(90deg, #000000 0%, #ffffff 100%)")
        img = _ramp_image(grad, 100, 40)
        assert img.getpixel((1, 20))[0] < 30
        assert img.getpixel((98, 20))[0] > 225

    def test_no_zero_deg_is_to_top(self) -> None:
        """`0deg` = to top：上白下黑（与 180deg 相反）。"""
        from clipwright.animation.mg_fastpath import _ramp_image

        grad = parse_gradient("linear-gradient(0deg, #000000 0%, #ffffff 100%)")
        img = _ramp_image(grad, 40, 100)
        assert img.getpixel((20, 1))[0] > 225 and img.getpixel((20, 98))[0] < 30

    def test_shape_gradient_covers_shape(self) -> None:
        """元素级渐变同样必须铺满（含圆角/圆形遮罩下）。"""
        mg = _mg([{"type": "shape", "shape": "rect", "width": 60, "height": 40,
                   "background": "linear-gradient(37deg, #000000 0%, #ffffff 100%)",
                   "border_radius": 0, "x": 10, "y": 10,
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(100, 60))
        box = _alpha_bbox(img)
        assert box == (10, 10, 70, 50)
        # 盒内不应出现半透明/透明像素（渐变本身不透明）
        crop = img.crop(box).getchannel("A")
        assert crop.getextrema() == (255, 255)
        mg = _mg([{"type": "shape", "shape": "rect", "width": 80, "height": 40,
                   "background": "linear-gradient(90deg, #000000 0%, #ffffff 100%)",
                   "border_radius": 0, "x": 10, "y": 10,
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(100, 60))
        left = img.getpixel((14, 30))[0]
        right = img.getpixel((86, 30))[0]
        assert right - left > 200

    def test_radial_gradient_uses_farthest_corner(self) -> None:
        """回归：径向渐变的归一化必须是 CSS 默认的 farthest-corner。

        白→黑、圆点居中：盒内点 (0.75,0.75) 到中心的归一化距离是 √2/2 ≈ 0.707，
        除以最远角距离 (√0.5²+0.5² = 0.707) 得 u=0.5 → 灰度约 127。
        旧实现按"半宽/半高"归一化（u=0.707）→ 灰度只有 ~75，整块渐变明显偏暗偏快。
        """
        from clipwright.animation.mg_fastpath import _radial_image

        g = parse_radial_gradient("radial-gradient(ellipse at center, #ffffff 0%, #000000 100%)")
        img = _radial_image(g, 200, 200)
        assert img.getpixel((100, 100))[0] > 240           # 中心白
        corner = img.getpixel((1, 1))[0]
        assert corner < 20                                  # 角落到末色标（黑）
        mid = img.getpixel((150, 150))[0]
        assert 110 < mid < 145, f"(0.75,0.75) 处灰度 {mid}，预期 ≈127"

    def test_radial_gradient_circle_shape_respects_aspect(self) -> None:
        """`circle`：在 200×100 的盒子里应是**正圆**——到中心像素距离相同的两点亮度相同。

        对比椭圆：`ellipse` 下这两点亮度会差很多（椭圆按半宽/半高计距离）。
        """
        from clipwright.animation.mg_fastpath import _radial_image

        g = parse_radial_gradient("radial-gradient(circle at center, #ffffff 0%, #000000 100%)")
        assert g.shape == "circle"
        img = _radial_image(g, 200, 100)
        # 两点到中心 (100,50) 都是 49px
        right = img.getpixel((149, 50))[0]
        down = img.getpixel((100, 99))[0]
        assert abs(right - down) <= 8, f"circle 未按像素距离归一化: right={right} down={down}"

        ge = parse_radial_gradient(
            "radial-gradient(ellipse at center, #ffffff 0%, #000000 100%)")
        img_e = _radial_image(ge, 200, 100)
        er, ed = img_e.getpixel((149, 50))[0], img_e.getpixel((100, 99))[0]
        assert abs(er - ed) > 40, f"ellipse 应与 circle 明显不同: {er} vs {ed}"

    def test_radial_gradient_brighter_at_center(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 80, "height": 80,
                   "background": "radial-gradient(ellipse at center, #ffffff 0%, #000000 100%)",
                   "border_radius": 0, "x": 10, "y": 10,
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(100, 100))
        assert img.getpixel((50, 50))[0] > img.getpixel((12, 12))[0] + 100

    def test_box_shadow_paints_outside_the_box(self) -> None:
        mg = _mg([{"type": "shape", "shape": "rect", "width": 20, "height": 20,
                   "color": "#ffffff", "border_radius": 0, "x": "center", "y": "center",
                   "box_shadow": "0 0 10px rgba(255,0,0,1)",
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(100, 100))
        outside = img.getpixel((50, 34))
        assert outside[3] > 0 and outside[0] > outside[2]  # 框外有红色泛光

    def test_text_shadow_paints_around_glyph(self) -> None:
        mg = _mg([{"type": "text", "content": "AA", "font_size": 40, "font_color": "#ffffff",
                   "text_shadow": "0 0 12px #ff0000", "x": "center", "y": "center",
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(200, 100))
        alpha = img.getchannel("A")
        assert alpha.getextrema()[1] > 200
        # 阴影让非零像素面积明显大于纯文本
        mg_no_shadow = _mg([{"type": "text", "content": "AA", "font_size": 40,
                             "font_color": "#ffffff", "x": "center", "y": "center",
                             "keyframes": _kf((0, {"opacity": 1}))}])
        solid = sum(c for v, c in enumerate(alpha.histogram()) if v > 80)
        plain = sum(c for v, c in enumerate(
            _frame(mg_no_shadow, 0.0, size=(200, 100)).getchannel("A").histogram()) if v > 80)
        assert solid > plain


class TestText:
    def test_text_is_drawn_in_font_color(self) -> None:
        mg = _mg([{"type": "text", "content": "帧艺", "font_size": 40,
                   "font_color": "#ff8800", "x": "left", "y": "top",
                   "keyframes": _kf((0, {"opacity": 1}))}])
        img = _frame(mg, 0.0, size=(200, 100))
        px = [img.getpixel((x, y)) for x in range(200) for y in range(100)]
        colored = [p for p in px if p[3] > 200]
        assert colored, "文本没有画出任何像素"
        assert colored[0][0] == 255 and colored[0][1] == 0x88

    def test_empty_text_is_skipped_not_crashed(self) -> None:
        mg = _mg([{"type": "text", "content": "", "font_size": 40,
                   "x": "center", "y": "center", "keyframes": _kf((0, {"opacity": 1}))}])
        assert _frame(mg, 0.0, size=(100, 50)).getchannel("A").getextrema() == (0, 0)

    def test_letter_spacing_widens_the_box(self) -> None:
        def width(**extra):
            mg = _mg([{"type": "text", "content": "AAAA", "font_size": 40,
                       "x": "left", "y": "top", "keyframes": _kf((0, {"opacity": 1})), **extra}])
            return _build_specs(mg, {})[0].payload["raw_width"] if False else _measured_width(mg)

        def _measured_width(mg):
            specs = layout_specs(_build_specs(mg, {}), 400, 100)
            return specs[0].width

        assert width(letter_spacing=10) > width() + 30

    def test_line_height_controls_box_height(self) -> None:
        mg = _mg([{"type": "text", "content": "x", "font_size": 20, "line_height": 2,
                   "x": "left", "y": "top", "keyframes": _kf((0, {"opacity": 1}))}])
        specs = layout_specs(_build_specs(mg, {}), 400, 100)
        assert specs[0].height == pytest.approx(40.0)


class TestParams:
    def test_params_substitute_placeholders(self) -> None:
        mg = _mg([{"type": "text", "content": "{text}", "font_size": 20,
                   "x": "left", "y": "top", "keyframes": _kf((0, {"opacity": 1}))}])
        specs = _build_specs(mg, {"text": "长文本内容"})
        layout_specs(specs, 400, 100)
        assert specs[0].content == "长文本内容"

    def test_unresolved_placeholder_is_ineligible(self) -> None:
        """未解析的占位符 → 判为不适用（回退 Chrome 走 `_ensure_mg_placeholders_filled`）。

        HTML 路径有"残留占位符二次填充"兜底，快路径没有那一步；
        宁可回退，也不把 `{text}` 字面量画进画面。
        """
        mg = _mg([{"type": "text", "content": "{missing}", "font_size": 20,
                   "x": "left", "y": "top", "keyframes": _kf((0, {"opacity": 1}))}])
        ok, why = fastpath_eligible(mg, {})
        assert not ok and "占位符" in why
        # 提供参数后立即可用
        ok2, _ = fastpath_eligible(mg, {"missing": "补上了"})
        assert ok2
        assert _build_specs(mg, {"missing": "补上了"})[0].content == "补上了"

    def test_variables_override_params(self) -> None:
        from clipwright.animation.mg_fastpath import render_mg_fastpath

        mg = _mg([{"type": "text", "content": "{text}", "font_size": 20,
                   "x": "left", "y": "top", "keyframes": _kf((0, {"opacity": 1}))}])
        ok, _ = fastpath_eligible(mg, {"text": "a"})
        assert ok
        specs = _build_specs(mg, {**{"text": "a"}, **{"text": "b"}})
        assert specs[0].content == "b"


class TestRenderEntry:
    def test_render_returns_none_and_logs_for_ineligible(self, tmp_path) -> None:
        from clipwright.animation.mg_fastpath import render_mg_fastpath

        mg = _mg([{"type": "image", "src": "x.png",
                   "keyframes": _kf((0, {"opacity": 1}))}])
        assert render_mg_fastpath(mg, tmp_path / "o.mov", (64, 64), 30.0) is None

    def test_render_never_raises_on_garbage(self, tmp_path) -> None:
        from clipwright.animation.mg_fastpath import render_mg_fastpath

        for garbage in (None, {}, {"elements": "x"}, {"duration_sec": 1, "elements": [None]}):
            assert render_mg_fastpath(garbage, tmp_path / "o.mov", (64, 64), 30.0) is None

    def test_render_produces_alpha_mov(self, tmp_path) -> None:
        import shutil
        import subprocess

        if not shutil.which("ffmpeg"):
            pytest.skip("需要 ffmpeg")
        from clipwright.animation.mg_fastpath import render_mg_fastpath

        mg = _mg([{"type": "shape", "shape": "rect", "width": 20, "height": 20,
                   "color": "#ff0000", "x": "center", "y": "center", "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 0}), (1, {"opacity": 1}))}], dur=1.0)
        mov = tmp_path / "o.mov"
        out = render_mg_fastpath(mg, mov, (128, 128), 10.0)
        assert out and Path(out).exists()
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,pix_fmt,width,height,nb_frames",
             "-of", "json", str(mov)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        info = json.loads(probe.stdout)["streams"][0]
        assert info["pix_fmt"] == "argb"          # 必须带 alpha，否则下游 overlay 合成会黑底
        assert (info["width"], info["height"]) == (128, 128)
        assert int(info["nb_frames"]) == 10

    def test_prores_codec_option(self, tmp_path) -> None:
        import shutil
        import subprocess

        if not shutil.which("ffmpeg"):
            pytest.skip("需要 ffmpeg")
        from clipwright.animation.mg_fastpath import render_mg_fastpath

        mg = _mg([{"type": "shape", "shape": "rect", "width": 20, "height": 20,
                   "color": "#00ff00", "x": 10, "y": 10, "border_radius": 0,
                   "keyframes": _kf((0, {"opacity": 1}))}], dur=0.5)
        mov = tmp_path / "o.mov"
        assert render_mg_fastpath(mg, mov, (64, 64), 10.0, codec="prores")
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,pix_fmt", "-of", "json", str(mov)],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        info = json.loads(probe.stdout)["streams"][0]
        assert info["codec_name"] == "prores"
        assert "yuva" in info["pix_fmt"]


class TestLayerCache:
    def test_layers_reused_across_frames(self) -> None:
        """缓存命中：同一元素在尺寸不变时应只画一次（V3 性能的关键前提）。"""
        import clipwright.animation.mg_fastpath as fp

        calls = {"n": 0}
        original = fp._paint_element

        def counting(spec, cache=None):
            calls["n"] += 1
            return original(spec, cache)

        fp._paint_element = counting
        try:
            mg = _mg([{"type": "shape", "shape": "rect", "width": 20, "height": 20,
                       "color": "#fff", "x": 10, "y": 10, "border_radius": 0,
                       "keyframes": _kf((0, {"opacity": 1}), (2, {"opacity": 0}))}], dur=2.0)
            specs = layout_specs(_build_specs(mg, {}), 64, 64)
            cache: dict = {}
            for i in range(10):
                fp.composite_frame(specs, (64, 64), i / 10.0, 2.0, cache=cache)
        finally:
            fp._paint_element = original
        assert calls["n"] == 1, f"图层未复用（重建 {calls['n']} 次）"

    def test_layer_rebuilt_when_size_animates(self) -> None:
        import clipwright.animation.mg_fastpath as fp

        calls = {"n": 0}
        original = fp._paint_element

        def counting(spec, cache=None):
            calls["n"] += 1
            return original(spec, cache)

        fp._paint_element = counting
        try:
            mg = _mg([{"type": "line", "width": 0, "height": 6, "color": "#fff",
                       "x": 10, "y": 10, "border_radius": 3,
                       "keyframes": _kf((0, {"opacity": 1, "width": 0}),
                                        (1, {"opacity": 1, "width": 60}))}], dur=1.0)
            specs = layout_specs(_build_specs(mg, {}), 100, 40)
            cache: dict = {}
            for i in range(10):
                fp.composite_frame(specs, (100, 40), i / 10.0, 1.0, cache=cache)
        finally:
            fp._paint_element = original
        assert calls["n"] > 1, "尺寸关键帧变化时图层必须重建"
