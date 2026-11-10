"""M4：ClipKind 注册表、schema 接受度与贡献门面接线。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from clipwright.plugins.contribution import (
    Contribution,
    ContributionKind,
    ContributionRegistry,
)
from clipwright.schema.timeline import Clip, Track
from clipwright.services.clip_kind_registry import (
    ClipKindDefinition,
    ClipKindError,
    ClipKindRegistry,
)


@pytest.fixture(autouse=True)
def clean_registry():
    ClipKindRegistry.clear()
    ContributionRegistry.clear_all()
    yield
    ClipKindRegistry.clear()
    ContributionRegistry.clear_all()


def _clip(kind: str):
    return Clip(
        id="c1", kind=kind, asset_id="a1", track_id="t1",
        start_sec=0.0, duration_sec=1.0,
    )


class TestBuiltins:
    def test_builtin_kind_accepted(self):
        assert _clip("video").kind == "video"
        assert Track(id="t1", kind="animation", index=0).kind == "animation"

    def test_unknown_kind_rejected_before_registration(self):
        with pytest.raises(ValidationError):
            _clip("plugin_scene")


class TestPluginKinds:
    def test_register_makes_schema_accept(self):
        ClipKindRegistry.register(ClipKindDefinition(
            kind_id="plugin_scene", display_name="代码场景",
            render_contract="media_overlay.v1", owner_plugin="demo",
        ))
        assert _clip("plugin_scene").kind == "plugin_scene"
        assert Track(id="t", kind="plugin_scene", index=0).kind == "plugin_scene"

    def test_builtin_cannot_be_overridden(self):
        with pytest.raises(ClipKindError):
            ClipKindRegistry.register(ClipKindDefinition(
                kind_id="video", owner_plugin="demo",
            ))

    def test_duplicate_other_owner_rejected(self):
        ClipKindRegistry.register(ClipKindDefinition(kind_id="k1", owner_plugin="a"))
        with pytest.raises(ClipKindError):
            ClipKindRegistry.register(ClipKindDefinition(kind_id="k1", owner_plugin="b"))

    def test_unknown_contract_rejected(self):
        with pytest.raises(ClipKindError):
            ClipKindRegistry.register(ClipKindDefinition(
                kind_id="k2", owner_plugin="a", render_contract="motion.v9",
            ))

    def test_unregister_removes_schema_acceptance(self):
        ClipKindRegistry.register(ClipKindDefinition(kind_id="k3", owner_plugin="a"))
        assert ClipKindRegistry.unregister_plugin("a") == ["k3"]
        with pytest.raises(ValidationError):
            _clip("k3")


class TestContributionWiring:
    def test_clip_kind_contribution_registers_definition(self):
        ContributionRegistry.register("plug_a", [
            Contribution(
                ContributionKind.CLIP_KIND, "plug_scene", None,
                meta={
                    "display_name": "插件场景",
                    "render_contract": "media_overlay.v1",
                    "preview_contract": "artifact.v1",
                    "capabilities": ["render"],
                },
            ),
        ])
        definition = ClipKindRegistry.get("plug_scene")
        assert definition is not None
        assert definition.owner_plugin == "plug_a"
        assert definition.render_contract == "media_overlay.v1"
        assert _clip("plug_scene").kind == "plug_scene"

    def test_render_guard_flags_plugin_kind(self):
        from clipwright.services.render import is_plugin_clip_kind

        assert is_plugin_clip_kind("video") is False
        ContributionRegistry.register("plug_a", [
            Contribution(ContributionKind.CLIP_KIND, "plug_scene", None, meta={}),
        ])
        assert is_plugin_clip_kind("plug_scene") is True

    def test_unregister_plugin_removes_kind(self):
        ContributionRegistry.register("plug_a", [
            Contribution(ContributionKind.CLIP_KIND, "plug_scene", None, meta={}),
        ])
        ContributionRegistry.unregister_plugin("plug_a")
        assert ClipKindRegistry.get("plug_scene") is None
        with pytest.raises(ValidationError):
            _clip("plug_scene")
