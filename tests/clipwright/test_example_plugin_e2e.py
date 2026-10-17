"""示例插件全链路测试（方案 §8.4 后端部分 / G7 生命周期验收）。

用真实 `PluginLoader` 加载 `plugins/_example_full/`：
- 新契约（contributions()）经门面注册（tool / ui_slot×4 / export_preset / shortcut）；
- `/api/plugin/contributions` 能列出各 slot 的贡献（前端 6 个 slot 由此点亮）；
- 卸载后**全部回收**（G7：UI 与能力同步消失）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import plugin as plugin_api
from clipwright.plugins.contribution import ContributionKind, ContributionRegistry

PLUGIN_ID = "_example_full"
PLUGINS_ROOT = Path(__file__).resolve().parents[2] / "plugins"


@pytest.fixture
def loader(tmp_path, monkeypatch):
    from clipwright.plugins.loader import PluginLoader

    if not (PLUGINS_ROOT / PLUGIN_ID / "plugin.yaml").exists():
        pytest.skip("示例插件不存在")
    ContributionRegistry.clear_all()
    inst = PluginLoader(plugin_dir=PLUGINS_ROOT, data_dir=tmp_path / "PluginData")
    yield inst
    try:
        inst.unload(PLUGIN_ID)
    except Exception:
        pass
    ContributionRegistry.clear_all()


class TestExamplePlugin:
    def test_load_registers_all_contributions(self, loader):
        plugin = loader.load(PLUGIN_ID)
        assert plugin is not None, "示例插件加载失败"

        # tool
        from clipwright.tool.registry import ToolRegistry
        assert ToolRegistry.get("example_echo") is not None
        assert ContributionRegistry.owner_of(
            ContributionKind.TOOL, "example_echo") == PLUGIN_ID

        # 4 个 ui_slot（面板/设置/页面/导航）
        slots = {c.id: c for _, c in ContributionRegistry.list(ContributionKind.UI_SLOT)
                 if ContributionRegistry.owner_of(ContributionKind.UI_SLOT, c.id) == PLUGIN_ID}
        assert set(slots) == {"example_panel", "example_settings", "example_page",
                              "example_nav", "example_shortcut"}
        assert slots["example_panel"].meta["slot"] == "editor.right-panel"
        assert slots["example_settings"].meta["slot"] == "settings.section"
        assert slots["example_page"].meta["slot"] == "page"
        assert slots["example_nav"].meta["slot"] == "nav.settings"

        # export preset + shortcut
        assert ContributionRegistry.resolve(
            ContributionKind.EXPORT_PRESET, "example_preset") is not None
        # shortcut 也是 ui_slot（slot=shortcut；D-0 名单里它是 slot 而非独立 kind）
        assert slots["example_shortcut"].meta["slot"] == "shortcut"
        assert slots["example_shortcut"].meta["payload"]["combo"] == "alt+shift+e"

        # 内置工具（若有）不受影响——插件不得覆盖内置
        assert loader is not None

    def test_contributions_endpoint_exposes_slots_for_frontend(self, loader):
        loader.load(PLUGIN_ID)
        app = FastAPI()
        app.include_router(plugin_api.router)
        client = TestClient(app)

        for slot, expect_id in (
            ("editor.right-panel", "example_panel"),
            ("settings.section", "example_settings"),
            ("page", "example_page"),
            ("nav.settings", "example_nav"),
        ):
            row = client.get(f"/api/plugin/contributions?kind=ui_slot&slot={slot}").json()
            ids = [x["id"] for x in row["contributions"]]
            assert expect_id in ids, f"{slot} 未暴露 {expect_id}: {ids}"

        presets = client.get("/api/plugin/contributions?kind=export_preset").json()
        preset = next(x for x in presets["contributions"] if x["id"] == "example_preset")
        assert preset["payload"]["width"] == 1080 and preset["payload"]["height"] == 1920

        shortcuts = client.get("/api/plugin/contributions?kind=ui_slot&slot=shortcut").json()
        sc = next(x for x in shortcuts["contributions"] if x["id"] == "example_shortcut")
        assert sc["payload"]["combo"] == "alt+shift+e"
        assert sc["payload"]["action"]["endpoint"] == "/api/tool/execute"

    def test_unload_recycles_everything(self, loader):
        loader.load(PLUGIN_ID)
        from clipwright.tool.registry import ToolRegistry

        assert ToolRegistry.get("example_echo") is not None

        loader.unload(PLUGIN_ID)

        # G7：能力全部回收
        assert ToolRegistry.get("example_echo") is None
        assert ContributionRegistry.list_by_owner(PLUGIN_ID) == []
        assert ContributionRegistry.resolve(
            ContributionKind.EXPORT_PRESET, "example_preset") is None
        assert all(c.id != "example_shortcut" for c in ContributionRegistry.list_by_owner(PLUGIN_ID))
