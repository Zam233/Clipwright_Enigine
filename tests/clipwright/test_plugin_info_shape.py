"""D4 测试：`plugin_info()` 两轨形态统一（都带 contributions，历史键保留）。"""
from __future__ import annotations

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.schema.plugin import PluginManifest


class _NewTrack(BasePlugin):
    manifest = PluginManifest(id="d4_new", name="新轨", version="1.0.0", kind="capability")

    def contributions(self):  # pragma: no cover - 仅为满足抽象
        return []


class _OldTrack(BasePlugin):
    manifest = PluginManifest(id="d4_old", name="旧轨", version="1.0.0", kind="capability")

    def initialize(self):  # pragma: no cover - 仅为满足抽象
        return None


def test_new_track_shape():
    ContributionRegistry.clear_all()
    try:
        ContributionRegistry.register("d4_new", [
            Contribution(ContributionKind.UI_SLOT, "panel", None,
                         meta={"slot": "settings.section", "title": "T"}),
            Contribution(ContributionKind.UI_SLOT, "panel2", None,
                         meta={"slot": "settings.section", "title": "T2"}),
        ], dispatch=False)
        info = _NewTrack().plugin_info()
        assert info["id"] == "d4_new"
        assert info["contributions"] == {"ui_slot": ["panel", "panel2"]}
        # 历史键恒存在（既有消费方无需分支）
        assert info["tools"] == [] and info["skills"] == [] and info["agents"] == []
    finally:
        ContributionRegistry.clear_all()


def test_old_track_falls_back_to_registries_with_same_shape():
    ContributionRegistry.clear_all()
    try:
        info = _OldTrack().plugin_info()
        assert info["id"] == "d4_old"
        assert "contributions" in info  # 统一形态：旧轨也有这个键
        assert isinstance(info["contributions"], dict)
        assert set(info) >= {"id", "contributions", "tools", "skills", "agents"}
    finally:
        ContributionRegistry.clear_all()
