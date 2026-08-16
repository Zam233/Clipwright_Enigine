"""P8: 管线配置模板复用测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from clipwright.main import app
from clipwright.api import pipeline as pipeline_api

client = TestClient(app)


def _clear() -> None:
    pipeline_api._pipe_templates.clear()


def test_save_list_get_delete() -> None:
    _clear()
    try:
        # 保存
        resp = client.post("/api/pipeline/templates", json={
            "name": "知识区_标准",
            "request": {
                "persona_id": "default",
                "category_plugin_id": "knowledge_longform",
                "topic": "占位",
                "extra_params": {"video_mode": "voiceover"},
                "dry_run": False,
            },
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "saved"

        # 列表
        listed = client.get("/api/pipeline/templates").json()
        assert any(t["name"] == "知识区_标准" for t in listed)

        # 读取
        got = client.get("/api/pipeline/templates/知识区_标准").json()
        assert got["request"]["category_plugin_id"] == "knowledge_longform"
        assert got["request"]["extra_params"]["video_mode"] == "voiceover"

        # 删除
        assert client.delete("/api/pipeline/templates/知识区_标准").status_code == 200
        assert client.get("/api/pipeline/templates/知识区_标准").status_code == 404
    finally:
        _clear()


def test_save_invalid_name_400() -> None:
    _clear()
    try:
        resp = client.post("/api/pipeline/templates", json={
            "name": "bad name!!", "request": {"persona_id": "x"},
        })
        assert resp.status_code == 400
    finally:
        _clear()
