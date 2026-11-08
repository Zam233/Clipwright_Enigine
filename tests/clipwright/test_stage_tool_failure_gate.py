"""工具失败率 → 降级 → 主 agent 返工（E2E 2026-10-30 用户裁定）。"""

from clipwright.services.stage_subagent import (
    TOOL_FAILURE_RATIO_LIMIT,
    tool_failure_degradation,
)


def test_high_failure_rate_is_a_degradation():
    calls = ([{"tool": "video_trim", "status": "error"}] * 60
             + [{"tool": "video_trim", "status": "success"}] * 73)
    hit, deg = tool_failure_degradation("edit", calls)
    assert hit is True and deg is not None
    assert deg["kind"] == "stage_tool_failures"
    assert "60/133" in deg["detail"] and "45%" in deg["detail"]
    assert "video_trim×60" in deg["detail"]
    assert deg["scope"] == "edit"


def test_low_failure_rate_is_clean():
    calls = ([{"tool": "video_trim", "status": "error"}] * 2
             + [{"tool": "video_trim", "status": "success"}] * 20)
    assert tool_failure_degradation("edit", calls) == (False, None)


def test_small_sample_is_exempt():
    assert tool_failure_degradation("edit", [{"tool": "t", "status": "error"}] * 3) == (False, None)


def test_not_found_counts_as_failure():
    calls = [{"tool": "frame_check", "status": "not_found"}] * 5 + [
        {"tool": "video_trim", "status": "success"}] * 5
    hit, deg = tool_failure_degradation("quality", calls)
    assert hit is True and "50%" in deg["detail"]


def test_threshold_constant_is_sane():
    assert 0.05 <= TOOL_FAILURE_RATIO_LIMIT <= 0.8
