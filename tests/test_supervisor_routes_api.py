"""B4 补充：路由表与模式开关端点（F5 前端依赖）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.config import settings


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(settings, "model_routes", {}, raising=False)
    monkeypatch.setattr(settings, "model_whitelist", [], raising=False)
    monkeypatch.setattr(settings, "supervisor_mode", "off", raising=False)
    from clipwright.services import settings_store

    # 不落真实配置文件
    monkeypatch.setattr(settings_store, "save", lambda patch: (list(patch), []))
    monkeypatch.setattr(settings_store, "apply_all", lambda obj=None: [])
    yield


def _client():
    from clipwright.api import supervisor as supervisor_api

    app = FastAPI()
    app.include_router(supervisor_api.admin_router)
    return TestClient(app)


def test_get_routes_returns_stages_and_providers():
    body = _client().get("/api/supervisor/routes").json()
    assert set(body["routes"]) == {
        "structure", "material", "edit", "animation", "audio", "quality",
    }
    assert settings.llm_provider in body["allowed_providers"]
    assert body["whitelist"] == []


def test_put_routes_hot_updates_settings():
    resp = _client().put("/api/supervisor/routes", json={
        "routes": {"edit": [{"provider": "p", "model": "m", "tier": "std"}]},
        "whitelist": ["p/m"],
    })
    assert resp.status_code == 200
    assert resp.json()["how"] == "persisted"
    assert settings.model_routes["edit"][0]["model"] == "m"
    assert settings.model_whitelist == ["p/m"]

    listed = _client().get("/api/supervisor/routes").json()
    assert listed["routes"]["edit"][0]["model"] == "m"


def test_put_routes_requires_a_field():
    resp = _client().put("/api/supervisor/routes", json={})
    assert resp.status_code == 400


def test_mode_roundtrip_and_validation():
    client = _client()
    assert client.get("/api/supervisor/mode").json()["mode"] == "off"

    ok = client.put("/api/supervisor/mode", json={"mode": "shadow"})
    assert ok.status_code == 200
    assert settings.supervisor_mode == "shadow"
    assert client.get("/api/supervisor/mode").json()["mode"] == "shadow"

    bad = client.put("/api/supervisor/mode", json={"mode": "turbo"})
    assert bad.status_code == 400


def test_persist_failure_still_hot_applies(monkeypatch):
    from clipwright.services import settings_store

    def boom(patch):
        raise RuntimeError("disk full")

    monkeypatch.setattr(settings_store, "save", boom)
    resp = _client().put("/api/supervisor/mode", json={"mode": "on"})
    assert resp.status_code == 200
    assert resp.json()["how"] == "memory_only"
    assert settings.supervisor_mode == "on"
