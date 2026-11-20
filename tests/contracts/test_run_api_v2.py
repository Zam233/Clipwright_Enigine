"""B10：run 按 run_id 寻址轨迹、REST 投影 v2 字段、重放按派发快照。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from clipwright.api import task as api_task
from clipwright.config import settings
from clipwright.services import agent_run, result_assembler as ra, run_store
from clipwright.services.workspace import Workspace


class _FakeRequest:
    class _State:
        user_id = None
        user_role = None

    state = _State()
    headers: dict[str, str] = {}


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "workspace_dir", tmp_path / "ws")
    monkeypatch.setattr(settings, "project_dir", tmp_path / "projects")
    run_store.clear(memory_only=True)


def _task_run(task_id: str, run_id: str, *, trace_path: str = "", status: str = "ok"):
    return ra.build(run_id=run_id, kind="stage", task_id=task_id, stage="edit",
                    status=status, summary="剪辑完成",
                    tool_calls=[{"tool": "video_trim", "status": "success"}],
                    tool_calls_path=trace_path, attempt=2)


def _write_trace(task_id: str, calls: list[dict]) -> Path:
    base = Workspace(task_id).dirs().root / "reports" / "tool_calls"
    base.mkdir(parents=True, exist_ok=True)
    path = base / "edit-2.json"
    path.write_text(json.dumps({"stage": "edit", "attempt": 2, "run_id": "run_trace",
                                "count": len(calls), "calls": calls}, ensure_ascii=False),
                    encoding="utf-8")
    return path


def test_trace_by_run_id(tmp_path):
    task_id = "task_tc_run"
    path = _write_trace(task_id, [{"tool": "video_trim", "status": "error"},
                                  {"tool": "video_concat", "status": "success"}])
    run_store.save(_task_run(task_id, "run_trace", trace_path=str(path)))
    result = asyncio.run(api_task.task_tool_calls(
        task_id, _FakeRequest(), stage="", attempt=0, run_id="run_trace",
        limit=2000, offset=0))
    assert result["run_id"] == "run_trace"
    assert result["total"] == 2 and result["failed"] == 1
    assert result["stage"] == "edit" and result["attempt"] == 2


def test_trace_run_id_missing_is_404():
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        asyncio.run(api_task.task_tool_calls(
            "task_tc_run", _FakeRequest(), stage="", attempt=0, run_id="nope",
            limit=2000, offset=0))
    assert exc.value.status_code == 404


def test_replay_payload_prefers_spec_snapshot():
    run = {
        "stage": "edit", "brief": "旧 brief",
        "details": {"spec": {"stage": "animation", "brief": "快照 brief",
                             "inputs": {"timeline": {"artifact": "art_1"}},
                             "provider": "tk1688", "model": "m1",
                             "timeout_sec": 0, "max_turns": 8}},
    }
    payload = api_task._replay_payload(run, "edit")
    assert payload["stage"] == "animation"
    assert payload["brief"] == "快照 brief"
    assert payload["inputs"]["timeline"]["artifact"] == "art_1"
    assert payload["provider"] == "tk1688" and payload["model"] == "m1"
    assert payload["max_turns"] == 8
    assert payload["refresh"] is True
    assert "timeout_sec" not in payload  # 0 = 不设上限，不往下传


def test_run_summary_projection_v2(monkeypatch):
    monkeypatch.setattr(agent_run, "list_runs", lambda task_id: [_task_run("t_sum", "r_sum").model_dump(mode="json")])
    out = asyncio.run(api_task.task_runs("t_sum", _FakeRequest()))
    item = out["items"][0]
    assert item["schema_version"] == 2 and item["run_id"] == "r_sum"
    assert item["terminal_reason"] == "completed"
    assert item["tool_calls_total"] == 1
    assert item["trace_state"] in ("partial", "complete", "empty")
    assert item["report_state"] in ("written", "unavailable", "not_applicable")
    assert "handoff_summary" in item and "cost_unknown" in item
