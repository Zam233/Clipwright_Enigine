"""V7：`timeline_view` 作为原子工具（复核图能被别的 Agent 调用）。

此前复核图只挂在 `RenderService._post_render_review` 内（渲染完顺手做一次），
质检/自愈循环/编辑器都拿不到。本工具把 `services/timeline_view.py` 暴露出来：
- `mode=cuts`：每个切点前后三帧对照条；
- `mode=overview`：全片均匀抽帧联络表。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from clipwright.schema.tool import ToolStatus
from clipwright.tool.timeline_view import TimelineViewTool

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要本机 ffmpeg/ffprobe",
)


def _clip(path: Path, seconds: float) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]


def _run(**kwargs):
    return asyncio.run(TimelineViewTool().execute(**kwargs))


class TestCutsMode:
    def test_generates_sheets(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 4.0)
        res = _run(video_path=str(video), mode="cuts", cut_times=[1.0, 2.5, 3.5],
                   out_dir=str(tmp_path / "out"))
        assert res.status == ToolStatus.SUCCESS
        assert res.output["mode"] == "cuts"
        assert res.output["count"] == 3
        assert all(Path(p).exists() for p in res.output["sheets"])
        assert res.output["cut_times"] == [1.0, 2.5, 3.5]

    def test_respects_max_cuts(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 4.0)
        res = _run(video_path=str(video), mode="cuts", cut_times=[0.5, 1.0, 2.0, 3.0],
                   out_dir=str(tmp_path / "out"), max_cuts=2)
        assert res.status == ToolStatus.SUCCESS
        assert res.output["count"] == 2

    def test_zero_and_negative_cuts_filtered(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 2.0)
        res = _run(video_path=str(video), mode="cuts", cut_times=[0.0, -1.0, 1.0],
                   out_dir=str(tmp_path / "out"))
        assert res.status == ToolStatus.SUCCESS
        assert res.output["count"] == 1

    def test_missing_cut_times_is_error(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 2.0)
        res = _run(video_path=str(video), mode="cuts", cut_times=[], out_dir=str(tmp_path / "out"))
        assert res.status == ToolStatus.ERROR
        assert "cut_times" in (res.error or "")


class TestOverviewMode:
    def test_generates_contact_sheet(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 4.0)
        res = _run(video_path=str(video), mode="overview", duration_sec=4.0,
                   out_dir=str(tmp_path / "out"), frames=6, cols=3)
        assert res.status == ToolStatus.SUCCESS
        assert res.output["mode"] == "overview"
        assert res.output["frames"] == 6
        assert res.output["count"] == 1
        assert Path(res.output["sheets"][0]).name == "overview.png"
        assert Path(res.output["sheets"][0]).exists()

    def test_requires_duration(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 2.0)
        res = _run(video_path=str(video), mode="overview", duration_sec=0,
                   out_dir=str(tmp_path / "out"))
        assert res.status == ToolStatus.ERROR
        assert "duration_sec" in (res.error or "")


class TestErrorHandling:
    def test_missing_video(self, tmp_path: Path) -> None:
        res = _run(video_path=str(tmp_path / "nope.mp4"), mode="cuts", cut_times=[1.0])
        assert res.status == ToolStatus.ERROR
        assert "不存在" in (res.error or "")

    def test_empty_video_path(self) -> None:
        res = _run(video_path="", mode="cuts", cut_times=[1.0])
        assert res.status == ToolStatus.ERROR

    def test_unwritable_out_dir(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 1.0)
        # 用一个"父路径是文件"的目录，mkdir 必然失败
        blocker = tmp_path / "blocker"
        blocker.write_text("x", encoding="utf-8")
        res = _run(video_path=str(video), mode="cuts", cut_times=[0.5],
                   out_dir=str(blocker / "sub"))
        assert res.status == ToolStatus.ERROR
        assert "目录" in (res.error or "")


class TestRegistration:
    def test_tool_registered_in_registry(self) -> None:
        from clipwright.tool import register_builtin_tools
        from clipwright.tool.registry import ToolRegistry

        register_builtin_tools()
        tools = ToolRegistry.list()
        names = [getattr(t, "name", str(t)) for t in (tools.values() if isinstance(tools, dict) else tools)]
        assert "timeline_view" in names

    def test_tool_exposes_llm_schema(self) -> None:
        schema = TimelineViewTool().to_llm_tool()
        assert schema["name"] == "timeline_view"
        props = schema["input_schema"]["properties"]
        assert "video_path" in props and "mode" in props
