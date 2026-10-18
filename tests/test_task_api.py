"""C2：Task API（PRD §8.4）。

HTTP 只做建单/查询/控制，执行走后台协程——因为一条任务可能跑十几分钟，
把连接占死会把超时留在最不该出错的地方。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.schema.task import Plan, PlanStep
from clipwright.services import agent_kernel as ak
from clipwright.services import task_events as te


class FixedPlanner:
    async def plan(self, task):  # noqa: ANN001, ANN201
        return Plan(steps=[PlanStep(id="s1", title="探测", capability="media_probe",
                                    args={"input_path": "a.mp4"})])

    async def reflect(self, task, step, outcome):  # noqa: ANN001, ANN201
        return {"action": "continue"}


async def ok_executor(task, step):  # noqa: ANN001, ANN201
    return {"status": "ok", "output": {"duration_sec": 1.0}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    ak.reset_kernel_state()
    te.clear()

    def factory():  # 确定性 Kernel：假规划器 + 假执行器 + 临时检查点目录
        return ak.AgentKernel(ak.KernelDeps(
            planner=FixedPlanner(), executor=ok_executor,
            checkpoint_dir=tmp_path / "tasks",
        ))

    from clipwright.api import task as api

    monkeypatch.setattr(api, "_kernel", factory)
    yield
    ak.reset_kernel_state()
    te.clear()


def _client() -> TestClient:
    from clipwright.api import task as api

    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_create_task_plans_and_does_not_run_when_plan_only():
    body = _client().post("/api/task", json={"topic": "做一个 30 秒视频"}).json()
    task = body["task"]
    assert body["planned"] is True
    assert body["started"] is False          # Q3：默认 plan_only
    assert task["plan"]["steps"][0]["capability"] == "media_probe"
    assert task["status"] == "pending"


def test_get_task_and_events():
    client = _client()
    created = client.post("/api/task", json={"topic": "x"}).json()
    tid = created["task"]["id"]
    got = client.get(f"/api/task/{tid}").json()
    assert got["task"]["id"] == tid
    assert got["events"] >= 1
    events = client.get(f"/api/task/{tid}/events").json()
    assert events["count"] >= 1
    assert events["items"][0]["type"] in ("task_start", "plan")


def test_unknown_task_404():
    assert _client().get("/api/task/task_nope").status_code == 404


def test_pause_cancel_inject():
    client = _client()
    tid = client.post("/api/task", json={"topic": "x"}).json()["task"]["id"]
    assert client.post(f"/api/task/{tid}/pause").json()["status"] == "paused"
    assert client.post(f"/api/task/{tid}/inject", json={"text": "换素材"}).status_code == 200
    assert client.post(f"/api/task/{tid}/cancel").json()["status"] == "cancelled"


def test_replan_rejected_after_terminal():
    client = _client()
    tid = client.post("/api/task", json={"topic": "x"}).json()["task"]["id"]
    client.post(f"/api/task/{tid}/cancel")
    assert client.post(f"/api/task/{tid}/plan").status_code == 409


def test_list_tasks_shape():
    client = _client()
    client.post("/api/task", json={"topic": "a"})
    body = client.get("/api/task").json()
    assert body["count"] >= 1
    assert {"id", "status", "topic", "steps"} <= set(body["items"][0])


def test_events_stream_emits_sse_frames(monkeypatch):
    """SSE：与 trace 流同构（data: 帧 + task_end 收尾）。"""
    from clipwright.api import task as api

    client = _client()
    tid = client.post("/api/task", json={"topic": "x"}).json()["task"]["id"]
    te.emit(tid, "task_end", "done", {})

    app = FastAPI()
    app.include_router(api.router)

    async def collect():
        transport = _FakeRequest()
        resp = await api.stream_task_events(tid, transport)  # type: ignore[arg-type]
        chunks = []
        async for chunk in resp.body_iterator:  # type: ignore[union-attr]
            chunks.append(chunk)
            if "task_end" in str(chunk):
                break
        return chunks

    chunks = asyncio.run(collect())
    assert any("data:" in str(c) for c in chunks)


class _FakeRequest:
    """最小 Request：SSE 生成器只用 is_disconnected()；鉴权只读 state。"""

    state = type("S", (), {"user_id": None, "user_role": None})()

    async def is_disconnected(self) -> bool:
        return False
