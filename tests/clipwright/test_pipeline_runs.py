"""GET /api/pipeline/runs 运行记录注册表测试。

覆盖注册表生命周期：start → complete / start → failure 均正确更新记录，
以及 GET /runs 端点的返回形状（对齐 PipelineAdminPage 期望）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.schema.pipeline import PipelineStatus, PipelineStep
from clipwright.services import pipeline_v2 as pv2

# ── Test app ──

app = FastAPI()
from clipwright.api.pipeline import router  # noqa: E402

app.include_router(router)
client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_registry() -> None:
    """每个用例前清空内存注册表，避免用例间串扰。"""
    pv2.clear_run_records()
    yield
    pv2.clear_run_records()


def _completed_step(name: str, started: datetime, completed: datetime, status: PipelineStatus = PipelineStatus.COMPLETED) -> PipelineStep:
    step = PipelineStep(agent_name=name, status=status)
    step.started_at = started
    step.completed_at = completed
    step.duration_ms = int((completed - started).total_seconds() * 1000)
    return step


# ── 注册表生命周期 ──

def test_start_records_running() -> None:
    pv2.record_run_start("pl_test_start", "话题A")
    runs = pv2.get_run_records()
    assert len(runs) == 1
    run = runs[0]
    assert run["id"] == "pl_test_start"
    assert run["topic"] == "话题A"
    assert run["status"] == "running"
    assert run["duration_ms"] == 0
    assert run["started_at"]
    assert run["agents"] == []


def test_start_then_complete_updates_spans() -> None:
    pv2.record_run_start("pl_test_complete", "话题B")
    base = datetime.now()
    steps = [
        _completed_step("structure", base, base + timedelta(seconds=1)),
        _completed_step("material", base + timedelta(seconds=1), base + timedelta(seconds=2)),
    ]
    pv2.record_run_complete("pl_test_complete", "completed", steps)

    runs = pv2.get_run_records()
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "completed"
    assert isinstance(run["duration_ms"], int) and run["duration_ms"] >= 0
    assert len(run["agents"]) == 2

    span0 = run["agents"][0]
    assert span0["agent"] == "structure"
    assert span0["start"] == 0
    assert span0["dur"] == 1000
    assert span0["status"] == "ok"

    span1 = run["agents"][1]
    assert span1["agent"] == "material"
    assert span1["start"] == 1000
    assert span1["dur"] == 1000
    assert span1["status"] == "ok"


def test_start_then_failure_updates_record() -> None:
    pv2.record_run_start("pl_test_fail", "话题C")
    base = datetime.now()
    ok_step = _completed_step("structure", base, base + timedelta(seconds=1))
    fail_step = _completed_step("edit", base + timedelta(seconds=1), base + timedelta(seconds=2), status=PipelineStatus.FAILED)
    pv2.record_run_complete("pl_test_fail", "failed", [ok_step, fail_step])

    runs = pv2.get_run_records()
    assert len(runs) == 1
    run = runs[0]
    assert run["status"] == "failed"
    assert isinstance(run["duration_ms"], int)

    statuses = {s["agent"]: s["status"] for s in run["agents"]}
    assert statuses == {"structure": "ok", "edit": "fail"}
    fail_span = run["agents"][1]
    assert fail_span["dur"] == 1000


def test_complete_without_start_backfills() -> None:
    """缺失开始记录时（进程内直接调用执行器），完成记录也能补写。"""
    pv2.record_run_complete("pl_test_backfill", "completed", [])
    runs = pv2.get_run_records()
    assert len(runs) == 1
    assert runs[0]["id"] == "pl_test_backfill"
    assert runs[0]["status"] == "completed"
    assert runs[0]["agents"] == []


def test_retry_span_marked() -> None:
    """retry_count > 0 的步骤标记为 retry 跨度。"""
    pv2.record_run_start("pl_test_retry", "话题D")
    base = datetime.now() - timedelta(seconds=1)
    step = _completed_step("edit", base, base + timedelta(seconds=1))
    step.retry_count = 1
    pv2.record_run_complete("pl_test_retry", "completed", [step])
    run = pv2.get_run_records()[0]
    assert run["agents"][0]["status"] == "retry"


# ── API 端点 ──

def test_get_runs_endpoint_empty() -> None:
    resp = client.get("/api/pipeline/runs")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_get_runs_endpoint_shape() -> None:
    pv2.record_run_start("pl_api_1", "API话题")
    base = datetime.now() - timedelta(seconds=1)
    pv2.record_run_complete("pl_api_1", "completed", [
        _completed_step("structure", base, base + timedelta(seconds=1)),
    ])

    resp = client.get("/api/pipeline/runs")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    run = data[0]
    # 对齐 PipelineAdminPage normalize(): id/topic/status/duration_ms/started_at/agents
    assert set(run.keys()) == {"id", "topic", "status", "duration_ms", "started_at", "agents"}
    assert run["id"] == "pl_api_1"
    assert run["topic"] == "API话题"
    assert run["status"] == "completed"
    assert isinstance(run["duration_ms"], int) and run["duration_ms"] >= 0
    assert isinstance(run["started_at"], str) and run["started_at"]
    assert run["agents"][0]["agent"] == "structure"
    assert run["agents"][0]["status"] == "ok"


def test_route_registered_in_main_app() -> None:
    """验证 /api/pipeline/runs 路由在主应用 OpenAPI schema 中注册。"""
    from clipwright.main import app as main_app
    paths = list(main_app.openapi().get("paths", {}).keys())
    assert "/api/pipeline/runs" in paths


# ── B3: retry 端点 400 路径 ──

def _retry_request() -> dict:
    return {
        "persona_id": "default",
        "category_plugin_id": "knowledge_longform",
        "topic": "t",
        "extra_params": {},
    }


def test_retry_unknown_pipeline_404() -> None:
    resp = client.post("/api/pipeline/retry/pl_ghost/material")
    assert resp.status_code == 404


def test_retry_agent_not_found_400() -> None:
    from clipwright.api.pipeline import _pipeline_results, add_event, create_trace
    create_trace("pl_retry_nf")
    add_event("pl_retry_nf", "system", "info", "started")
    _pipeline_results["pl_retry_nf"] = {
        "pipeline_id": "pl_retry_nf",
        "request": _retry_request(),
        "steps": [
            {
                "agent_name": "structure",
                "status": "completed",
                "result": {"scenes": [{"title": "s"}]},
                "error": None,
            },
        ],
    }
    try:
        resp = client.post("/api/pipeline/retry/pl_retry_nf/nonexistent")
        assert resp.status_code == 400
    finally:
        _pipeline_results.pop("pl_retry_nf", None)
        from clipwright.services.trace import clear as _clear_trace
        _clear_trace("pl_retry_nf")


def test_retry_no_preceding_result_400() -> None:
    """目标 agent 之前无成功结果 → 明确 400（而非全量重跑）。"""
    from clipwright.api.pipeline import _pipeline_results, add_event, create_trace
    create_trace("pl_retry_nopre")
    add_event("pl_retry_nopre", "system", "info", "started")
    _pipeline_results["pl_retry_nopre"] = {
        "pipeline_id": "pl_retry_nopre",
        "request": _retry_request(),
        "steps": [
            {"agent_name": "edit", "status": "failed", "result": None, "error": "boom"},
        ],
    }
    try:
        resp = client.post("/api/pipeline/retry/pl_retry_nopre/edit")
        assert resp.status_code == 400
    finally:
        _pipeline_results.pop("pl_retry_nopre", None)
        from clipwright.services.trace import clear as _clear_trace
        _clear_trace("pl_retry_nopre")


# ── B4: regenerate-scene 端点已移除 ──

def test_regenerate_scene_endpoint_removed() -> None:
    """B4 处置：regenerate-scene 端点移除（原按索引替换、前端零调用），请求应 404。"""
    resp = client.post("/api/pipeline/regenerate-scene/pl_any/0")
    assert resp.status_code == 404


def test_regenerate_scene_not_in_openapi() -> None:
    from clipwright.main import app as main_app
    paths = list(main_app.openapi().get("paths", {}).keys())
    assert "/api/pipeline/regenerate-scene/{pipeline_id}/{scene_index}" not in paths


# ── G2: cancel 端点 ──

def test_cancel_unknown_pipeline_404() -> None:
    resp = client.post("/api/pipeline/cancel/pl_ghost_cancel")
    assert resp.status_code == 404


def test_cancel_sets_flag_and_returns_cancelling() -> None:
    from clipwright.api.pipeline import create_trace, add_event
    from clipwright.services.pipeline_v2 import clear_cancel, is_cancelled
    create_trace("pl_cancel_api")
    add_event("pl_cancel_api", "system", "info", "started")
    try:
        resp = client.post("/api/pipeline/cancel/pl_cancel_api")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"
        assert is_cancelled("pl_cancel_api") is True
    finally:
        clear_cancel("pl_cancel_api")
        from clipwright.services.trace import clear as _clear_trace
        _clear_trace("pl_cancel_api")


# ── B5/B13: V1 同步端点 deprecated 标记 ──

def test_run_v2_deprecated_flag() -> None:
    """/run-v2 响应体含 deprecated: true（B13：前端零调用，保留兼容）。"""
    import clipwright.services.pipeline_v2 as pv2

    class _FakeState:
        status = "completed"
        error = None
        steps = []

    async def _fake_run(self, request, pipeline_id=""):
        return _FakeState()

    orig = pv2.PipelineOrchestratorV2
    try:
        pv2.PipelineOrchestratorV2 = type(  # type: ignore[assignment]
            "FakeOrch", (), {"run": _fake_run},
        )
        resp = client.post("/api/pipeline/run-v2", json={
            "persona_id": "default",
            "category_plugin_id": "knowledge_longform",
            "topic": "t",
            "extra_params": {},
        })
        assert resp.status_code == 200
        assert resp.json().get("deprecated") is True
    finally:
        pv2.PipelineOrchestratorV2 = orig
