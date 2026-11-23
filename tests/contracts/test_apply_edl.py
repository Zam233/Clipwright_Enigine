"""R3：apply_edl 批量裁剪后落时间线。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
from clipwright.services import task_blackboard as bb
from clipwright.tool.edl_apply import ApplyEdlTool


def _timeline() -> Timeline:
    tl = Timeline(id="tl", duration_sec=20)
    tl.tracks.append(Track(id="v", name="视频", kind=ClipKind.VIDEO, index=0, clips=[
        Clip(id="old", kind=ClipKind.VIDEO, asset_id="old.mp4", track_id="v",
             start_sec=0, duration_sec=4),
    ]))
    return tl


def test_apply_edl_adds_clips() -> None:
    tid = "task_apply_edl"
    bb.reset(tid)
    bb.put(tid, "timeline", _timeline().model_dump(mode="json"), by="edit")
    fake = AsyncMock(return_value=SimpleNamespace(
        status="success", output={"items": [
            {"index": 0, "status": "ok", "output_path": "/tmp/a.mp4"},
            {"index": 1, "status": "ok", "output_path": "/tmp/b.mp4"},
        ]},
    ))
    try:
        with patch("clipwright.tool.registry.ToolRegistry.execute", fake):
            out = asyncio.run(ApplyEdlTool().execute(
                task_id=tid,
                items=[{"asset": "a.mp4", "duration_sec": 3, "position": "end"},
                       {"asset": "b.mp4", "duration_sec": 3, "position": "end"}],
            ))
        assert out.status == "success", out.error
        assert len(out.output["added_clip_ids"]) == 2
        assert out.output["items"][0]["status"] == "ok"
    finally:
        bb.reset(tid)
