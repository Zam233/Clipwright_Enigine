"""进度播报（PRD §14）：8 类触发点 + 落 reports + 去重。"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from clipwright.services import progress


@pytest.fixture(autouse=True)
def _clean(tmp_path, monkeypatch):
    monkeypatch.setattr("clipwright.services.workspace.workspace_root",
                        lambda: tmp_path / "workspaces", raising=False)
    progress.reset()
    yield
    progress.reset()


def _task(used: int = 0, limit: int = 80) -> SimpleNamespace:
    return SimpleNamespace(
        id="task_prog",
        limits=SimpleNamespace(max_turns=limit, max_tool_calls=800,
                               max_wall_sec=3600.0, budget_usd=None),
        usage=SimpleNamespace(turns=used, tool_calls=0, wall_sec=0.0, cost_usd=0.0),
    )


def _read_jsonl(tmp_path) -> list[dict]:
    hits = list((tmp_path / "workspaces").rglob("progress.jsonl"))
    if not hits:
        return []
    return [json.loads(ln) for ln in hits[0].read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_emit_writes_event_and_reports(tmp_path):
    emitted: list[tuple] = []
    detail = progress.emit_progress(
        "task_prog", trigger="start", action="开工", conclusion="开始执行",
        next_action="先摸清现状", completed=0, total=6, once_key="start")
    assert detail is not None
    assert detail["percent"] == 0 and detail["progress_text"] == "0/6"

    rows = _read_jsonl(tmp_path)
    assert rows and rows[-1]["trigger"] == "start"
    assert rows[-1]["task_id"] == "task_prog"
    md = next((tmp_path / "workspaces").rglob("progress.md"))
    assert "开工" in md.read_text(encoding="utf-8")


def test_total_unknown_never_fabricates_percent(tmp_path):
    detail = progress.emit_progress("task_prog", trigger="stage_start", stage="edit",
                                    completed=2, total=0, once_key="s")
    assert detail["percent"] is None
    assert "总数未定" in detail["progress_text"]


def test_once_key_dedupes(tmp_path):
    assert progress.emit_progress("task_prog", trigger="start", once_key="start") is not None
    assert progress.emit_progress("task_prog", trigger="start", once_key="start") is None
    assert len([r for r in _read_jsonl(tmp_path) if r["trigger"] == "start"]) == 1


def test_budget_half_fires_once_per_dimension(tmp_path):
    t = _task(used=60, limit=80)          # turns 75%
    first = progress.on_budget_half(t)
    assert first is not None and first["budget"]["dimension"] == "turns"
    assert progress.on_budget_half(t) is None, "同一维度只播一次"


def test_budget_half_skips_below_threshold(tmp_path):
    assert progress.on_budget_half(_task(used=10, limit=80)) is None


def test_progress_event_type_is_registered():
    from clipwright.schema.task import EVENT_TYPES

    assert "progress" in EVENT_TYPES
    # 计数漂移守卫：新增事件类型时同步这里（goal_update / soft_landing 为终止机制引入）
    assert len(EVENT_TYPES) == 31


def test_emit_never_raises_when_event_channel_broken(monkeypatch, tmp_path):
    def _boom(*a, **kw):
        raise RuntimeError("events down")

    monkeypatch.setattr("clipwright.services.task_events.emit", _boom, raising=False)
    detail = progress.emit_progress("task_prog", trigger="start", once_key="start")
    assert detail is not None, "事件通道坏了也要返回 detail（落盘成功）"
    assert _read_jsonl(tmp_path)
