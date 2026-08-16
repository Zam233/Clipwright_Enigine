"""P8: 失败诊断报告端点测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from clipwright.main import app
from clipwright.api import pipeline as pipeline_api

client = TestClient(app)


def test_diagnostics_404_for_unknown() -> None:
    resp = client.get("/api/pipeline/diagnostics/pl_nonexistent")
    assert resp.status_code == 404


def test_diagnostics_structured_report() -> None:
    # 注入一个失败的管线结果
    pid = "pl_diag_test"
    pipeline_api._pipeline_results[pid] = {
        "pipeline_id": pid,
        "status": "failed",
        "error": "LLM 调用超时（connection timed out）",
        "steps": [
            {"agent_name": "structure", "status": "completed"},
            {"agent_name": "material", "status": "failed", "error": "LLM 调用超时（connection timed out）"},
        ],
    }
    try:
        resp = client.get(f"/api/pipeline/diagnostics/{pid}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "failed"
        assert body["error_category"] == "transient"
        assert body["failed_step"]["agent_name"] == "material"
        assert any("可重试" in s for s in body["suggestions"])
        assert body["steps_summary"][0]["agent_name"] == "structure"
    finally:
        pipeline_api._pipeline_results.pop(pid, None)
