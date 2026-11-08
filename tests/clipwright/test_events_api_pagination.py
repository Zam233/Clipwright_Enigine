"""事件接口的续传/向前分页（PRD §7.2 恢复算法 / D2 元数据）。"""

from __future__ import annotations

import asyncio

import pytest

from clipwright.services import task_events


class _Req:
    class _State:
        user_id = None
        user_role = None

    state = _State()
    headers: dict = {}

    def __getitem__(self, key):  # pragma: no cover
        raise KeyError(key)


@pytest.fixture(autouse=True)
def _clean():
    task_events.clear()
    yield
    task_events.clear(drop_spill=True)


def _seed(task_id: str, n: int = 10) -> None:
    for i in range(n):
        task_events.emit(task_id, "narration", f"e{i}", {"i": i})


def test_since_seq_backfill():
    from clipwright.api.task import get_task_events

    _seed("task_ev1", 5)
    out = asyncio.run(get_task_events("task_ev1", _Req(), since_seq=3, before_seq=0, limit=0))
    assert [e["seq"] for e in out["items"]] == [4, 5]
    assert out["since_seq"] == 3 and out["count"] == 2


def test_before_seq_pages_backwards_with_limit():
    from clipwright.api.task import get_task_events

    _seed("task_ev2", 10)
    out = asyncio.run(get_task_events("task_ev2", _Req(), since_seq=0, before_seq=11, limit=3))
    # 往前翻：取紧邻当前窗口的 3 条
    assert [e["seq"] for e in out["items"]] == [8, 9, 10]
    assert out["has_more"] is True


def test_limit_on_full_stream_keeps_earliest():
    from clipwright.api.task import get_task_events

    _seed("task_ev3", 10)
    out = asyncio.run(get_task_events("task_ev3", _Req(), since_seq=0, before_seq=0, limit=4))
    assert [e["seq"] for e in out["items"]] == [1, 2, 3, 4]
    assert out["has_more"] is True


def test_metadata_reports_range():
    from clipwright.api.task import get_task_events

    _seed("task_ev4", 3)
    out = asyncio.run(get_task_events("task_ev4", _Req(), since_seq=0, before_seq=0, limit=0))
    assert out["oldest_available_seq"] == 1
    assert out["latest_seq"] == 3
    assert out["has_more"] is False
    assert "spilled_max_seq" in out


def test_empty_task_returns_zero_range():
    from clipwright.api.task import get_task_events

    out = asyncio.run(get_task_events("task_ev_empty", _Req(), since_seq=0,
                                      before_seq=0, limit=0))
    assert out["count"] == 0 and out["items"] == []
    assert out["oldest_available_seq"] == 0 and out["latest_seq"] == 0
