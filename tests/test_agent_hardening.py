"""收口批次：任务鉴权 / 事件不阻塞 / 成本如实 / 配置只读（A14）。

这四条是上一轮自查出的缺口，逐条钉成测试：

1. `api/task.py` 所有端点必须有归属校验（jwt 模式下越权要被拒）；
2. `task_events` 落库不能在事件循环里同步写（Mongo 一通就卡任务）；
3. 工具成本：工具自报就记，不自报就记"未知"，**不许伪造 0**；
4. A14：agent 无法改用户配置（没有任何一条能力能写 settings）。
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.schema.task import Plan, PlanStep, TaskRequest
from clipwright.services import agent_kernel as ak
from clipwright.services import task_events as te
from clipwright.tool import surface as sf


# ── 1. 任务端点鉴权 ────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    ak.reset_kernel_state()
    te.clear()
    sf.clear_records()
    sf.clear_cache()

    from clipwright.api import task as api

    # M3：Kernel 只接受"驱动器"注入（没有 planner/executor 了）。
    # 归属校验这类用例根本不跑主循环，用一个不产生任何工具调用的驱动器即可。
    class _NoopDriver:
        async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                           guard=None, on_tool_call=None):
            return {"status": "ok", "finish": {"conclusion": "noop"},
                    "tool_calls": [], "rounds": 1, "stopped_reason": "model_final"}

    monkeypatch.setattr(api, "_kernel", lambda: ak.AgentKernel(ak.KernelDeps(
        driver=_NoopDriver(), checkpoint_dir=tmp_path / "tasks",
    )))
    api.clear_owners()
    yield
    ak.reset_kernel_state()
    te.clear()
    api.clear_owners()


def _client() -> TestClient:
    from clipwright.api import task as api

    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def _as_user(monkeypatch, uid: str | None) -> None:
    """模拟 jwt 模式下的当前用户（off 模式 uid=None）。"""
    import clipwright.authz as authz

    monkeypatch.setattr(authz, "current_user_id", lambda request: uid)
    monkeypatch.setattr(authz, "is_admin", lambda request: False)


def test_owner_is_registered_and_others_are_rejected(monkeypatch):
    _as_user(monkeypatch, "user_a")
    created = _client().post("/api/task", json={"topic": "x"}).json()
    tid = created["task"]["id"]

    from clipwright.api import task as api

    assert api._TASK_OWNERS[tid] == "user_a"

    # 同一个用户能读
    assert _client().get(f"/api/task/{tid}").status_code == 200

    # 换一个人 → 403（读、事件、暂停、取消、插话、重规划全都要拒）
    monkeypatch.setattr(
        __import__("clipwright.authz", fromlist=["current_user_id"]),
        "current_user_id", lambda request: "user_b",
    )
    for method, path, kwargs in [
        ("get", f"/api/task/{tid}", {}),
        ("get", f"/api/task/{tid}/events", {}),
        ("post", f"/api/task/{tid}/pause", {}),
        ("post", f"/api/task/{tid}/cancel", {}),
        ("post", f"/api/task/{tid}/inject", {"json": {"text": "删掉第 3 段"}}),
        ("post", f"/api/task/{tid}/plan", {}),
        ("post", f"/api/task/{tid}/resume", {}),
    ]:
        resp = getattr(_client(), method)(path, **kwargs)
        assert resp.status_code == 403, f"{method.upper()} {path} 应拒绝，实际 {resp.status_code}"


def test_admin_can_access_any_task(monkeypatch):
    from clipwright.authz import is_admin

    _as_user(monkeypatch, "user_a")
    tid = _client().post("/api/task", json={"topic": "x"}).json()["task"]["id"]
    monkeypatch.setattr(
        __import__("clipwright.authz", fromlist=["is_admin"]), "is_admin",
        lambda request: True,
    )
    monkeypatch.setattr(
        __import__("clipwright.authz", fromlist=["current_user_id"]), "current_user_id",
        lambda request: "admin_x",
    )
    assert _client().get(f"/api/task/{tid}").status_code == 200


def test_list_filters_by_owner(monkeypatch):
    _as_user(monkeypatch, "user_a")
    client = _client()
    a1 = client.post("/api/task", json={"topic": "A 的任务"}).json()["task"]["id"]

    monkeypatch.setattr(
        __import__("clipwright.authz", fromlist=["current_user_id"]), "current_user_id",
        lambda request: "user_b",
    )
    a2 = client.post("/api/task", json={"topic": "B 的任务"}).json()["task"]["id"]

    ids_b = [i["id"] for i in client.get("/api/task").json()["items"]]
    assert a2 in ids_b and a1 not in ids_b, "列表不能泄漏别人的任务"


def test_off_mode_has_no_owner_gate(monkeypatch):
    """off/token 模式（无 uid）不受影响——单机部署不能被这条鉴权挡住。"""
    _as_user(monkeypatch, None)
    client = _client()
    tid = client.post("/api/task", json={"topic": "x"}).json()["task"]["id"]
    assert client.get(f"/api/task/{tid}").status_code == 200
    assert client.post(f"/api/task/{tid}/pause").status_code == 200


# ── 2. 事件落库不阻塞事件循环 ─────────────────────────────


def test_persist_offloads_to_thread_when_loop_running(monkeypatch):
    """有事件循环时必须丢线程池：主线程不能被 Mongo 写卡住。"""
    from clipwright.services import task_events as mod

    threads: list[str] = []
    done = threading.Event()

    class FakeCollection:
        def insert_one(self, payload):  # noqa: ANN001
            threads.append(threading.current_thread().name)
            done.set()

    class FakeDB:
        def __getitem__(self, name):  # noqa: ANN001
            return FakeCollection()

    class FakeMongo:
        is_connected = True
        db = FakeDB()

    monkeypatch.setattr(mod, "_mongo_ready", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "clipwright.context",
                        type("ctx", (), {"mongo": FakeMongo}))

    async def scenario():  # noqa: ANN202
        te.emit("task_persist", "plan", "x")
        await asyncio.sleep(0.2)   # 让线程池跑完

    main = threading.current_thread().name
    asyncio.run(scenario())
    assert done.is_set(), "事件应当最终落库"
    assert threads and threads[0] != main, "落库必须发生在非事件循环线程"


def test_persist_sync_when_no_loop(monkeypatch):
    """没有事件循环（脚本/单测）时同步写即可。"""
    from clipwright.services import task_events as mod

    calls: list[str] = []

    class FakeCollection:
        def insert_one(self, payload):  # noqa: ANN001
            calls.append("insert")

    class FakeDB:
        def __getitem__(self, name):  # noqa: ANN001
            return FakeCollection()

    class FakeMongo:
        is_connected = True
        db = FakeDB()

    monkeypatch.setattr(mod, "_mongo_ready", lambda: True)
    monkeypatch.setitem(__import__("sys").modules, "clipwright.context",
                        type("ctx", (), {"mongo": FakeMongo}))
    te.emit("task_sync", "plan", "x")
    assert calls == ["insert"]


# ── 3. 成本如实（工具自报 vs 未知）────────────────────────


def _fake_result(name: str, *, output=None, cost=None):  # noqa: ANN001
    from clipwright.schema.tool import ToolExecResult, ToolStatus

    result = ToolExecResult(status=ToolStatus.SUCCESS, tool_name=name, output=output or {})
    if cost is not None:
        setattr(result, "cost_usd", cost)
    return result


def test_tool_reported_cost_is_recorded(monkeypatch):
    async def fake(cls, name, **kwargs):  # noqa: ANN001, ARG001
        return _fake_result(name, cost=0.02)

    from clipwright.tool.registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "execute", classmethod(fake))
    out = asyncio.run(sf.execute("vision_llm", {"input_path": "a.mp4"}))
    assert out["cost_usd"] == 0.02
    assert out["cost_unknown"] is False
    assert out["record"]["cost_usd"] == 0.02


def test_costly_tool_without_cost_is_marked_unknown(monkeypatch):
    """costly 档但工具没报成本 → 标 unknown，不许装成 0。"""
    async def fake(cls, name, **kwargs):  # noqa: ANN001, ARG001
        return _fake_result(name)

    from clipwright.tool.registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "execute", classmethod(fake))
    out = asyncio.run(sf.execute("vision_llm", {"input_path": "a.mp4"}))
    assert out["cost_usd"] is None
    assert out["cost_unknown"] is True
    assert out["record"]["cost_usd"] is None


def test_read_tool_without_cost_is_not_flagged(monkeypatch):
    async def fake(cls, name, **kwargs):  # noqa: ANN001, ARG001
        return _fake_result(name)

    from clipwright.tool.registry import ToolRegistry

    monkeypatch.setattr(ToolRegistry, "execute", classmethod(fake))
    out = asyncio.run(sf.execute("media_probe", {"input_path": "a.mp4"}))
    assert out["cost_unknown"] is False, "只读工具没成本不是异常"


def test_kernel_counts_unknown_cost_calls(monkeypatch):
    """成本未知要计数（金额闸对它无效），不许当成 0 悄悄过。

    M3：注入点从 planner/executor 换成驱动器——脚本化驱动器按模型的行为
    调用一次 costly 工具并回填"成本未知"。
    """
    class _UnknownCostDriver:
        async def __call__(self, task, *, context_text="", shadow=False,  # noqa: ANN001
                           guard=None, on_tool_call=None):
            result = {"status": "ok", "cost_usd": None, "cost_unknown": True}
            if on_tool_call is not None:
                on_tool_call("vision_llm", result)
            return {"status": "ok", "finish": {"conclusion": "ok"},
                    "tool_calls": [{"tool": "vision_llm"}], "rounds": 2,
                    "stopped_reason": "model_final"}

    k = ak.AgentKernel(ak.KernelDeps(driver=_UnknownCostDriver(),
                                     checkpoint_dir=__import__("tempfile").mkdtemp()))
    task = k.create(TaskRequest(topic="x", plan_only=False))
    task = asyncio.run(k.run(task))
    assert task.usage.unknown_cost_calls == 1
    assert task.usage.cost_usd == 0.0


# ── 4. A14：配置只读 ─────────────────────────────────────


def test_no_capability_can_write_user_config():
    """A14：能力面上不存在任何"改配置"的能力。"""
    from clipwright.services.capability_matrix import TIER_MAP

    suspicious = {"set_settings", "update_config", "write_settings",
                  "set_persona", "update_persona", "set_model_routes"}
    assert not (set(TIER_MAP) & suspicious)
    names = {e.name for e in sf.build_surface()}
    assert not (names & suspicious)


def test_kernel_does_not_import_settings_store():
    """内核不得直接改配置（连 import 都不该有）。"""
    import inspect

    from clipwright.services import agent_kernel as kernel_mod

    source = inspect.getsource(kernel_mod)
    assert "settings_store" not in source
    assert "apply_all" not in source


def test_settings_endpoint_requires_admin(monkeypatch):
    """后端那一侧的兜底：非管理员改配置必须被拒。"""
    from fastapi import HTTPException

    from clipwright.authz import require_admin

    state = type("S", (), {"user_id": "u1", "user_role": "user"})()
    with pytest.raises(HTTPException) as exc:
        require_admin(type("R", (), {"state": state})())
    assert exc.value.status_code == 403

    # 管理员与 off 模式（无 user_id）放行
    require_admin(type("R", (), {"state": type("S", (), {"user_id": "a", "user_role": "admin"})()})())
    require_admin(type("R", (), {"state": type("S", (), {"user_id": None})})())
