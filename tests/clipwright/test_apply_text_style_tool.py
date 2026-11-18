"""apply_text_style 工具：样式写入文字轨（不是生成 mp4）。"""

from __future__ import annotations

import pytest

from clipwright.services import task_blackboard as bb
from clipwright.tool import ApplyTextStyleTool


def _timeline():
    return {
        "version": 1, "width": 1920, "height": 1080,
        "tracks": [
            {"id": "t_text", "kind": "text", "index": 1, "clips": [
                {"id": "tc_1", "kind": "text", "asset_id": "", "track_id": "t_text",
                 "start_sec": 1.0, "duration_sec": 3.0, "text": "测试", "metadata": {}},
            ]},
        ],
    }


@pytest.mark.asyncio
async def test_apply_text_style_updates_clip_metadata():
    task_id = "task_style_tool_test"
    bb.put(task_id, "timeline", _timeline(), by="edit")
    result = await ApplyTextStyleTool().execute(
        task_id, "tc_1",
        style={"font_size": 72, "fill": "#ffd400", "box": True,
               "box_color": "#dc1414", "box_radius": 14, "stroke_width": 3},
        motion={"type": "slide_in", "reveal_sec": 0.5},
        duration_sec=2.4, duration_reason="短促强调",
    )
    assert result.status == "success", result.error
    clip = bb.get(task_id)["timeline"]["tracks"][0]["clips"][0]
    assert clip["metadata"]["style"]["box"] is True
    assert clip["metadata"]["motion"]["type"] == "slide_in"
    assert clip["metadata"]["style"]["fill"] == "#ffd400"
    assert abs(clip["duration_sec"] - 2.4) < 0.01


@pytest.mark.asyncio
async def test_apply_text_style_accepts_glitch_and_rebuilds_html():
    task_id = "task_style_tool_test2"
    bb.put(task_id, "timeline", _timeline(), by="edit")
    result = await ApplyTextStyleTool().execute(
        task_id, "tc_1", motion={"type": "glitch"},
        style={"font_size": 72, "fill": {"type": "gradient", "colors": ["#ff00ff", "#00ffff"]}},
    )
    assert result.status == "success", result.error
    clip = bb.get(task_id)["timeline"]["tracks"][0]["clips"][0]
    assert clip["metadata"]["motion"]["type"] == "glitch"
    assert clip["metadata"]["mg_html"]
