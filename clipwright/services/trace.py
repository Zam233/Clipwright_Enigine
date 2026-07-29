"""执行追踪 — 实时记录 Pipeline 中各 Agent 的 LLM 调用、Tool 执行、插件使用。"""

from __future__ import annotations

import json
import time
from typing import Any, Optional

# 全局追踪存储: pipeline_id → list[trace_event]
_traces: dict[str, list[dict[str, Any]]] = {}

# 每管线最大事件数（防止内存泄漏）
_MAX_EVENTS_PER_PIPELINE = 5000

# 最大管线数（防止 _traces 键集无限增长）
_MAX_PIPELINES = 1000

# 事件自动清理 TTL（秒）：创建超过此时间的事件在读取时被清除
_EVENT_TTL_SEC = 3600  # 1 小时


def _trim_events(pipeline_id: str) -> None:
    """裁剪管线的事件列表，防止无限增长。"""
    events = _traces.get(pipeline_id)
    if events and len(events) > _MAX_EVENTS_PER_PIPELINE:
        # 保留最后 _MAX_EVENTS_PER_PIPELINE 条
        _traces[pipeline_id] = events[-_MAX_EVENTS_PER_PIPELINE:]


def _expire_old_events(pipeline_id: str) -> None:
    """清除超过 TTL 的事件。"""
    events = _traces.get(pipeline_id)
    if not events:
        return
    cutoff = time.time() - _EVENT_TTL_SEC
    _traces[pipeline_id] = [e for e in events if e["time"] >= cutoff]


def create_trace(pipeline_id: str) -> None:
    """创建新的追踪记录。"""
    _cleanup_stale()
    _traces[pipeline_id] = []


def _cleanup_stale() -> None:
    """清理无事件的管线键；超限时按最近事件时间淘汰最旧管线。"""
    if len(_traces) <= _MAX_PIPELINES:
        return
    for pid in [p for p, evs in _traces.items() if not evs]:
        _traces.pop(pid, None)
    if len(_traces) > _MAX_PIPELINES:
        items = sorted(_traces.items(), key=lambda kv: kv[1][-1]["time"] if kv[1] else 0.0)
        for pid, _ in items[: len(_traces) - _MAX_PIPELINES]:
            _traces.pop(pid, None)


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
    if pipeline_id not in _traces:
        _traces[pipeline_id] = []
    _traces[pipeline_id].append({
        "time": time.time(),
        "agent": agent,
        "type": event_type,
        "summary": summary,
        "detail": detail,
    })
    _trim_events(pipeline_id)


def get_events(pipeline_id: str, since: float = 0) -> list[dict[str, Any]]:
    """获取指定管线自 since 以来的追踪事件。"""
    _expire_old_events(pipeline_id)
    events = _traces.get(pipeline_id, [])
    return [e for e in events if e["time"] >= since]


def get_all_events(pipeline_id: str) -> list[dict[str, Any]]:
    _expire_old_events(pipeline_id)
    return _traces.get(pipeline_id, [])


def clear(pipeline_id: str) -> None:
    _traces.pop(pipeline_id, None)


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
        events.append({
            "time": __import__("time").time(),
            "agent": "tool",
            "type": "tool",
            "summary": summary,
            "detail": {"tool": tool_name, "params": copy.deepcopy(params)},
        })


def format_tool_call(tool_name: str, params: dict) -> str:
    params_str = ", ".join(f"{k}={v}" for k, v in list(params.items())[:3])
    return f"{tool_name}({params_str})"


def format_llm_call(model: str, prompt_preview: str) -> str:
    return f"LLM({model}) → {prompt_preview[:80]}"
