"""任务 → 项目 → 编辑器的闭环（收口批次）。

为什么要测：任务是"出片"，编辑器是"继续剪"。中间如果没有一步"落成项目"，
用户拿到 timeline 却没地方打开——这是产品闭环上的硬伤，不是锦上添花。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from clipwright.schema.task import Plan, PlanStep
from clipwright.services import agent_kernel as ak
from clipwright.services import pipeline_capability as pc
from clipwright.services import task_events as te

TIMELINE = {
    "duration_sec": 42.0,
    "tracks": [
        {"kind": "video", "clips": [{"id": "c1"}, {"id": "c2"}]},
        {"kind": "audio", "clips": [{"id": "a1"}]},
    ],
}


class _Planner:
    async def plan(self, task):  # noqa: ANN001, ANN201
        return Plan(steps=[PlanStep(id="s1", capability="media_probe",
                                    args={"input_path": "a.mp4"})])

    async def reflect(self, task, step, outcome):  # noqa: ANN001, ANN201
        return {"action": "continue"}


async def _ok(task, step):  # noqa: ANN001, ANN201
    return {"status": "ok", "output": {"duration_sec": 1.0}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    ak.reset_kernel_state()
    te.clear()
    pc.clear_results()
    from clipwright.api import task as api

    monkeypatch.setattr(api, "_kernel", lambda: ak.AgentKernel(ak.KernelDeps(
        planner=_Planner(), executor=_ok, checkpoint_dir=tmp_path / "tasks")))
    api.clear_owners()
    # 不落真实项目文件
    from clipwright.api import project as project_api

    created: list[dict] = []

    class _FakeManager:
        def create(self, **kwargs):  # noqa: ANN003
            created.append(kwargs)
            return {"id": f"proj_{len(created)}", "name": kwargs.get("name", "")}

    monkeypatch.setattr(project_api, "_manager", _FakeManager())
    yield created
    api.clear_owners()
    ak.reset_kernel_state()
    pc.clear_results()


def _client() -> TestClient:
    from clipwright.api import task as api

    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _make_task(topic: str = "县域经济") -> str:
    return _client().post("/api/task", json={"topic": topic}).json()["task"]["id"]


def test_result_endpoint_reports_no_timeline(_clean):
    tid = _make_task()
    body = _client().get(f"/api/task/{tid}/result").json()
    assert body["has_timeline"] is False
    assert body["tracks"] == 0


def test_result_endpoint_reports_timeline_metrics(_clean):
    tid = _make_task()
    pc._LAST_RESULT[tid] = {"pipeline_id": "pl_x", "timeline": TIMELINE}
    body = _client().get(f"/api/task/{tid}/result").json()
    assert body["has_timeline"] is True
    assert body["tracks"] == 2
    assert body["clips"] == 3
    assert body["duration_sec"] == 42.0


def test_to_project_creates_project_from_task(_clean):
    tid = _make_task("县域经济为什么能撑起半个制造业")
    pc._LAST_RESULT[tid] = {"pipeline_id": "pl_x", "timeline": TIMELINE}
    out = _client().post(f"/api/task/{tid}/to-project", json={}).json()
    assert out["project_id"] == "proj_1"
    created = _clean
    assert created and created[0]["timeline"] is TIMELINE
    assert "县域经济" in created[0]["name"]


def test_to_project_refuses_without_timeline(_clean):
    tid = _make_task()
    resp = _client().post(f"/api/task/{tid}/to-project", json={})
    assert resp.status_code == 409
    assert "timeline" in resp.json()["detail"]


def test_to_project_uses_given_name(_clean):
    tid = _make_task()
    pc._LAST_RESULT[tid] = {"timeline": TIMELINE}
    out = _client().post(f"/api/task/{tid}/to-project", json={"name": "自定义项目名"}).json()
    assert out["name"] == "自定义项目名"
    assert _clean[0]["name"] == "自定义项目名"


def test_to_project_respects_owner(monkeypatch, _clean):
    import clipwright.authz as authz

    monkeypatch.setattr(authz, "current_user_id", lambda request: "user_a")
    tid = _make_task()
    pc._LAST_RESULT[tid] = {"timeline": TIMELINE}

    monkeypatch.setattr(authz, "current_user_id", lambda request: "user_b")
    resp = _client().post(f"/api/task/{tid}/to-project", json={})
    assert resp.status_code == 403
