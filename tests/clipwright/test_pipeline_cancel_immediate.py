"""C9: 取消即时性 — cancel 端点中断运行中的后台任务（不再只等下一 agent 边界）。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from unittest.mock import MagicMock

from clipwright.main import app

client = TestClient(app)


def test_cancel_calls_task_cancel_on_running_pipeline() -> None:
    """运行中的管线任务存在 → cancel 端点调用 task.cancel()（即时取消）。"""
    from clipwright.api import pipeline as pipeline_api
    from clipwright.services.pipeline_v2 import clear_cancel
    from clipwright.api.pipeline import create_trace, add_event

    pid = "pl_c9_test"
    task = MagicMock()
    task.done.return_value = False
    pipeline_api._running_pipelines[pid] = task
    create_trace(pid)
    add_event(pid, "system", "info", "started")
    try:
        resp = client.post("/api/pipeline/cancel/pl_c9_test")
        assert resp.status_code == 200, resp.text
        assert resp.json()["status"] == "cancelling"
        task.cancel.assert_called_once()
    finally:
        clear_cancel(pid)
        pipeline_api._running_pipelines.pop(pid, None)
        from clipwright.services.trace import clear as _clear_trace
        _clear_trace(pid)


def test_cancel_no_task_still_marks_cooperative() -> None:
    """任务不在运行（例如已结束）→ 不抛错，仍写协作式取消标志，返回 cancelling。"""
    from clipwright.api.pipeline import create_trace, add_event
    from clipwright.services.pipeline_v2 import clear_cancel, is_cancelled
    pid = "pl_c9_coop"
    create_trace(pid)
    add_event(pid, "system", "info", "started")
    try:
        resp = client.post("/api/pipeline/cancel/pl_c9_coop")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"
        assert is_cancelled(pid) is True
    finally:
        clear_cancel(pid)
        from clipwright.services.trace import clear as _clear_trace
        _clear_trace(pid)


def test_cancel_finished_task_marks_cooperative() -> None:
    """任务已 done → 不调用 cancel（避免对已完成任务二次取消），仅标记协作式。"""
    from clipwright.api import pipeline as pipeline_api
    from clipwright.services.pipeline_v2 import clear_cancel, is_cancelled
    from clipwright.api.pipeline import create_trace, add_event

    pid = "pl_c9_done"
    task = MagicMock()
    task.done.return_value = True
    pipeline_api._running_pipelines[pid] = task
    create_trace(pid)
    add_event(pid, "system", "info", "started")
    try:
        resp = client.post("/api/pipeline/cancel/pl_c9_done")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelling"
        task.cancel.assert_not_called()
        assert is_cancelled(pid) is True
    finally:
        clear_cancel(pid)
        pipeline_api._running_pipelines.pop(pid, None)
        from clipwright.services.trace import clear as _clear_trace
        _clear_trace(pid)
