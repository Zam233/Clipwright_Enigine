"""E6: get_events(since) 索引化 — 二分定位起始下标，避免全量过滤。"""

from __future__ import annotations

import time

from clipwright.services import trace as T


def _fresh_trace(pid: str) -> None:
    T.clear(pid)
    T.create_trace(pid)


def _add_events_with_times(pid: str, times: list[float]) -> None:
    for t in times:
        T.add_event(pid, "agent", "info", "e")
        T._traces[pid][-1]["time"] = t
        # 同步更新 E6 时间索引（生产路径 add_event 已同步；测试重写 time 后手动对齐）
        T._trace_times[pid][-1] = t


class TestSinceIndex:
    def test_order_preserved(self) -> None:
        pid = "pl_since_1"
        _fresh_trace(pid)
        base = time.time()
        times = [base + i * 0.5 for i in range(50)]
        _add_events_with_times(pid, times)

        all_evts = T.get_events(pid)
        assert len(all_evts) == 50
        assert [e["time"] for e in all_evts] == times

    def test_since_middle_returns_tail(self) -> None:
        pid = "pl_since_2"
        _fresh_trace(pid)
        base = time.time()
        times = [base + i * 0.5 for i in range(50)]
        _add_events_with_times(pid, times)

        mid = times[30]
        evts = T.get_events(pid, since=mid)
        assert len(evts) == 20  # indices 30..49
        assert evts[0]["time"] == times[30]
        assert evts[-1]["time"] == times[49]

    def test_since_zero_returns_all(self) -> None:
        pid = "pl_since_3"
        _fresh_trace(pid)
        base = time.time()
        _add_events_with_times(pid, [base + i for i in range(10)])
        assert len(T.get_events(pid, since=0)) == 10
        assert len(T.get_events(pid, since=-1)) == 10

    def test_unknown_pipeline_empty(self) -> None:
        T.clear("pl_ghost")
        assert T.get_events("pl_ghost") == []
        assert T.get_events("pl_ghost", since=123.0) == []

    def test_large_tail_query_correctness(self) -> None:
        """10k 事件下 since=末尾 → 只返回尾部，顺序正确。"""
        pid = "pl_since_4"
        _fresh_trace(pid)
        base = time.time()
        n = 10000
        times = [base + i * 0.01 for i in range(n)]
        _add_events_with_times(pid, times)

        evts = T.get_events(pid, since=times[-2] - 0.005)
        assert len(evts) == 2
        assert evts[0]["time"] == times[-2]
        assert evts[-1]["time"] == times[-1]

    def test_expiry_drops_old_events(self, monkeypatch) -> None:
        pid = "pl_since_5"
        _fresh_trace(pid)
        now = time.time()
        old = now - (T._EVENT_TTL_SEC + 100)
        _add_events_with_times(pid, [old, now])
        # 尾部事件新 → 惰性清理不触发（不整体复制），两个事件都在
        evts = T.get_events(pid)
        assert len(evts) == 2

    def test_expiry_trims_when_tail_stale(self) -> None:
        """尾部事件过时 → 惰性整体裁剪（E6 尾部时间戳门控）。"""
        pid = "pl_since_6"
        _fresh_trace(pid)
        now = time.time()
        very_old = now - (T._EVENT_TTL_SEC + 200)
        stale = now - (T._EVENT_TTL_SEC + 100)
        _add_events_with_times(pid, [very_old, stale])
        evts = T.get_events(pid)
        assert evts == []  # 全部过期 → 裁剪后为空
        # 索引同步清空
        assert T._trace_times.get(pid) == []
