"""MG 预览端点测试（Phase 2.6）：/mg/list 模板列表 + /preview 路由与降级。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from clipwright.main import app

client = TestClient(app)


def test_mg_list_returns_templates():
    r = client.get("/api/animation/mg/list")
    assert r.status_code == 200, r.text
    items = r.json()
    assert isinstance(items, list) and len(items) >= 8
    ids = [i["animation_id"] for i in items]
    assert "mg_title_reveal" in ids
    for i in items:
        assert i.get("name")
        assert isinstance(i.get("params"), dict)


def test_preview_requires_valid_body():
    r = client.post("/api/animation/preview", json={})
    assert r.status_code in (400, 404)  # 无 animation_id 且无 mg_json


def test_preview_503_when_hyperframes_unavailable(monkeypatch):
    """Hyperframes 不可用时明确 503（前端据此提示），不静默 500。"""
    import clipwright.animation.hyperframes_renderer as hf_mod

    async def _unavailable() -> bool:
        return False

    monkeypatch.setattr(hf_mod.HyperframesRenderer, "ais_available", staticmethod(_unavailable))
    r = client.post("/api/animation/preview", json={"animation_id": "mg_title_reveal"})
    assert r.status_code == 503
    assert "Hyperframes" in r.json()["detail"]


def test_preview_rejects_bad_mg_json():
    r = client.post("/api/animation/preview", json={"mg_json": {"foo": 1}})
    assert r.status_code == 400
