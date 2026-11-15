"""MG 动效下限：确定性补主运动，避免 mg_motion_weak 降级。"""

from __future__ import annotations

from clipwright.animation.mg.type_generation import ensure_motion_floor, motion_report


def test_motion_floor_adds_shift_and_coverage():
    mg = {
        "duration_sec": 6.0, "width": 1920, "height": 1080,
        "elements": [
            {"type": "text", "content": "测试", "x": "center", "y": "center",
             "font_size": 64, "keyframes": [{"time": 0, "opacity": 0},
                                             {"time": 0.5, "opacity": 1}]},
            {"type": "shape", "shape": "rect", "x": 100, "y": 100, "width": 100, "height": 50,
             "keyframes": [{"time": 0, "opacity": 0}, {"time": 0.5, "opacity": 1}]},
        ],
    }
    out = ensure_motion_floor(mg, width=1920)
    motion = motion_report(out, 6.0)
    assert motion["max_shift"] >= 1920 * 0.04
    assert motion["coverage"] >= 0.45
    assert motion["moving_count"] >= 2
