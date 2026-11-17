"""TextAnimationEngine + text-motion-core 插件契约。"""

from __future__ import annotations

import pytest

from clipwright.animation.text_engine import (
    TextAnimationEngine, TextMotionRegistry, TextMotionUnavailable,
)
from plugins.text_motion_core.main import TextMotionCorePlugin


@pytest.fixture(autouse=True)
def _isolate_registry():
    # conftest 的 session fixture 会注册 __test__；本模块用独立 owner，避免污染
    TextMotionRegistry.unregister_plugin("__test_engine__")
    for c in TextMotionCorePlugin().contributions():
        TextMotionRegistry.register(c.object, plugin_id="__test_engine__")
    yield
    TextMotionRegistry.unregister_plugin("__test_engine__")


def test_core_plugin_covers_all_base_motions():
    ids = {x["id"] for x in TextMotionRegistry.list()}
    assert {"fade_in", "fade_out", "slide_in", "scale_in", "typewriter",
            "word_by_word", "highlight"} <= ids


def test_unregister_removes_capability_and_fails_explicitly(monkeypatch):
    monkeypatch.setattr(TextMotionRegistry, "_providers", {})
    with pytest.raises(TextMotionUnavailable):
        TextAnimationEngine().plan(
            text="测试", style={"font_size": 56, "fill": "#ffffff"},
            motion={"type": "typewriter", "reveal_sec": 0.5},
            width=1920, height=1080, display_sec=2,
        )


def test_engine_plan_and_html_for_typewriter():
    plan = TextAnimationEngine().plan(
        text="逐字测试", style={"font_size": 64, "fill": "#ffffff"},
        motion={"type": "typewriter", "reveal_sec": 0.6},
        width=1280, height=720, display_sec=2,
    )
    assert plan.provider_id == "typewriter"
    assert plan.fidelity == "exact"
    texts = [e for e in plan.mg_def["elements"] if e.get("type") == "text"]
    assert len(texts) == 4
    assert "<!DOCTYPE html>" in TextAnimationEngine().render_html(plan)
