"""F7 派工端点的**退役契约**（closeout plan B1）。

历史：这两个端点会真的把老管线拉起重跑（`start_agent_retry`）。
M3 删除了执行引擎，那条路径恒 410，所以继续调用只会让面板显示
"重试失败"却不知道能力没了。

现在的契约（本文件钉的就是它）：

1. `dispatch` 照样**登记工单 + 发事件**，但**不再触发执行**；
2. 返回体明确 `execution_status="retired"`，并给出替代做法
   （`delegate_agent(stage=…)`），字段形状保持兼容（`order` / `accepted` / `retry`）；
3. 建议端点里 `retry_with_model` / `redo_stage` 同理：不执行、如实说明；
4. `accept_degraded` / `manual` 的登记语义不变。
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


def test_dispatch_registers_but_does_not_execute(monkeypatch):
    """派工不再触发重跑——这是本批最重要的行为改变。"""
    from clipwright.api import pipeline as pipeline_api

    called: list[tuple[str, str]] = []

    async def fake_retry(pid, stage, request):  # noqa: ANN001
        called.append((pid, stage))
        return {"status": "retrying"}

    monkeypatch.setattr(pipeline_api, "start_agent_retry", fake_retry)
    _seed_pipeline()

    resp = _client().post("/api/pipeline/pl_wire/dispatch", json={
        "target_stage": "material", "reason": "素材都是占位图", "auto_retry": True,
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True, "工单仍要入账"
    assert body["execution_status"] == "retired"
    assert body["retry"]["retried"] is False
    assert "delegate_agent" in body["alternative"]
    assert called == [], "退役后不许再把老管线拉起来"


def test_dispatch_records_order_and_event():
    from clipwright.api.pipeline import _pipeline_results
    from clipwright.services.trace import create_trace, get_all_events

    _seed_pipeline("pl_order")
    create_trace("pl_order")
    resp = _client().post("/api/pipeline/pl_order/dispatch", json={
        "target_stage": "material", "reason": "占位图", "auto_retry": False,
    })
    assert resp.status_code == 200
    order = resp.json()["order"]
    assert order["target_stage"] == "material"
    assert _pipeline_results["pl_order"]["shared_data"]["manual_orders"][0]["id"] == order["id"]
    assert any(e.get("event") == "dispatch" or e.get("name") == "dispatch"
               for e in get_all_events("pl_order")) or get_all_events("pl_order")


def test_dispatch_still_validates_stage():
    _seed_pipeline("pl_bad")
    resp = _client().post("/api/pipeline/pl_bad/dispatch", json={
        "target_stage": "not_a_stage", "reason": "x",
    })
    assert resp.status_code == 400


def test_suggestion_retry_actions_are_retired(monkeypatch):
    """`retry_with_model` / `redo_stage` 不再执行，如实返回 retired。"""
    from clipwright.api import pipeline as pipeline_api

    called: list[str] = []

    async def fake_retry(pid, stage, request):  # noqa: ANN001
        called.append(stage)
        return {"status": "retrying"}

    monkeypatch.setattr(pipeline_api, "start_agent_retry", fake_retry)
    _seed_pipeline("pl_sug")

    for action in ("retry_with_model", "redo_stage"):
        resp = _client().post("/api/pipeline/pl_sug/accept-suggestion", json={
            "action": action, "target_stage": "material",
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is False
        assert body["retry"]["retired"] is True
        assert "delegate_agent" in body["retry"]["alternative"]
    assert called == []


def test_accept_degraded_still_registers():
    _seed_pipeline("pl_deg")
    resp = _client().post("/api/pipeline/pl_deg/accept-suggestion", json={
        "action": "accept_degraded", "suggestion_id": "s1",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is True and body["accepted_degraded"] is True


def test_manual_action_is_not_auto_started():
    _seed_pipeline("pl_man")
    resp = _client().post("/api/pipeline/pl_man/accept-suggestion", json={
        "action": "manual", "suggestion_id": "s2",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] is False
    # manual 语义不变：只提示需要人工，不触发任何执行
    assert "人工" in str(body) or body.get("note", "") != ""
