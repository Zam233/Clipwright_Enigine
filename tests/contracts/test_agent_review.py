"""R10：终态任务只读复盘快照。"""

from __future__ import annotations

from types import SimpleNamespace

from clipwright.services import agent_review


def test_build_review_is_read_only(monkeypatch) -> None:
    task = SimpleNamespace(
        id="task_review",
        status="done",
        terminal_reason="achieved",
        request=SimpleNamespace(topic="t", extra={}),
    )
    runs = [SimpleNamespace(
        stage="material", role="material", kind="stage", status="ok",
        attempt=1, duration_ms=1200, tool_calls=3, summary="ok",
        degradations=[], failure=None,
    )]
    events = [
        SimpleNamespace(type="subagent_tool", detail={"tool": "read_image"}),
        SimpleNamespace(type="subagent_tool", detail={"tool": "read_image"}),
        SimpleNamespace(type="task_end", detail={}),
    ]
    monkeypatch.setattr(agent_review.run_store, "list_task", lambda _tid: runs)
    monkeypatch.setattr(agent_review.task_events, "events", lambda _tid: events)
    monkeypatch.setattr(agent_review.task_blackboard, "get",
                        lambda _tid: {"final_timeline": {}, "script_skeleton": {}})
    monkeypatch.setattr(agent_review, "_report_files", lambda _task: [])

    out = agent_review.build_review(task)
    assert out["read_only"] is True
    assert out["task"]["status"] == "done"
    assert out["runs"][0]["stage"] == "material"
    assert out["tools"]["read_image"] == 2
    assert out["events"]["by_type"]["subagent_tool"] == 2
    assert "final_timeline" in out["blackboard_keys"]
