"""C8: 熔断健康探测端点测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient

from clipwright.main import app
from clipwright.services.pipeline_v2 import PipelineOrchestratorV2

client = TestClient(app)


def test_breaker_status_ok_when_no_failures() -> None:
    # 清空熔断状态（类级共享）
    PipelineOrchestratorV2._circuit_breakers = {}
    resp = client.get("/api/pipeline/breaker-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["tripped_agents"] == 0
    assert isinstance(body["agents"], list)


def test_breaker_status_reports_open_breaker() -> None:
    from datetime import datetime, timezone
    PipelineOrchestratorV2._circuit_breakers = {
        "edit": {"fail_count": 3, "last_fail_at": datetime.now(timezone.utc)},
        "audio": {"fail_count": 1, "last_fail_at": datetime.now(timezone.utc)},
    }
    try:
        resp = client.get("/api/pipeline/breaker-status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["tripped_agents"] == 1
        by_name = {a["agent"]: a for a in body["agents"]}
        assert by_name["edit"]["open"] is True
        assert by_name["edit"]["fail_count"] == 3
        assert by_name["audio"]["open"] is False
    finally:
        PipelineOrchestratorV2._circuit_breakers = {}
