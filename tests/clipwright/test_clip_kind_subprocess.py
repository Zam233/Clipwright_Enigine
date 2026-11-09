"""M4：subprocess 插件声明 clip_kind → 注册表可见 → 卸载回收。"""
from __future__ import annotations

import pytest

from clipwright.plugins.loader import PluginLoader
from clipwright.services.clip_kind_registry import ClipKindRegistry

PLUGIN_SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "",
    "",
    "class KindPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(",
    "            ContributionKind.CLIP_KIND, 'probe_scene', None,",
    "            meta={",
    "                'display_name': '探针场景',",
    "                'track_label': '探针',",
    "                'render_contract': 'media_overlay.v1',",
    "                'preview_contract': 'artifact.v1',",
    "                'capabilities': ['render'],",
    "            },",
    "        )]",
    "",
])


@pytest.fixture
def loader(tmp_path):
    root = tmp_path / "plugins" / "ck_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: ck_probe\nname: ck probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n  - kind: clip_kind\n    id: probe_scene\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    instance = PluginLoader(plugin_dir=tmp_path / "plugins",
                            data_dir=tmp_path / "PluginData")
    yield instance
    instance.stop_all_hosts()
    ClipKindRegistry.clear()


def test_subprocess_clip_kind_registered_and_reclaimed(loader):
    loader.load("ck_probe")
    definition = ClipKindRegistry.get("probe_scene")
    assert definition is not None
    assert definition.owner_plugin == "ck_probe"
    assert definition.render_contract == "media_overlay.v1"

    loader.unload("ck_probe")
    assert ClipKindRegistry.get("probe_scene") is None
