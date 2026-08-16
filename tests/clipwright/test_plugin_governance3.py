"""P10: 插件治理 · 第六批 — M1 签名/权限 / M2 依赖解析 / M8 启停持久化 / M10 manifest 增强 / M15 配置迁移 / P1-6 diagram_style 约定兼容 测试。"""

from __future__ import annotations

import pytest

from clipwright.animation.diagram_svg import DiagramStyle
from clipwright.config import settings
from clipwright.plugins.base import BasePlugin
from clipwright.plugins.loader import (
    PluginLoadError,
    PluginLoader,
    check_permissions,
    sign_manifest,
    verify_manifest_signature,
)
from clipwright.schema.plugin import PluginManifest, PluginKind


def _manifest(**over) -> PluginManifest:
    base = dict(
        id="perf_plugin",
        name="权限测试插件",
        version="1.0.0",
        kind=PluginKind.CAPABILITY,
    )
    base.update(over)
    return PluginManifest(**base)


# ── M1: 签名 ──


class TestManifestSignature:
    def test_sign_and_verify_roundtrip(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "plugin_signature_key", "test-key")
        m = _manifest(author="alice")
        sig = sign_manifest(m, key="test-key")
        assert sig
        m2 = _manifest(author="alice", signature=sig)
        assert verify_manifest_signature(m2)

    def test_tampered_manifest_fails_verification(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "plugin_signature_key", "test-key")
        m = _manifest(author="alice")
        sig = sign_manifest(m, key="test-key")
        m2 = _manifest(author="mallory", signature=sig)
        assert not verify_manifest_signature(m2)

    def test_signature_deterministic(self) -> None:
        a = sign_manifest(_manifest(name="插件A"), key="k")
        b = sign_manifest(_manifest(name="插件A"), key="k")
        assert a == b

    def test_unsigned_manifest_passes_when_no_key_required(self) -> None:
        m = _manifest()
        assert verify_manifest_signature(m) is True


# ── M1: 权限声明 ──


class TestPermissionCheck:
    def test_all_declared_permissions_allowed(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "plugin_allowed_permissions", ["network", "fs_read", "fs_write", "shell", "http", "noop"])
        assert check_permissions(_manifest(permissions=["network", "fs_read"])) == []

    def test_unknown_permission_rejected(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, "plugin_allowed_permissions", ["network", "fs_read"])
        assert check_permissions(_manifest(permissions=["network", "exec_arbitrary"])) == ["exec_arbitrary"]

    def test_empty_permissions_ok(self) -> None:
        assert check_permissions(_manifest()) == []


# ── M2: 依赖解析 ──


class TestDependencyResolution:
    def test_missing_dependency_rejected(self, tmp_path) -> None:
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        (tmp_path / "plugins" / "dependent").mkdir(parents=True)
        m = _manifest(id="dependent", dependencies=["not_installed_plugin"])
        assert loader._resolve_dependencies(m) == ["not_installed_plugin"]

    def test_all_dependencies_satisfied(self, tmp_path) -> None:
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        (tmp_path / "plugins" / "base_plugin").mkdir(parents=True)
        (tmp_path / "plugins" / "base_plugin" / "plugin.yaml").write_text(
            "id: base_plugin\nname: base\nkind: capability\n", encoding="utf-8")
        m = _manifest(id="dependent", dependencies=["base_plugin"])
        assert loader._resolve_dependencies(m) == []


# ── M8: 启停持久化 ──


class TestEnableDisablePersistence:
    def test_default_enabled(self, tmp_path) -> None:
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        assert loader.is_enabled("some_plugin") is True

    def test_disable_persists_marker(self, tmp_path) -> None:
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        loader.set_enabled("p8", False)
        assert loader.is_enabled("p8") is False
        state_file = tmp_path / "data" / "plugins" / "p8" / ".enabled"
        assert state_file.read_text(encoding="utf-8").strip() == "disabled"

    def test_enable_after_disable(self, tmp_path) -> None:
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        loader.set_enabled("p8", False)
        loader.set_enabled("p8", True)
        assert loader.is_enabled("p8") is True

    def test_disabled_plugin_skips_load(self, tmp_path) -> None:
        (tmp_path / "plugins" / "p8").mkdir(parents=True)
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        loader.set_enabled("p8", False)
        assert loader.load("p8") is None


# ── M10: manifest 增强字段 ──


class TestManifestEnhancedFields:
    def test_manifest_accepts_governance_fields(self) -> None:
        m = _manifest(
            license="MIT", homepage="https://example.com",
            compat_api_version="0.1.0", permissions=["network"],
            dependencies=["base"], signature="abc", config_schema_version=2,
        )
        assert m.license == "MIT"
        assert m.homepage == "https://example.com"
        assert m.permissions == ["network"]
        assert m.dependencies == ["base"]
        assert m.config_schema_version == 2

    def test_metadata_reports_signed_verified(self) -> None:
        from clipwright.schema.plugin import PluginMetadata
        m = _manifest(id="meta_p", signature="abc")
        meta = PluginMetadata(manifest=m, signed=bool(m.signature), verified=False)
        assert meta.signed is True
        assert meta.verified is False  # 无签名密钥 → 无法验证


# ── M15: 配置迁移 ──


class TestConfigMigration:
    def test_migrate_config_bumps_schema_version(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(settings, "plugin_dir", tmp_path / "plugins")
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        cfg = {"old_field": "v"}
        out = loader._migrate_config("mig_p", cfg, target_schema=3)
        assert out["_schema_version"] == 3

    def test_migrate_config_noop_below_target(self, tmp_path) -> None:
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        out = loader._migrate_config("mig_p", {"_schema_version": 5}, target_schema=3)
        assert out["_schema_version"] == 5

    def test_migrate_config_applies_migrations_module(self, tmp_path, monkeypatch) -> None:
        """插件目录提供 migrations.py 时执行 migrate_config(config, from, to)。"""
        (tmp_path / "plugins" / "mig_p").mkdir(parents=True)
        (tmp_path / "plugins" / "mig_p" / "migrations.py").write_text(
            "def migrate_config(config, frm, to):\n"
            "    config['migrated'] = f'{frm}->{to}'\n"
            "    return config\n",
            encoding="utf-8")
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        out = loader._migrate_config("mig_p", {"k": "v"}, target_schema=2)
        assert out["migrated"] == "1->2"
        assert out["_schema_version"] == 2


# ── P1-3: diagram_style 插件类在 initialize() 注册 Hook ──


class TestDiagramStylePluginClass:
    def test_plugin_class_registers_hook_on_initialize(self) -> None:
        from clipwright.plugins.hooks import HookPoint, HookRegistry
        from plugins.diagram_style.presets import DiagramStylePlugin

        HookRegistry.clear()
        p = DiagramStylePlugin()
        p.initialize()
        assert len(HookRegistry._hooks[HookPoint.DIAGRAM_STYLE_PRESET]) == 1


# ── P1-6: DiagramStyle 约定兼容 ──


class TestDiagramStyleConventionCompat:
    def test_from_dict_filters_unknown_keys(self) -> None:
        s = DiagramStyle.from_dict({"primary_color": "#112233", "not_a_field": 42, "evil": None})
        assert s.primary_color == "#112233"
        assert not hasattr(s, "not_a_field")

    def test_preset_with_extra_keys_no_typeerror(self, tmp_path, monkeypatch) -> None:
        from clipwright.plugins.hooks import HookPoint, HookRegistry
        HookRegistry.clear()

        def hook(ctx):
            return {"presets": {"weird": {"primary_color": "#111111", "extra": "x", "font_size": "abc"}}}

        HookRegistry.register(HookPoint.DIAGRAM_STYLE_PRESET, hook)
        s = DiagramStyle.from_persona({"style_preset": "weird"})
        assert isinstance(s, DiagramStyle)
        assert s.primary_color == "#111111"
        assert s.font_size == 28  # 非法类型被忽略，保留默认
        HookRegistry.clear()


# ── API: enable / disable / permissions ──


class TestPluginGovernanceApi:
    def _make_client(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from clipwright.api.plugin import router
        app = FastAPI()
        app.include_router(router)
        return TestClient(app)

    def test_permissions_endpoint(self) -> None:
        client = self._make_client()
        resp = client.get("/api/plugin/permissions")
        assert resp.status_code == 200
        assert "allowed" in resp.json()
        assert "network" in resp.json()["allowed"]

    def test_enable_disable_roundtrip(self, tmp_path) -> None:
        from clipwright.api import plugin as plugin_api
        from clipwright.plugins.loader import PluginLoader

        client = self._make_client()
        loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")
        plugin_api._loader = loader

        resp = client.post("/api/plugin/some_id/disable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] == "false"
        assert loader.is_enabled("some_id") is False

        resp = client.post("/api/plugin/some_id/enable")
        assert resp.status_code == 200
        assert resp.json()["enabled"] == "true"
        assert loader.is_enabled("some_id") is True

    def test_routes_registered_in_main_app(self) -> None:
        from clipwright.main import app as main_app
        schema = main_app.openapi()
        paths = list(schema.get("paths", {}).keys())
        assert any(p.endswith("/{plugin_id}/enable") for p in paths)
        assert any(p.endswith("/{plugin_id}/disable") for p in paths)
        assert any(p.endswith("/permissions") for p in paths)
        assert any(p.endswith("/errors") for p in paths)


# ── M7: 错误通道 ──


class TestPluginErrorBus:
    def test_record_and_list_roundtrip(self) -> None:
        from clipwright.plugins.error_bus import PluginErrorBus
        bus = PluginErrorBus(cap=10)
        bus.record("p_err", "load", "导入失败", "detail")
        errs = bus.list()
        assert len(errs) == 1
        assert errs[0]["plugin_id"] == "p_err"
        assert errs[0]["phase"] == "load"
        assert "导入失败" in errs[0]["message"]

    def test_cap_limits_buffer(self) -> None:
        from clipwright.plugins.error_bus import PluginErrorBus
        bus = PluginErrorBus(cap=3)
        for i in range(5):
            bus.record("p", "load", f"err{i}")
        errs = bus.list()
        assert len(errs) == 3
        assert errs[-1]["message"] == "err4"

    def test_clear_by_plugin(self) -> None:
        from clipwright.plugins.error_bus import PluginErrorBus
        bus = PluginErrorBus()
        bus.record("a", "load", "x")
        bus.record("b", "load", "y")
        assert bus.clear("a") == 1
        assert [e["plugin_id"] for e in bus.list()] == ["b"]

    def test_error_bus_api_endpoints(self, tmp_path) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from clipwright.api import plugin as plugin_api
        from clipwright.api.plugin import router
        from clipwright.plugins.error_bus import get_error_bus

        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        plugin_api._loader = PluginLoader(plugin_dir=tmp_path / "plugins", data_dir=tmp_path / "data")

        get_error_bus().record("ep", "action", "动作失败")
        resp = client.get("/api/plugin/errors")
        assert resp.status_code == 200
        assert any(e["plugin_id"] == "ep" for e in resp.json())

        resp = client.delete("/api/plugin/errors?plugin_id=ep")
        assert resp.status_code == 200
        assert resp.json()["removed"] == 1
