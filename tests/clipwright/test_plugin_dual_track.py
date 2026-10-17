"""A-2/A-4 双轨桥接测试 —— 新旧契约插件都能注册、回收（M2 验收）。"""
from __future__ import annotations

import pytest

from clipwright.plugins.base import BasePlugin
from clipwright.plugins.contribution import Contribution, ContributionKind, ContributionRegistry
from clipwright.schema.plugin import ContributionDecl, PluginManifest

NEW_TOOL = "audit_bridge_new_tool"
OLD_TOOL = "audit_bridge_old_tool"


class NewTrackTool:
    name = NEW_TOOL
    description = "新轨工具"
    required_tools = []

    async def run(self):
        return None


class OldTrackTool:
    name = OLD_TOOL
    description = "旧轨工具"
    required_tools = []

    async def run(self):
        return None


def _manifest(pid: str, **kw) -> PluginManifest:
    return PluginManifest(id=pid, name=pid, kind="capability", **kw)


class NewTrackPlugin(BasePlugin):
    """只实现 contributions()（不写 initialize）。"""
    manifest = _manifest("audit_bridge_new", api_version="2", contributions=[
        ContributionDecl(kind="tool", id=NEW_TOOL),
    ])

    def contributions(self):
        return [Contribution(ContributionKind.TOOL, NEW_TOOL, NewTrackTool())]


class OldTrackPlugin(BasePlugin):
    """只实现旧契约 initialize()。"""
    manifest = _manifest("audit_bridge_old")

    def initialize(self) -> None:
        from clipwright.tool.registry import ToolRegistry
        ToolRegistry.register(OldTrackTool(), plugin_id="audit_bridge_old")


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    ContributionRegistry.unregister_plugin("audit_bridge_new")
    ContributionRegistry.unregister_plugin("audit_bridge_old")
    from clipwright.tool.registry import ToolRegistry
    ToolRegistry.unregister(NEW_TOOL)
    ToolRegistry.unregister(OLD_TOOL)


class TestDualTrack:
    def test_new_track_registers_via_facade(self):
        from clipwright.tool.registry import ToolRegistry

        plugin = NewTrackPlugin()
        plugin.initialize()  # 加载器调用点不变，桥接内部走门面

        assert ToolRegistry.get(NEW_TOOL) is not None
        assert ContributionRegistry.owner_of(ContributionKind.TOOL, NEW_TOOL) == "audit_bridge_new"
        assert ContributionRegistry.declared_keys("audit_bridge_new") == {f"tool:{NEW_TOOL}"}

        plugin.shutdown()  # 门面回收
        assert ToolRegistry.get(NEW_TOOL) is None
        assert ContributionRegistry.resolve(ContributionKind.TOOL, NEW_TOOL) is None

    def test_old_track_still_works(self):
        from clipwright.tool.registry import ToolRegistry

        plugin = OldTrackPlugin()
        plugin.initialize()

        assert ToolRegistry.get(OLD_TOOL) is not None

        plugin.shutdown()
        # 旧轨由 loader._teardown 回收；桥接不越权（未走门面 → 不回收）
        assert ToolRegistry.get(OLD_TOOL) is not None
        ToolRegistry.unregister(OLD_TOOL)

    def test_undeclared_contribution_rejected_via_bridge(self):
        """声明即授权在桥接层生效：声明外的贡献被拒且不进注册表。"""
        from clipwright.tool.registry import ToolRegistry

        class SneakyPlugin(BasePlugin):
            manifest = _manifest(
                "audit_bridge_sneaky", api_version="2",
                contributions=[ContributionDecl(kind="tool", id=NEW_TOOL)],
            )

            def contributions(self):
                return [
                    Contribution(ContributionKind.TOOL, NEW_TOOL, NewTrackTool()),
                    Contribution(ContributionKind.TOOL, "audit_bridge_sneaky_extra", NewTrackTool()),
                ]

        plugin = SneakyPlugin()
        plugin.initialize()
        assert ToolRegistry.get(NEW_TOOL) is not None
        assert ContributionRegistry.resolve(
            ContributionKind.TOOL, "audit_bridge_sneaky_extra") is None
        plugin.shutdown()
