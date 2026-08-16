"""P8: 批量应用模板（批量选题生成）端点测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from clipwright.main import app

client = TestClient(app)


def _create_template() -> str:
    resp = client.post("/api/template/create", json={
        "name": "批量测试模板",
        "description": "",
        "category": "test",
        "tags": [],
        "timeline": {
            "id": "", "width": 1920, "height": 1080, "fps": 30, "duration_sec": 10,
            "tracks": [{"id": "t1", "name": "V1", "kind": "video", "index": 0,
                        "locked": False, "muted": False, "clips": []}],
        },
    })
    assert resp.status_code == 200, resp.text
    return resp.json()["template_id"]


def test_batch_apply_template() -> None:
    tid = _create_template()
    try:
        resp = client.post(f"/api/template/{tid}/batch-apply", json={
            "items": [
                {"topic": "选题A", "overrides": {"duration_sec": 12}},
                {"topic": "选题B"},
            ],
        })
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "applied"
        assert len(body["results"]) == 2
        assert body["results"][0]["topic"] == "选题A"
        assert body["results"][0]["timeline"]["duration_sec"] == 12
        assert body["results"][1]["timeline"]["_meta"]["topic"] == "选题B"
    finally:
        client.delete(f"/api/template/{tid}")


def test_batch_apply_empty_items() -> None:
    tid = _create_template()
    try:
        resp = client.post(f"/api/template/{tid}/batch-apply", json={"items": []})
        assert resp.status_code == 200
        assert resp.json()["results"] == []
    finally:
        client.delete(f"/api/template/{tid}")


def test_batch_apply_missing_template_404() -> None:
    resp = client.post("/api/template/tpl_nonexistent/batch-apply", json={"items": [{"topic": "x"}]})
    assert resp.status_code == 404
