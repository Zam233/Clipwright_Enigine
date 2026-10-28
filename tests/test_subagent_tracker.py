"""B7：子代理追踪（注册/状态机/取消/树/事件）+ 端点。

对应 PRD F12 与验收 A14（列表/状态/取消准确）。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from clipwright.services.trace import create_trace, get_events
from clipwright.supervisor import tracker as tr


@pytest.fixture(autouse=True)
def _clean():
    tr.clear_trackers()
    yield
    tr.clear_trackers()


def _client():
    from clipwright.api import supervisor as supervisor_api

    app = FastAPI()
    # subagents 端点已迁到规范前缀 /api/supervisor（admin_router）
    app.include_router(supervisor_api.router)
    app.include_router(supervisor_api.admin_router)
    return TestClient(app)


# ── 状态机与列表 ────────────────────────────────────────────


def test_register_creates_pending_node_and_emits_start_event():
    create_trace("pl_sa")
    t = tr.get_tracker("pl_sa")
    node = t.register("素材可用性核查员", provider="p", model="m")

    assert node.status == "pending"
    assert node.id.startswith("sa_")
    assert node.model == {"provider": "p", "model": "m"}
    assert t.get(node.id) is node
    assert [e["type"] for e in get_events("pl_sa") if e["agent"] == "supervisor"] == ["subagent_start"]


def test_status_transitions_and_progress_event():
    create_trace("pl_sa2")
    t = tr.get_tracker("pl_sa2")
    node = t.register("字幕校对员")

    t.update(node.id, status="running", last_output="正在校对第 3 段…")
    assert t.get(node.id).status == "running"

    t.finish(node.id, status="done", last_output="全部通过", duration_ms=1234)
    final = t.get(node.id)
    assert final.status == "done"
    assert final.duration_ms == 1234
    assert final.last_output == "全部通过"

    types = [e["type"] for e in get_events("pl_sa2") if e["agent"] == "supervisor"]
    assert "subagent_progress" in types
    assert types[-1] == "subagent_end"


def test_tree_nests_children_under_parent():
    t = tr.get_tracker("pl_tree")
    root = t.register("总控核查员")
    child = t.register("子任务执行者", parent_id=root.id)

    tree = t.tree()
    assert len(tree) == 1
    assert tree[0]["id"] == root.id
    assert tree[0]["children"][0]["id"] == child.id


def test_snapshot_counts_by_status():
    t = tr.get_tracker("pl_snap")
    a = t.register("a")
    b = t.register("b")
    t.update(a.id, status="running")
    t.finish(b.id, status="failed", last_output="boom")

    snap = t.snapshot()
    assert snap["count"] == 2
    assert snap["by_status"] == {"running": 1, "failed": 1}
    assert len(snap["tree"]) == 2


# ── 取消 ────────────────────────────────────────────────────


def test_cancel_sets_status_and_runs_callback():
    t = tr.get_tracker("pl_cancel")
    called = {"n": 0}
    node = t.register("可取消", cancel_cb=lambda: called.__setitem__("n", called["n"] + 1))

    assert t.cancel(node.id) is True
    assert t.get(node.id).status == "cancelled"
    assert called["n"] == 1
    # 再取消一次无效
    assert t.cancel(node.id) is False


def test_cancel_rejects_non_cancellable_and_finished():
    t = tr.get_tracker("pl_cancel2")
    fixed = t.register("不可取消", cancellable=False)
    assert t.cancel(fixed.id) is False

    done = t.register("已完成")
    t.finish(done.id, status="done")
    assert t.cancel(done.id) is False


# ── 端点 ────────────────────────────────────────────────────


def test_subagents_endpoint_empty_is_not_404():
    body = _client().get("/api/supervisor/pl_none/subagents").json()
    assert body["count"] == 0
    assert body["tree"] == []
    assert body["nodes"] == []


def test_subagents_endpoint_returns_tree():
    t = tr.get_tracker("pl_api")
    node = t.register("核查员", provider="p", model="m")
    t.update(node.id, status="running")

    body = _client().get("/api/supervisor/pl_api/subagents").json()
    assert body["count"] == 1
    assert body["tree"][0]["role"] == "核查员"
    assert body["by_status"] == {"running": 1}


def test_cancel_endpoint_ok_404_409():
    t = tr.get_tracker("pl_api2")
    node = t.register("可取消")
    client = _client()

    ok = client.post(f"/api/supervisor/pl_api2/subagents/{node.id}/cancel")
    assert ok.status_code == 200
    assert ok.json()["cancelled"] is True

    missing = client.post("/api/supervisor/pl_api2/subagents/sa_missing/cancel")
    assert missing.status_code == 404

    again = client.post(f"/api/supervisor/pl_api2/subagents/{node.id}/cancel")
    assert again.status_code == 409


# ── 注册表有界 ──────────────────────────────────────────────


def test_registry_is_bounded(monkeypatch):
    monkeypatch.setattr(tr, "_MAX_TRACKERS", 2)
    tr.get_tracker("a")
    tr.get_tracker("b")
    tr.get_tracker("c")
    assert tr.tracker_count() == 2
