"""worker 入口类选择：按定义顺序 + 优先 *Plugin，避免字母序选中辅助类。"""
from __future__ import annotations

import pytest

from clipwright.plugins.loader import PluginLoader

SOURCE = "\n".join([
    "from clipwright.plugins.base import BasePlugin",
    "from clipwright.plugins.contribution import Contribution, ContributionKind",
    "",
    "",
    "class AAHelper(BasePlugin):",
    "    def contributions(self):",
    "        return []",
    "",
    "",
    "class ZEntryPlugin(BasePlugin):",
    "    def contributions(self):",
    "        return [Contribution(ContributionKind.TOOL, 'entry_tool', None,",
    "                             meta={'name': 'entry_tool'})]",
    "",
])


@pytest.fixture
def loader(tmp_path):
    root = tmp_path / "plugins" / "entry_probe"
    root.mkdir(parents=True)
    (root / "plugin.yaml").write_text(
        "id: entry_probe\nname: entry probe\nversion: 1.0.0\nkind: capability\n"
        "runtime: subprocess\npermissions: []\n"
        "contributions:\n  - kind: tool\n    id: entry_tool\n",
        encoding="utf-8",
    )
    (root / "main.py").write_text(SOURCE, encoding="utf-8")
    instance = PluginLoader(plugin_dir=tmp_path / "plugins",
                            data_dir=tmp_path / "PluginData")
    yield instance
    instance.stop_all_hosts()


def test_prefers_plugin_suffixed_class(loader):
    loader.load("entry_probe")
    from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

    assert ContributionRegistry.resolve(ContributionKind.TOOL, "entry_tool") is not None
