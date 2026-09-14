"""回归：带变换/关键帧/蒙版的片段必须真的走 filter_complex 合成路径。

背景（2026-09-13 由遮罩升级牵出）：`filter_complex` 图里的黑底写作
`color=c=black:s={W}:{H}:...`——**冒号是选项分隔符**，ffmpeg 报
`No option name near ':H'`，整条 trim 命令失败 → 静默降级为纯色段。
于是**任何需要 overlay 合成的片段（静态变换 / 关键帧变换 / 蒙版 / opacity 关键帧）
都渲染成了纯色块**，而日志里只有一行不易察觉的 trim 失败。

本文件锁定三件事：
1. 带变换的片段产出**不是**纯色（角落仍有画面内容，且与无变换版本不同）；
2. `_final_ffmpeg_log` 里没有 trim 失败；
3. 关键帧 opacity 同样走 overlay 且画面随透明度变化。
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要本机 ffmpeg/ffprobe",
)


def _clip(path: Path, seconds: float = 2.0) -> None:
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", f"testsrc2=size=320x240:rate=30:duration={seconds}",
         "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-300:]


def _render(tmp_path: Path, clip_extra: dict):
    from clipwright.schema.timeline import Timeline
    from clipwright.services.render import RenderService

    src = tmp_path / "src.mp4"
    _clip(src)
    clip = {
        "id": "c1", "kind": "video", "asset_id": str(src), "track_id": "v0",
        "start_sec": 0.0, "duration_sec": 2.0, "source_offset_sec": 0,
        "speed": 1, "volume": 0, "opacity": 1, "keyframes": [], "metadata": {},
        **clip_extra,
    }
    timeline = Timeline(**{
        "id": "transform_case", "width": 320, "height": 240, "fps": 30, "duration_sec": 2,
        "tracks": [{"id": "v0", "name": "v", "kind": "video", "index": 0, "clips": [clip]}],
    })
    svc = RenderService(work_dir=tmp_path / "engine")
    out = tmp_path / "out.mp4"
    res = asyncio.run(svc.render(timeline, out, width=320, height=240, fps=30))
    return res, str(out)


def _luma(video: str, at: float, points: list[tuple[float, float]], tmp: Path) -> list[float]:
    png = tmp / f"l_{at:.2f}.png"
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", video,
         "-frames:v", "1", str(png)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-200:]
    from PIL import Image

    values: list[float] = []
    with Image.open(png) as img:
        rgb = img.convert("RGB")
        for fx, fy in points:
            x = min(rgb.width - 1, max(0, int(fx * rgb.width)))
            y = min(rgb.height - 1, max(0, int(fy * rgb.height)))
            r0, g0, b0 = rgb.getpixel((x, y))
            values.append(0.299 * r0 + 0.587 * g0 + 0.114 * b0)
    return values


def _mean_luma(video: str, at: float, tmp: Path) -> float:
    """整帧平均亮度（0-255）。图案逐帧变化，单点采样不可比，故用均值。"""
    png = tmp / f"m_{at:.2f}.png"
    r = subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{at:.2f}", "-i", video,
         "-frames:v", "1", str(png)],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr[-200:]
    from PIL import Image, ImageStat

    with Image.open(png) as img:
        return float(ImageStat.Stat(img.convert("L")).mean[0])


class TestTransformComposes:
    def test_static_transform_not_a_solid_color(self, tmp_path: Path) -> None:
        res, out = _render(tmp_path, {"metadata": {"transform": {"scale": 1.2, "x": 0.1, "y": 0.0}}})
        assert res.success, res.error
        assert "trim(" not in res.ffmpeg_log, res.ffmpeg_log[-400:]
        # 缩放 1.2 后四周会露出黑边：中心有画面、角落是黑底
        center, corner = _luma(out, 1.0, [(0.5, 0.5), (0.01, 0.01)], tmp_path)
        assert center > 10, f"中心应为画面内容，实际亮度 {center:.0f}"
        assert corner < center, f"缩放后角落应是露出黑底（{corner:.0f} vs {center:.0f}）"

    def test_transform_differs_from_untransformed(self, tmp_path: Path) -> None:
        plain_dir = tmp_path / "plain"
        plain_dir.mkdir()
        _res_plain, plain = _render(plain_dir, {})
        moved_dir = tmp_path / "moved"
        moved_dir.mkdir()
        _res_moved, moved = _render(moved_dir, {"metadata": {"transform": {"scale": 1.0, "x": 0.3, "y": 0.0}}})

        px_plain = _luma(plain, 1.0, [(0.15, 0.5)], tmp_path)
        px_moved = _luma(moved, 1.0, [(0.15, 0.5)], tmp_path)
        assert abs(px_plain[0] - px_moved[0]) > 5, (
            f"平移 0.3×画幅后该点亮度应明显变化：{px_plain[0]:.0f} → {px_moved[0]:.0f}")

    def test_opacity_keyframes_render_via_overlay(self, tmp_path: Path) -> None:
        res, out = _render(tmp_path, {
            "keyframes": [
                {"time": 0.0, "properties": {"opacity": 1.0}},
                {"time": 0.5, "properties": {"opacity": 0.0}},
                {"time": 1.0, "properties": {"opacity": 1.0}},
            ],
        })
        assert res.success, res.error
        assert "trim(" not in res.ffmpeg_log, res.ffmpeg_log[-400:]
        # 用**整帧均值**判断亮度衰减：testsrc2 的图案逐帧变化，单点采样不可比
        bright_a = _mean_luma(out, 0.1, tmp_path)
        dark = _mean_luma(out, 0.5, tmp_path)     # opacity 关键帧 0.0 → 全黑
        bright_b = _mean_luma(out, 1.5, tmp_path)  # 回到 1.0
        assert dark < 5, f"opacity=0 时整帧应近全黑，实际均值 {dark:.1f}"
        assert bright_a > 30 and bright_b > 30, (bright_a, bright_b)
        # 与无关键帧版本对比：中点必须明显更暗
        plain_dir = tmp_path / "plain_op"
        plain_dir.mkdir()
        _res_plain, plain = _render(plain_dir, {})
        assert _mean_luma(plain, 0.5, tmp_path) > dark + 30
