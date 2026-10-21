"""C3：管线降级为能力 + 双轨开关 + 老 API 兼容。

对应 PRD A9（老 API 在 agent 模式下仍可用）、A13（off 模式行为不变）。

三条断言最重要：
1. `agent_mode=off` 时 **绝对不创建 Task**（A13 的对照基线，不能"看起来一样"）；
2. `on` 时老客户端拿到的 `pipeline_id` 能继续喂给 `/status`、`/result`、`/cancel`；
3. `run_pipeline` 能力在非 on 模式下明确拒绝，不假装能跑。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.config import settings
from clipwright.schema.pipeline import PipelineRequest
from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import pipeline_capability as pc
from clipwright.services import task_events as te


class FixedPlanner:
    async def plan(self, task):  # noqa: ANN001, ANN201
        return Plan(steps=[PlanStep(id="s1", title="整片生成", capability="run_pipeline",
                                    kind="pipeline", args={"topic": task.request.topic})])

    async def reflect(self, task, step, outcome):  # noqa: ANN001, ANN201
        return {"action": "continue"}


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    ak.reset_kernel_state()
    te.clear()
    pc.clear_results()
    monkeypatch.setattr(settings, "agent_mode", "off", raising=False)
    yield
    ak.reset_kernel_state()
    te.clear()
    pc.clear_results()


# ── 双轨开关 ────────────────────────────────────────────


def test_capability_refuses_when_mode_is_not_on(monkeypatch):
    task = ak.AgentKernel(ak.KernelDeps(planner=FixedPlanner())).create(
        TaskRequest(topic="x"))
    step = PlanStep(id="s1", capability="run_pipeline", kind="pipeline")
    for mode in ("off", "shadow"):
        monkeypatch.setattr(settings, "agent_mode", mode, raising=False)
        out = asyncio.run(pc.run(task, step))
        assert out["status"] == "refused"
        assert "只在 on 模式" in out["error"]


def test_capability_refuses_when_month_budget_exhausted(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)

    async def no_budget():  # noqa: ANN202
        return False, 999

    import clipwright.services.budget as budget_mod

    monkeypatch.setattr(budget_mod, "check_budget", no_budget)
    task = ak.AgentKernel(ak.KernelDeps(planner=FixedPlanner())).create(
        TaskRequest(topic="x"))
    out = asyncio.run(pc.run(task, PlanStep(id="s1", capability="run_pipeline")))
    assert out["status"] == "refused"
    assert "预算" in out["error"]


def test_capability_runs_orchestrator_and_returns_metrics(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    import clipwright.services.budget as budget_mod

    async def ok_budget():  # noqa: ANN202
        return True, 0

    monkeypatch.setattr(budget_mod, "check_budget", ok_budget)

    captured: dict = {}

    class FakeState:
        status = "completed"
        output_path = "out/final.mp4"
        shared_data = {"timeline": {"duration_sec": 42.0, "tracks": [
            {"kind": "video", "clips": [{"id": "c1"}, {"id": "c2"}]},
            {"kind": "audio", "clips": [{"id": "a1"}]},
        ]}}

        def model_dump(self, mode: str = "json"):  # noqa: ANN001, ARG002
            return {"pipeline_id": captured.get("pid"), "status": self.status,
                    "output_path": self.output_path}

    class FakeOrch:
        async def run(self, request, pipeline_id="", task_id=""):  # noqa: ANN001
            captured.update(request=request, pid=pipeline_id, tid=task_id)
            return FakeState()

    import clipwright.services.pipeline_v2 as pv_mod

    monkeypatch.setattr(pv_mod, "PipelineOrchestratorV2", FakeOrch)

    task = ak.AgentKernel(ak.KernelDeps(planner=FixedPlanner())).create(
        TaskRequest(topic="县域经济", persona_id="p1", category_plugin_id="knowledge_long"))
    step = PlanStep(id="s1", capability="run_pipeline", kind="pipeline",
                    args={"topic": "县域经济", "extra_params": {"duration_sec": 300}})
    out = asyncio.run(pc.run(task, step))

    assert out["status"] == "ok"
    assert out["output"]["tracks"] == 2
    assert out["output"]["clips"] == 3
    assert out["output"]["duration_sec"] == 42.0
    # 请求参数合并正确（Step 参数优先，extra_params 带上时长）
    assert isinstance(captured["request"], PipelineRequest)
    assert captured["request"].persona_id == "p1"
    assert captured["request"].extra_params["duration_sec"] == 300
    # 结果可被老 /result 兼容层回读
    assert pc.last_result(task.id) is not None


def test_capability_reports_pipeline_failure_as_step_error(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    import clipwright.services.budget as budget_mod
    import clipwright.services.pipeline_v2 as pv_mod

    async def ok_budget():  # noqa: ANN202
        return True, 0

    monkeypatch.setattr(budget_mod, "check_budget", ok_budget)

    class FakeState:
        status = "failed"
        output_path = None
        shared_data = {}
        error = "剪辑阶段崩了"

        def model_dump(self, mode: str = "json"):  # noqa: ANN001, ARG002
            return {"status": self.status, "error": self.error}

    class FakeOrch:
        async def run(self, request, pipeline_id="", task_id=""):  # noqa: ANN001
            return FakeState()

    monkeypatch.setattr(pv_mod, "PipelineOrchestratorV2", FakeOrch)
    task = ak.AgentKernel(ak.KernelDeps(planner=FixedPlanner())).create(
        TaskRequest(topic="x"))
    out = asyncio.run(pc.run(task, PlanStep(id="s1", capability="run_pipeline")))
    assert out["status"] == "error"
    assert "剪辑阶段崩了" in out["error"]


# ── 老 API 兼容（A9 / A13）──────────────────────────────


def _client() -> TestClient:
    from clipwright.api import pipeline as api

    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def test_off_mode_does_not_create_task(monkeypatch):
    """A13：off 模式下不能偷偷建 Task（否则"行为不变"是假的）。"""
    created: list[str] = []
    monkeypatch.setattr(ak.AgentKernel, "create",
                        lambda self, request, mode="on": created.append(request.topic),
                        raising=True)
    body = {"persona_id": "p1", "category_plugin_id": "knowledge_long", "topic": "选题"}
    try:
        _client().post("/api/pipeline/run-async", json=body)
    except Exception:  # noqa: BLE001 — 老路径可能因缺少依赖/环境而抛，但只要没建 Task 就达标
        pass
    assert created == [], "agent_mode=off 时不应创建 Task"


def test_on_mode_run_async_returns_task_id(monkeypatch):
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    monkeypatch.setattr(ak, "AgentKernel", ak.AgentKernel)
    # 用假 kernel 避免真的起后台任务
    class FakeKernel:
        def create(self, request, mode="on"):  # noqa: ANN001, ANN201
            from clipwright.schema.task import Task

            return Task(id="task_abc123", request=request, mode=mode)

    import clipwright.api.pipeline as api_mod
    import clipwright.api.task as task_api_mod

    monkeypatch.setattr(ak, "AgentKernel", FakeKernel)
    monkeypatch.setattr(task_api_mod, "_schedule", lambda tid: None)

    body = {"persona_id": "p1", "category_plugin_id": "knowledge_long", "topic": "选题"}
    out = _client().post("/api/pipeline/run-async", json=body).json()
    assert out["pipeline_id"] == "task_abc123"
    assert out["task_id"] == "task_abc123"
    assert out["mode"] == "agent"
    assert out["status"] == "started"


def test_status_and_cancel_accept_task_id(monkeypatch):
    from clipwright.schema.task import Task

    task = Task(id="task_zzz", request=TaskRequest(topic="x"))
    task.status = "running"

    class FakeKernel:
        def get(self, task_id):  # noqa: ANN001, ANN201
            return task if task_id == "task_zzz" else None

        def cancel(self, task_id):  # noqa: ANN001, ANN201
            task.status = "cancelled"
            return task

    monkeypatch.setattr(ak, "AgentKernel", FakeKernel)
    client = _client()

    status = client.get("/api/pipeline/status/task_zzz").json()
    assert status["status"] == "running" and status["mode"] == "agent"

    cancelled = client.post("/api/pipeline/cancel/task_zzz").json()
    assert cancelled["status"] == "cancelled"

    result = client.get("/api/pipeline/result/task_zzz").json()
    assert result["task_id"] == "task_zzz"
    assert result["status"] == "cancelled"


def test_result_returns_pipeline_output_when_available(monkeypatch):
    from clipwright.schema.task import Task

    task = Task(id="task_out", request=TaskRequest(topic="x"))
    task.status = "done"
    pc._LAST_RESULT["task_out"] = {"pipeline_id": "pl_x", "status": "completed",
                                  "timeline": {"tracks": []}}

    class FakeKernel:
        def get(self, task_id):  # noqa: ANN001, ANN201
            return task if task_id == "task_out" else None

    monkeypatch.setattr(ak, "AgentKernel", FakeKernel)
    body = _client().get("/api/pipeline/result/task_out").json()
    assert body["status"] == "completed"
    assert body["task_id"] == "task_out"
    assert "timeline" in body


# ── Kernel 侧接线 ────────────────────────────────────────


def test_kernel_executes_run_pipeline_capability(monkeypatch):
    """默认执行器必须真的把 run_pipeline 转给能力，而不是返回"未接入"。"""
    monkeypatch.setattr(settings, "agent_mode", "on", raising=False)
    calls: list[str] = []

    async def fake_run(task, step, owner=""):  # noqa: ANN001
        calls.append(step.capability)
        return {"status": "ok", "output": {"tracks": 1, "clips": 2, "duration_sec": 10.0}}

    monkeypatch.setattr(pc, "run", fake_run)
    k = ak.AgentKernel(ak.KernelDeps(planner=FixedPlanner(), executor=ak.default_executor))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert calls == ["run_pipeline"]
    assert task.status == "done"
