"""子 agent 可寻址子会话（PRD modern-agent-review M2）。

现场：`agent_run` 早就有 get_run/list_runs/inspect/cancel 这些服务函数与 agent 工具，
但**用户/前端没有入口**——run 详情里点不开一个子会话，也无法追问或重放。
这组测试锁 HTTP 面：列表 / 详情 / 追问（投递到下一次派发）/ 重放（真重跑，不回放）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from clipwright.services import agent_run
from clipwright.services.agent_run import AgentResult


class _FakeRequest:
    class _State:
        user_id = None
        user_role = None

    state = _State()
    headers: dict[str, str] = {}

    def __getitem__(self, key):  # pragma: no cover
        raise KeyError(key)


@pytest.fixture(autouse=True)
def _clean():
    agent_run.reset()
    agent_run._PENDING_NOTES.clear()  # noqa: SLF001
    yield
    agent_run.reset()
    agent_run._PENDING_NOTES.clear()  # noqa: SLF001


def _seed(task_id: str = "task_runs_1") -> AgentResult:
    result = AgentResult(
        run_id="run_a", kind="stage", task_id=task_id, stage="animation",
        status="degraded", attempt=2, summary="动画已编排（弱动效）",
        degradations=[{"kind": "text_motion_weak", "detail": "位移不足"}],
        tool_calls=[{"tool": "mg_generate", "status": "success"}],
        tool_calls_path="reports/tool_calls/animation-2.json",
        outputs={"report_path": "reports/07-animation-2.md"},
        brief="把文字动效做强",
    )
    agent_run._record(result)  # noqa: SLF001
    return result


def _call(coro):
    return asyncio.run(coro)


def test_lists_runs_with_addressable_ids():
    from clipwright.api.task import task_runs

    _seed()
    body = _call(task_runs("task_runs_1", _FakeRequest()))
    assert body["count"] == 1
    run = body["items"][0]
    assert run["run_id"] == "run_a"
    assert run["stage"] == "animation" and run["status"] == "degraded"
    assert run["degradations"] == 1
    assert run["tool_calls_path"].endswith("animation-2.json")


def test_run_detail_returns_full_result():
    from clipwright.api.task import task_run_detail

    _seed()
    body = _call(task_run_detail("task_runs_1", "run_a", _FakeRequest()))
    assert body["run"]["run_id"] == "run_a"
    assert body["run"]["degradations"][0]["kind"] == "text_motion_weak"


def test_run_detail_404_for_unknown_run():
    from fastapi import HTTPException

    from clipwright.api.task import task_run_detail

    with pytest.raises(HTTPException) as ei:
        _call(task_run_detail("task_runs_1", "run_missing", _FakeRequest()))
    assert ei.value.status_code == 404


def test_message_is_delivered_to_next_dispatch():
    from clipwright.api.task import RunMessageBody, task_run_message

    _seed()
    body = _call(task_run_message("task_runs_1", "run_a",
                                  RunMessageBody(text="文字改大一点，别压字幕"), _FakeRequest()))
    assert body["stage"] == "animation" and body["pending"] == 1
    assert agent_run.peek_pending_notes("task_runs_1", "animation") == [
        "文字改大一点，别压字幕"]
    assert agent_run.take_pending_notes("task_runs_1", "animation") == [
        "文字改大一点，别压字幕"]
    assert agent_run.take_pending_notes("task_runs_1", "animation") == [], "只消费一次"


def test_replay_runs_for_real_not_from_cache(monkeypatch):
    from clipwright.api import task as task_api

    _seed()
    seen: list[dict] = []

    async def _fake_execute(name, args, **kw):  # noqa: ANN001, ANN003
        seen.append({"name": name, **args})
        return {"status": "ok", "output": {"run_id": "run_new"}, "replayed": False}

    monkeypatch.setattr("clipwright.tool.surface.execute", _fake_execute, raising=False)
    monkeypatch.setattr(task_api, "_kernel",
                        lambda: SimpleNamespace(get=lambda tid: SimpleNamespace(id=tid)),
                        raising=False)

    body = _call(task_api.task_run_replay("task_runs_1", "run_a", _FakeRequest()))
    assert body["status"] == "ok"
    assert seen and seen[0]["stage"] == "animation"
    assert seen[0]["refresh"] is True, "重放必须显式跳过幂等回放"
    assert seen[0]["brief"] == "把文字动效做强", "重放沿用原来的 brief"


def test_replay_refuses_when_task_absent(monkeypatch):
    from fastapi import HTTPException

    from clipwright.api import task as task_api

    _seed()
    monkeypatch.setattr(task_api, "_kernel", lambda: SimpleNamespace(get=lambda tid: None),
                        raising=False)
    with pytest.raises(HTTPException) as ei:
        _call(task_api.task_run_replay("task_runs_1", "run_a", _FakeRequest()))
    assert ei.value.status_code == 409
