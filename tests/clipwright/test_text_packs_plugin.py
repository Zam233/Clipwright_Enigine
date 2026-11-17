"""CP-4 prompt/style/asset pack 插件契约。"""

from __future__ import annotations

from pathlib import Path

from clipwright.animation.text_plugin_registry import (
    TextAssetPackRegistry, TextPromptPackRegistry, TextStylePackRegistry,
)
from clipwright.plugins.loader import PluginLoader


def test_text_packs_load_and_unload(tmp_path, monkeypatch):
    for reg in (TextPromptPackRegistry, TextStylePackRegistry, TextAssetPackRegistry):
        monkeypatch.setattr(reg, "_items", {})
    loader = PluginLoader(plugin_dir=Path("plugins"), data_dir=tmp_path)
    assert loader.load("text_packs")
    assert TextPromptPackRegistry.get("spotlight_quote")["prompt"]
    assert TextStylePackRegistry.get("editorial_dark")["style"]["box"] is True
    assert TextAssetPackRegistry.get("paper_assets")["assets"][0]["kind"] == "texture"
    loader.unload("text_packs")
    assert TextPromptPackRegistry.get("spotlight_quote") is None


def test_style_pack_changes_plan_style():
    from clipwright.animation.text_engine import TextAnimationEngine
    from clipwright.animation.text_plugin_registry import TextStylePackRegistry
    from plugins.text_packs.main import TextPacksPlugin

    for c in TextPacksPlugin().contributions():
        if c.kind.value == "text_style_pack":
            TextStylePackRegistry.register(c.id, c.meta, plugin_id="__pack_test__")
    plan = TextAnimationEngine().plan(
        text="样式包", style={"font_size": 56, "fill": "#ffffff"},
        motion={"type": "fade_in"}, width=640, height=360, display_sec=2,
        style_pack_id="editorial_dark",
    )
    assert plan.style["box"] is True
