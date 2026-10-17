"""B5+/F7 接线：人工改派与诊断建议**真的会触发重跑**。

之前这两个端点只「登记 + 返回 requires_retry」，没有任何消费方，UI 却写着
「重试该环节后生效」。这里把「是否真的调起重试」「跑不起来时原因是否如实回传」
钉成测试——这条链断了用户就会以为活已经派下去了。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def _clean():
    from clipwright.api.pipeline import _pipeline_results
    from clipwright.supervisor.tracker import clear_trackers

    _pipeline_results.clear()
    clear_trackers()
    yield
    _pipeline_results.clear()
    clear_trackers()


def _client():
    from clipwright.api import supervisor as supervisor_api

    app = FastAPI()
    app.include_router(supervisor_api.router)
    return TestClient(app)


def _seed_pipeline(pid: str = "pl_wire") -> None:
    """造一条「跑过 structure 且失败」的管线结果，供重试前置校验通过。"""
    from clipwright.api.pipeline import _pipeline_results

    _pipeline_results[pid] = {
        "pipeline_id": pid,
        "status": "failed",
        "request": {
            "topic": "测试选题",
            "persona_id": "",
            "platform": "douyin",
            "duration_sec": 30,
            "extra_params": {"pipeline_timeout_sec": 60},
        },
        "steps": [
            {"agent_name": "structure", "status": "completed", "result": {"scenes": []}},
            {"agent_name": "material", "status": "failed", "result": None},
        ],
        "shared_data": {},
    }


def test_dispatch_triggers_retry(monkeypatch):
    from clipwright.api import pipeline as pipeline_api

    calls: list[tuple[str, str]] = []

    async def fake_retry(pid, agent, request):  # noqa: ANN001
        calls.append((pid, agent))
        return {"pipeline_id": pid, "status": "retrying", "agent": agent}

    monkeypatch.setattr(pipeline_api, "start_agent_retry", fake_retry)
    _seed_pipeline()

    body = _client().post("/api/pipeline/pl_wire/dispatch", json={
        "target_stage": "material", "reason": "第 3 段素材是占位图",
    }).json()

    assert calls == [("pl_wire", "material")]
    assert body["retry"]["retried"] is True
    assert body["requires_retry"] is False
    assert body["order"]["target_stage"] == "material"


def test_dispatch_reports_why_retry_did_not_start(monkeypatch):
    """跑不起来（如无前置成功结果）时：工单仍入账，但必须如实说明原因。"""
    from clipwright.api import pipeline as pipeline_api

    async def boom(pid, agent, request):  # noqa: ANN001
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="无可用前置成功结果")

    monkeypatch.setattr(pipeline_api, "start_agent_retry", boom)
    _seed_pipeline()

    body = _client().post("/api/pipeline/pl_wire/dispatch", json={
        "target_stage": "material", "reason": "再试一次",
    }).json()

    assert body["accepted"] is True
    assert body["retry"]["retried"] is False
    assert "无可用前置成功结果" in body["retry"]["reason"]
    assert body["requires_retry"] is True


def test_dispatch_can_skip_retry(monkeypatch):
    """auto_retry=false：只登记（管线正在跑时用）。"""
    from clipwright.api import pipeline as pipeline_api

    async def fake_retry(pid, agent, request):  # noqa: ANN001
        raise AssertionError("不应调用重试")

    monkeypatch.setattr(pipeline_api, "start_agent_retry", fake_retry)
    _seed_pipeline()
    body = _client().post("/api/pipeline/pl_wire/dispatch", json={
        "target_stage": "material", "reason": "先记着", "auto_retry": False,
    }).json()
    assert body["retry"]["retried"] is False
    assert body["requires_retry"] is True


def test_suggestion_retry_actions_run(monkeypatch):
    from clipwright.api import pipeline as pipeline_api

    calls: list[str] = []

    async def fake_retry(pid, agent, request):  # noqa: ANN001
        calls.append(agent)
        return {"pipeline_id": pid, "status": "retrying", "agent": agent}

    monkeypatch.setattr(pipeline_api, "start_agent_retry", fake_retry)
    _seed_pipeline()

    body = _client().post("/api/pipeline/pl_wire/accept-suggestion", json={
        "suggestion_id": "s1", "action": "retry_with_model", "target_stage": "material",
    }).json()

    assert calls == ["material"]
    assert body["accepted"] is True
    assert body["action"] == "retry_with_model"
    assert body["retry"]["retried"] is True


def test_suggestion_accept_degraded_keeps_old_behaviour():
    _seed_pipeline()
    body = _client().post("/api/pipeline/pl_wire/accept-suggestion").json()
    assert body["accepted_degraded"] is True
    assert body["action"] == "accept_degraded"
    from clipwright.api.pipeline import _pipeline_results

    assert _pipeline_results["pl_wire"]["shared_data"]["accepted_degraded"] is True


def test_suggestion_manual_is_not_executed(monkeypatch):
    from clipwright.api import pipeline as pipeline_api

    async def fake_retry(pid, agent, request):  # noqa: ANN001
        raise AssertionError("manual 不应触发重试")

    monkeypatch.setattr(pipeline_api, "start_agent_retry", fake_retry)
    body = _client().post("/api/pipeline/pl_wire/accept-suggestion", json={
        "action": "manual", "target_stage": "edit",
    }).json()
    assert body["accepted"] is False
    assert "人工处理" in body["reason"]


def test_suggestion_retry_without_stage_says_so(monkeypatch):
    """没有 target_stage 时不能假装重试了。"""
    from clipwright.api import pipeline as pipeline_api

    async def unused(pid, agent, request):  # noqa: ANN001
        raise AssertionError("没有阶段不该调用重试")

    monkeypatch.setattr(pipeline_api, "start_agent_retry", unused)
    _seed_pipeline()
    body = _client().post("/api/pipeline/pl_wire/accept-suggestion", json={
        "action": "redo_stage", "target_stage": "",
    }).json()
    assert body["accepted"] is False
    assert body["retry"]["retried"] is False
    assert "目标阶段" in body["retry"]["reason"]
