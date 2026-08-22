"""执行追踪 — 实时记录 Pipeline 中各 Agent 的 LLM 调用、Tool 执行、插件使用。"""

from __future__ import annotations

import asyncio
import bisect
import json
import time
from typing import Any, Optional

# 全局追踪存储: pipeline_id → list[trace_event]
_traces: dict[str, list[dict[str, Any]]] = {}

# 生产加固 1.5: SSE 事件驱动通知（替代 0.5s 盲轮询）。
# waiters 存 Future，signal 用 call_soon_threadsafe 跨线程安全唤醒。
_waiters: dict[str, list[asyncio.Future]] = {}


def signal_new_event(pipeline_id: str) -> None:
    """通知该管线有新事件（线程安全，可在 executor 线程调用）。"""
    for fut in list(_waiters.get(pipeline_id, ())):
        if not fut.done():
            fut.get_loop().call_soon_threadsafe(fut.set_result, None)


async def wait_new_event(pipeline_id: str, timeout: float = 1.0) -> None:
    """等待新事件通知；超时静默返回（调用方据此做断连检查等慢循环事务）。"""
    fut = asyncio.get_running_loop().create_future()
    _waiters.setdefault(pipeline_id, []).append(fut)
    try:
        await asyncio.wait_for(fut, timeout)
    except asyncio.TimeoutError:
        pass
    finally:
        lst = _waiters.get(pipeline_id)
        if lst and fut in lst:
            lst.remove(fut)

# 每管线最大事件数（防止内存泄漏）
_MAX_EVENTS_PER_PIPELINE = 5000

# 最大管线数（防止 _traces 键集无限增长）
_MAX_PIPELINES = 1000

# 事件自动清理 TTL（秒）：创建超过此时间的事件在读取时被清除
_EVENT_TTL_SEC = 3600  # 1 小时

# E6: per-pipeline 事件时间索引（与 _traces[pid] 同步 append），
# get_events(since) 用 bisect 二分定位起始下标，避免全量过滤。
_trace_times: dict[str, list[float]] = {}

# 生产加固 1.8: 单调递增序号游标 — SSE 增量读取不再依赖浮点时间戳
# （系统时钟回拨时 time.time() 非单调，会丢事件）。
_seq_counters: dict[str, int] = {}
_seq_index: dict[str, list[int]] = {}  # 与 _traces 同步的 seq 索引（bisect 定位）


def _next_seq(pipeline_id: str) -> int:
    n = _seq_counters.get(pipeline_id, 0) + 1
    _seq_counters[pipeline_id] = n
    return n


def _trim_events(pipeline_id: str) -> None:
    """裁剪管线的事件列表，防止无限增长。"""
    events = _traces.get(pipeline_id)
    if events and len(events) > _MAX_EVENTS_PER_PIPELINE:
        # 保留最后 _MAX_EVENTS_PER_PIPELINE 条
        dropped = len(events) - _MAX_EVENTS_PER_PIPELINE
        _traces[pipeline_id] = events[-_MAX_EVENTS_PER_PIPELINE:]
        _times = _trace_times.get(pipeline_id)
        if _times is not None:
            _trace_times[pipeline_id] = _times[-_MAX_EVENTS_PER_PIPELINE:]
            del _times[:dropped]
        _seqs = _seq_index.get(pipeline_id)
        if _seqs is not None:
            _seq_index[pipeline_id] = _seqs[-_MAX_EVENTS_PER_PIPELINE:]


def _expire_old_events(pipeline_id: str) -> None:
    """惰性清除超过 TTL 的事件：仅当尾部事件过时时才整体裁剪（E6）。

    每次读只检查尾部时间戳，命中 TTL 才重建列表，避免每次读取全量复制。
    """
    events = _traces.get(pipeline_id)
    if not events:
        return
    cutoff = time.time() - _EVENT_TTL_SEC
    if events[-1]["time"] < cutoff:
        _traces[pipeline_id] = [e for e in events if e["time"] >= cutoff]
        _times = _trace_times.get(pipeline_id)
        if _times is not None:
            _trace_times[pipeline_id] = [t for t in _times if t >= cutoff]


def create_trace(pipeline_id: str) -> None:
    """创建新的追踪记录。"""
    _cleanup_stale()
    _traces[pipeline_id] = []
    _trace_times[pipeline_id] = []
    _seq_counters[pipeline_id] = 0
    _seq_index[pipeline_id] = []


def _cleanup_stale() -> None:
    """清理无事件的管线键；超限时按最近事件时间淘汰最旧管线。"""
    if len(_traces) <= _MAX_PIPELINES:
        return
    for pid in [p for p, evs in _traces.items() if not evs]:
        _traces.pop(pid, None)
        _trace_times.pop(pid, None)
    if len(_traces) > _MAX_PIPELINES:
        items = sorted(_traces.items(), key=lambda kv: kv[1][-1]["time"] if kv[1] else 0.0)
        for pid, _ in items[: len(_traces) - _MAX_PIPELINES]:
            _traces.pop(pid, None)
            _trace_times.pop(pid, None)


def add_event(
    pipeline_id: str,
    agent: str,
    event_type: str,
    summary: str,
    detail: Any = None,
) -> None:
    """添加一条追踪事件。

    Args:
        pipeline_id: 管线 ID
        agent: Agent 名称
        event_type: llm / tool / skill / plugin / agent_start / agent_end / info
        summary: 简短描述
        detail: 详细数据（可选）
    """
    now = time.time()
    if pipeline_id not in _traces:
        _traces[pipeline_id] = []
        _trace_times[pipeline_id] = []
        _seq_counters[pipeline_id] = 0
        _seq_index[pipeline_id] = []
    seq = _next_seq(pipeline_id)
    _traces[pipeline_id].append({
        "time": now,
        "seq": seq,
        "agent": agent,
        "type": event_type,
        "summary": summary,
        "detail": detail,
    })
    _times = _trace_times.get(pipeline_id)
    if _times is not None:
        _times.append(now)
    _seqs = _seq_index.get(pipeline_id)
    if _seqs is not None:
        _seqs.append(seq)
    _trim_events(pipeline_id)
    signal_new_event(pipeline_id)


def get_events(pipeline_id: str, since: float = 0) -> list[dict[str, Any]]:
    """获取指定管线自 since 以来的追踪事件。

    E6: 时间单调递增，用 bisect 在索引上二分定位起始下标（O(log n)），
    仅返回尾部切片，避免每次全量过滤。
    """
    _expire_old_events(pipeline_id)
    events = _traces.get(pipeline_id, [])
    if not events:
        return []
    if since <= 0:
        return list(events)
    times = _trace_times.get(pipeline_id)
    if times is not None and len(times) == len(events):
        start = bisect.bisect_left(times, since)
        return events[start:]
    # 索引缺失（兼容旧路径）→ 回退全量过滤
    return [e for e in events if e["time"] >= since]


def get_all_events(pipeline_id: str) -> list[dict[str, Any]]:
    _expire_old_events(pipeline_id)
    return _traces.get(pipeline_id, [])


def get_events_since_seq(pipeline_id: str, after_seq: int) -> list[dict[str, Any]]:
    """生产加固 1.8: 按单调 seq 游标取增量事件（时钟回拨免疫）。"""
    events = _traces.get(pipeline_id, [])
    if not events:
        return []
    seqs = _seq_index.get(pipeline_id)
    if seqs is not None and len(seqs) == len(events):
        start = bisect.bisect_right(seqs, after_seq)
        return events[start:]
    return [e for e in events if e.get("seq", 0) > after_seq]


def clear(pipeline_id: str) -> None:
    _traces.pop(pipeline_id, None)
    _trace_times.pop(pipeline_id, None)
    _seq_counters.pop(pipeline_id, None)
    _seq_index.pop(pipeline_id, None)
    _waiters.pop(pipeline_id, None)


def add_tool_event(tool_name: str, params: dict, pipeline_id: str = "") -> None:
    """推送工具调用事件（按 pipeline_id 隔离，用于 SSE 流式输出）。

    Args:
        tool_name: 工具名称
        params: 工具参数
        pipeline_id: 目标 pipeline ID。为空时不推送（而非推送到所有）。
    """
    import copy
    if not pipeline_id:
        return
    summary = f"🔧 {tool_name}({', '.join(f'{k}={v}' for k, v in list(params.items())[:2])})"
    events = _traces.get(pipeline_id)
    if events is not None:
        now = __import__("time").time()
        seq = _next_seq(pipeline_id)
        events.append({
            "time": now,
            "seq": seq,
            "agent": "tool",
            "type": "tool",
            "summary": summary,
            "detail": {"tool": tool_name, "params": copy.deepcopy(params)},
        })
        _times = _trace_times.get(pipeline_id)
        if _times is not None:
            _times.append(now)
        _seqs = _seq_index.get(pipeline_id)
        if _seqs is not None:
            _seqs.append(seq)
        _trim_events(pipeline_id)
        signal_new_event(pipeline_id)


def format_tool_call(tool_name: str, params: dict) -> str:
    params_str = ", ".join(f"{k}={v}" for k, v in list(params.items())[:3])
    return f"{tool_name}({params_str})"


def format_llm_call(model: str, prompt_preview: str) -> str:
    return f"LLM({model}) → {prompt_preview[:80]}"
