"""Tests for typed plugin config — validation, extraction, API endpoints."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.plugins.config_types import (
    TYPED_CONFIG_TYPES,
    typed_config_to_values,
    validate_typed_config,
)
from clipwright.plugins.loader import PluginLoader, _extract_flat_values
from clipwright.api.plugin import router as plugin_router, set_loader


# ── Unit tests: typed_config_to_values ──

def test_extract_flat_values() -> None:
    cfg = {
        "fields": {
            "api_key": {"type": "string", "value": "sk-xxx", "label": "Key"},
            "max_results": {"type": "int", "value": 10, "label": "Max"},
        }
    }
    result = typed_config_to_values(cfg)
    assert result == {"api_key": "sk-xxx", "max_results": 10}


def test_extract_empty_fields() -> None:
    assert typed_config_to_values({}) == {}
    assert typed_config_to_values({"fields": {}}) == {}


def test_extract_skip_missing_value() -> None:
    cfg = {"fields": {"key": {"type": "string", "label": "K"}}}
    assert typed_config_to_values(cfg) == {}


# ── Unit tests: validate_typed_config ──

def test_validate_valid() -> None:
    cfg = {
        "fields": {
            "api_key": {"type": "string", "value": "sk-xxx", "label": "Key"},
            "enabled": {"type": "bool", "value": True, "label": "On"},
            "max_results": {"type": "int", "value": 10, "label": "Max"},
            "score": {"type": "float", "value": 0.8, "label": "Score"},
            "opts": {"type": "dict", "value": {"a": 1}, "label": "Opts"},
            "tags": {"type": "list", "value": ["a", "b"], "label": "Tags"},
        }
    }
    assert validate_typed_config(cfg) == []


def test_validate_wrong_type() -> None:
    cfg = {"fields": {"count": {"type": "int", "value": "not_int", "label": "C"}}}
    errors = validate_typed_config(cfg)
    assert len(errors) == 1
    assert "int" in errors[0]


def test_validate_invalid_type_name() -> None:
    cfg = {"fields": {"key": {"type": "unknown", "value": "x", "label": "K"}}}
    errors = validate_typed_config(cfg)
    assert len(errors) == 1
    assert "unknown" in errors[0]


def test_validate_missing_value() -> None:
    cfg = {"fields": {"key": {"type": "string", "label": "K"}}}
    errors = validate_typed_config(cfg)
    assert len(errors) == 1
    assert "value" in errors[0]


def test_validate_missing_fields() -> None:
    errors = validate_typed_config({"something": "else"})
    assert len(errors) == 1
    assert "fields" in errors[0]


def test_validate_fields_not_dict() -> None:
    errors = validate_typed_config({"fields": "not_dict"})
    assert len(errors) == 1


def test_validate_field_is_not_dict() -> None:
    cfg = {"fields": {"key": "not_dict"}}
    errors = validate_typed_config(cfg)
    assert len(errors) == 1


# ── Loader tests ──

def _make_test_env() -> tuple[PluginLoader, Path, Path]:
    src = Path(tempfile.mkdtemp(prefix="tsrc_"))
    data = Path(tempfile.mkdtemp(prefix="tdata_"))
    pd = src / "tp"
    pd.mkdir(parents=True)
    (pd / "__init__.py").write_text(
        "from clipwright.plugins import CapabilityPlugin\n"
        "class TP(CapabilityPlugin):\n    def initialize(self): pass\n"
    )
    (pd / "plugin.yaml").write_text("name: TP\nkind: capability\nentry_point: tp\n")
    (pd / "config.yaml").write_text(
        "fields:\n  key1:\n    type: string\n    value: default_val\n    label: K1\n"
    )
    loader = PluginLoader(plugin_dir=src, data_dir=data)
    loader.load("tp")
    return loader, src, data


def test_loader_flat_config() -> None:
    loader, _, _ = _make_test_env()
    plugin = loader.get("tp")
    assert plugin is not None
    assert plugin.config == {"key1": "default_val"}  # flat values


def test_loader_typed_config() -> None:
    loader, _, _ = _make_test_env()
    cfg = loader.get_typed_config("tp")
    assert "fields" in cfg
    assert cfg["fields"]["key1"]["type"] == "string"
    assert cfg["fields"]["key1"]["value"] == "default_val"


# ── API tests ──

@pytest.fixture
def api_client() -> TestClient:
    loader, src, data = _make_test_env()
    app = FastAPI()
    app.include_router(plugin_router)
    set_loader(loader)
    return TestClient(app)


def test_get_returns_typed(api_client: TestClient) -> None:
    resp = api_client.get("/api/plugin/tp/config")
    assert resp.status_code == 200
    data = resp.json()
    assert "fields" in data
    assert data["fields"]["key1"]["type"] == "string"


def test_put_valid_typed(api_client: TestClient) -> None:
    body = "fields:\n  key1:\n    type: string\n    value: updated\n    label: K1\n"
    resp = api_client.put(
        "/api/plugin/tp/config",
        content=body.encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200


def test_put_type_mismatch(api_client: TestClient) -> None:
    body = "fields:\n  count:\n    type: int\n    value: not_int\n    label: C\n"
    resp = api_client.put(
        "/api/plugin/tp/config",
        content=body.encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400


def test_put_invalid_type_name(api_client: TestClient) -> None:
    body = "fields:\n  key:\n    type: bogus\n    value: x\n    label: K\n"
    resp = api_client.put(
        "/api/plugin/tp/config",
        content=body.encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400


def test_put_missing_fields(api_client: TestClient) -> None:
    body = "just: plain_yaml\n"
    resp = api_client.put(
        "/api/plugin/tp/config",
        content=body.encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 400


# ── M13: 未加载插件预配置 ──


def _unloaded_env() -> tuple[TestClient, object]:
    """创建未加载的插件环境：loader 已知插件目录但不加载。"""
    src = Path(tempfile.mkdtemp(prefix="tsrc13_"))
    data = Path(tempfile.mkdtemp(prefix="tdata13_"))
    pd = src / "pre_cfg"
    pd.mkdir(parents=True)
    (pd / "__init__.py").write_text(
        "from clipwright.plugins import CapabilityPlugin\n"
        "class PC(CapabilityPlugin):\n    def initialize(self): pass\n"
    )
    (pd / "plugin.yaml").write_text("name: PC\nkind: capability\nentry_point: pre_cfg\n")
    (pd / "config.yaml").write_text(
        "fields:\n  api_key:\n    type: string\n    value: ''\n    label: API Key\n"
    )
    loader = PluginLoader(plugin_dir=src, data_dir=data)
    app = FastAPI()
    app.include_router(plugin_router)
    set_loader(loader)
    return TestClient(app), loader


def test_get_config_unloaded_plugin_returns_defaults() -> None:
    client, loader = _unloaded_env()
    assert loader.get("pre_cfg") is None  # 未加载
    resp = client.get("/api/plugin/pre_cfg/config")
    assert resp.status_code == 200
    assert "fields" in resp.json()


def test_put_config_unloaded_plugin_persists() -> None:
    client, loader = _unloaded_env()
    assert loader.get("pre_cfg") is None
    body = "fields:\n  api_key:\n    type: string\n    value: sk-预配置\n    label: API Key\n"
    resp = client.put(
        "/api/plugin/pre_cfg/config",
        content=body.encode(),
        headers={"Content-Type": "text/plain"},
    )
    assert resp.status_code == 200
    # 预配置已落盘（未加载也生效于下次加载）
    merged = loader._get_merged_config("pre_cfg")
    assert merged["fields"]["api_key"]["value"] == "sk-预配置"


def test_routes_in_openapi(api_client: TestClient) -> None:
    schema = api_client.app.openapi()
    paths = list(schema.get("paths", {}).keys())
    assert any("plugin/{plugin_id}/config" in p for p in paths)
