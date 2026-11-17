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
