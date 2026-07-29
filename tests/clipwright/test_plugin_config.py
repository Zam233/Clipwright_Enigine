"""Tests for plugin config API — GET/PUT/DELETE /api/plugin/{id}/config."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.plugins.loader import PluginLoader
from clipwright.api.plugin import router as plugin_router, set_loader


# ── Helpers ──

def _create_test_env() -> tuple[PluginLoader, Path, Path]:
    """创建测试环境：临时 source dir + data dir + 一个带 config.yaml 的测试插件。"""
    src_dir = Path(tempfile.mkdtemp(prefix="test_plugin_src_"))
    data_dir = Path(tempfile.mkdtemp(prefix="test_plugin_data_"))

    # 创建插件目录（带 __init__.py，让 has_manifest 通过）
    plugin_dir = src_dir / "test_plugin"
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "__init__.py").write_text(
        "from clipwright.plugins import CapabilityPlugin\n"
        "class TestPlugin(CapabilityPlugin):\n"
        "    def initialize(self): pass\n"
    )

    # 创建 plugin.yaml 清单
    (plugin_dir / "plugin.yaml").write_text(
        "name: Test Plugin\nkind: capability\nentry_point: test_plugin\n"
    )

    # 创建默认 config.yaml
    (plugin_dir / "config.yaml").write_text("default_key: default_value\nnested:\n  deep: kept\n")

    loader = PluginLoader(plugin_dir=src_dir, data_dir=data_dir)
    loader.load("test_plugin")

    return loader, src_dir, data_dir


# ── Loader method tests ──


def test_get_config_returns_source_defaults() -> None:
    loader, src, data = _create_test_env()
    cfg = loader.get_config("test_plugin")
    assert cfg["default_key"] == "default_value"
    assert cfg["nested"]["deep"] == "kept"


def test_save_get_merged() -> None:
    loader, src, data = _create_test_env()
    loader.save_config("test_plugin", {"override_key": "override_val", "default_key": "overwritten"})
    cfg = loader.get_config("test_plugin")
    assert cfg["override_key"] == "override_val"
    assert cfg["default_key"] == "overwritten"  # override wins
    assert cfg["nested"]["deep"] == "kept"  # unchanged from source


def test_config_file_written_to_data_dir() -> None:
    loader, src, data = _create_test_env()
    loader.save_config("test_plugin", {"key": "val"})
    config_path = data / "plugins" / "test_plugin" / "config.yaml"
    assert config_path.exists()
    written = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert written == {"key": "val"}


def test_get_config_nonexistent_plugin() -> None:
    loader, src, data = _create_test_env()
    cfg = loader.get_config("nonexistent")
    assert cfg == {}


def test_delete_config_reverts_to_defaults() -> None:
    loader, src, data = _create_test_env()
    loader.save_config("test_plugin", {"default_key": "overwritten"})
    assert loader.get_config("test_plugin")["default_key"] == "overwritten"

    config_path = data / "plugins" / "test_plugin" / "config.yaml"
    config_path.unlink()
    # Simulate DELETE logic: refresh plugin config
    plugin = loader.get("test_plugin")
    if plugin:
        plugin.config = loader._get_merged_config("test_plugin")

    cfg = loader.get_config("test_plugin")
    assert cfg["default_key"] == "default_value"


# ── API endpoint tests ──


@pytest.fixture
def app_with_loader() -> FastAPI:
    """创建带插件 API 路由的测试 app，注入测试 loader。"""
    loader, src, data = _create_test_env()
    app = FastAPI()
    app.include_router(plugin_router)
    set_loader(loader)
    return app


def test_get_config_200(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    resp = client.get("/api/plugin/test_plugin/config")
    assert resp.status_code == 200
    data = resp.json()
    assert data["default_key"] == "default_value"


def test_get_config_404(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    resp = client.get("/api/plugin/nonexistent/config")
    assert resp.status_code == 404


def test_put_config_200(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    body = "fields:\n  key:\n    type: string\n    value: hello\n    label: K\n"
    resp = client.put(
        "/api/plugin/test_plugin/config",
        content=body.encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    assert resp.json()["plugin_id"] == "test_plugin"


def test_put_config_get_merged(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    client.put(
        "/api/plugin/test_plugin/config",
        content=(
            b"fields:\n"
            b"  default_key:\n    type: string\n    value: overwritten\n    label: D\n"
        ),
        headers={"Content-Type": "text/plain"},
    )
    resp = client.get("/api/plugin/test_plugin/config")
    data = resp.json()
    # source is flat, override is typed → flat dict (no fields merge)
    assert data["default_key"] == "overwritten"


def test_put_config_invalid_yaml(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    body = "key: {invalid: yaml\n"
    resp = client.put(
        "/api/plugin/test_plugin/config",
        content=body.encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400


def test_put_config_empty_body(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    resp = client.put(
        "/api/plugin/test_plugin/config",
        content=b"",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400


def test_put_config_not_dict(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    resp = client.put(
        "/api/plugin/test_plugin/config",
        content=b"- list item\n- not a dict\n",
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400


def test_delete_config_reverts(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    # First override with typed format
    client.put(
        "/api/plugin/test_plugin/config",
        content=(
            b"fields:\n"
            b"  default_key:\n    type: string\n    value: overridden\n    label: D\n"
        ),
        headers={"Content-Type": "text/plain"},
    )
    assert client.get("/api/plugin/test_plugin/config").json()["default_key"] == "overridden"

    # Then delete
    resp = client.delete("/api/plugin/test_plugin/config")
    assert resp.status_code == 200

    # Should revert to defaults
    assert client.get("/api/plugin/test_plugin/config").json()["default_key"] == "default_value"


def test_delete_config_404(app_with_loader: FastAPI) -> None:
    client = TestClient(app_with_loader)
    resp = client.delete("/api/plugin/nonexistent/config")
    assert resp.status_code == 404


def test_routes_in_openapi(app_with_loader: FastAPI) -> None:
    schema = app_with_loader.openapi()
    paths = list(schema.get("paths", {}).keys())
    assert any("plugin/{plugin_id}/config" in p for p in paths)
