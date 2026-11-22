"""R4：validate_animation_clip / preview_animation_clip。"""

from __future__ import annotations

import asyncio

from clipwright.tool.animation_preview import PreviewAnimationClipTool, _grid
from clipwright.tool.animation_validate import ValidateAnimationClipTool


def _mg() -> dict:
    return {
        "animation_id": "test_anim",
        "duration_sec": 3.0, "width": 1920, "height": 1080,
        "elements": [{
            "type": "text", "content": "A", "x": "center", "y": "center",
            "font_size": 80, "font_color": "#ffffff",
            "keyframes": [{"time": 0, "opacity": 0},
                          {"time": 0.5, "opacity": 1}],
        }],
    }


def test_validate_animation_clip_rejects_bad_color() -> None:
    mg = _mg()
    mg["elements"][0]["glow_color"] = ""
    out = asyncio.run(ValidateAnimationClipTool().execute(mg_def=mg))
    assert out.output["ok"] is False
    assert any("glow_color" in x for x in out.output["issues"])


def test_validate_animation_clip_accepts_valid() -> None:
    out = asyncio.run(ValidateAnimationClipTool().execute(mg_def=_mg()))
    assert out.output["ok"] is True, out.output["issues"]


def test_preview_animation_clip_writes_html(tmp_path) -> None:
    out = asyncio.run(PreviewAnimationClipTool().execute(
        mg_def=_mg(), workspace_root=str(tmp_path), grid=False,
    ))
    assert out.status == "success"
    assert out.output["preview_path"]
    assert out.output["grid_path"] == ""
    assert "<html" in out.output["preview_html"].lower()


def test_preview_grid_composes_frames(tmp_path) -> None:
    from PIL import Image

    frames = []
    for i, color in enumerate(("#ff0000", "#00ff00")):
        p = tmp_path / f"f{i}.png"
        Image.new("RGB", (320, 180), color).save(p)
        frames.append(p)
    out = _grid(frames, tmp_path / "grid.png", cols=2, labels=[0.0, 1.0])
    assert out.is_file()
    with Image.open(out) as im:
        assert im.width == 1280 and im.height >= 180
