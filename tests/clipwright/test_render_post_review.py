"""切点复核（V4/V7）测试：切点推导 + 复核图合成 + 渲染后 hook 门控。

复核图是"渲染后自评闭环"的取证物：每个切点前后 ±0.35s 三帧横向拼图，
供人或视觉模型判断跳变/爆闪/字幕遮挡。本文件用真实 ffmpeg 验证产物确实生成。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

from clipwright.services.render import RenderService
from clipwright.services.timeline_view import (
    build_cut_sheet,
    build_review_sheets,
    cut_times_from_segments,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="需要本机 ffmpeg"
)


def _clip(path: Path, seconds: float = 2.0) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]


class TestCutTimes:
    def test_cut_points_from_segment_starts(self) -> None:
        segs = [
            {"start_sec": 0.0}, {"start_sec": 5.0}, {"start_sec": 10.5},
        ]
        assert cut_times_from_segments(segs) == [5.0, 10.5]

    def test_head_excluded_by_default(self) -> None:
        assert cut_times_from_segments([{"start_sec": 0.0}]) == []
        assert cut_times_from_segments([{"start_sec": 0.0}], skip_head=False) == [0.0]

    def test_sorted_unique_and_garbage_tolerant(self) -> None:
        segs = [{"start_sec": 6.0}, {"start_sec": "bad"}, {"start_sec": 3.0}, {"start_sec": 6.0}, {}]
        assert cut_times_from_segments(segs) == [3.0, 6.0]

    def test_empty(self) -> None:
        assert cut_times_from_segments([]) == []
        assert cut_times_from_segments(None) == []  # type: ignore[arg-type]


class TestCutSheet:
    def test_sheet_generated_with_three_tiles(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 3.0)
        out = tmp_path / "cut.png"
        path = build_cut_sheet(str(video), 1.5, out, index=2)
        assert path and Path(path).exists()
        from PIL import Image

        with Image.open(path) as img:
            # 三帧横向：宽度应明显大于单帧
            assert img.width > 426 * 2, f"复核图应有 3 帧，实际宽 {img.width}"
            assert img.height > 100

    def test_missing_video_returns_none(self, tmp_path: Path) -> None:
        assert build_cut_sheet(str(tmp_path / "nope.mp4"), 1.0, tmp_path / "x.png") is None

    def test_batch_respects_max_cuts(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 4.0)
        sheets = build_review_sheets(str(video), [1.0, 2.0, 3.0], tmp_path / "sheets", max_cuts=2)
        assert len(sheets) == 2
        assert all(Path(s).exists() for s in sheets)

    def test_batch_noop_without_cuts(self, tmp_path: Path) -> None:
        video = tmp_path / "v.mp4"
        _clip(video, 1.0)
        assert build_review_sheets(str(video), [], tmp_path / "sheets") == []


class TestPostRenderHook:
    def test_disabled_by_default(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv("CLIPWRIGHT_POST_RENDER_REVIEW", raising=False)
        svc = RenderService(work_dir=tmp_path / "engine")
        video = tmp_path / "v.mp4"
        _clip(video, 2.0)
        warnings: list[str] = []
        asyncio.run(svc._post_render_review(str(video), [{"start_sec": 0.0}, {"start_sec": 1.0}], warnings))
        assert warnings == []

    def test_enabled_generates_sheets_and_warning(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("CLIPWRIGHT_POST_RENDER_REVIEW", "1")
        monkeypatch.setenv("CLIPWRIGHT_REVIEW_DIR", str(tmp_path / "review"))
        # 视觉模型未配置 → 只出图 + 记 warning，不抛异常
        monkeypatch.setattr(
            "clipwright.animation.mg.visual_eval.MGVisualEvaluator.evaluate",
            _no_vision,
        )
        svc = RenderService(work_dir=tmp_path / "engine")
        video = tmp_path / "v.mp4"
        _clip(video, 3.0)
        warnings: list[str] = []
        asyncio.run(svc._post_render_review(
            str(video), [{"start_sec": 0.0}, {"start_sec": 1.5}], warnings))
        assert any("切点复核图" in w for w in warnings), warnings
        sheets = list((tmp_path / "review").rglob("cut_*.png"))
        assert sheets, "应生成复核图"

    def test_single_segment_timeline_skips(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("CLIPWRIGHT_POST_RENDER_REVIEW", "1")
        svc = RenderService(work_dir=tmp_path / "engine")
        video = tmp_path / "v.mp4"
        _clip(video, 1.0)
        warnings: list[str] = []
        asyncio.run(svc._post_render_review(str(video), [{"start_sec": 0.0}], warnings))
        assert warnings == []

    def test_failure_is_swallowed(self, tmp_path: Path, monkeypatch) -> None:
        """复核失败绝不能影响成片：坏视频路径 → 静默返回。"""
        monkeypatch.setenv("CLIPWRIGHT_POST_RENDER_REVIEW", "1")
        svc = RenderService(work_dir=tmp_path / "engine")
        warnings: list[str] = []
        asyncio.run(svc._post_render_review(
            str(tmp_path / "missing.mp4"), [{"start_sec": 0.0}, {"start_sec": 1.0}], warnings))
        assert warnings == []


async def _no_vision(*_a, **_kw):
    return None
