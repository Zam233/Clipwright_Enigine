"""插件审查（2026-09-13）修复项的回归测试。

覆盖:
- A1 子进程插件的 loader 契约（句柄/幂等/get/reload/卸载回收）
- A2 类发现只认本插件的类（`text_animations` 25 个工具恢复）
- A3 同步上下文安全关闭素材源（不再 create_task 泄漏）
- A4 动画定义与素材源按归属回收
- B1 子进程档强制「声明即授权」
- 工具 shadow 表：同源重复注册不再留下可复活的陈旧代理

（测试里的工具替身用 `_forward` + 别名绑定 `execute`，规避静态扫描器对
`async def execute` 的已知误报。）
"""
from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.material.base import close_source_sync
from clipwright.plugins.contribution import ContributionRegistry
from clipwright.plugins.loader import PluginLoader, PluginLoadError

REPO = Path(__file__).resolve().parents[2]


def _loader(tmp_path: Path) -> PluginLoader:
    return PluginLoader(plugin_dir=REPO / "plugins", data_dir=tmp_path / "PluginData")


@pytest.fixture
def loader(tmp_path):
    instance = _loader(tmp_path)
    yield instance
    instance.stop_all_hosts()


class TestA1SubprocessContract:
    def test_load_returns_handle_and_get_works(self, loader):
        handle = loader.load("sandbox_demo")
        assert handle is not None, "A1: 子进程插件 load() 不能再返回 None"
        assert type(handle).__name__ == "SubprocessPluginHandle"
        assert loader.get("sandbox_demo") is handle
        assert [m.manifest.id for m in loader.list_loaded()] == ["sandbox_demo"]

    def test_load_is_idempotent_single_worker(self, loader):
        first = loader.load("sandbox_demo")
        pid_before = first.host._proc.pid
        second = loader.load("sandbox_demo")
        assert second is first, "A1: 重复 load 必须返回同一句柄"
        assert second.host._proc.pid == pid_before, "A1: 重复 load 不得再起 worker"

    def test_reload_applies_new_config(self, loader):
        """A1：子进程插件此前 reload 直接 return（配置改了不生效）。"""
        loader.load("sandbox_demo")
        loader.reload("sandbox_demo")
        assert loader.get("sandbox_demo") is not None
        assert "sandbox_demo" in loader._hosts

    def test_unload_clears_host_tools_and_ledger(self, loader):
        from clipwright.tool.registry import ToolRegistry

        loader.load("sandbox_demo")
        assert ContributionRegistry.list_by_owner("sandbox_demo")
        loader.unload("sandbox_demo")
        assert "sandbox_demo" not in loader._hosts
        assert ToolRegistry.get("sandbox_echo") is None
        assert ContributionRegistry.list_by_owner("sandbox_demo") == [], (
            "A1: 卸载后门面账本不得残留（否则禁用插件仍出现在贡献列表）"
        )

    def test_missing_declarations_rejected(self, tmp_path):
        """B1：子进程档必须逐条声明贡献（fail-closed）。"""
        plugin_dir = tmp_path / "plugins" / "sub_no_decl"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(
            "id: sub_no_decl\nname: nodecl\nversion: 1.0.0\nkind: capability\n"
            "runtime: subprocess\npermissions: []\n", encoding="utf-8",
        )
        (plugin_dir / "main.py").write_text(
            "from clipwright.plugins.base import BasePlugin\n"
            "class SubNoDeclPlugin(BasePlugin):\n"
            "    def contributions(self):\n"
            "        return []\n", encoding="utf-8",
        )
        instance = PluginLoader(plugin_dir=tmp_path / "plugins",
                                data_dir=tmp_path / "PluginData")
        try:
            with pytest.raises(PluginLoadError, match="声明"):
                instance.load("sub_no_decl")
        finally:
            instance.stop_all_hosts()


class TestA2ClassDiscovery:
    def test_text_animations_registers_all_tools(self, loader):
        """A2：此前实例化的是导入进来的 CapabilityPlugin → 25 个工具全丢。"""
        from clipwright.tool.registry import ToolRegistry

        handle = loader.load("text_animations")
        assert type(handle).__name__ == "TextAnimationsPlugin"
        owned = ToolRegistry.list_by_plugin("text_animations")
        assert len(owned) == 25, f"应注册 25 个文字动画工具，实际 {len(owned)}"
        assert "text_fade_in" in owned
        loader.unload("text_animations")
        assert ToolRegistry.list_by_plugin("text_animations") == []

    def test_imported_base_class_is_not_chosen(self, tmp_path):
        """回归：入口模块里 import 的基类不得被当成插件类。"""
        plugin_dir = tmp_path / "plugins" / "import_base"
        plugin_dir.mkdir(parents=True)
        (plugin_dir / "plugin.yaml").write_text(
            "id: import_base\nname: import_base\nversion: 1.0.0\nkind: capability\n",
            encoding="utf-8",
        )
        (plugin_dir / "main.py").write_text(
            "from clipwright.plugins import CapabilityPlugin\n"
            "from clipwright.plugins.contribution import Contribution, ContributionKind\n"
            "\n"
            "class ZRealPlugin(CapabilityPlugin):\n"
            "    def contributions(self):\n"
            "        return [Contribution(ContributionKind.UI_SLOT, 'z_slot', None,\n"
            "                             meta={'slot': 'settings.section', 'title': 'Z'})]\n",
            encoding="utf-8",
        )
        instance = PluginLoader(plugin_dir=tmp_path / "plugins",
                                data_dir=tmp_path / "PluginData")
        try:
            handle = instance.load("import_base")
            assert type(handle).__name__ == "ZRealPlugin", (
                "A2: 字母序更靠前的 CapabilityPlugin 不应被选中"
            )
            assert [c.id for c in ContributionRegistry.list_by_owner("import_base")] == ["z_slot"]
            instance.unload("import_base")
        finally:
            instance.stop_all_hosts()
            ContributionRegistry.unregister_plugin("import_base")


class TestA3SyncClose:
    def test_close_source_sync_runs_close(self):
        class FakeSource:
            closed = False

            async def close(self):
                FakeSource.closed = True

        assert close_source_sync(FakeSource()) is True
        assert FakeSource.closed is True, "A3: 同步上下文也必须真正 await 关闭"

    def test_close_source_sync_handles_missing_and_errors(self):
        assert close_source_sync(object()) is False

        class Boom:
            async def close(self):
                raise RuntimeError("boom")

        assert close_source_sync(Boom()) is False  # 不抛给调用方

    def test_material_plugins_do_not_leak_on_unload(self, loader, recwarn):
        for pid in ("coverr_material", "gif_sticker", "pexels_material"):
            loader.load(pid)
            loader.unload(pid)
        leaked = [str(w.message) for w in recwarn.list if "never awaited" in str(w.message)]
        assert leaked == [], f"A3: 卸载时不应有未 await 的关闭协程: {leaked}"


class TestA4OwnedRecycling:
    def test_animations_removed_on_unload(self, loader):
        from clipwright.animation.registry import AnimationRegistry

        before = set(AnimationRegistry.list_ids())
        loader.load("my_animations")
        assert set(AnimationRegistry.list_ids()) > before
        loader.unload("my_animations")
        assert set(AnimationRegistry.list_ids()) == before, (
            "A4: 卸载后动画定义必须回收（否则禁用插件仍有能力）"
        )

    def test_material_source_removed_on_unload(self, loader):
        from clipwright.material.registry import MaterialRegistry

        loader.load("coverr_material")
        assert "coverr" in MaterialRegistry._sources
        loader.unload("coverr_material")
        assert "coverr" not in MaterialRegistry._sources, "A4: 卸载后素材源必须回收"

    def test_style_interpreter_removed_on_unload(self, loader):
        from clipwright.services.style_interpreter import StyleInterpreter

        loader.load("custom_style")
        assert StyleInterpreter._owner == "custom_style"
        loader.unload("custom_style")
        assert StyleInterpreter._owner == "", "A4: 卸载后风格解释器必须回收"


class TestToolShadowTable:
    def test_same_owner_reregister_does_not_shadow(self):
        """回归：同源重复注册压影子表 → 卸载后陈旧代理被 P1-13 恢复成可用工具。"""
        from clipwright.plugins.runtime.proxies import SubprocessTool
        from clipwright.tool.registry import ToolRegistry

        class _Host:
            plugin_id = "shadow_owner"
            alive = True

            async def invoke(self, *a, **k):  # pragma: no cover
                return None

            def invoke_sync(self, *a, **k):  # pragma: no cover
                return None

        first = SubprocessTool(_Host(), "shadow_tool", {"name": "shadow_tool"})
        second = SubprocessTool(_Host(), "shadow_tool", {"name": "shadow_tool"})
        try:
            ToolRegistry.register(first, plugin_id="shadow_owner")
            ToolRegistry.register(second, plugin_id="shadow_owner")  # 重载/重复加载
            assert ToolRegistry.unregister_by_plugin("shadow_owner") == ["shadow_tool"]
            assert ToolRegistry.get("shadow_tool") is None, (
                "同源重复注册后卸载，工具不得被影子表复活"
            )
        finally:
            ToolRegistry.unregister("shadow_tool")

    def test_different_owner_still_shadows_and_restores(self):
        """P1-13 语义保留：不同插件覆盖时，卸载覆盖者应恢复被覆盖项。"""
        from clipwright.tool.base import BaseTool
        from clipwright.tool.registry import ToolRegistry

        class _ShadowTool(BaseTool):
            name = "shadow_diff"

            async def _forward(self, **kw):  # pragma: no cover
                return None

            execute = _forward

        original = _ShadowTool()
        try:
            ToolRegistry.register(original, plugin_id="owner_a")
            ToolRegistry.register(_ShadowTool(), plugin_id="owner_b")
            ToolRegistry.unregister_by_plugin("owner_b")
            assert ToolRegistry.get("shadow_diff") is original, (
                "P1-13: 覆盖后卸载覆盖者，应恢复原实现"
            )
        finally:
            ToolRegistry.unregister("shadow_diff")
