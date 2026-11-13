"""C1/C2/B7：加载失败回收、Provider schema 清理、配置迁移与 override 授权。"""

from __future__ import annotations

from pathlib import Path

import pytest

from clipwright.plugins.contribution import ContributionRegistry
from clipwright.plugins.loader import PluginLoader
from clipwright.plugins.override_policy import ALLOWED_BUILTIN_OVERRIDES, override_problems
from clipwright.schema.plugin import ContributionDecl, PluginManifest


def _loader(tmp_path: Path) -> PluginLoader:
    return PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")


def test_partial_registration_failure_leaves_no_contribution(tmp_path):
    """Loader 在 initialize 抛错时必须清退本轮已注册的贡献。

    新轨插件由桥接层短路 `initialize()` 主体，因此这里直接验证 Loader 的失败
    清理路径：让 `initialize` 先注册一个工具再抛错（模拟旧轨/自定义生命周期）。
    """
    loader = _loader(tmp_path)
    plugin_dir = tmp_path / "plugins" / "broken_partial"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "id: broken_partial\nname: broken\nkind: capability\n",
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(
        "from clipwright.plugins.base import BasePlugin\n"
        "from clipwright.tool.base import BaseTool\n"
        "from clipwright.tool.registry import ToolRegistry\n"
        "\n"
        "class _T(BaseTool):\n"
        "    name = 'broken_partial_tool'\n"
        "    async def execute(self, **kwargs):\n"
        "        return {}\n"
        "\n"
        "class BrokenPartialPlugin(BasePlugin):\n"
        "    def initialize(self):\n"
        "        ToolRegistry.register(_T(), plugin_id='broken_partial')\n"
        "        raise RuntimeError('boom after registration')\n",
        encoding="utf-8",
    )
    from clipwright.plugins import PluginLoadError

    with pytest.raises(PluginLoadError):
        loader.load("broken_partial")
    from clipwright.tool.registry import ToolRegistry

    assert ToolRegistry.get("broken_partial_tool") is None


def test_provider_schema_forgotten_on_unload(tmp_path):
    from clipwright.plugins import provider_decl

    loader = _loader(tmp_path)
    plugin_dir = tmp_path / "plugins" / "prov_plug"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.yaml").write_text(
        "id: prov_plug\nname: prov\nkind: capability\napi_version: '2'\n"
        "contributions:\n"
        "  - kind: llm_provider\n    id: prov_x\n    config_schema:\n      api_key: {type: string, secret: true}\n",
        encoding="utf-8",
    )
    (plugin_dir / "main.py").write_text(
        "from clipwright.plugins.base import BasePlugin\n"
        "from clipwright.plugins.contribution import Contribution, ContributionKind\n"
        "\n"
        "class _P:\n"
        "    def build_client(self, **kwargs): return object()\n"
        "    def capabilities(self): return {}\n"
        "    def normalize_messages(self, messages): return messages\n"
        "    def uses_system_kwarg(self): return True\n"
        "    def default_model(self): return 'x'\n"
        "\n"
        "class ProvPlug(BasePlugin):\n"
        "    def contributions(self):\n"
        "        return [Contribution(ContributionKind.LLM_PROVIDER, 'prov_x', _P())]\n"
        "    def initialize(self):\n"
        "        pass\n",
        encoding="utf-8",
    )
    loader.load("prov_plug")
    assert any(s.get("id") == "prov_x" for s in provider_decl.schemas_for_kind("llm_provider"))
    loader.unload("prov_plug")
    assert not any(s.get("id") == "prov_x" for s in provider_decl.schemas_for_kind("llm_provider"))


def test_config_migration_failure_keeps_version(tmp_path, monkeypatch):
    loader = _loader(tmp_path)
    data_dir = loader.get_plugin_data_dir("mig_plug")
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "config.yaml"
    original = "fields:\n  a: {value: 1}\n"
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(loader, "_parse_config", lambda p: {"fields": {"a": {"value": 1}}})

    def _boom(config, frm, to):
        raise RuntimeError("migration failed")

    import clipwright.plugins.loader as loader_mod

    fake_module = type("M", (), {"migrate_config": staticmethod(_boom)})
    monkeypatch.setattr(
        loader_mod.importlib, "import_module", lambda name: fake_module,
    )
    result = loader._migrate_config("mig_plug", {"fields": {"a": {"value": 1}}}, target_schema=2)
    assert result.get("_schema_version", 1) == 1
    assert target.read_text(encoding="utf-8") == original


def test_override_policy_rejects_unlisted_target():
    manifest = PluginManifest(
        id="ext_plug", name="ext", kind="capability", override_builtin=True,
        contributions=[ContributionDecl(kind="tool", id="video_trim")],
    )
    problems = override_problems(manifest, plugin_dir="/tmp/ext_plug", signature_ok=False)
    assert problems and "允许表" in problems[0]


def test_override_policy_allows_bundled_listed_target():
    manifest = PluginManifest(
        id="tts_qwen", name="qwen", kind="capability", override_builtin=True,
        contributions=[ContributionDecl(kind="tts_provider", id="qwen-tts")],
    )
    bundled = ALLOWED_BUILTIN_OVERRIDES.intersection({"tts_provider:qwen-tts"})
    assert bundled
    problems = override_problems(
        manifest,
        plugin_dir=Path(__file__).resolve().parents[2] / "plugins" / "tts_qwen",
        signature_ok=False,
    )
    assert problems == []


def test_override_policy_rejects_external_unsigned():
    manifest = PluginManifest(
        id="tts_qwen", name="qwen", kind="capability", override_builtin=True,
        contributions=[ContributionDecl(kind="tts_provider", id="qwen-tts")],
    )
    problems = override_problems(manifest, plugin_dir="/tmp/ext", signature_ok=False)
    assert problems and "签名" in problems[0]
