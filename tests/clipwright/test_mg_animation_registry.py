"""B8-T8.7 / 审计 §3.13：MG 动画 JSON 必须随 `AnimationDef` 进注册表，engine 不认插件目录。"""

from __future__ import annotations

import pytest

from clipwright.animation.mg_renderer import MGRenderer
from clipwright.plugins.contribution import ContributionRegistry
from plugins.mg_animations.main import MGAnimationPlugin

MG_ID = "mg_countdown"


@pytest.fixture(autouse=True)
def _mg_registered():
    owner = "mg_animations_test"
    items = MGAnimationPlugin().contributions()
    ContributionRegistry.declared_scope(owner, [c.key for c in items])
    report = ContributionRegistry.register(owner, items)
    assert report.ok, report.rejected
    try:
        yield
    finally:
        ContributionRegistry.unregister_plugin(owner)


class TestRegistryBackedLoad:
    def test_animation_def_carries_json(self) -> None:
        from clipwright.animation.registry import AnimationRegistry

        defn = AnimationRegistry.get(MG_ID)
        assert defn is not None
        meta = (defn.properties_meta or {}).get("mg_json")
        assert meta is not None and meta.default, "AnimationDef 未携带 MG JSON 全文"

    def test_load_animation_reads_registry(self) -> None:
        data = MGRenderer.load_animation(MG_ID)
        assert data is not None
        assert data.get("animation_id") == MG_ID
        assert data.get("elements"), "MG JSON 应包含 elements"

    def test_list_animations_includes_plugin_defs(self) -> None:
        ids = {a["id"] for a in MGRenderer.list_animations()}
        assert MG_ID in ids

    def test_disabled_plugin_loses_animation(self) -> None:
        ContributionRegistry.unregister_plugin("mg_animations_test")
        assert MGRenderer.load_animation(MG_ID) is None
        assert MG_ID not in {a["id"] for a in MGRenderer.list_animations()}
