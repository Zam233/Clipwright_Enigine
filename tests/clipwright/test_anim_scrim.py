"""② 渲染期底板兜底：单测（scrim_frame / bake / should_scrim / render 接线）。"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from clipwright.services import anim_scrim

pytest.importorskip("PIL")


def _rgba_with_text(size=(160, 80), box=(40, 30, 120, 50)):
    from PIL import Image

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(box[1], box[3]):
        for x in range(box[0], box[2]):
            img.putpixel((x, y), (255, 255, 255, 255))
    return img


def test_scrim_frame_is_a_card_under_the_text():
    img = _rgba_with_text()
    scrim = anim_scrim.scrim_frame(img, alpha=0.75, grow=8, blur=0)
    assert scrim.mode == "RGBA"
    # 底板是纯黑
    assert scrim.getpixel((80, 40))[:3] == (0, 0, 0)
    # 文字区域 alpha ≈ 0.75*255
    center = scrim.getpixel((80, 40))[3]
    assert 180 <= center <= 200, center
    # 底板比文字大一圈（bbox 外扩），但画面角落仍然透明
    assert scrim.getpixel((30, 40))[3] > 0, "文字左右应被底板覆盖"
    assert scrim.getpixel((2, 2))[3] == 0, "远离文字处不应有底板"


def test_scrim_frame_returns_transparent_when_no_alpha():
    from PIL import Image

    empty = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    out = anim_scrim.scrim_frame(empty, alpha=0.8, grow=6, blur=2)
    assert out.getchannel("A").getextrema() == (0, 0)


def _tiny_mov(path: str, seconds: float = 0.4, size=(160, 80), fps: int = 10) -> str:
    # 不要用 lavfi color/drawbox：那条路的 alpha 不可靠（实测产出全不透明），
    # 会让测试素材本身失去意义。这里用 PIL 造 RGBA 帧再走 rawvideo 管道。
    from PIL import Image

    w, h = size
    frames = max(2, int(seconds * fps))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-c:v", "qtrle", "-pix_fmt", "argb", path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    for _ in range(frames):
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        for y in range(30, 50):
            for x in range(40, 120):
                img.putpixel((x, y), (255, 255, 255, 255))
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait(timeout=60)
    if proc.returncode != 0 or not Path(path).exists():
        pytest.skip("ffmpeg qtrle 编码不可用")
    return path


def test_bake_scrim_into_mov_produces_alpha_coverage(tmp_path):
    src = _tiny_mov(str(tmp_path / "in.mov"))
    out = anim_scrim.bake_scrim_into_mov(str(src), str(tmp_path / "out.mov"),
                                        alpha=0.75, grow=8, blur=2)
    assert out and Path(out).exists()

    png = str(tmp_path / "edge.png")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", "0.1", "-i", out,
                    "-frames:v", "1", "-pix_fmt", "rgba", png], capture_output=True)
    from PIL import Image

    img = Image.open(png)
    hist = img.getchannel("A").histogram()
    semi = sum(hist[20:200])
    assert semi > 1000, "底板应产生大量半透明像素（文字周围）"


def test_should_scrim_uses_brightness_threshold(monkeypatch):
    monkeypatch.setattr(anim_scrim, "sample_brightness", lambda *a, **k: 200.0)
    assert anim_scrim.should_scrim("x.mp4", 1.0, 140) is True
    monkeypatch.setattr(anim_scrim, "sample_brightness", lambda *a, **k: 60.0)
    assert anim_scrim.should_scrim("x.mp4", 1.0, 140) is False
    monkeypatch.setattr(anim_scrim, "sample_brightness", lambda *a, **k: -1.0)
    assert anim_scrim.should_scrim("x.mp4", 1.0, 140) is False, "采样失败不应强行加底板"


@pytest.mark.asyncio
async def test_render_bakes_scrim_only_for_bright_windows(monkeypatch, tmp_path):
    """接线层：浅背景才烘焙，烘焙失败保留原 MOV。"""
    from clipwright.services.render import RenderService

    svc = RenderService(work_dir=str(tmp_path / "w"))
    calls: list[tuple] = []

    def fake_should(video, at_sec, threshold=None):
        return at_sec < 2.0  # 只有第一个窗口"背景偏亮"

    def fake_bake(src, out, alpha=0.0, grow=0, blur=0.0):
        calls.append((src, out, alpha, grow, blur))
        Path(out).write_bytes(b"stub")
        return out

    monkeypatch.setattr(anim_scrim, "should_scrim", fake_should)
    monkeypatch.setattr(anim_scrim, "bake_scrim_into_mov", fake_bake)

    pairs = [("a.mov", 0.0, 5.0), ("b.mov", 10.0, 5.0)]
    got = await svc._bake_scrims("video.mp4", pairs)
    assert len(calls) == 1 and calls[0][0] == "a.mov"
    assert got[0][0].endswith("_scrim.mov")
    assert got[1][0] == "b.mov", "深背景窗口不应被改"


@pytest.mark.asyncio
async def test_render_skips_scrim_when_disabled(monkeypatch):
    from clipwright.services.render import RenderService

    svc = RenderService(work_dir="/tmp/cw-scrim-off")
    monkeypatch.setattr(anim_scrim, "settings_of",
                        lambda: {"enabled": False, "alpha": 0.75, "grow": 8,
                                 "blur": 6.0, "light_threshold": 140.0})
    monkeypatch.setattr(anim_scrim, "should_scrim", lambda *a, **k: True)
    bake = AsyncMock()
    monkeypatch.setattr(anim_scrim, "bake_scrim_into_mov", bake)

    pairs = [("a.mov", 0.0, 5.0)]
    got = await svc._bake_scrims("video.mp4", pairs)
    assert got == pairs
    bake.assert_not_called()
