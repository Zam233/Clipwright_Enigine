"""文字动画真实 E2E：ffmpeg 烧 ASS → 抽帧 → 像素断言。

覆盖已经实现的效果：typewriter 逐字、slide 位移、scale 缩放、矢量圆角底板。
这些断言看的是**成片像素**，不是 .ass 文本，避免“单测通过、成片没效果”。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from clipwright.services.render import RenderService

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="需要 ffmpeg/ffprobe",
)

W, H = 640, 360


def _make_base(path: Path) -> None:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error",
        "-f", "lavfi", "-i", f"color=c=0x202030:s={W}x{H}:d=2:r=10",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True, timeout=60)


def _frame(video: Path, t: float, out: Path) -> Image.Image:
    subprocess.run([
        "ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(video),
        "-frames:v", "1", "-q:v", "2", str(out),
    ], check=True, timeout=60)
    return Image.open(out).convert("RGB")


def _white_bbox(img: Image.Image):
    px = img.load()
    xs, ys = [], []
    for y in range(img.height):
        for x in range(img.width):
            r, g, b = px[x, y]
            if r > 200 and g > 200 and b > 200:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys), len(xs)


async def _render(tmp_path: Path, overlays: list[dict]) -> Path:
    base = tmp_path / "base.mp4"
    _make_base(base)
    svc = RenderService(work_dir=tmp_path)
    out = await svc._apply_text_ass(str(base), overlays, "libx264", "ultrafast",
                                     width=W, height=H)
    assert Path(out).exists() and Path(out).stat().st_size > 1000
    return Path(out)


def _ov(text: str, start: float, dur: float, *, motion: dict | None = None,
        style: dict | None = None) -> dict:
    return {
        "text": text, "start_sec": start, "duration_sec": dur,
        "category": "text", "position": "center",
        "style": {"font_size": 48, "fill": "#ffffff", "stroke_width": 2,
                  "stroke_color": "#000000", "position": "center", **(style or {})},
        "motion": motion or {"type": "fade_in", "reveal_sec": 0.3},
    }


@pytest.mark.asyncio
async def test_typewriter_reveals_more_pixels_over_time(tmp_path):
    video = await _render(tmp_path, [
        _ov("逐字测试", 0.2, 1.6, motion={"type": "typewriter", "reveal_sec": 0.9}),
    ])
    bbox_mid = _white_bbox(_frame(video, 0.45, tmp_path / "tw_mid.jpg"))
    bbox_end = _white_bbox(_frame(video, 1.5, tmp_path / "tw_end.jpg"))
    assert bbox_mid and bbox_end
    assert bbox_end[4] > bbox_mid[4], (bbox_mid, bbox_end)
    assert (bbox_end[2] - bbox_end[0]) > (bbox_mid[2] - bbox_mid[0])


@pytest.mark.asyncio
async def test_slide_moves_up_and_scale_grows(tmp_path):
    slide_video = await _render(tmp_path, [
        _ov("滑动", 0.2, 1.6, motion={"type": "slide_in", "reveal_sec": 0.5}),
    ])
    slide_early = _white_bbox(_frame(slide_video, 0.28, tmp_path / "slide_early.jpg"))
    slide_late = _white_bbox(_frame(slide_video, 1.3, tmp_path / "slide_late.jpg"))
    assert slide_early and slide_late
    assert slide_late[1] < slide_early[1], "slide_in 应向上移动"

    scale_video = await _render(tmp_path, [
        _ov("缩放", 0.2, 1.6, motion={"type": "scale_in", "reveal_sec": 0.5}),
    ])
    scale_early = _white_bbox(_frame(scale_video, 0.28, tmp_path / "scale_early.jpg"))
    scale_late = _white_bbox(_frame(scale_video, 1.3, tmp_path / "scale_late.jpg"))
    assert scale_early and scale_late
    early_w = scale_early[2] - scale_early[0]
    late_w = scale_late[2] - scale_late[0]
    assert late_w > early_w, (early_w, late_w)


@pytest.mark.asyncio
async def test_rounded_vector_box_renders_red_pixels(tmp_path):
    video = await _render(tmp_path, [
        _ov("底板", 0.2, 1.5, style={
            "box": True, "box_color": "#dc1414", "box_radius": 16,
            "box_padding_x": 20, "box_padding_y": 10, "stroke_width": 0,
        }),
    ])
    img = _frame(video, 0.8, tmp_path / "box.jpg")
    px = img.load()
    red = sum(1 for y in range(img.height) for x in range(img.width)
              if px[x, y][0] > 150 and px[x, y][1] < 90 and px[x, y][2] < 90)
    assert red > 1000, f"红色底板像素太少: {red}"
