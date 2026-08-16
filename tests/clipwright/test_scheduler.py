"""P8: 定时调度器逻辑测试（Mongo 层用 monkeypatch 隔离）。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from clipwright.services import scheduler


def _doc(sid: str, interval: int = 0, next_at: str = "", active: bool = True) -> dict:
    return {
        "schedule_id": sid,
        "name": f"任务{sid}",
        "interval_sec": interval,
        "daily_hhmm": "",
        "payload": {},
        "owner_id": "",
        "active": active,
        "last_run_at": None,
        "next_run_at": next_at,
        "created_at": "",
    }


def test_due_interval_task() -> None:
    now = datetime.now(timezone.utc)
    due = _doc("s1", interval=60, next_at=(now - timedelta(seconds=1)).isoformat())
    not_due = _doc("s2", interval=60, next_at=(now + timedelta(seconds=30)).isoformat())
    assert scheduler._due(due, now) is True
    assert scheduler._due(not_due, now) is False


def test_due_inactive_never() -> None:
    now = datetime.now(timezone.utc)
    inactive = _doc("s3", interval=60, next_at=(now - timedelta(seconds=1)).isoformat(), active=False)
    assert scheduler._due(inactive, now) is False


def test_compute_next_interval() -> None:
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    nxt = scheduler._compute_next(_doc("s4", interval=90), now)
    assert datetime.fromisoformat(nxt) == now + timedelta(seconds=90)


def test_compute_next_daily_rollover() -> None:
    now = datetime(2026, 1, 1, 23, 30, 0, tzinfo=timezone.utc)
    doc = _doc("s5")
    doc["daily_hhmm"] = "08:00"
    nxt = datetime.fromisoformat(scheduler._compute_next(doc, now))
    # 今日 08:00 已过 → 明日 08:00
    assert nxt.day == 2
    assert (nxt.hour, nxt.minute) == (8, 0)
