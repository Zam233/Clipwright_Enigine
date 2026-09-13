"""M8 测试：受信任 UI bundle 门禁（默认关闭 + 签名强制，fail-closed）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.api import plugin as plugin_api
from clipwright.config import settings
from clipwright.schema.plugin import PluginManifest


class _FakeLoader:
    def __init__(self, root: Path) -> None:
        self.plugin_dir = root

    def discover(self) -> list[str]:
        return ["signed_plug", "unsigned_plug"]

    def _parse_manifest(self, pid: str, path: Path) -> PluginManifest:
        return PluginManifest(
            id=pid, name=pid, kind="capability",
            signature=("deadbeef" if pid == "signed_plug" else ""),
        )


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "plugins"
    for pid in ("signed_plug", "unsigned_plug"):
        d = root / pid
        d.mkdir(parents=True)
        (d / "bundle.html").write_text("<html><body>hi</body></html>", encoding="utf-8")
    monkeypatch.setattr(plugin_api, "_loader", _FakeLoader(root))
    monkeypatch.setattr(settings, "plugin_allow_ui_bundle", False)
    app = FastAPI()
    app.include_router(plugin_api.router)
    return TestClient(app)


class TestBundleGate:
    def test_disabled_by_default(self, env):
        resp = env.get("/api/plugin/signed_plug/bundle")
        assert resp.status_code == 403
        assert "未开启" in resp.json()["detail"]

    def test_enabled_but_unsigned_rejected(self, env, monkeypatch):
        monkeypatch.setattr(settings, "plugin_allow_ui_bundle", True)
        resp = env.get("/api/plugin/unsigned_plug/bundle")
        assert resp.status_code == 403
        assert "签名" in resp.json()["detail"]

    def test_enabled_and_signed_served_with_csp(self, env, monkeypatch):
        monkeypatch.setattr(settings, "plugin_allow_ui_bundle", True)
        resp = env.get("/api/plugin/signed_plug/bundle")
        assert resp.status_code == 200
        assert "hi" in resp.text
        assert "Content-Security-Policy" in resp.headers

    def test_missing_bundle_file_404(self, env, monkeypatch):
        monkeypatch.setattr(settings, "plugin_allow_ui_bundle", True)
        (Path(plugin_api._loader.plugin_dir) / "signed_plug" / "bundle.html").unlink()
        assert env.get("/api/plugin/signed_plug/bundle").status_code == 404

    def test_unknown_plugin_404(self, env, monkeypatch):
        monkeypatch.setattr(settings, "plugin_allow_ui_bundle", True)
        assert env.get("/api/plugin/nope/bundle").status_code == 404
