"""text-motion-core 生命周期与隔离测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.animation.text_engine import TextAnimationEngine, TextMotionRegistry, TextMotionUnavailable
from clipwright.plugins.loader import PluginLoader


def test_plugin_load_unload_and_explicit_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(TextMotionRegistry, "_providers", {})
    loader = PluginLoader(plugin_dir=Path("plugins"), data_dir=tmp_path)
    loader.load("text_motion_core")
    assert any(p["id"] == "typewriter" for p in TextMotionRegistry.list())
    loader.unload("text_motion_core")
    with pytest.raises(TextMotionUnavailable):
        TextAnimationEngine().plan(
            text="测试", style={"font_size": 56, "fill": "#ffffff"},
            motion={"type": "typewriter", "reveal_sec": 0.5},
            width=1920, height=1080, display_sec=2,
        )


def test_plugin_does_not_import_engine_mutation_modules():
    root = Path("plugins/text_motion_core")
    forbidden = ("task_blackboard", "quality_agent", "services.render")
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in text, f"{path} imports forbidden {token}"


def test_render_fails_when_provider_is_unloaded(monkeypatch, tmp_path):
    from clipwright.schema.timeline import Clip, ClipKind, Timeline, Track
    from clipwright.services.render import RenderService

    monkeypatch.setattr(TextMotionRegistry, "_providers", {})
    clip = Clip(
        id="tc_lifecycle", kind=ClipKind.TEXT, asset_id="", track_id="t_text",
        start_sec=0, duration_sec=2, text="测试",
        metadata={"renderer": "mg_hyperframes", "mg_html": "<html/>",
                   "text_provider_id": "typewriter"},
    )
    tl = Timeline(width=1920, height=1080, fps=30, duration_sec=2, tracks=[
        Track(id="t_text", kind=ClipKind.TEXT, index=0, clips=[clip]),
    ])
    with pytest.raises(RuntimeError, match="TEXT_MOTION_UNAVAILABLE"):
        RenderService(work_dir=tmp_path)._extract_segments(tl)
