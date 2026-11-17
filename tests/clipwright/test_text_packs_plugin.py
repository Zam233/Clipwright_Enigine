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
