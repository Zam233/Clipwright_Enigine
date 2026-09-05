"""V3: 关键帧表达式模块——时间基归一化 + 缓动感知插值 + 单元测试。"""

from __future__ import annotations

import re

from clipwright.utils.keyframes_expr import (
    easing_u,
    normalize_keyframe_times,
    property_expression,
)


class TestNormalizeKeyframeTimes:
    def test_marker_clip_local_shifts_to_segment_relative(self) -> None:
        kfs = [{"time": 0, "properties": {"opacity": 0}},
               {"time": 0.5, "properties": {"opacity": 1}}]
        norm = normalize_keyframe_times(kfs, 4.0, 10.0, time_base_marker="clip_local")
        assert [k["time"] for k in norm] == [0.0, 0.5]  # 已是相对秒，不位移

    def test_marker_absolute_shifts_to_relative(self) -> None:
        kfs = [{"time": 10.0, "properties": {"opacity": 0}},
               {"time": 12.0, "properties": {"opacity": 1}}]
        norm = normalize_keyframe_times(kfs, 4.0, 10.0, time_base_marker="absolute")
        assert [k["time"] for k in norm] == [0.0, 2.0]

    def test_heuristic_pipeline_absolute(self) -> None:
        """管线绝对秒（min >= start_sec）→ 减 start_sec。"""
        kfs = [{"time": 5.0, "properties": {"opacity": 0}},
               {"time": 5.4, "properties": {"opacity": 1}}]
        norm = normalize_keyframe_times(kfs, 10.0, 5.0)
        assert [k["time"] for k in norm] == [0.0, 0.4]

    def test_heuristic_frontend_relative(self) -> None:
        """前端相对秒（min < start_sec）→ 不位移。"""
        kfs = [{"time": 0.0, "properties": {"opacity": 0}},
               {"time": 3.0, "properties": {"opacity": 1}}]
        norm = normalize_keyframe_times(kfs, 5.0, 5.0)
        assert [k["time"] for k in norm] == [0.0, 3.0]

    def test_times_clamped_to_duration(self) -> None:
        kfs = [{"time": 0.0, "properties": {"opacity": 0}},
               {"time": 99.0, "properties": {"opacity": 1}}]
        norm = normalize_keyframe_times(kfs, 5.0, 0.0, time_base_marker="clip_local")
        assert norm[-1]["time"] == 5.0

    def test_sorted_output(self) -> None:
        kfs = [{"time": 2, "properties": {"opacity": 1}},
               {"time": 1, "properties": {"opacity": 0}}]
        norm = normalize_keyframe_times(kfs, 5.0, 0.0, time_base_marker="clip_local")
        assert [k["time"] for k in norm] == [1.0, 2.0]


class TestPropertyExpression:
    def test_two_keyframes_linear(self) -> None:
        kfs = [{"time": 0, "properties": {"opacity": 0}},
               {"time": 2, "properties": {"opacity": 1}}]
        expr = property_expression(kfs, "opacity", 1.0)
        # 结构：首帧钳位 + 线性段 + 端点钳位（外层 if 收尾双括号）
        assert expr.startswith("if(lt(t,0),0,")
        assert "(t-0)/2" in expr
        assert expr.endswith(",1))")

    def test_three_keyframes_nested(self) -> None:
        kfs = [
            {"time": 0, "properties": {"opacity": 0}},
            {"time": 1, "properties": {"opacity": 1}, "easing": "ease-out-cubic"},
            {"time": 3, "properties": {"opacity": 0}, "easing": "ease-in"},
        ]
        expr = property_expression(kfs, "opacity", 1.0)
        assert expr.count("if(lt(t,") == 3  # 钳位 + 2 段
        # 缓动生效：段 [0,1] ease-out-cubic → pow(u-1,3)；段 [1,3] ease-in → pow(u,2)
        assert "pow(((t-0)/1)-1,3)" in expr
        assert "pow((t-1)/2,2)" in expr

    def test_missing_property_returns_none(self) -> None:
        kfs = [{"time": 0, "properties": {"opacity": 0}}]
        assert property_expression(kfs, "translate_x", 0.0) is None

    def test_single_keyframe_constant(self) -> None:
        kfs = [{"time": 0, "properties": {"opacity": 0.5}}]
        assert property_expression(kfs, "opacity", 1.0) == "0.5"

    def test_t_offset_shifts_window(self) -> None:
        kfs = [{"time": 0, "properties": {"opacity": 0}},
               {"time": 1, "properties": {"opacity": 1}}]
        expr = property_expression(kfs, "opacity", 1.0, t_offset=5.0)
        assert "if(lt(t,5)" in expr
        assert "(t-5)/1" in expr


class TestEasing:
    def test_unknown_falls_back_linear(self) -> None:
        assert easing_u("nonexistent", "u") == "(u)"
        assert easing_u(None, "u") == "(u)"

    def test_known_easings_expand(self) -> None:
        assert easing_u("ease-in-cubic", "u") == "pow(u,3)"
        assert "sin" in easing_u("ease-out-elastic", "u")
        assert easing_u("ease-out-bounce", "u").count("if(") == 3

    def test_expressions_are_valid_ffmpeg_function_calls(self) -> None:
        """所有缓动模板展开后括号配平（ffmpeg 表达式语法完整性）。"""
        for name in EASING_NAMES_ALL():
            e = easing_u(name, "(t-x)/d")
            assert e.count("(") == e.count(")"), name


def EASING_NAMES_ALL():
    from clipwright.utils.keyframes_expr import EASING_EXPR
    return list(EASING_EXPR.keys())


class TestNumericFormatting:
    def test_no_scientific_notation(self) -> None:
        kfs = [{"time": 0, "properties": {"opacity": 1e-7}},
               {"time": 1, "properties": {"opacity": 1}}]
        expr = property_expression(kfs, "opacity", 1.0)
        assert not re.search(r"e-0?\d", expr), expr
