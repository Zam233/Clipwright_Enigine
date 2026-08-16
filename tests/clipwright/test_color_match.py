"""P8: 跨片段色彩匹配（color_match）测试。"""

from __future__ import annotations

from clipwright.tool.color import _extract_yavg
from clipwright.tool.registry import ToolRegistry
from clipwright.tool import register_builtin_tools


def test_extract_yavg_parses_last_value() -> None:
    stderr = (
        "frame:1 pts:0 pts_time:0\n"
        "lavfi.signalstats.YAVG=0.5\n"
        "frame:2 pts:1 pts_time:1\n"
        "lavfi.signalstats.YAVG=0.62\n"
    )
    assert _extract_yavg(stderr) == 0.62


def test_extract_yavg_no_match_none() -> None:
    assert _extract_yavg("no signalstats output") is None


def test_color_match_tool_registered() -> None:
    register_builtin_tools()
    tool = ToolRegistry.get("color_match")
    assert tool is not None
    assert tool.name == "color_match"
