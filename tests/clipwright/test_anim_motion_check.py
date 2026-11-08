"""② 渲染后动效自检：单测（判定/跳过/开关/接线）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clipwright.services import anim_motion_check as C


def _mov(path: Path, *, moving: bool, seconds: float = 1.2, size=(160, 90), fps: int = 10) -> str:
    """造一个会动/不会动的 MOV（黑底 + 白块平移）。"""
    from PIL import Image

    w, h = size
    frames = max(4, int(seconds * fps))
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgba",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-c:v", "qtrle", "-pix_fmt", "argb", str(path)]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None
    for i in range(frames):
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        x = 10 + (i * 6 if moving else 0)
        for y in range(20, 50):
            for px in range(x, min(w, x + 30)):
                img.putpixel((px, y), (255, 255, 255, 255))
        proc.stdin.write(img.tobytes())
    proc.stdin.close()
    proc.wait(timeout=60)
    if proc.returncode != 0:
        pytest.skip("ffmpeg qtrle 不可用")
    return str(path)


def test_moving_mov_passes(tmp_path):
    rep = C.check_mov_motion(_mov(tmp_path / "moving.mov", moving=True),
                             min_frames=3, min_ratio=0.2)
    assert rep["ok"] is True, rep
    assert rep["distinct_frames"] >= 3


def test_static_mov_fails(tmp_path):
    rep = C.check_mov_motion(_mov(tmp_path / "static.mov", moving=False))
    assert rep["ok"] is False
    assert rep["distinct_ratio"] < 0.25


def test_missing_file_is_skipped_not_failed(tmp_path):
    rep = C.check_mov_motion(str(tmp_path / "nope.mov"))
    assert rep["ok"] is True and rep["skipped"] == "missing"


def test_check_overlays_respects_switch(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "settings_of", lambda: {"enabled": False, "fps": 4,
                                                   "min_frames": 8, "min_ratio": 0.25})
    rep = C.check_overlays([(_mov(tmp_path / "m.mov", moving=True), 0.0, 1.2)])
    assert rep["disabled"] is True and rep["checked"] == 0


def test_check_overlays_collects_failures(monkeypatch, tmp_path):
    monkeypatch.setattr(C, "settings_of", lambda: {"enabled": True, "fps": 10,
                                                   "min_frames": 8, "min_ratio": 0.5})
    moving = _mov(tmp_path / "mv.mov", moving=True)
    static = _mov(tmp_path / "st.mov", moving=False)
    rep = C.check_overlays([(moving, 0.0, 1.2), (static, 6.0, 1.2)])
    assert rep["checked"] == 2
    assert rep["failed"] == 1
    assert rep["failed_items"][0]["start_sec"] == 6.0


@pytest.mark.asyncio
async def test_render_records_warning_for_static_animation(monkeypatch, tmp_path):
    """接线层：自检失败写进 RenderResult.warnings，不抛异常。"""
    from clipwright.services.render import RenderService

    svc = RenderService(work_dir=str(tmp_path / "w"))
    monkeypatch.setattr(C, "check_overlays", lambda pairs: {
        "checked": 1, "failed": 1,
        "results": [], "failed_items": [
            {"start_sec": 12.5, "distinct_frames": 1, "sampled_frames": 24,
             "distinct_ratio": 0.042},
        ],
    })
    rep = await svc._check_animation_motion([("a.mov", 12.5, 6.0)])
    assert rep["failed"] == 1
    assert any("动画动效不足" in w for w in svc.warnings)


@pytest.mark.asyncio
async def test_render_motion_check_survives_exception(monkeypatch, tmp_path):
    from clipwright.services.render import RenderService

    svc = RenderService(work_dir=str(tmp_path / "w2"))

    def boom(pairs):
        raise RuntimeError("ffmpeg missing")

    monkeypatch.setattr(C, "check_overlays", boom)
    assert await svc._check_animation_motion([("a.mov", 0.0, 6.0)]) is None
    assert svc.warnings == [], "自检失败不应污染 warnings"
