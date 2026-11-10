"""M4：subprocess 插件的 Category 声明 → 宿主适配 → 静态提示词 + RPC 方法。"""
from __future__ import annotations

import pytest

from clipwright.category.registry import CategoryRegistry
from clipwright.plugins.loader import PluginLoader

PLUGIN_SOURCE = "\n".join([
    "from clipwright.category.base import BaseCategoryPlugin",
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "",
    "",
    "class ProbeCategory(BaseCategoryPlugin):",
    "    plugin_id = 'cat_probe'",
    "    display_name = '探针类型'",
    "    description = 'test category'",
    "",
    "    def translate_persona(self, params):",
    "        return {'cut_density': 'high', 'echo': params}",
    "",
    "    def get_shot_params(self, translated):",
    "        return {'min_shot_ms': 800, 'translated': translated}",
    "",
    "",
    "class CatPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(",
    "            ContributionKind.CATEGORY, 'cat_probe', ProbeCategory(),",
    "            meta={",
    "                'display_name': '探针类型',",
    "                'prompts': {'structure': '钩子→论点→证据', 'animation': '克制运镜'",
    "                            },",
    "                'declares': {'search_hint': 'probe'},",
    "            },",
    "        )]",
    "",
])


@pytest.fixture
def loader(tmp_path):
    root = tmp_path / "plugins" / "cat_probe_pkg"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: cat_probe_pkg\nname: cat probe\nversion: 1.0.0\nkind: category\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n  - kind: category\n    id: cat_probe\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(PLUGIN_SOURCE, encoding="utf-8")
    instance = PluginLoader(plugin_dir=tmp_path / "plugins",
                            data_dir=tmp_path / "PluginData")
    yield instance
    instance.stop_all_hosts()
    CategoryRegistry.clear()


def test_category_adapter_static_and_rpc(loader):
    loader.load("cat_probe_pkg")
    plugin = CategoryRegistry.get("cat_probe")
    assert plugin is not None
    assert plugin.display_name == "探针类型"

    bundle = plugin.get_prompt_bundle()
    assert bundle.slots["structure"].text == "钩子→论点→证据"
    assert bundle.slots["animation"].text == "克制运镜"
    assert plugin.get_declares().search_hint == "probe"

    # 可执行部分经 RPC 回到 worker 里的插件实例
    assert plugin.translate_persona({"tone": "calm"}) == {
        "cut_density": "high", "echo": {"tone": "calm"},
    }
    assert plugin.get_shot_params({"cut_density": "high"})["min_shot_ms"] == 800

    loader.unload("cat_probe_pkg")
    assert CategoryRegistry.get("cat_probe") is None
